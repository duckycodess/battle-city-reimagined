"""The asyncio shell around an authoritative session.

:class:`~battle_city_server.session.GameSession` decides what is allowed and what a tick
produces. This module does the I/O: it reads frames, hands decoded messages to the
session, and writes whatever the session says to send. It owns the things the session
deliberately does not.

**Admission.** Everything a stranger can do before proving membership is bounded. The
server holds at most :attr:`~battle_city_server.config.SessionLimits.max_connections`
connections at once; a connection has
:attr:`~battle_city_server.config.SessionLimits.join_deadline_seconds` to join and
:attr:`~battle_city_server.config.SessionLimits.max_join_attempts` tries to get its
token right. Each bound answers a cost an unauthenticated peer could otherwise impose
for free: a task and a queue each, an indefinite hold, and unlimited guesses.

**Frame deadlines.** Waiting for a frame to *start* is unbounded, because an idle joined
client is a normal client. Waiting for a frame that has started to *finish* is not: a
peer that declares sixty-four kilobytes and then stops sending would otherwise park a
reader for ever. The deadline is supplied to the protocol channel as a payload guard,
so the rule lives with the framing and the timer lives here.

**Per-connection output queues.** Every client gets a bounded queue and a writer task.
Writing straight from the tick loop would mean one client's blocked socket stalls the
whole session, which over TCP is a thing a client can do on purpose. A queue that fills
is a client that is not keeping up with a reliable stream and never will: its queue is
dropped, it is told why, and it is closed.

**Frame-level failures.** A refusal that leaves the stream readable — an unknown field,
an out-of-range slot, a message in the wrong direction — is answered and the connection
continues. A refusal that means the stream itself cannot be trusted, which is a
malformed, oversized or stalled frame, is answered and then closed, because the next
bytes on that stream are no longer known to be a frame boundary.

**Containment.** A failure while producing a tick ends the session with
:data:`~battle_city_protocol.codes.RejectionCode.INTERNAL_ERROR` and tells every client.
It does not escape into the task that was driving the clock, because a tick loop that
dies silently leaves every connected client waiting for a snapshot that will never come
— a hang is a worse failure than an ending.

**Connection lifetime.** A connection that ends for any reason — clean close, reset,
decode failure, deadline, overflow — reaches the same place: the session is told to drop
it, which discards its queued input and revokes its slot, and the run carries on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from typing import Final

from battle_city_protocol import (
    ByteStream,
    ClientMessage,
    MessageError,
    Rejected,
    RejectionCode,
    ServerChannel,
    ServerMessage,
    server_channel,
)
from battle_city_sim import SimulationState

from .authority import SessionAuthority
from .clock import TickClock
from .config import SessionConfig
from .logs import content_label, log_event, session_logger
from .session import GameSession, Reply

FATAL_FRAME_CODES: Final[frozenset[RejectionCode]] = frozenset(
    {RejectionCode.MALFORMED_FRAME, RejectionCode.FRAME_TOO_LARGE}
)
"""Refusals after which the stream is no longer known to be at a frame boundary."""

DEFAULT_FLUSH_TIMEOUT: Final[float] = 5.0
"""Seconds a closing connection is given to flush its last message before it is cut."""


class _Connection:
    """One connected client: its channel, its outbound queue and its writer task."""

    __slots__ = (
        "attempts",
        "channel",
        "join_deadline",
        "closing",
        "finished",
        "identifier",
        "outbound",
        "shutdown",
        "writer",
    )

    def __init__(self, identifier: int, channel: ServerChannel, capacity: int) -> None:
        self.identifier = identifier
        self.channel = channel
        self.outbound: asyncio.Queue[ServerMessage | None] = asyncio.Queue(maxsize=capacity)
        self.closing = False
        self.finished = False
        self.attempts = 0
        self.join_deadline = 0.0
        self.writer: asyncio.Task[None] | None = None
        self.shutdown: asyncio.Task[None] | None = None


class SessionServer:
    """Runs one authoritative session over any number of connected channels.

    The authority is injected rather than built, because there are now two kinds. Handed
    a :class:`~battle_city_server.config.SessionConfig` it runs a bare game session, the
    way it always did; handed a
    :class:`~battle_city_server.lobby.MatchSession` it runs a lobby that becomes one.
    Nothing in this class can tell the difference, which is the point: sockets, queues
    and deadlines belong here, and every rule belongs on the other side of
    :class:`~battle_city_server.authority.SessionAuthority`.
    """

    def __init__(
        self,
        session: SessionConfig | SessionAuthority,
        *,
        flush_timeout: float = DEFAULT_FLUSH_TIMEOUT,
    ) -> None:
        self._session: SessionAuthority = (
            GameSession(session) if isinstance(session, SessionConfig) else session
        )
        self._connections: dict[int, _Connection] = {}
        self._flush_timeout = flush_timeout
        self._log = session_logger()
        self._ticking = asyncio.Event()
        self._wake()

    @property
    def session(self) -> SessionAuthority:
        """The authority. Tests and diagnostics read it; nothing else should drive it."""
        return self._session

    @property
    def state(self) -> SimulationState:
        return self._session.state

    @property
    def tick(self) -> int:
        return self._session.tick

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def channel_for(self, stream: ByteStream) -> ServerChannel:
        """Wrap ``stream`` as a channel carrying this session's frame deadline.

        Transports build their channels through here so none of them can forget the
        deadline. A channel built without one would read a half-sent frame for ever.
        """
        deadline = self._session.limits.frame_deadline_seconds

        async def guard(read: Awaitable[bytes]) -> bytes:
            return await asyncio.wait_for(read, deadline)

        return server_channel(stream, payload_guard=guard)

    async def serve(self, channel: ServerChannel) -> None:
        """Serve one client until its channel ends. Intended to be run as a task."""
        limits = self._session.limits
        if len(self._connections) >= limits.max_connections:
            await self._refuse(channel, RejectionCode.TOO_MANY_CONNECTIONS)
            return

        connection = _Connection(self._session.connect(), channel, limits.max_outbound_messages)
        connection.join_deadline = asyncio.get_running_loop().time() + limits.join_deadline_seconds
        self._connections[connection.identifier] = connection
        connection.writer = asyncio.create_task(self._write_loop(connection))
        self._log_connection("connection_opened", connection)
        try:
            await self._read_loop(connection)
        finally:
            await self._drop(connection)

    async def advance_tick(self) -> None:
        """Run one tick and queue everything it produced.

        A failure here is contained. The session is ended with a reason every client is
        told, rather than left running with a tick loop that has stopped.
        """
        try:
            replies = self._session.advance_tick()
        except Exception as error:
            self._log_session(
                "tick_failed",
                reason=RejectionCode.INTERNAL_ERROR,
                detail=f"{type(error).__name__}",
                level=logging.ERROR,
            )
            await self.close(RejectionCode.INTERNAL_ERROR, "the server could not run a tick")
            return
        for reply in replies:
            self._deliver(reply)
        self._sweep_join_deadlines()
        await asyncio.sleep(0)

    async def run(self, clock: TickClock, *, ticks: int | None = None) -> None:
        """Advance ticks on ``clock`` until the session closes or ``ticks`` have run.

        An authority that is not ticking yet — a lobby, still deciding — is waited on
        rather than polled. Asking a fixed-rate clock for tick zero over and over would
        burn a core deciding that tick zero is still not due, and would count catch-up
        against a run that has not begun.
        """
        advanced = 0
        while not self._session.closed and (ticks is None or advanced < ticks):
            if not self._session.ticking:
                await self._ticking.wait()
                self._ticking.clear()
                continue
            await clock.wait_for_tick(self._session.tick)
            await self.advance_tick()
            advanced += 1

    async def close(
        self, code: RejectionCode = RejectionCode.SERVER_SHUTDOWN, detail: str = ""
    ) -> None:
        """End the session and close every connection, telling each one why."""
        for reply in self._session.close(code, detail):
            self._deliver(reply)
        self._wake()
        connections = list(self._connections.values())
        for connection in connections:
            connection.closing = True
            self._finish(connection)
        await asyncio.gather(*(self._close_later(connection) for connection in connections))

    async def _refuse(self, channel: ServerChannel, code: RejectionCode) -> None:
        """Turn a connection away before it costs the session anything."""
        self._log_session("connection_refused", reason=code, peer=channel.peer)
        with contextlib.suppress(OSError, MessageError):
            await channel.send(self._session.closing_notice(code))
        await channel.close()

    async def _read_loop(self, connection: _Connection) -> None:
        limits = self._session.limits
        held_slot = self._session.slot_of(connection.identifier) is not None
        while not connection.closing:
            joined = self._session.slot_of(connection.identifier) is not None
            if held_slot and not joined:
                self._rearm_join(connection)
            held_slot = joined
            try:
                message = await self._receive(connection, bounded=not joined)
            except TimeoutError:
                # Either the connection never joined, or a frame it had begun sending
                # stopped arriving. Both are a peer holding a resource it is not using.
                code = RejectionCode.JOIN_TIMEOUT if not joined else RejectionCode.FRAME_TIMEOUT
                self._close_with(connection, code)
                return
            except MessageError as error:
                # The refusal is logged by the session, where the code is decided.
                self._deliver(
                    self._session.rejection(connection.identifier, error.code, error.detail)
                )
                if error.code in FATAL_FRAME_CODES:
                    connection.closing = True
                    return
                if self._spent_attempts(connection, limits.max_join_attempts, joined=joined):
                    return
                continue
            except OSError:
                return
            if message is None:
                return
            replies = self._session.handle(connection.identifier, message)
            for reply in replies:
                self._deliver(reply)
            self._wake()
            if (
                not joined
                and self._refused(replies)
                and self._spent_attempts(connection, limits.max_join_attempts, joined=joined)
            ):
                return

    async def _receive(self, connection: _Connection, *, bounded: bool) -> ClientMessage | None:
        """Read the next message, bounding the wait while the peer is still a stranger.

        The bound is absolute, measured from when the connection was accepted, not per
        message. A peer that trickles one refused message every nine seconds would
        otherwise hold a connection open indefinitely without ever joining.
        """
        if not bounded:
            return await connection.channel.receive()
        remaining = connection.join_deadline - asyncio.get_running_loop().time()
        if remaining <= 0.0:
            raise TimeoutError("join deadline passed")
        return await asyncio.wait_for(connection.channel.receive(), remaining)

    def _rearm_join(self, connection: _Connection) -> None:
        """Give a connection its join budget back when the authority lets go of its slot.

        A slot can go away while the connection stays exactly where it is. The lobby
        handover is the case that matters: the moment a match starts, membership stops
        being the seat the lobby granted and becomes the session membership this
        connection has not proved yet, so :meth:`SessionAuthority.slot_of` returns
        ``None`` again and the reader is a stranger once more.

        The deadline it was holding was measured from when the socket was accepted, and
        a lobby that spent longer than the budget deciding — which is every lobby with
        two people talking in it — has already used all of it. Without this the next
        read would time out before the client could possibly answer, the host would be
        closed with ``join_timeout``, and the match it had just started would never be
        joined by anybody. Re-arming restarts the budget rather than removing it: the
        wait stays bounded, it is just bounded from the moment the connection became a
        stranger instead of from a deadline that belonged to a phase that is over.
        """
        budget = self._session.limits.join_deadline_seconds
        connection.join_deadline = asyncio.get_running_loop().time() + budget
        connection.attempts = 0

    def _sweep_join_deadlines(self) -> None:
        """Close connections whose join budget ran out while their reader was parked.

        :meth:`_rearm_join` can only bound a reader that comes back to ask for the next
        message. A connection that was already waiting on one when the handover happened
        is not asking for anything: it is sitting in a read that will not return until
        its peer sends a frame, which a client that takes its credential and then goes
        quiet never will. The deadline it was given would be checked at the top of a
        read that never starts.

        So the bound is applied from here as well, where there is no read to wait for,
        and the connection is closed the way an overflowing one is -- from the writer's
        end, rather than by cancelling a read whose position in the frame nobody knows.
        That is the same reasoning as :meth:`_overflow`: a reader parked on a message
        that is never coming is woken by closing the channel underneath it.

        It runs per tick, so it exists only once a match is running, which is exactly
        when a connection can hold a slot the other players are waiting on. Before the
        handover an unjoined connection is bounded by its own reader, which is awake.
        """
        now = asyncio.get_running_loop().time()
        for connection in list(self._connections.values()):
            if connection.closing:
                continue
            if self._session.slot_of(connection.identifier) is not None:
                continue
            if now < connection.join_deadline:
                continue
            self._close_with(connection, RejectionCode.JOIN_TIMEOUT)
            self._close_later(connection)

    def _spent_attempts(self, connection: _Connection, budget: int, *, joined: bool) -> bool:
        """Count one failed approach and say whether the connection is out of them."""
        if joined:
            return False
        connection.attempts += 1
        if connection.attempts < budget:
            return False
        self._close_with(connection, RejectionCode.TOO_MANY_ATTEMPTS)
        return True

    def _close_with(self, connection: _Connection, code: RejectionCode) -> None:
        """Tell a connection why it is being closed and stop reading from it."""
        self._log_connection("connection_closed", connection, reason=code)
        self._deliver(
            Reply(
                connection=connection.identifier,
                message=self._session.closing_notice(code),
                close=True,
            )
        )
        connection.closing = True

    async def _write_loop(self, connection: _Connection) -> None:
        while True:
            message = await connection.outbound.get()
            if message is None:
                return
            try:
                await connection.channel.send(message)
            except (OSError, MessageError) as error:
                self._log_connection(
                    "write_failed",
                    connection,
                    detail=type(error).__name__,
                    level=logging.WARNING,
                )
                return

    def _deliver(self, reply: Reply) -> None:
        connection = self._connections.get(reply.connection)
        if connection is None or connection.closing:
            return
        try:
            connection.outbound.put_nowait(reply.message)
        except asyncio.QueueFull:
            self._overflow(connection)
            return
        if reply.close:
            connection.closing = True
            self._finish(connection)

    def _overflow(self, connection: _Connection) -> None:
        """Drop a hopeless client's backlog, tell it why, and close it.

        The backlog is discarded rather than kept because none of it is worth sending:
        a client that is this far behind needs the next keyframe, not a replay of the
        snapshots it already missed.
        """
        while not connection.outbound.empty():
            connection.outbound.get_nowait()
        connection.closing = True
        connection.outbound.put_nowait(
            self._session.closing_notice(
                RejectionCode.QUEUE_OVERFLOW, "client is not reading fast enough"
            )
        )
        self._log_connection(
            "connection_closed",
            connection,
            reason=RejectionCode.QUEUE_OVERFLOW,
            level=logging.WARNING,
        )
        self._finish(connection)
        # The reader is parked on a receive this client may never answer, so the channel
        # is closed from here rather than waiting for the reader to notice.
        self._close_later(connection)

    def _finish(self, connection: _Connection) -> None:
        """Signal the writer task that nothing further will be queued."""
        with contextlib.suppress(asyncio.QueueFull):
            connection.outbound.put_nowait(None)

    async def _drop(self, connection: _Connection) -> None:
        """Forget a connection, whatever ended it, and let the run carry on without it."""
        self._connections.pop(connection.identifier, None)
        slot = self._session.slot_of(connection.identifier)
        for reply in self._session.disconnect(connection.identifier):
            self._deliver(reply)
        self._wake()
        self._log_connection("connection_dropped", connection, slot=slot)
        connection.closing = True
        self._finish(connection)
        await self._close_later(connection)

    def _close_later(self, connection: _Connection) -> asyncio.Task[None]:
        """Start closing ``connection`` once, and hand back the task that is doing it.

        One connection has exactly one shutdown, however it was triggered: an overflow
        starts it without waiting, and the reader, when it notices, waits on that same
        task rather than racing it to the close.
        """
        if connection.shutdown is None:
            connection.shutdown = asyncio.create_task(self._flush_and_close(connection))
        return connection.shutdown

    async def _flush_and_close(self, connection: _Connection) -> None:
        """Give the writer a bounded chance to send what is queued, then close.

        The bound matters: a client that has stopped reading would otherwise hold a
        writer task, a queue and a socket open for as long as it liked.
        """
        if connection.finished:
            return
        connection.finished = True
        writer = connection.writer
        if writer is not None:
            try:
                await asyncio.wait_for(asyncio.shield(writer), self._flush_timeout)
            except TimeoutError, asyncio.CancelledError:
                writer.cancel()
        await connection.channel.close()

    def _wake(self) -> None:
        """Release the tick loop once there is something for it to do, or nothing left.

        Both conditions matter. A match that started gives the loop ticks to run; a
        lobby that ended gives it a reason to stop waiting and return.
        """
        if self._session.ticking or self._session.closed:
            self._ticking.set()

    @staticmethod
    def _refused(replies: tuple[Reply, ...]) -> bool:
        """Whether answering a message produced a refusal rather than progress."""
        return any(isinstance(reply.message, Rejected) for reply in replies)

    def _log_session(
        self,
        event: str,
        *,
        reason: RejectionCode | None = None,
        detail: str | None = None,
        peer: str | None = None,
        level: int = logging.INFO,
    ) -> None:
        log_event(
            self._log,
            event,
            session_id=self._session.session_id,
            tick=self._session.tick,
            content=content_label(self._session.content),
            rules_digest=self._session.rules_digest,
            peer=peer,
            reason=reason,
            detail=detail,
            level=level,
        )

    def _log_connection(
        self,
        event: str,
        connection: _Connection,
        *,
        slot: int | None = None,
        reason: RejectionCode | None = None,
        detail: str | None = None,
        level: int = logging.INFO,
    ) -> None:
        log_event(
            self._log,
            event,
            session_id=self._session.session_id,
            tick=self._session.tick,
            content=content_label(self._session.content),
            rules_digest=self._session.rules_digest,
            slot=slot if slot is not None else self._session.slot_of(connection.identifier),
            peer=connection.channel.peer,
            connection=connection.identifier,
            reason=reason,
            detail=detail,
            level=level,
        )
