"""Drawing the gap between two authoritative snapshots, and nothing more.

The Phase 8 measurements in ``tests/networking/README.md`` found one problem worth
fixing on the client and one that is not the client's to fix.

The one worth fixing is *spacing*. A 60 Hz session rendered at 120 fps shows every
snapshot twice — 49.7% duplicated frames on a perfect link — and jitter bunches
snapshots so that some frames receive none and the next receives two and jumps twice as
far. Neither has anything to do with how long a packet took: a constant delay of 0, 50,
100 or 200 ms produced identically smooth motion in every trial.

The one that is not the client's to fix is *response time*. It is a round trip plus a
tick, and the only way to shorten it is to draw the tank somewhere the server has not
put it yet. That is prediction, and the networking specification reserves prediction,
rollback and reconciliation for measured need, protocol versioning and acceptance tests.
The same specification says, in the same paragraph, that *clients may render
interpolation between authoritative snapshots*. This module is exactly that and stops
exactly there.

What is authoritative and what is drawn
---------------------------------------
Nothing here is authoritative and nothing here is new. Every pixel this module produces
lies on the straight line between two positions the server reported, at a moment between
the two ticks it reported them for. It never extrapolates past the newest snapshot, it
never invents an entity, and it never removes one the server still lists.
:attr:`~battle_city_client.online.OnlineSession.board` — what the HUD reads, what the
state hash is taken from, what input is offered against — is untouched by this file.
Only :attr:`~battle_city_client.online.OnlineSession.render_board`, which the renderer
uses for the playfield, passes through here.

The render clock
----------------
Playback runs on its own clock, in *milliticks*: thousandths of an authoritative tick,
in exact integers, so a frame of 17 ms at 60 Hz banks 1020 of them with no rounding
error to accumulate. It is placed once, when the first snapshot arrives, at
:data:`SMOOTHING_TICKS` behind that snapshot's tick. After that each frame advances it
by the frame's own wall time and then nudges it a :data:`CORRECTION_DIVISOR`-th of the
way back towards :data:`SMOOTHING_TICKS` behind the newest snapshot.

Each half of that does a job the other cannot.

* Frame time is what makes playback *smooth*. The newest tick a client holds is a
  staircase, and a clock that chased it would step the way the staircase steps, which is
  the stutter this module exists to remove.
* The nudge is what keeps playback *in the right place*. It is small -- a few per cent
  of playback speed in steady state, well under what an eye resolves -- and it is
  bounded by :data:`MAX_CORRECTION_FRACTION` so that catching up never races the picture
  past a tick of motion in one frame. It closes a whole tick of error in about a second.

Two hard bounds sit behind the nudge for what it cannot absorb. The clock never passes
the newest snapshot's tick, because past that there is nothing to interpolate towards
and guessing would be prediction; and if it falls further behind than
:data:`SMOOTHING_TICKS` plus :data:`CATCHUP_SLACK_TICKS` it is re-anchored outright,
which is what happens after a stall -- a dragged window, a suspended laptop -- and what
stops the client playing an ever-growing backlog back in slow motion.

The delay is the whole cost of this file: the picture is :data:`SMOOTHING_TICKS` ticks
older than the newest snapshot, which at 60 Hz is at most 33 ms added to the response
time measured in the baseline -- the full two ticks on a steady link, and less on a
jittery one, where a frame was already waiting on a snapshot that had not arrived. That
is a deliberate trade and it is recorded in the measurement table beside the stutter it
buys.

Teleports are not interpolated
------------------------------
A respawn moves a tank across the stage between two ticks. Sliding it there would draw
it through walls it was never in. Any single-axis move larger than
:data:`TELEPORT_PIXELS` per tick of the gap — comfortably above the fastest thing the
simulation moves, a projectile at three pixels a tick — is treated as a jump: the entity
is held at the earlier position until the render clock reaches the later tick, and then
it is simply there.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from typing import Final

from .remote import RemoteBoard, RemoteShot, RemoteTank
from .timing import NOMINAL_TICK_RATE

MILLITICKS_PER_TICK: Final[int] = 1000
"""Resolution of the render clock. One tick is a thousand milliticks, exactly."""

SMOOTHING_TICKS: Final[int] = 2
"""How far behind the newest snapshot the picture is played back.

Two ticks is 33 ms at 60 Hz, and it bounds the entire added input delay, so it is as
small as it can be rather than as large as it could safely be. It has to be at least one, or
the clock sits on the newest tick with nothing ahead of it to interpolate towards;
beyond two the measurements showed no further improvement on any condition, because the
jitter the clock has to absorb is handled by the per-frame correction rather than by the
depth of the buffer. Raising it would buy nothing and cost every player response time.
"""

CATCHUP_SLACK_TICKS: Final[int] = 4
"""Extra lag tolerated before the render clock is pulled forward.

Without slack, one late arrival would snap the picture forward and undo the smoothing
it exists to provide. With it, playback absorbs an occasional straggler and only
re-anchors when the client has genuinely stopped keeping up.
"""

TELEPORT_PIXELS: Final[int] = 8
"""Per-tick distance above which a move is a jump rather than travel.

Half a tile. The fastest thing the simulation moves is a projectile at three pixels a
tick, so nothing legitimate reaches this; a respawn crosses the stage and sails past it.
"""

CORRECTION_DIVISOR: Final[int] = 64
"""Fraction of the clock's error closed per frame. See *The render clock*."""

MAX_CORRECTION_FRACTION: Final[int] = 8
"""Hard bound on the correction, as a fraction of the frame's own playback.

An eighth, so playback never runs outside 0.875x to 1.125x of real time however far out
the clock is. The lag bounds below make a large error hard to reach in ordinary play, so
this rarely binds; it is here so that the guarantee about playback speed is a property of
this one constant rather than something that happens to follow from two others.
"""

MAX_BUFFERED_SNAPSHOTS: Final[int] = 16
"""Boards kept for interpolation. Bounded, like every other queue the client holds.

Comfortably more than the deepest the clock may lag, so the board the clock is playing
from is always still here and a long frame cannot interpolate from a board that fell off
the back of the queue.
"""


@dataclass(slots=True)
class SnapshotInterpolator:
    """A short history of authoritative boards, and a clock that plays it back."""

    tick_rate: int = NOMINAL_TICK_RATE
    smoothing_ticks: int = SMOOTHING_TICKS
    _boards: deque[RemoteBoard] = field(
        default_factory=lambda: deque(maxlen=MAX_BUFFERED_SNAPSHOTS), repr=False
    )
    _render: int | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        """Whether this interpolator does anything. Zero smoothing makes it a pass-through."""
        return self.smoothing_ticks > 0

    @property
    def latest(self) -> RemoteBoard | None:
        """The newest authoritative board recorded. This is what the HUD would read."""
        return self._boards[-1] if self._boards else None

    @property
    def render_tick(self) -> int | None:
        """Where the render clock is, in whole ticks, or ``None`` before the first board."""
        return None if self._render is None else self._render // MILLITICKS_PER_TICK

    def record(self, board: RemoteBoard) -> None:
        """Take one authoritative board.

        Recording the same tick twice replaces it rather than queueing it: terrain damage
        arrives as events *after* the snapshot for its tick, and the board the client
        holds is rebuilt when it does. A board older than the newest one is a different
        run — a rejoin, a fresh session — and the history is started again rather than
        interleaved.
        """
        if self._boards:
            newest = self._boards[-1]
            if board.tick == newest.tick:
                self._boards[-1] = board
                return
            if board.tick < newest.tick:
                self._boards.clear()
                self._render = None
        self._boards.append(board)
        self._bound()

    def advance(self, elapsed_ms: int) -> None:
        """Move the render clock on by one frame of wall time."""
        if elapsed_ms < 0:
            raise ValueError(f"elapsed_ms must not be negative, found {elapsed_ms}")
        if not self._boards:
            return
        if self._render is None:
            # First frame of a stream. The clock is placed, not advanced: this frame's
            # wall time was spent waiting for the first snapshot, not playing it back.
            # Placing it here rather than when a board arrives is what makes a join
            # whose keyframe and first tick land on the same frame anchor on the later
            # of the two, instead of starting a tick in debt and spending the next
            # second of play running fast to clear it.
            self._render = self._target()
            return
        step = elapsed_ms * self.tick_rate
        drift = (self._target() - self._render - step) // CORRECTION_DIVISOR
        limit = step // MAX_CORRECTION_FRACTION
        self._render += step + max(-limit, min(limit, drift))
        self._bound()

    def view(self) -> RemoteBoard | None:
        """The board to draw: the newest one, or a blend of the two the clock sits between.

        The returned board is an authoritative one with its moving entities nudged along.
        Terrain, players, lives, the base, the outcome and the state hash are taken whole
        from the earlier of the two snapshots and are never blended, because none of them
        is a position and a halfway-destroyed brick is not a thing the server ever said.
        """
        if not self._boards:
            return None
        if not self.enabled or self._render is None:
            return self._boards[-1]
        index = self._bracket(self._render)
        earlier = self._boards[index]
        if index + 1 >= len(self._boards):
            return earlier
        later = self._boards[index + 1]
        span = later.tick - earlier.tick
        numerator = self._render - earlier.tick * MILLITICKS_PER_TICK
        denominator = span * MILLITICKS_PER_TICK
        if numerator <= 0:
            return earlier
        limit = TELEPORT_PIXELS * span
        return replace(
            earlier,
            tanks=tuple(
                _blend_tank(tank, _tank_at(later, tank.entity_id), numerator, denominator, limit)
                for tank in earlier.tanks
            ),
            shots=tuple(
                _blend_shot(shot, _shot_at(later, shot.entity_id), numerator, denominator, limit)
                for shot in earlier.shots
            ),
        )

    # -- the clock -------------------------------------------------------------

    def _target(self) -> int:
        """Where playback belongs: ``smoothing_ticks`` behind the newest board.

        Not clamped to the oldest board held. A client that has just joined has one
        snapshot and aims at a tick it has not been sent yet, which is right: it draws
        that one snapshot until the stream catches up with the clock, rather than
        anchoring on the newest tick and never building a buffer at all.
        """
        return (self._boards[-1].tick - self.smoothing_ticks) * MILLITICKS_PER_TICK

    def _bound(self) -> None:
        """Keep the render clock behind the newest tick and within reach of it."""
        if self._render is None or not self._boards:
            return
        newest = self._boards[-1].tick
        ceiling = newest * MILLITICKS_PER_TICK
        floor = (newest - self.smoothing_ticks - CATCHUP_SLACK_TICKS) * MILLITICKS_PER_TICK
        if self._render > ceiling:
            self._render = ceiling
        elif self._render < floor:
            self._render = self._target()

    def _bracket(self, render: int) -> int:
        """Index of the newest recorded board at or before ``render``.

        Zero when the clock is still behind everything held, which is what happens for
        the first :data:`SMOOTHING_TICKS` after a join: the oldest board is drawn as it
        stands until the clock reaches it.
        """
        index = 0
        for position, board in enumerate(self._boards):
            if board.tick * MILLITICKS_PER_TICK <= render:
                index = position
            else:
                break
        return index


def _tank_at(board: RemoteBoard, entity_id: int) -> RemoteTank | None:
    for tank in board.tanks:
        if tank.entity_id == entity_id:
            return tank
    return None


def _shot_at(board: RemoteBoard, entity_id: int) -> RemoteShot | None:
    for shot in board.shots:
        if shot.entity_id == entity_id:
            return shot
    return None


def _blend_tank(
    earlier: RemoteTank,
    later: RemoteTank | None,
    numerator: int,
    denominator: int,
    limit: int,
) -> RemoteTank:
    """Move ``earlier`` part of the way towards ``later``, or leave it exactly where it is.

    Everything but the two coordinates is the earlier snapshot's. A facing, a variant and
    an invincibility countdown are states the server held for the whole of the tick being
    drawn, and a tank halfway through turning is not one of them.
    """
    if later is None or _jumped(earlier.x, earlier.y, later.x, later.y, limit):
        return earlier
    return replace(
        earlier,
        x=_lerp(earlier.x, later.x, numerator, denominator),
        y=_lerp(earlier.y, later.y, numerator, denominator),
    )


def _blend_shot(
    earlier: RemoteShot,
    later: RemoteShot | None,
    numerator: int,
    denominator: int,
    limit: int,
) -> RemoteShot:
    if later is None or _jumped(earlier.x, earlier.y, later.x, later.y, limit):
        return earlier
    return replace(
        earlier,
        x=_lerp(earlier.x, later.x, numerator, denominator),
        y=_lerp(earlier.y, later.y, numerator, denominator),
    )


def _jumped(x: int, y: int, other_x: int, other_y: int, limit: int) -> bool:
    return abs(other_x - x) > limit or abs(other_y - y) > limit


def _lerp(start: int, end: int, numerator: int, denominator: int) -> int:
    """``start`` moved ``numerator/denominator`` of the way to ``end``, to the nearest pixel.

    Rounded rather than truncated, and that is not a detail. Truncating makes a tank
    moving two pixels a tick spend a third of its 120 fps frames apparently still, which
    is most of the stutter this module exists to remove.

    Rounded on the magnitude, so a tank driving left is drawn in the mirror of where the
    same tank driving right would be. Rounding the signed value instead sends a halfway
    case towards negative infinity, which is away from the start one way down the stage
    and towards it the other -- a one-pixel difference between two directions that the
    simulation treats identically, and the sort of asymmetry that is invisible until
    something compares two clients.
    """
    distance = end - start
    rounded = (abs(distance) * numerator + denominator // 2) // denominator
    return start + rounded if distance >= 0 else start - rounded
