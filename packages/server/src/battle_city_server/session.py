"""The authoritative session: membership, input ordering and the tick.

This module owns every rule about *who may say what, when*. It is deliberately free of
asyncio, sockets and clocks: a session is advanced by calling :meth:`GameSession.advance_tick`,
and the result of handling a message is a tuple of replies the caller delivers. That
makes the authority model testable one tick at a time, and it keeps the arrival timing
of a socket out of the simulation, as the consistency specification requires.

Order is decided here, not by the network
-----------------------------------------
A batch is queued against an explicit tick. When that tick runs, queued batches are
read in ascending slot order and turned into commands. Two clients whose packets
crossed therefore produce the same tick input either way: arrival order is normalised
into tick order, and the rules engine further resolves entity commands by walking
entities rather than the command list.

A bad batch cannot stall the session
------------------------------------
Each batch is checked against the pre-tick state while commands are built, so an intent
the state cannot support is dropped and answered before the tick starts and every other
client's input for that tick survives. If the rules engine still refuses the assembled
input — it validates a whole tick against conditions this layer deliberately does not
duplicate, such as a respawn cell that another tank is standing on — the tick is run
empty and the contributing slots are told. Either way the tick advances. A session that
could be frozen by one malformed intent would be a denial of service with extra steps.

What a client cannot do
-----------------------
Nothing here reads a score, a seed, a tile or an entity position from a message. A
client names an action kind and, for a move, a direction; the tank identifier comes
from the server's own player record. Membership is proved with a preconfigured token
compared using :func:`hmac.compare_digest`, tokens are never logged, and a disconnect
revokes the credential: this release has no reconnect and no host migration, so a slot
that leaves stays gone and the simulation carries on without it.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from battle_city_protocol import (
    ClientMessage,
    ContentRef,
    InputAccepted,
    InputBatch,
    JoinAccepted,
    JoinRequest,
    PlayerAction,
    Rejected,
    RejectionCode,
    ServerMessage,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TickEvents,
)
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    Command,
    Event,
    InvalidInputError,
    SimulationState,
    TickInput,
    new_game,
    step,
)

from .config import SessionConfig
from .content import rules_digest
from .logs import content_label, log_event, session_logger
from .translation import IllegalActionError, commands_for, protocol_events, snapshot_of

_DUMMY_TOKEN: str = "0" * 32
"""Compared against when a slot is unknown, so a miss costs the same work as a hit."""


@dataclass(frozen=True, slots=True)
class Reply:
    """One message for one connection, and whether the connection is finished.

    The session never writes to a socket. It says what should be sent and to whom, and
    the transport layer decides how; that is what keeps the authority model testable
    without an event loop.
    """

    connection: int
    message: ServerMessage
    close: bool = False


@dataclass(frozen=True, slots=True)
class _Batch:
    """One client's queued intents for one tick, with the sequence that carried them.

    The sequence travels with the actions so a refusal at tick time names the batch that
    was refused rather than whatever the client sent most recently.
    """

    sequence: int
    actions: tuple[PlayerAction, ...]


@dataclass(slots=True)
class _Member:
    """Server-side state for one player slot."""

    slot: int
    connection: int | None = None
    last_sequence: int | None = None
    pending: dict[int, _Batch] = field(default_factory=dict)
    batches_since_tick: int = 0
    revoked: bool = False


class GameSession:
    """One authoritative run with its membership, its input queues and its tick."""

    def __init__(self, config: SessionConfig) -> None:
        self._config = config
        self._state = new_game(
            config.stage,
            seed=config.seed,
            rules=config.rules,
            player_slots=config.slots,
        )
        self._members = {slot: _Member(slot=slot) for slot in config.slots}
        self._connection_slot: dict[int, int] = {}
        self._connections: set[int] = set()
        self._next_connection = 1
        self._closed = False
        self._server_commands: dict[int, list[Command]] = {}
        self._log = session_logger()
        self._info = SessionInfo(
            tick_rate=config.tick_rate,
            keyframe_interval=config.limits.keyframe_interval,
            max_players=len(config.credentials),
            content=config.content,
            rules_digest=rules_digest(config.rules),
            state_version=CANONICAL_STATE_VERSION,
        )

    @property
    def config(self) -> SessionConfig:
        return self._config

    @property
    def info(self) -> SessionInfo:
        """The session terms every client is told at join."""
        return self._info

    @property
    def state(self) -> SimulationState:
        """The authoritative state. This is the only state there is."""
        return self._state

    @property
    def tick(self) -> int:
        """The next tick to be simulated, which is the default target for input."""
        return self._state.tick

    @property
    def closed(self) -> bool:
        return self._closed

    def joined_slots(self) -> tuple[int, ...]:
        return tuple(
            slot for slot, member in sorted(self._members.items()) if member.connection is not None
        )

    def slot_of(self, connection: int) -> int | None:
        """The slot ``connection`` owns, or ``None`` while it has not joined."""
        return self._connection_slot.get(connection)

    def pending_ticks(self, slot: int) -> tuple[int, ...]:
        """Ticks this slot currently has input queued for, ascending. For diagnostics."""
        return tuple(sorted(self._members[slot].pending))

    def schedule_commands(self, tick: int, commands: Sequence[Command]) -> None:
        """Queue simulation commands the *server* issues, such as a wave or a powerup.

        No client can reach this. Spawning, despawning and enemy behaviour are campaign
        and bot concerns that the authoritative side owns; this is where a later wave
        scheduler or AI driver hands them to the session, and keeping one entry point
        for them means client input and server input are still assembled into a single
        ordered tick input.

        Server commands are placed ahead of client commands in the tick, because spawn
        order is the caller's decision and the rules engine applies spawns in submitted
        order. They are dropped along with everything else if the tick has to be run
        empty.
        """
        if tick < self._state.tick:
            raise ValueError(f"tick {tick} is behind tick {self._state.tick}")
        if tick > self._state.tick + self._config.limits.max_tick_lead:
            raise ValueError(
                f"tick {tick} is more than {self._config.limits.max_tick_lead} ticks ahead"
            )
        self._server_commands.setdefault(tick, []).extend(commands)

    def connect(self) -> int:
        """Register a connection that has not joined yet and return its identifier."""
        connection = self._next_connection
        self._next_connection += 1
        self._connections.add(connection)
        return connection

    def disconnect(self, connection: int) -> None:
        """Forget ``connection``, dropping any input it had queued.

        The slot's credential is revoked with it. The simulation keeps running: a run
        does not pause because a player left, and this release offers that player no way
        back in.
        """
        self._connections.discard(connection)
        slot = self._connection_slot.pop(connection, None)
        if slot is None:
            return
        member = self._members[slot]
        member.connection = None
        member.pending.clear()
        member.revoked = True

    def handle(self, connection: int, message: ClientMessage) -> tuple[Reply, ...]:
        """Answer one decoded client message. Nothing is mutated before it is accepted."""
        if connection not in self._connections:
            return ()
        if isinstance(message, JoinRequest):
            return self._handle_join(connection, message)
        return self._handle_input(connection, message)

    def close(
        self, code: RejectionCode = RejectionCode.SESSION_CLOSED, detail: str = ""
    ) -> tuple[Reply, ...]:
        """End the session, telling every connection why."""
        if self._closed:
            return ()
        self._closed = True
        self._log_event("session_closed", reason=code, detail=_short(detail) or None)
        message = self.closing_notice(code, detail)
        return tuple(
            Reply(connection=connection, message=message, close=True)
            for connection in sorted(self._connections)
        )

    def advance_tick(self) -> tuple[Reply, ...]:
        """Run exactly one tick and return the snapshot and events it produced."""
        if self._closed:
            return ()
        tick = self._state.tick
        replies: list[Reply] = []
        commands: list[Command] = list(self._server_commands.pop(tick, ()))
        contributors: list[tuple[_Member, _Batch]] = []

        for slot in sorted(self._members):
            member = self._members[slot]
            member.batches_since_tick = 0
            batch = member.pending.pop(tick, None)
            if batch is None:
                continue
            try:
                built = commands_for(slot, self._state.find_player(slot), batch.actions)
            except IllegalActionError as error:
                replies.extend(self._reject(member, batch, str(error)))
                continue
            commands.extend(built)
            contributors.append((member, batch))

        tick_input = TickInput(tick=tick, commands=tuple(commands))
        try:
            result = step(self._state, tick_input, self._config.rules)
        except InvalidInputError as error:
            # The rules engine validates a whole tick against the pre-tick state and
            # refuses all of it or none of it. Rather than searching for the offending
            # command, the tick runs empty: the session keeps its cadence, and every
            # client whose input was in the refused batch is told so.
            for member, batch in contributors:
                replies.extend(self._reject(member, batch, str(error)))
            result = step(self._state, TickInput(tick=tick), self._config.rules)

        self._state = result.state
        replies.extend(self._broadcast_tick(result.events))
        if self._state.finished:
            replies.extend(self.close(RejectionCode.SESSION_CLOSED, "run finished"))
        return tuple(replies)

    def snapshot(self, *, keyframe: bool) -> StateSnapshot:
        """Describe the current state for every client in the session."""
        return snapshot_of(
            self._state,
            session_id=self._config.session_id,
            tick_rate=self._config.tick_rate,
            state_version=CANONICAL_STATE_VERSION,
            keyframe=keyframe,
        )

    def closing_notice(self, code: RejectionCode, detail: str = "") -> SessionClosed:
        """Build the message a connection is given as it is closed."""
        return SessionClosed(session_id=self._config.session_id, code=code, detail=_short(detail))

    def rejection(
        self, connection: int, code: RejectionCode, detail: str = "", *, sequence: int | None = None
    ) -> Reply:
        """Build a refusal, and record it.

        Every refusal is logged here, where the code is decided, so one message cannot
        be refused twice in the log or refused silently. The record carries the stable
        code, never the message that caused it.
        """
        clean = _short(detail)
        self._log_event(
            "message_refused",
            connection=connection,
            reason=code,
            detail=clean or None,
            sequence=sequence,
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

    def _log_event(
        self,
        event: str,
        *,
        connection: int | None = None,
        slot: int | None = None,
        reason: RejectionCode | None = None,
        detail: str | None = None,
        sequence: int | None = None,
        level: int = logging.INFO,
    ) -> None:
        """Emit one session record. No caller passes a credential; none may."""
        log_event(
            self._log,
            event,
            session_id=self._config.session_id,
            tick=self._state.tick,
            content=content_label(self._config.content),
            rules_digest=self._info.rules_digest,
            slot=slot if slot is not None else self.slot_of(connection) if connection else None,
            connection=connection,
            sequence=sequence,
            reason=reason,
            detail=detail,
            level=level,
        )

    def _handle_join(self, connection: int, message: JoinRequest) -> tuple[Reply, ...]:
        if self._closed:
            return (self.rejection(connection, RejectionCode.SESSION_CLOSED),)
        if connection in self._connection_slot:
            return (self.rejection(connection, RejectionCode.ALREADY_JOINED),)
        if message.session_id != self._config.session_id:
            return (self.rejection(connection, RejectionCode.UNKNOWN_SESSION),)

        expected = self._config.token_for(message.slot)
        # Compare even when the slot is unknown: the answer is the same either way, and
        # the work is the same either way. The token never reaches the reply or a log.
        matched = hmac.compare_digest(
            message.token, expected if expected is not None else _DUMMY_TOKEN
        )
        if expected is None:
            return (self.rejection(connection, RejectionCode.UNKNOWN_SLOT, f"slot {message.slot}"),)
        if not matched:
            return (
                self.rejection(connection, RejectionCode.INVALID_TOKEN, f"slot {message.slot}"),
            )

        mismatch = _content_mismatch(message.content, self._config.content)
        if mismatch is not None:
            return (self.rejection(connection, RejectionCode.CONTENT_MISMATCH, mismatch),)

        member = self._members[message.slot]
        if member.revoked:
            return (
                self.rejection(
                    connection, RejectionCode.MEMBERSHIP_REVOKED, f"slot {message.slot}"
                ),
            )
        if member.connection is not None:
            return (
                self.rejection(connection, RejectionCode.SLOT_OCCUPIED, f"slot {message.slot}"),
            )

        member.connection = connection
        self._connection_slot[connection] = member.slot
        self._log_event("join_accepted", connection=connection, slot=member.slot)
        accepted = JoinAccepted(
            session_id=self._config.session_id,
            slot=member.slot,
            tick=self._state.tick,
            session=self._info,
        )
        return (
            Reply(connection=connection, message=accepted),
            Reply(connection=connection, message=self.snapshot(keyframe=True)),
        )

    def _handle_input(self, connection: int, message: InputBatch) -> tuple[Reply, ...]:
        slot = self._connection_slot.get(connection)
        if slot is None:
            return (
                self.rejection(connection, RejectionCode.NOT_JOINED, sequence=message.sequence),
            )
        if message.session_id != self._config.session_id:
            return (
                self.rejection(
                    connection, RejectionCode.UNKNOWN_SESSION, sequence=message.sequence
                ),
            )
        if message.slot != slot:
            return (
                self.rejection(
                    connection,
                    RejectionCode.WRONG_PLAYER,
                    f"connection owns slot {slot}",
                    sequence=message.sequence,
                ),
            )
        if self._closed:
            return (
                self.rejection(connection, RejectionCode.SESSION_CLOSED, sequence=message.sequence),
            )

        member = self._members[slot]
        if member.last_sequence is not None and message.sequence <= member.last_sequence:
            return (
                self.rejection(
                    connection,
                    RejectionCode.SEQUENCE_NOT_MONOTONIC,
                    f"last accepted sequence was {member.last_sequence}",
                    sequence=message.sequence,
                ),
            )
        if member.batches_since_tick >= self._config.limits.max_batches_per_tick:
            return (
                self.rejection(
                    connection,
                    RejectionCode.RATE_LIMITED,
                    f"at most {self._config.limits.max_batches_per_tick} batches per tick",
                    sequence=message.sequence,
                ),
            )

        target = self._state.tick if message.target_tick is None else message.target_tick
        if target < self._state.tick:
            return (
                self.rejection(
                    connection,
                    RejectionCode.TICK_IN_PAST,
                    f"tick {target} is behind tick {self._state.tick}",
                    sequence=message.sequence,
                ),
            )
        if target > self._state.tick + self._config.limits.max_tick_lead:
            return (
                self.rejection(
                    connection,
                    RejectionCode.TICK_OUT_OF_RANGE,
                    f"at most {self._config.limits.max_tick_lead} ticks ahead",
                    sequence=message.sequence,
                ),
            )
        queued_ticks = len(member.pending)
        if target not in member.pending and queued_ticks >= self._config.limits.max_pending_batches:
            return (
                self.rejection(
                    connection,
                    RejectionCode.QUEUE_OVERFLOW,
                    f"at most {self._config.limits.max_pending_batches} queued batches",
                    sequence=message.sequence,
                ),
            )

        # A later batch for a tick that has not run yet replaces the earlier one: the
        # client changed its mind before the deadline, which is its own business. The
        # tick is still assembled from one batch per slot, so the outcome does not
        # depend on how many times that slot spoke.
        member.pending[target] = _Batch(sequence=message.sequence, actions=message.actions)
        member.last_sequence = message.sequence
        member.batches_since_tick += 1
        return (
            Reply(
                connection=connection,
                message=InputAccepted(
                    session_id=self._config.session_id,
                    slot=slot,
                    sequence=message.sequence,
                    tick=target,
                ),
            ),
        )

    def _broadcast_tick(self, events: tuple[Event, ...]) -> tuple[Reply, ...]:
        keyframe = self._state.tick % self._config.limits.keyframe_interval == 0
        snapshot = self.snapshot(keyframe=keyframe)
        translated = protocol_events(events)
        replies: list[Reply] = []
        for connection in sorted(self._connection_slot):
            replies.append(Reply(connection=connection, message=snapshot))
            if translated:
                replies.append(
                    Reply(
                        connection=connection,
                        message=TickEvents(
                            session_id=self._config.session_id,
                            tick=self._state.tick,
                            events=translated,
                        ),
                    )
                )
        return tuple(replies)

    def _reject(self, member: _Member, batch: _Batch, detail: str) -> tuple[Reply, ...]:
        """Tell a slot its queued batch was refused, naming that batch's sequence."""
        if member.connection is None:
            return ()
        return (
            self.rejection(
                member.connection,
                RejectionCode.ILLEGAL_COMMAND,
                detail,
                sequence=batch.sequence,
            ),
        )


def _content_mismatch(claimed: ContentRef, expected: ContentRef) -> str | None:
    """Return which part of the content reference disagrees, or ``None``."""
    if claimed == expected:
        return None
    for name in ("pack_id", "pack_version", "level_id", "content_schema_version"):
        if getattr(claimed, name) != getattr(expected, name):
            return f"session runs {name} {getattr(expected, name)}"
    return "session runs different content"


def _short(detail: str) -> str:
    """Keep a detail printable ASCII and within the protocol's bound."""
    cleaned = "".join(char if " " <= char <= "~" else " " for char in detail)
    return cleaned[:200]
