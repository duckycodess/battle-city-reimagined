"""A deterministic latency harness for the online client, with no sockets in it.

Phase 8 asks what an online match actually *looks like* at 0, 50, 100 and 200 ms, and
whether anything should be done about it. Answering that with real sockets would mean
measuring the loopback interface, the scheduler and the test runner's load as well as
the client, and reporting a different number every run. So nothing here opens one.

How a trial runs
----------------
One integer millisecond clock drives everything. At each millisecond the harness, in
this order: delivers whatever the uplink has for the server, runs an authoritative tick
if one falls due, and runs a client frame if one falls due. Server ticks fall at
``k * 1000 // tick_rate`` and frames at ``j * 1000 // frame_rate``, so a 60 Hz session
rendered at 120 fps gets the real 8/8/9 ms frame pattern rather than a tidy one.

The two sides are the production ones. :class:`~battle_city_server.GameSession` is the
authority, message by message, exactly as the asyncio shell drives it;
:class:`~battle_city_client.online.OnlineSession` is the whole of the client's network
behaviour. Between them sit two :class:`DelayLine` queues and nothing else: no socket,
no event loop, no wall clock, no threads.

What a delay line does, and does not do
---------------------------------------
It delays. It is lossless, it never reorders — an arrival is clamped to be no earlier
than the previous one — and its jitter comes from a seeded :class:`random.Random`, so a
trial replays identically on any machine. Loss and reordering are deliberately absent:
the shipped transport is reliable TCP, so a measurement that dropped messages would be
measuring a transport this build does not have.

Measuring the two axes apart
----------------------------
Constant latency and jitter do different things and are reported separately. A constant
delay shifts the whole snapshot stream later without changing its spacing, so it costs
*response time* and nothing else. Jitter and a frame rate that does not divide the tick
rate change the *spacing* at which new snapshots reach a frame, which is what a player
sees as stutter. :class:`MotionTrace` measures the second; ``response_ms`` on
:class:`TrialResult` measures the first.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any, Final

from battle_city_client.intents import PlayerIntent
from battle_city_client.online import OnlineSession
from battle_city_content import Pack, load_pack
from battle_city_protocol import (
    ClientMessage,
    ContentRef,
    InputAccepted,
    MatchMode,
    MatchSettings,
    MatchStarting,
    Rejected,
    RejectionCode,
    ServerMessage,
)
from battle_city_server import (
    GameSession,
    PlayerCredential,
    SessionConfig,
    SessionLimits,
    content_ref_for,
    stage_from_level,
)
from battle_city_sim import Direction, Tile, state_hash

TICK_RATE: Final[int] = 60
"""The session cadence every trial runs at, matching the shipped default."""

MILLISECONDS_PER_SECOND: Final[int] = 1000

SESSION_ID: Final[str] = "latency-trial"
PACK_ID: Final[str] = "latency-pack"
PACK_VERSION: Final[str] = "1.0.0"
LEVEL_ID: Final[str] = "latency-lane"
TOKEN: Final[str] = "token-latency-0001"
SLOT: Final[int] = 1

GRID_SIZE: Final[int] = 16
BASE_CELL: Final[tuple[int, int]] = (8, 15)
SPAWN_CELL: Final[tuple[int, int]] = (1, 8)
ENEMY_CELLS: Final[tuple[tuple[int, int], ...]] = ((1, 1), (14, 1))

TANK_SPEED: Final[int] = 2
"""Pixels a tank advances per tick, restated here so a threshold can be read as pixels."""

LANE_START_X: Final[int] = SPAWN_CELL[0] * 16
LANE_END_X: Final[int] = (GRID_SIZE - 1) * 16
"""Where the measured tank starts and where the stage edge stops it."""

TRAVEL_TICKS: Final[int] = (LANE_END_X - LANE_START_X) // TANK_SPEED
"""Ticks of uninterrupted rightward travel the lane affords: 112 at the shipped speed."""

HOLD_RIGHT: Final[PlayerIntent] = PlayerIntent(direction=Direction.RIGHT, fire=False)
"""The one input every trial uses: hold right, never fire. Steady motion is measurable."""


# -- content ------------------------------------------------------------------


def _rows() -> list[str]:
    """Sixteen rows of open ground with one home base, as tile-code strings.

    Open deliberately. The lane the measured tank drives down has to be free of terrain
    so that every pixel of its motion is the simulation's constant speed and not a wall
    it stopped against: a trace with a collision in it measures the stage, not the link.
    """
    cells = [[Tile.EMPTY.value for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[BASE_CELL[1]][BASE_CELL[0]] = Tile.HOME.value
    return ["".join(str(value) for value in row) for row in cells]


def _level_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": LEVEL_ID,
        "name": "Latency Lane",
        "grid": {"width": GRID_SIZE, "height": GRID_SIZE, "rows": _rows()},
        "spawns": {
            "players": [{"slot": SLOT, "x": SPAWN_CELL[0], "y": SPAWN_CELL[1]}],
            "enemies": [{"x": x, "y": y} for x, y in ENEMY_CELLS],
        },
    }


def write_pack(root: Path) -> Pack:
    """Write and load the one-lane measurement stage as a real content pack.

    Loaded through :func:`battle_city_content.load_pack` rather than assembled by hand,
    so the stage every measurement runs on passed the content schema, the validator and
    the simulation's stage contract.
    """
    levels = root / "levels"
    levels.mkdir(parents=True, exist_ok=True)
    (levels / f"{LEVEL_ID}.json").write_text(json.dumps(_level_document()), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "id": PACK_ID,
        "version": PACK_VERSION,
        "name": "Latency measurement pack",
        "content_schema_version": 1,
        "authors": ["Battle City Reimagined contributors"],
        "license": {
            "spdx_id": "NOASSERTION",
            "notice": "Original layout written for the networking measurements of this rebuild.",
        },
        "levels": [{"id": LEVEL_ID, "path": f"levels/{LEVEL_ID}.json"}],
    }
    path = root / "pack.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return load_pack(path)


def content_ref(pack: Pack) -> ContentRef:
    return content_ref_for(pack, pack.level(LEVEL_ID))


def session_config(pack: Pack, *, limits: SessionLimits | None = None) -> SessionConfig:
    return SessionConfig(
        session_id=SESSION_ID,
        stage=stage_from_level(pack.level(LEVEL_ID)),
        content=content_ref(pack),
        credentials=(PlayerCredential(slot=SLOT, token=TOKEN),),
        seed=11,
        tick_rate=TICK_RATE,
        limits=limits if limits is not None else SessionLimits(),
    )


# -- the link -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkProfile:
    """One reproducible network condition, in whole milliseconds.

    ``delay_ms`` is one way, so a round trip costs twice it. ``jitter_ms`` is the
    symmetric bound on the wobble added to each delivery, which keeps the mean delay
    equal to ``delay_ms`` and so keeps the jitter axis independent of the latency axis.
    """

    name: str
    delay_ms: int = 0
    jitter_ms: int = 0
    seed: int = 1

    @property
    def round_trip_ms(self) -> int:
        return 2 * self.delay_ms

    @property
    def delay_ticks(self) -> int:
        """One-way delay expressed in whole authoritative ticks, rounded down."""
        return self.delay_ms * TICK_RATE // MILLISECONDS_PER_SECOND


class DelayLine[T]:
    """A lossless, in-order queue that holds each item for a bounded delay.

    Arrival times are forced to be non-decreasing. That is what makes the queue a model
    of the reliable ordered stream the client actually has: jitter may bunch two
    messages together, but it can never deliver the second before the first.
    """

    __slots__ = ("_delay_ms", "_jitter_ms", "_last_arrival", "_queue", "_rng")

    def __init__(self, *, delay_ms: int, jitter_ms: int, rng: Random) -> None:
        self._delay_ms = delay_ms
        self._jitter_ms = jitter_ms
        self._rng = rng
        self._queue: deque[tuple[int, T]] = deque()
        self._last_arrival = 0

    def send(self, now_ms: int, item: T) -> None:
        wobble = 0 if self._jitter_ms == 0 else self._rng.randint(-self._jitter_ms, self._jitter_ms)
        arrival = max(now_ms + max(self._delay_ms + wobble, 0), self._last_arrival)
        self._last_arrival = arrival
        self._queue.append((arrival, item))

    def take(self, now_ms: int) -> tuple[T, ...]:
        """Everything due at or before ``now_ms``, oldest first."""
        ready: list[T] = []
        while self._queue and self._queue[0][0] <= now_ms:
            ready.append(self._queue.popleft()[1])
        return tuple(ready)

    def __len__(self) -> int:
        return len(self._queue)


# -- what a trial reports ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MotionTrace:
    """The rendered position of one tank, frame by frame, and what it says.

    The samples are whatever the client would have blitted: one integer per rendered
    frame, taken from the same board the renderer draws. A frame before the first
    snapshot has nothing to draw and is recorded as ``None``.

    Every reading below is taken over the *moving window* — from the last frame that
    still showed the starting position to the first that showed the final one — because
    the frames before the tank sets off and after it stops against the stage edge are
    genuinely still and would otherwise be counted as stutter.
    """

    samples: tuple[int | None, ...]

    @property
    def window(self) -> tuple[int, ...]:
        drawn = [sample for sample in self.samples if sample is not None]
        if len(drawn) < 2:
            return tuple(drawn)
        finish = max(drawn)
        start = next((index for index, x in enumerate(drawn) if x > drawn[0]), None)
        if start is None:
            return tuple(drawn)
        end = next(index for index, x in enumerate(drawn) if x == finish)
        return tuple(drawn[start - 1 : end + 1])

    @property
    def steps(self) -> tuple[int, ...]:
        window = self.window
        return tuple(b - a for a, b in zip(window, window[1:], strict=False))

    @property
    def frames(self) -> int:
        """Frames inside the moving window that had a previous frame to differ from."""
        return len(self.steps)

    @property
    def still_frames(self) -> int:
        """Frames that redrew the tank where the last frame left it, mid-motion.

        This is the stutter reading. A frame that shows no movement while the server is
        reporting movement is a frame the player paid for and did not get.
        """
        return sum(1 for step in self.steps if step == 0)

    @property
    def max_step(self) -> int:
        """The largest single-frame jump, in pixels. The other half of stutter."""
        return max((abs(step) for step in self.steps), default=0)

    @property
    def still_permille(self) -> int:
        """Still frames per thousand, so a threshold can be an integer comparison."""
        return 0 if self.frames == 0 else self.still_frames * 1000 // self.frames


@dataclass(frozen=True, slots=True, kw_only=True)
class TrialResult:
    """Everything one run of :func:`run_trial` observed."""

    profile: LinkProfile
    frame_rate: int
    trace: MotionTrace
    frames: int
    server_ticks: int
    batches_sent: int
    accepted: int
    accepted_ticks: tuple[int, ...]
    collapsed: int
    rejections: tuple[tuple[str, int], ...]
    first_batch_ms: int | None
    first_motion_ms: int | None
    state_hash: str
    final_tick: int

    @property
    def rate_limited(self) -> int:
        return dict(self.rejections).get(RejectionCode.RATE_LIMITED.value, 0)

    @property
    def commandless_ticks(self) -> int:
        """Authoritative ticks inside the input window that carried no input from us.

        The window runs from the first tick the server accepted a batch for to the last.
        A tick inside it with no accepted batch is a tick the server ran while this
        player was holding a direction down and had nothing to apply for them.
        """
        if not self.accepted_ticks:
            return 0
        distinct = set(self.accepted_ticks)
        return (max(distinct) - min(distinct) + 1) - len(distinct)

    @property
    def response_ms(self) -> int | None:
        """Milliseconds from offering the first input to seeing the tank move.

        This is the constant-latency reading: a round trip, plus the tick the server
        applied the input on, plus whatever the client's own presentation delays it by.
        """
        if self.first_batch_ms is None or self.first_motion_ms is None:
            return None
        return self.first_motion_ms - self.first_batch_ms


# -- the trial ----------------------------------------------------------------


@dataclass(slots=True)
class _Inbox:
    """The replies the client read, tallied as they arrived."""

    accepted: int = 0
    accepted_ticks: list[int] = field(default_factory=list)
    collapsed: int = 0
    rejections: dict[str, int] = field(default_factory=dict)

    def record(self, message: ServerMessage) -> None:
        if isinstance(message, InputAccepted):
            self.accepted += 1
            # The server keeps one batch per slot per tick: a second batch accepted for
            # a tick that already had one replaced it, and the first batch's actions
            # never reached the simulation. Counting it here is the only place the
            # collapse is visible without reaching inside the server.
            if message.tick in self.accepted_ticks:
                self.collapsed += 1
            self.accepted_ticks.append(message.tick)
        elif isinstance(message, Rejected):
            code = message.code.value
            self.rejections[code] = self.rejections.get(code, 0) + 1


def run_trial(
    pack: Pack,
    *,
    profile: LinkProfile,
    frame_rate: int,
    duration_ms: int = 3000,
    limits: SessionLimits | None = None,
) -> TrialResult:
    """Play one match over ``profile`` and report what the client drew."""
    game = GameSession(session_config(pack, limits=limits))
    connection = game.connect()
    session = _client_for(game, pack)

    rng = Random(profile.seed)
    downlink: DelayLine[ServerMessage] = DelayLine(
        delay_ms=profile.delay_ms, jitter_ms=profile.jitter_ms, rng=rng
    )
    uplink: DelayLine[ClientMessage] = DelayLine(
        delay_ms=profile.delay_ms, jitter_ms=profile.jitter_ms, rng=rng
    )

    request = session.join_request()
    assert request is not None, "the handover should leave the session ready to join"
    uplink.send(0, request)

    inbox = _Inbox()
    samples: list[int | None] = []
    batches_sent = 0
    first_batch_ms: int | None = None
    first_motion_ms: int | None = None
    start_x: int | None = None
    next_tick = 0
    next_frame = 0
    server_ticks = 0

    for now_ms in range(duration_ms + 1):
        for outgoing in uplink.take(now_ms):
            for reply in game.handle(connection, outgoing):
                downlink.send(now_ms, reply.message)

        if now_ms >= _instant(next_tick, TICK_RATE):
            for reply in game.advance_tick():
                downlink.send(now_ms, reply.message)
            next_tick += 1
            server_ticks += 1

        if now_ms < _instant(next_frame, frame_rate):
            continue
        next_frame += 1

        for incoming in downlink.take(now_ms):
            inbox.record(incoming)
            session.apply(incoming)

        batch = session.input_batch(HOLD_RIGHT)
        if batch is not None:
            uplink.send(now_ms, batch)
            batches_sent += 1
            if first_batch_ms is None:
                first_batch_ms = now_ms

        drawn = _drawn_x(session)
        samples.append(drawn)
        if drawn is not None:
            if start_x is None:
                start_x = drawn
            elif first_motion_ms is None and drawn != start_x:
                first_motion_ms = now_ms

    return TrialResult(
        profile=profile,
        frame_rate=frame_rate,
        trace=MotionTrace(tuple(samples)),
        frames=len(samples),
        server_ticks=server_ticks,
        batches_sent=batches_sent,
        accepted=inbox.accepted,
        accepted_ticks=tuple(inbox.accepted_ticks),
        collapsed=inbox.collapsed,
        rejections=tuple(sorted(inbox.rejections.items())),
        first_batch_ms=first_batch_ms,
        first_motion_ms=first_motion_ms,
        state_hash=state_hash(game.state),
        final_tick=game.state.tick,
    )


def _client_for(game: GameSession, pack: Pack) -> OnlineSession:
    """A client session that has just been handed its credential by a lobby.

    The lobby itself is not run here. What matters to a latency measurement is the
    playing phase, and the handover is one message; ``tests/multiplayer`` is where the
    lobby that mints it is exercised end to end.
    """
    session = OnlineSession(
        session_id=SESSION_ID,
        display_name="pilot",
        content=content_ref(pack),
        ticket="ticket-latency-000",
    )
    session.apply(
        MatchStarting(
            session_id=SESSION_ID,
            slot=SLOT,
            token=TOKEN,
            session=game.info,
            settings=MatchSettings(
                mode=MatchMode.COOP,
                level_id=LEVEL_ID,
                content=content_ref(pack),
                tick_rate=TICK_RATE,
                max_players=1,
            ),
        )
    )
    return session


def _drawn_x(session: OnlineSession) -> int | None:
    """The horizontal pixel the renderer would blit this client's tank at."""
    board = session.board
    if board is None:
        return None
    tank = board.tank_of(SLOT)
    return None if tank is None else tank.x


def _instant(index: int, rate: int) -> int:
    """When event ``index`` of a ``rate``-per-second series falls, in whole milliseconds."""
    return index * MILLISECONDS_PER_SECOND // rate
