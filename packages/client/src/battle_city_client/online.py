"""One online session, from the client's side: lobby, handover, and a remote run.

:class:`OnlineSession` is the whole of the client's network behaviour and it contains no
network. It consumes decoded :class:`~battle_city_protocol.ServerMessage` values and
produces :class:`~battle_city_protocol.ClientMessage` values; a transport hands it the
former and sends the latter. That is what lets the entire online flow — joining, agreeing
to settings, the handover, play, a disconnect, an ending — be tested with two in-process
peers and no sockets, no pygame and no clock.

The rule this file exists to keep
---------------------------------
**An online client runs no simulation.** There is no :class:`~battle_city_sim.step` call
here, no local state to advance and no outcome this client can decide. What is drawn is
the last snapshot the server sent, what the HUD reads is in that snapshot, and the run
ends when the server says it ended. A client that predicted, or that kept a local run
going while the socket was quiet, would be showing a game that disagrees with the one
being played — and the networking specification is explicit that prediction needs
measured need, versioning and acceptance tests before anyone writes it.

What it will say and when
-------------------------
Outgoing messages are *offered*, never sent from here: each builder returns a message or
``None``, and ``None`` means the session has nothing legitimate to say in its current
phase. Input is offered once per authoritative tick, because a batch is for a tick and a
second one for the same tick would only replace the first — and would spend the server's
accepted rate for nothing.

What is drawn, and what is authoritative
----------------------------------------
:attr:`OnlineSession.board` is the newest snapshot the server sent. It is what the HUD
reads, what the state hash on screen comes from, and what input is offered against.
:attr:`OnlineSession.render_board` is the same board with moving entities drawn a couple
of ticks behind, so that a display running faster than the tick rate, or a link whose
arrivals wobble, does not show the same frame twice. That is presentation and only
presentation -- see :mod:`battle_city_client.interpolation`, and the measurements in
``tests/networking/README.md`` that decided it was worth doing. It is still true that
this client runs no simulation: interpolation never passes the newest snapshot and never
invents an entity.

Competitive modes
-----------------
The lobby view carries which modes the server offers and which it will actually start.
A client shows both and lets a host select a competitive mode, because the setting is
real and versioned; it does not pretend the match will begin. When the server refuses to
start one, the refusal arrives with the reason and is shown as it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from battle_city_protocol import (
    ActionKind,
    ContentRef,
    DirectionCode,
    EventKind,
    InputBatch,
    JoinAccepted,
    JoinRequest,
    LobbyConfigure,
    LobbyInfo,
    LobbyJoin,
    LobbyLeave,
    LobbyMember,
    LobbyReady,
    LobbyStart,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchSettings,
    MatchStarting,
    PlayerAction,
    Rejected,
    RejectionCode,
    ServerMessage,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TeamAssignment,
    TickEvents,
)
from battle_city_sim import Direction

from .intents import PlayerIntent
from .interpolation import SMOOTHING_TICKS, SnapshotInterpolator
from .remote import RemoteBoard, RemoteStateError, board_from_snapshot, terrain_from_rows

WIRE_DIRECTIONS: Final[dict[Direction, DirectionCode]] = {
    Direction.UP: DirectionCode.UP,
    Direction.DOWN: DirectionCode.DOWN,
    Direction.LEFT: DirectionCode.LEFT,
    Direction.RIGHT: DirectionCode.RIGHT,
}
"""The client's facings and the wire's, side by side. The inverse lives on the server."""


class OnlinePhase(Enum):
    """Where this client is in the life of one online session."""

    CONNECTING = "connecting"
    """The link is opening, or the seat request is in flight."""

    LOBBY = "lobby"
    """Seated. Agreeing, or waiting for the host."""

    STARTING = "starting"
    """The match began and this client is proving its membership of the session."""

    PLAYING = "playing"
    """Joined. Everything drawn from here is authoritative."""

    ENDED = "ended"
    """The server closed the session, or the link did."""


@dataclass(slots=True)
class LobbyView:
    """The lobby as this client last saw it. Replaced wholesale, never patched."""

    revision: int
    settings: MatchSettings
    members: tuple[LobbyMember, ...]
    host_slot: int
    startable: bool
    blocked: RejectionCode | None

    def member(self, slot: int) -> LobbyMember | None:
        for member in self.members:
            if member.slot == slot:
                return member
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class OnlineConfig:
    """Everything the client needs to reach one lobby, supplied at launch.

    There is no lobby browser and no account service in this build: a player is given an
    address, a session identifier and a ticket out of band, exactly as the server expects
    them to be arranged. Typing them into the game would need a text-entry screen and a
    place to keep them, and keeping a credential is a decision the product specification
    reserves for a separate proposal about accounts and privacy.
    """

    endpoint: str
    session_id: str
    ticket: str
    display_name: str
    content: ContentRef

    def session(self) -> OnlineSession:
        """A fresh client session for this lobby."""
        return OnlineSession(
            session_id=self.session_id,
            display_name=self.display_name,
            content=self.content,
            ticket=self.ticket,
        )


@dataclass(slots=True)
class OnlineSession:
    """The client's view of one hosted match, and everything it is allowed to say."""

    session_id: str
    display_name: str
    content: ContentRef
    ticket: str
    phase: OnlinePhase = OnlinePhase.CONNECTING
    slot: int | None = None
    host: bool = False
    info: LobbyInfo | None = None
    lobby: LobbyView | None = None
    settings: MatchSettings | None = None
    session: SessionInfo | None = None
    board: RemoteBoard | None = None
    notice: str = ""
    closed_reason: RejectionCode | None = None
    smoothing_ticks: int = field(default=SMOOTHING_TICKS, kw_only=True)
    """Ticks of playback delay the renderer draws with. Zero draws the newest snapshot.

    Read once, when the session is built, because playback delay is a property of the
    client rather than of a match: changing it mid-run would move the picture under the
    player. A measurement that wants the Phase 7 behaviour builds a session with zero.

    Keyword-only, so that adding it did not renumber the positional arguments of a
    session that existed before it. A caller that built one positionally still builds the
    same session, and nothing has to know this field was inserted in the middle.
    """
    _token: str | None = field(default=None, repr=False)
    _sequence: int = 0
    _last_input_tick: int | None = None
    _terrain_tick: int | None = None
    _view: SnapshotInterpolator = field(default_factory=SnapshotInterpolator, repr=False)

    def __post_init__(self) -> None:
        self._view.smoothing_ticks = self.smoothing_ticks

    # -- queries ---------------------------------------------------------------

    @property
    def seated(self) -> bool:
        return self.slot is not None

    @property
    def live(self) -> bool:
        """Whether the session is still going anywhere."""
        return self.phase is not OnlinePhase.ENDED

    @property
    def render_board(self) -> RemoteBoard | None:
        """The board the renderer draws the playfield from. Never the HUD's board.

        It is an authoritative board with moving entities placed between two the server
        sent. Everything a player reads as a number -- the tick, the lives, the hash --
        comes from :attr:`board`, which this never touches.
        """
        drawn = self._view.view()
        return self.board if drawn is None else drawn

    @property
    def outcome(self) -> int | None:
        """The outcome the *server* recorded, or ``None``. The client records none."""
        return None if self.board is None else self.board.outcome

    @property
    def ready(self) -> bool:
        """Whether this client has agreed to the lobby's current settings."""
        if self.lobby is None or self.slot is None:
            return False
        member = self.lobby.member(self.slot)
        return member is not None and member.ready

    def can_start(self) -> bool:
        """Whether this client is the host of a lobby the server says may start."""
        return self.host and self.lobby is not None and self.lobby.startable

    def mode_playable(self, mode: MatchMode) -> bool:
        """Whether the server said it will actually start ``mode``."""
        return self.info is not None and mode in self.info.playable_modes

    # -- outgoing --------------------------------------------------------------

    def join_message(self) -> LobbyJoin:
        """The seat request. Sent once, as soon as the link is open."""
        return LobbyJoin(
            session_id=self.session_id,
            ticket=self.ticket,
            display_name=self.display_name,
            content=self.content,
        )

    def ready_message(self, ready: bool) -> LobbyReady | None:
        """Agree to, or withdraw agreement from, the revision this client is looking at."""
        if self.phase is not OnlinePhase.LOBBY or self.slot is None or self.lobby is None:
            return None
        return LobbyReady(
            session_id=self.session_id,
            slot=self.slot,
            revision=self.lobby.revision,
            ready=ready,
        )

    def configure_message(
        self,
        *,
        mode: MatchMode | None = None,
        level_id: str | None = None,
        teams: tuple[TeamAssignment, ...] = (),
    ) -> LobbyConfigure | None:
        """Propose settings. Only a host has anything to propose."""
        if not self.host or self.phase is not OnlinePhase.LOBBY:
            return None
        if self.slot is None or self.lobby is None:
            return None
        chosen = self.lobby.settings.mode if mode is None else mode
        return LobbyConfigure(
            session_id=self.session_id,
            slot=self.slot,
            revision=self.lobby.revision,
            mode=chosen,
            level_id=self.lobby.settings.level_id if level_id is None else level_id,
            teams=teams,
        )

    def start_message(self) -> LobbyStart | None:
        """Ask to start. The server decides; this client never assumes it worked."""
        if not self.host or self.phase is not OnlinePhase.LOBBY:
            return None
        if self.slot is None or self.lobby is None:
            return None
        return LobbyStart(session_id=self.session_id, slot=self.slot, revision=self.lobby.revision)

    def leave_message(self) -> LobbyLeave | None:
        """Give the seat up on purpose, so the roster says why it changed."""
        if self.phase is not OnlinePhase.LOBBY or self.slot is None:
            return None
        return LobbyLeave(session_id=self.session_id, slot=self.slot)

    def join_request(self) -> JoinRequest | None:
        """Prove membership of the session the lobby started, with the token it sent us."""
        if self.phase is not OnlinePhase.STARTING or self.slot is None or self._token is None:
            return None
        return JoinRequest(
            session_id=self.session_id,
            slot=self.slot,
            token=self._token,
            content=self.content,
        )

    def input_batch(self, intent: PlayerIntent) -> InputBatch | None:
        """Offer this client's intent for the tick the server last reported.

        One batch per authoritative tick. The actions are the three a client is allowed:
        a facing, a shot, and a respawn when this slot has no tank and has lives left.
        Whether any of them is legal for the tick they land on is the server's call, and
        this client does not second-guess it — duplicating a simulation rule here is how
        a client and a server start to disagree.
        """
        if self.phase is not OnlinePhase.PLAYING or self.slot is None or self.board is None:
            return None
        tick = self.board.tick
        if self._last_input_tick is not None and tick <= self._last_input_tick:
            return None
        actions = self._actions(intent)
        if not actions:
            return None
        self._last_input_tick = tick
        self._sequence += 1
        return InputBatch(
            session_id=self.session_id,
            slot=self.slot,
            sequence=self._sequence,
            actions=actions,
        )

    def _actions(self, intent: PlayerIntent) -> tuple[PlayerAction, ...]:
        if self.slot is None or self.board is None:
            return ()
        player = self.board.player(self.slot)
        if player is not None and player.tank_id is None:
            return () if player.lives <= 0 else (PlayerAction(kind=ActionKind.RESPAWN),)
        actions: list[PlayerAction] = []
        if intent.direction is not None:
            actions.append(
                PlayerAction(kind=ActionKind.MOVE, direction=WIRE_DIRECTIONS[intent.direction])
            )
        if intent.fire:
            actions.append(PlayerAction(kind=ActionKind.FIRE))
        return tuple(actions)

    # -- incoming --------------------------------------------------------------

    def apply(self, message: ServerMessage) -> None:
        """Take one authoritative message. Nothing here argues with the server."""
        match message:
            case LobbyWelcome():
                self.slot = message.slot
                self.host = message.host
                self.info = message.lobby
                self.phase = OnlinePhase.LOBBY
                self.notice = ""
            case LobbyState():
                self.lobby = LobbyView(
                    revision=message.revision,
                    settings=message.settings,
                    members=message.members,
                    host_slot=message.host_slot,
                    startable=message.startable,
                    blocked=message.blocked,
                )
            case MatchStarting():
                self.slot = message.slot
                self._token = message.token
                self.session = message.session
                self.settings = message.settings
                self._view.tick_rate = message.session.tick_rate
                self.phase = OnlinePhase.STARTING
                self.notice = ""
            case JoinAccepted():
                self.slot = message.slot
                self.session = message.session
                self._view.tick_rate = message.session.tick_rate
                self.phase = OnlinePhase.PLAYING
            case StateSnapshot():
                self._apply_snapshot(message)
            case TickEvents():
                self._apply_events(message)
            case Rejected():
                self.notice = _refusal_notice(message.code, message.detail)
            case SessionClosed():
                self.phase = OnlinePhase.ENDED
                self.closed_reason = message.code
                self.notice = _refusal_notice(message.code, message.detail)

    def advance_presentation(self, elapsed_ms: int) -> None:
        """Move the render clock on by one frame of wall time.

        This is the only clock in the online path and it drives nothing but drawing. No
        tick is advanced here, no input is offered, and a client that never called it
        would show the newest snapshot every frame exactly as it did before.
        """
        self._view.advance(elapsed_ms)

    def link_lost(self, detail: str = "") -> None:
        """Record that the transport ended without the server saying why.

        A lost link is not a result. The session ends for this client, the reason is
        recorded as the transport failure it was, and no outcome is invented to fill the
        gap — whatever the server went on to decide, this client did not see it.
        """
        if self.phase is OnlinePhase.ENDED:
            return
        self.phase = OnlinePhase.ENDED
        self.notice = detail or "CONNECTION LOST"

    def _apply_snapshot(self, snapshot: StateSnapshot) -> None:
        try:
            if snapshot.grid is not None:
                terrain = terrain_from_rows(snapshot.grid)
                self._terrain_tick = snapshot.tick
            elif self.board is not None:
                terrain = self.board.grid
            else:
                # Everything before the first keyframe is unrenderable, which is a state
                # that lasts exactly until the keyframe that accompanies a join.
                return
            self.board = board_from_snapshot(snapshot, terrain)
            self._view.record(self.board)
        except RemoteStateError as error:
            self.notice = f"SERVER STATE UNREADABLE: {error}"

    def _apply_events(self, events: TickEvents) -> None:
        board = self.board
        if board is None:
            return
        for event in events.events:
            if event.kind is EventKind.TILE_DAMAGED:
                board = board.damaged(event)
        self.board = board
        # The events for a tick arrive after its snapshot, so the board the buffer is
        # holding for that tick is the one without them. Recording it again replaces it.
        self._view.record(board)


def _refusal_notice(code: RejectionCode, detail: str) -> str:
    """Render a server refusal for a player, keeping the stable code visible.

    The code is what a log and a bug report agree on, so it is shown rather than
    translated away; the detail is the server's own words about which bound was broken.
    """
    text = code.value.replace("_", " ").upper()
    return f"{text}: {detail.upper()}" if detail else text
