"""Watching a session this client is not playing in.

:class:`SpectatorView` is the client's half of the server's spectator seam. It is handed
the authoritative frames a session produces — the terms and a keyframe when it starts
watching, a snapshot and that tick's events for every tick after, and the closing notice
— and it turns them into the same :class:`~battle_city_client.remote.RemoteBoard` the
online session gives the renderer. From the renderer's point of view a spectated match
and a played one are the same board.

What is missing is the point
----------------------------
There is no slot here, no ticket, no credential, no sequence number and no builder that
returns a :class:`~battle_city_protocol.ClientMessage`. A spectator cannot move, fire,
respawn, ready up, configure a lobby or start a match, because this class has no method
that produces anything a server would read. Compare
:class:`~battle_city_client.online.OnlineSession`, which offers a message for every one
of those: the difference between the two files is the whole of "a spectator cannot play".

It also steps nothing. Like the online session it holds no
:class:`~battle_city_sim.SimulationState`, runs no rules engine and decides no outcome;
what is drawn is the last snapshot the server sent.

In process, for now
-------------------
This view is registered directly on a running server session inside the same process —
it satisfies the server's observer protocol structurally, which is how the client package
avoids depending on the server package. Admitting a spectator over a socket needs a
protocol message, an admission rule and a decision about what a spectator may see of a
match in progress; none of that is here, and a view built from frames is the half that
does not change when it lands.
"""

from __future__ import annotations

from battle_city_protocol import (
    EventKind,
    GameEvent,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TickEvents,
)

from .remote import RemoteBoard, RemoteStateError, board_from_snapshot, terrain_from_rows


class SpectatorView:
    """The authoritative frames a watcher has seen, as something a renderer can draw."""

    __slots__ = ("_board", "_closing", "_events", "_notice", "_session", "_watching")

    def __init__(self) -> None:
        self._session: SessionInfo | None = None
        self._board: RemoteBoard | None = None
        self._events: tuple[GameEvent, ...] = ()
        self._closing: SessionClosed | None = None
        self._notice: str = ""
        self._watching = False

    # -- what the renderer and a test read -------------------------------------

    @property
    def session(self) -> SessionInfo | None:
        """The terms the session was opened under, or ``None`` before it was."""
        return self._session

    @property
    def board(self) -> RemoteBoard | None:
        """The last authoritative board, or ``None`` before the first keyframe."""
        return self._board

    @property
    def tick(self) -> int:
        """The tick of the last board. Zero before one arrived."""
        return 0 if self._board is None else self._board.tick

    @property
    def events(self) -> tuple[GameEvent, ...]:
        """The events of the most recent tick, for a HUD or a sound cue."""
        return self._events

    @property
    def watching(self) -> bool:
        """Whether frames are still arriving."""
        return self._watching and self._closing is None

    @property
    def closing(self) -> SessionClosed | None:
        """The notice the session ended with, or ``None`` while it has not."""
        return self._closing

    @property
    def notice(self) -> str:
        """A short line about the last thing that went wrong, or ``""``."""
        return self._notice

    # -- the observer surface, and the whole of it -----------------------------

    def opened(self, info: SessionInfo, snapshot: StateSnapshot) -> None:
        """Start watching from ``snapshot``, which the server sends as a keyframe."""
        self._session = info
        self._watching = True
        self._closing = None
        self._notice = ""
        self._apply(snapshot)

    def observed(self, snapshot: StateSnapshot, events: TickEvents | None) -> None:
        """One authoritative tick. Ignored once the session has closed."""
        if self._closing is not None:
            return
        self._watching = True
        self._apply(snapshot)
        self._events = () if events is None else events.events
        if events is not None:
            self._apply_damage(events)

    def closed(self, notice: SessionClosed) -> None:
        """The session ended. The last board stays, so the final tick remains on screen."""
        self._closing = notice
        self._watching = False

    # -- internals -------------------------------------------------------------

    def _apply(self, snapshot: StateSnapshot) -> None:
        """Read one snapshot against the terrain this view is holding.

        A keyframe replaces the terrain; a snapshot before the first keyframe is not
        renderable and is dropped, which cannot happen through
        :meth:`opened` and can only happen to a view driven by hand.
        """
        try:
            if snapshot.grid is not None:
                terrain = terrain_from_rows(snapshot.grid)
            elif self._board is not None:
                terrain = self._board.grid
            else:
                return
            self._board = board_from_snapshot(snapshot, terrain)
        except RemoteStateError as error:
            self._notice = f"SERVER STATE UNREADABLE: {error}"

    def _apply_damage(self, events: TickEvents) -> None:
        """Keep the terrain current between keyframes, exactly as a player's view does."""
        board = self._board
        if board is None:
            return
        for event in events.events:
            if event.kind is EventKind.TILE_DAMAGED:
                board = board.damaged(event)
        self._board = board
