"""The lobby: who is playing, what they agreed to play, and when it starts.

Before this module a session's stage, content, rules, seed and per-slot tokens were
arranged out of band and handed to the server as one frozen value. That is still exactly
what a :class:`~battle_city_server.session.GameSession` receives — nothing about the
authority model moved. What changed is who arranges it: a :class:`Lobby` is the out-of-
band arrangement, made in the open, by the people who are going to play.

What the lobby owns
-------------------
Seats, presence, readiness, the match settings and the moment of start. Every one of
them is server state: a client asks, and the server decides. A client cannot name its
own slot, cannot award itself a team, cannot mint a credential and cannot start a match
it is not the host of.

Agreement is explicit and revisioned
------------------------------------
Readiness is agreement to a *specific* configuration, named by its revision. When the
host changes the mode, the stage or the teams, the revision rises and every readiness is
cleared, so consent never carries over to settings nobody saw. A ready or start message
built against an older revision is refused with
:data:`~battle_city_protocol.codes.RejectionCode.SETTINGS_STALE` rather than applied.

Competitive modes are configurable and are not startable
--------------------------------------------------------
A lobby will let a host select free-for-all or team battle, will carry the teams, and
will record the choice in versioned settings. It will not start one. The shared
simulation has a single player faction, player projectiles pass through player tanks,
and the only outcomes it records are "the base was destroyed" and "the players were
eliminated": there is no player-versus-player damage and no competitive result. Starting
a match under those rules and calling it a duel would be a lie told by the server, so
the lobby refuses with
:data:`~battle_city_protocol.codes.RejectionCode.MODE_UNSUPPORTED` and says so in the
roster, before anyone presses start. Competitive play needs simulation rules that do not
exist yet; those belong to an accepted gameplay proposal and to the simulation package,
not to a postprocessing step here.

Handover
--------
:class:`MatchSession` is a lobby that becomes a game session on the same connections.
At start it mints one membership token per seated slot and sends it to that connection
alone — never in the broadcast roster — and then every later message from that
connection is the game session's business. There is no reconnect and no host migration:
a host that leaves ends the lobby, and a player that drops after start loses the slot,
exactly as before.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass, field
from typing import Final

from battle_city_protocol import (
    MAX_LEVELS_PER_LOBBY,
    MAX_LOBBY_MEMBERS,
    MAX_TICK_RATE,
    TEAM_MODES,
    ClientMessage,
    ContentRef,
    InputBatch,
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
    MessageError,
    Rejected,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
)
from battle_city_protocol.validation import require_token
from battle_city_sim import DEFAULT_RULES, Rules, SimulationState, Stage

from .config import (
    DEFAULT_LIMITS,
    DEFAULT_TICK_RATE,
    PlayerCredential,
    ServerConfigurationError,
    SessionConfig,
    SessionLimits,
)
from .content import rules_digest
from .logs import content_label, log_event, session_logger
from .session import GameSession, Reply

PLAYABLE_MODES: Final[frozenset[MatchMode]] = frozenset({MatchMode.COOP})
"""The modes this build will actually start.

Co-op is the one the shared simulation supports honestly: players on one side, enemies
on the other, one base to defend, and the outcomes the simulation already records. The
competitive modes are offered, configured and recorded; they are not started. See the
module docstring for why that is a refusal rather than a gap.
"""

OFFERED_MODES: Final[tuple[MatchMode, ...]] = tuple(MatchMode)
"""Every mode a host may select. Selecting one is not the same as being able to run it."""

TOKEN_BYTES: Final[int] = 24
"""Entropy in a minted membership token, before URL-safe encoding widens it to 32 chars."""

_DUMMY_TICKET: Final[str] = "0" * 32
"""Compared against when no ticket matches, so a miss costs the same work as a hit."""


class LobbyNotStartedError(RuntimeError):
    """The authoritative state was asked for before a match existed.

    A lobby has no simulation. Raising says so; returning an empty board would be a
    server inventing a run to keep a caller happy.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyTicket:
    """A preconfigured seat and the ticket that claims it.

    Seats are bound to tickets exactly as session slots were bound to credentials: a
    lobby decides seating before anyone connects, so admission proves membership rather
    than negotiating it, and no client can talk its way into being player one.

    The ticket is a secret. It is compared with :func:`hmac.compare_digest`, it is never
    logged, and it is never echoed into a rejection detail.
    """

    slot: int
    ticket: str
    host: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyLevel:
    """One stage a lobby offers, already validated and already mapped.

    The stage and the content reference are built by the caller — through
    :func:`~battle_city_server.content.stage_from_level` and
    :func:`~battle_city_server.content.content_ref_for` — so a lobby never reads a file
    and a level that could not be played is reported at configuration time rather than
    at start.
    """

    level_id: str
    stage: Stage
    content: ContentRef

    @property
    def seats(self) -> frozenset[int]:
        """Slots this stage has a spawn for. A roster must fit inside it."""
        return frozenset(spawn.slot for spawn in self.stage.player_spawns)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyConfig:
    """One lobby, described completely before anyone connects."""

    session_id: str
    tickets: tuple[LobbyTicket, ...]
    levels: tuple[LobbyLevel, ...]
    seed: int
    capacity: int | None = None
    mode: MatchMode = MatchMode.COOP
    tick_rate: int = DEFAULT_TICK_RATE
    rules: Rules = DEFAULT_RULES
    limits: SessionLimits = field(default=DEFAULT_LIMITS)
    cheats_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.tickets:
            raise ServerConfigurationError("a lobby needs at least one ticket")
        if len(self.tickets) > MAX_LOBBY_MEMBERS:
            raise ServerConfigurationError(f"a lobby seats at most {MAX_LOBBY_MEMBERS} players")
        seen: set[int] = set()
        hosts = 0
        for ticket in self.tickets:
            if ticket.slot in seen:
                raise ServerConfigurationError(f"duplicate ticket for slot {ticket.slot}")
            seen.add(ticket.slot)
            hosts += 1 if ticket.host else 0
            _require_usable_ticket(ticket)
        if hosts != 1:
            raise ServerConfigurationError(f"a lobby needs exactly one host ticket, found {hosts}")
        if not self.levels:
            raise ServerConfigurationError("a lobby needs at least one level")
        if len(self.levels) > MAX_LEVELS_PER_LOBBY:
            raise ServerConfigurationError(f"a lobby offers at most {MAX_LEVELS_PER_LOBBY} levels")
        if len({level.level_id for level in self.levels}) != len(self.levels):
            raise ServerConfigurationError("a lobby must not offer one level twice")
        if not 1 <= self.tick_rate <= MAX_TICK_RATE:
            raise ServerConfigurationError(f"tick_rate must be 1 to {MAX_TICK_RATE}")
        if self.capacity is not None and not 1 <= self.capacity <= len(self.tickets):
            raise ServerConfigurationError(f"capacity must be 1 to {len(self.tickets)}")
        if self.cheats_enabled and self.mode is not MatchMode.COOP:
            raise ServerConfigurationError("cheats must be disabled outside co-op")

    @property
    def seats(self) -> int:
        """How many members may be in the lobby at once."""
        return len(self.tickets) if self.capacity is None else self.capacity

    @property
    def host_slot(self) -> int:
        """The seat the host ticket claims. There is exactly one, and it never moves."""
        return next(ticket.slot for ticket in self.tickets if ticket.host)

    def level(self, level_id: str) -> LobbyLevel | None:
        for level in self.levels:
            if level.level_id == level_id:
                return level
        return None


@dataclass(slots=True)
class _Seat:
    """Server-side state for one lobby seat."""

    slot: int
    host: bool
    connection: int | None = None
    display_name: str = ""
    ready: bool = False
    team: int | None = None

    @property
    def occupied(self) -> bool:
        return self.connection is not None

    def vacate(self) -> None:
        self.connection = None
        self.display_name = ""
        self.ready = False
        self.team = None


class Lobby:
    """Seats, agreement and the decision to start. No sockets, no clock, no asyncio."""

    def __init__(self, config: LobbyConfig) -> None:
        self._config = config
        self._seats = {
            ticket.slot: _Seat(slot=ticket.slot, host=ticket.host) for ticket in config.tickets
        }
        self._connections: set[int] = set()
        self._connection_seat: dict[int, int] = {}
        self._mode = config.mode
        self._level = config.levels[0]
        self._revision = 0
        self._closed = False
        self._started = False
        self._log = session_logger()

    # -- queries ---------------------------------------------------------------

    @property
    def config(self) -> LobbyConfig:
        return self._config

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def started(self) -> bool:
        return self._started

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def level(self) -> LobbyLevel:
        """The stage the lobby is currently configured to play."""
        return self._level

    @property
    def mode(self) -> MatchMode:
        return self._mode

    @property
    def info(self) -> LobbyInfo:
        """What this lobby can be asked for, told to each member as it arrives."""
        return LobbyInfo(
            capacity=self._config.seats,
            offered_modes=OFFERED_MODES,
            playable_modes=tuple(mode for mode in OFFERED_MODES if mode in PLAYABLE_MODES),
            offered_levels=tuple(level.level_id for level in self._config.levels),
        )

    @property
    def settings(self) -> MatchSettings:
        """The configuration the lobby currently holds, explicit and versioned."""
        return MatchSettings(
            mode=self._mode,
            level_id=self._level.level_id,
            content=self._level.content,
            tick_rate=self._config.tick_rate,
            max_players=self._config.seats,
            cheats_enabled=self._config.cheats_enabled,
        )

    def seat_of(self, connection: int) -> int | None:
        """The slot ``connection`` holds, or ``None`` while it holds none."""
        return self._connection_seat.get(connection)

    def occupied_slots(self) -> tuple[int, ...]:
        return tuple(slot for slot, seat in sorted(self._seats.items()) if seat.occupied)

    def members(self) -> tuple[LobbyMember, ...]:
        """The roster, ascending by slot. Carries no credential and has nowhere to put one."""
        return tuple(
            LobbyMember(
                slot=seat.slot,
                display_name=seat.display_name,
                ready=seat.ready,
                host=seat.host,
                connected=True,
                team=seat.team,
            )
            for _, seat in sorted(self._seats.items())
            if seat.occupied
        )

    def blocked_reason(self) -> RejectionCode | None:
        """Why the match cannot start yet, or ``None`` when it can.

        Checked in order of how fundamental the obstacle is, so a lobby configured for a
        mode this build cannot run says *that*, rather than complaining about readiness
        for a match it was never going to start.
        """
        if self._mode not in PLAYABLE_MODES:
            return RejectionCode.MODE_UNSUPPORTED
        occupied = self.occupied_slots()
        if not occupied:
            return RejectionCode.MEMBERS_NOT_READY
        if not set(occupied) <= self._level.seats:
            return RejectionCode.STAGE_UNSUPPORTED
        if any(not self._seats[slot].ready for slot in occupied):
            return RejectionCode.MEMBERS_NOT_READY
        return None

    def state_message(self) -> LobbyState:
        """The broadcast roster, including whether the match may start and why not."""
        blocked = self.blocked_reason()
        return LobbyState(
            session_id=self._config.session_id,
            revision=self._revision,
            settings=self.settings,
            members=self.members(),
            host_slot=self._config.host_slot,
            startable=blocked is None,
            blocked=blocked,
        )

    # -- lifecycle -------------------------------------------------------------

    def connect(self, identifier: int) -> None:
        """Register a connection that has not claimed a seat yet."""
        self._connections.add(identifier)

    def disconnect(self, connection: int) -> tuple[Reply, ...]:
        """Free whatever seat ``connection`` held and tell the rest of the lobby.

        A host that disappears ends the lobby. There is no host migration in this
        release — the networking specification keeps it for a separate proposal — so the
        alternative would be a lobby nobody can configure or start, which is worse than
        an ending that says why.
        """
        self._connections.discard(connection)
        slot = self._connection_seat.pop(connection, None)
        if slot is None:
            return ()
        seat = self._seats[slot]
        was_host = seat.host
        seat.vacate()
        self._log_event("lobby_seat_vacated", connection=connection, slot=slot)
        if was_host:
            return self.close(RejectionCode.SESSION_CLOSED, "the host left the lobby")
        return self._broadcast()

    def close(self, code: RejectionCode, detail: str = "") -> tuple[Reply, ...]:
        """End the lobby, telling every connection why."""
        if self._closed:
            return ()
        self._closed = True
        self._log_event("lobby_closed", reason=code, detail=_short(detail) or None)
        notice = self.closing_notice(code, detail)
        return tuple(
            Reply(connection=connection, message=notice, close=True)
            for connection in sorted(self._connections)
        )

    def closing_notice(self, code: RejectionCode, detail: str = "") -> SessionClosed:
        return SessionClosed(session_id=self._config.session_id, code=code, detail=_short(detail))

    def rejection(
        self,
        connection: int,
        code: RejectionCode,
        detail: str = "",
        *,
        sequence: int | None = None,
    ) -> Reply:
        """Build a refusal, and record it. The record never carries a ticket."""
        clean = _short(detail)
        self._log_event(
            "lobby_message_refused",
            connection=connection,
            reason=code,
            detail=clean or None,
            level=logging.WARNING,
        )
        return Reply(
            connection=connection,
            message=Rejected(
                session_id=self._config.session_id,
                code=code,
                detail=clean,
                sequence=sequence,
            ),
        )

    # -- messages --------------------------------------------------------------

    def handle(self, connection: int, message: ClientMessage) -> tuple[Reply, ...]:
        """Answer one decoded lobby message. Nothing is mutated before it is accepted."""
        if connection not in self._connections:
            return ()
        if self._closed:
            return (self.rejection(connection, RejectionCode.SESSION_CLOSED),)
        match message:
            case LobbyJoin():
                return self._handle_join(connection, message)
            case LobbyConfigure():
                return self._handle_configure(connection, message)
            case LobbyReady():
                return self._handle_ready(connection, message)
            case LobbyStart():
                return self._handle_start(connection, message)
            case LobbyLeave():
                return self._handle_leave(connection, message)
            case JoinRequest() | InputBatch():
                return (
                    self.rejection(
                        connection,
                        RejectionCode.UNEXPECTED_MESSAGE,
                        "the match has not started",
                    ),
                )

    def _handle_join(self, connection: int, message: LobbyJoin) -> tuple[Reply, ...]:
        if connection in self._connection_seat:
            return (self.rejection(connection, RejectionCode.ALREADY_JOINED),)
        if message.session_id != self._config.session_id:
            return (self.rejection(connection, RejectionCode.UNKNOWN_SESSION),)

        # Every ticket is compared, and the comparison is constant time, so neither the
        # work done nor the answer given says anything about which tickets exist.
        matched: LobbyTicket | None = None
        for ticket in self._config.tickets:
            if hmac.compare_digest(message.ticket, ticket.ticket):
                matched = ticket
        if matched is None:
            hmac.compare_digest(message.ticket, _DUMMY_TICKET)
            return (self.rejection(connection, RejectionCode.INVALID_TOKEN),)

        mismatch = _pack_mismatch(message.content, self._level.content)
        if mismatch is not None:
            return (self.rejection(connection, RejectionCode.CONTENT_MISMATCH, mismatch),)

        seat = self._seats[matched.slot]
        if seat.occupied:
            return (self.rejection(connection, RejectionCode.SLOT_OCCUPIED, f"slot {seat.slot}"),)
        if len(self._connection_seat) >= self._config.seats:
            return (
                self.rejection(connection, RejectionCode.LOBBY_FULL, f"{self._config.seats} seats"),
            )

        seat.connection = connection
        seat.display_name = message.display_name
        seat.ready = False
        seat.team = message.team if self._mode in TEAM_MODES else None
        self._connection_seat[connection] = seat.slot
        self._log_event("lobby_seat_taken", connection=connection, slot=seat.slot)
        # The one lobby message that says "you": sent to the arriving connection alone,
        # ahead of the roster that everybody gets.
        welcome = Reply(
            connection=connection,
            message=LobbyWelcome(
                session_id=self._config.session_id,
                slot=seat.slot,
                host=seat.host,
                lobby=self.info,
            ),
        )
        return (welcome, *self._broadcast())

    def _handle_configure(self, connection: int, message: LobbyConfigure) -> tuple[Reply, ...]:
        seat = self._owned_seat(connection, message.slot)
        if isinstance(seat, Reply):
            return (seat,)
        if not seat.host:
            return (self.rejection(connection, RejectionCode.NOT_HOST),)
        if message.revision != self._revision:
            return (self._stale(connection),)
        level = self._config.level(message.level_id)
        if level is None:
            return (
                self.rejection(
                    connection, RejectionCode.INVALID_FIELD, "level_id is not offered here"
                ),
            )
        for assignment in message.teams:
            target = self._seats.get(assignment.slot)
            if target is None or not target.occupied:
                return (
                    self.rejection(
                        connection,
                        RejectionCode.INVALID_FIELD,
                        f"teams names slot {assignment.slot}, which is not seated",
                    ),
                )

        self._mode = message.mode
        self._level = level
        assigned = {assignment.slot: assignment.team for assignment in message.teams}
        for slot, target in self._seats.items():
            target.team = assigned.get(slot) if message.mode in TEAM_MODES else None
            # Agreement is to a configuration, so changing it withdraws every agreement.
            target.ready = False
        self._revision += 1
        self._log_event(
            "lobby_configured",
            connection=connection,
            slot=seat.slot,
            detail=f"mode={message.mode.value} level={level.level_id} revision={self._revision}",
        )
        return self._broadcast()

    def _handle_ready(self, connection: int, message: LobbyReady) -> tuple[Reply, ...]:
        seat = self._owned_seat(connection, message.slot)
        if isinstance(seat, Reply):
            return (seat,)
        if message.revision != self._revision:
            return (self._stale(connection),)
        seat.ready = message.ready
        return self._broadcast()

    def _handle_start(self, connection: int, message: LobbyStart) -> tuple[Reply, ...]:
        seat = self._owned_seat(connection, message.slot)
        if isinstance(seat, Reply):
            return (seat,)
        if not seat.host:
            return (self.rejection(connection, RejectionCode.NOT_HOST),)
        if message.revision != self._revision:
            return (self._stale(connection),)
        blocked = self.blocked_reason()
        if blocked is not None:
            return (self.rejection(connection, blocked, _blocked_detail(blocked, self._mode)),)
        self._started = True
        self._log_event("lobby_started", connection=connection, slot=seat.slot)
        return ()

    def _handle_leave(self, connection: int, message: LobbyLeave) -> tuple[Reply, ...]:
        seat = self._owned_seat(connection, message.slot)
        if isinstance(seat, Reply):
            return (seat,)
        return self.disconnect(connection)

    # -- credentials -----------------------------------------------------------

    def session_config(self) -> SessionConfig:
        """Describe the session the lobby agreed on, minting one token per seated slot.

        Called once, by :class:`MatchSession`, after :meth:`blocked_reason` has already
        said the match may start. Tokens are minted here and leave through
        :class:`~battle_city_protocol.messages.MatchStarting` to one connection each.
        """
        occupied = self.occupied_slots()
        return SessionConfig(
            session_id=self._config.session_id,
            stage=self._level.stage,
            content=self._level.content,
            credentials=tuple(
                PlayerCredential(slot=slot, token=secrets.token_urlsafe(TOKEN_BYTES))
                for slot in occupied
            ),
            seed=self._config.seed,
            tick_rate=self._config.tick_rate,
            rules=self._config.rules,
            limits=self._config.limits,
        )

    def connection_for(self, slot: int) -> int | None:
        return self._seats[slot].connection

    # -- helpers ---------------------------------------------------------------

    def _owned_seat(self, connection: int, slot: int) -> _Seat | Reply:
        """Resolve the seat a message claims, refusing one the connection does not own.

        This is the whole of the lobby's anti-spoofing rule and it is one check: the
        seat comes from the connection, and a message that names a different slot is
        refused. A client cannot ready another player, cannot move another player onto
        another team and cannot start a match in the host's name.
        """
        held = self._connection_seat.get(connection)
        if held is None:
            return self.rejection(connection, RejectionCode.NOT_JOINED)
        if slot != held:
            return self.rejection(
                connection, RejectionCode.WRONG_PLAYER, f"connection holds slot {held}"
            )
        return self._seats[held]

    def _stale(self, connection: int) -> Reply:
        return self.rejection(
            connection, RejectionCode.SETTINGS_STALE, f"the lobby is at revision {self._revision}"
        )

    def _broadcast(self) -> tuple[Reply, ...]:
        """Send the roster to every seated connection. One message, encoded once."""
        message = self.state_message()
        return tuple(
            Reply(connection=connection, message=message)
            for connection in sorted(self._connection_seat)
        )

    def _log_event(
        self,
        event: str,
        *,
        connection: int | None = None,
        slot: int | None = None,
        reason: RejectionCode | None = None,
        detail: str | None = None,
        level: int = logging.INFO,
    ) -> None:
        log_event(
            self._log,
            event,
            session_id=self._config.session_id,
            tick=0,
            content=content_label(self._level.content),
            rules_digest=rules_digest(self._config.rules),
            slot=slot if slot is not None else self.seat_of(connection) if connection else None,
            connection=connection,
            reason=reason,
            detail=detail,
            level=level,
        )


class MatchSession:
    """A lobby that becomes a game session, on the same connections.

    It is the authority the asyncio shell holds for a hosted match: lobby messages reach
    the lobby, gameplay messages reach the game session, and exactly one of the two is
    in charge at any moment. The transition happens once, in :meth:`_start`, and is not
    reversible: a match that ended does not return to its lobby, because this release has
    no session resumption of any kind.
    """

    def __init__(self, config: LobbyConfig) -> None:
        self._config = config
        self._lobby = Lobby(config)
        self._game: GameSession | None = None
        self._connections: set[int] = set()
        self._next_connection = 1
        self._closed = False

    # -- the authority surface -------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._config.session_id

    @property
    def limits(self) -> SessionLimits:
        return self._config.limits

    @property
    def content(self) -> ContentRef:
        return self._lobby.level.content

    @property
    def rules_digest(self) -> str:
        return rules_digest(self._config.rules)

    @property
    def closed(self) -> bool:
        """Over, whichever phase ended it.

        A lobby can end itself — the host leaves and there is no migration — so this
        asks the phase that is in charge rather than only remembering a call to
        :meth:`close`.
        """
        if self._game is not None:
            return self._game.closed
        return self._closed or self._lobby.closed

    @property
    def ticking(self) -> bool:
        """A lobby has no tick to run; a started match has one every period."""
        return self._game is not None and not self.closed

    @property
    def tick(self) -> int:
        return 0 if self._game is None else self._game.tick

    @property
    def state(self) -> SimulationState:
        if self._game is None:
            raise LobbyNotStartedError("no match has started in this lobby yet")
        return self._game.state

    @property
    def lobby(self) -> Lobby:
        """The lobby, for diagnostics and tests. Nothing else should drive it."""
        return self._lobby

    @property
    def game(self) -> GameSession | None:
        """The game session, once one has started."""
        return self._game

    def connect(self, identifier: int | None = None) -> int:
        connection = self._next_connection if identifier is None else identifier
        self._next_connection = max(self._next_connection, connection) + 1
        self._connections.add(connection)
        self._lobby.connect(connection)
        if self._game is not None:
            self._game.connect(connection)
        return connection

    def disconnect(self, connection: int) -> tuple[Reply, ...]:
        """Drop a connection from whichever phase is in charge, and only that one.

        Exactly one of the two owns membership at any moment, and a disconnect has to
        ask the same question. Once the match has started the lobby is over: its seats
        are the record of who the match began with, and a player that drops is the game
        session's business, which revokes the slot and lets the run carry on.

        Routing a mid-match drop through the lobby as well is not merely redundant, it
        is wrong, because the lobby has a rule the match does not share: a host that
        leaves ends the lobby, since there is no migration and a lobby nobody can
        configure is worse than an ending. A started match has nothing left to host --
        the settings are agreed, the stage is loaded and the simulation is running --
        so applying that rule after the handover would close every other player's
        session because the host's socket went. Co-op continuity after one player drops
        is the behaviour this release promises, and this is where it is kept.
        """
        self._connections.discard(connection)
        if self._game is not None:
            return self._game.disconnect(connection)
        return self._lobby.disconnect(connection)

    def slot_of(self, connection: int) -> int | None:
        if self._game is not None:
            return self._game.slot_of(connection)
        return self._lobby.seat_of(connection)

    def joined_slots(self) -> tuple[int, ...]:
        """Slots that joined the started match. A lobby seat is not one of these."""
        return () if self._game is None else self._game.joined_slots()

    def snapshot(self, *, keyframe: bool) -> StateSnapshot:
        if self._game is None:
            raise LobbyNotStartedError("no match has started in this lobby yet")
        return self._game.snapshot(keyframe=keyframe)

    def handle(self, connection: int, message: ClientMessage) -> tuple[Reply, ...]:
        if connection not in self._connections:
            return ()
        if self._game is not None:
            return self._game.handle(connection, message)
        replies = self._lobby.handle(connection, message)
        if self._lobby.started and self._game is None:
            return replies + self._start()
        return replies

    def advance_tick(self) -> tuple[Reply, ...]:
        """Run one tick, or none at all while the lobby is still deciding.

        Returning nothing is the load-bearing half: no simulation advances before a
        match starts, so a lobby cannot accumulate a run that nobody agreed to.
        """
        if self._game is None:
            return ()
        return self._game.advance_tick()

    def close(
        self, code: RejectionCode = RejectionCode.SESSION_CLOSED, detail: str = ""
    ) -> tuple[Reply, ...]:
        if self._game is not None:
            return self._game.close(code, detail)
        self._closed = True
        return self._lobby.close(code, detail)

    def closing_notice(self, code: RejectionCode, detail: str = "") -> SessionClosed:
        if self._game is not None:
            return self._game.closing_notice(code, detail)
        return self._lobby.closing_notice(code, detail)

    def rejection(
        self,
        connection: int,
        code: RejectionCode,
        detail: str = "",
        *,
        sequence: int | None = None,
    ) -> Reply:
        if self._game is not None:
            return self._game.rejection(connection, code, detail, sequence=sequence)
        return self._lobby.rejection(connection, code, detail, sequence=sequence)

    # -- the handover ----------------------------------------------------------

    def _start(self) -> tuple[Reply, ...]:
        """Turn the agreed configuration into a session and hand out the credentials.

        Each member is told its own token on its own connection. The roster that every
        member already has carries none, and there is no message in which a credential
        reaches anyone but its owner.

        A configuration the session layer refuses — a stage that cannot seat the roster
        slipping past the lobby's own check, for instance — ends the lobby rather than
        leaving it in a state where start was accepted and nothing started.
        """
        settings = self._lobby.settings
        try:
            config = self._lobby.session_config()
        except ServerConfigurationError as error:
            return self._lobby.close(RejectionCode.STAGE_UNSUPPORTED, str(error))
        game = GameSession(config)
        for connection in sorted(self._connections):
            game.connect(connection)
        self._game = game

        replies: list[Reply] = []
        for credential in config.credentials:
            owner = self._lobby.connection_for(credential.slot)
            if owner is None:
                continue
            replies.append(
                Reply(
                    connection=owner,
                    message=MatchStarting(
                        session_id=config.session_id,
                        slot=credential.slot,
                        token=credential.token,
                        session=game.info,
                        settings=settings,
                    ),
                )
            )
        return tuple(replies)


def _pack_mismatch(claimed: ContentRef, expected: ContentRef) -> str | None:
    """Return which part of the content pack disagrees, or ``None``.

    A lobby compares the pack, its version and the schema version, and deliberately not
    the level: the level is what the lobby is still choosing, and a client that has the
    right pack has every level in it. The full reference, level included, is checked
    again when the client joins the session the lobby started, where it is settled.
    """
    for name in ("pack_id", "pack_version", "content_schema_version"):
        if getattr(claimed, name) != getattr(expected, name):
            return f"lobby runs {name} {getattr(expected, name)}"
    return None


def _blocked_detail(code: RejectionCode, mode: MatchMode) -> str:
    """Say why a start was refused, in words a player can act on."""
    match code:
        case RejectionCode.MODE_UNSUPPORTED:
            return f"{mode.value} needs competitive simulation rules this build does not have"
        case RejectionCode.STAGE_UNSUPPORTED:
            return "the chosen stage has no spawn for every player in the lobby"
        case _:
            return "every player must be ready"


def _require_usable_ticket(ticket: LobbyTicket) -> None:
    """Refuse a ticket the protocol would refuse, naming the slot and not the ticket."""
    try:
        require_token("ticket", ticket.ticket)
    except MessageError as error:
        raise ServerConfigurationError(
            f"slot {ticket.slot} has an unusable ticket: {error.detail}"
        ) from error


def _short(detail: str) -> str:
    """Keep a detail printable ASCII and within the protocol's bound."""
    cleaned = "".join(char if " " <= char <= "~" else " " for char in detail)
    return cleaned[:200]
