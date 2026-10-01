"""The asyncio shell around an authoritative session.

:class:`~battle_city_server.session.GameSession` decides what is allowed and what a tick
produces. This module does the I/O: it reads frames, hands decoded messages to the
session, and writes whatever the session says to send. It owns three things the session
deliberately does not.

**Per-connection output queues.** Every client gets a bounded queue and a writer task.
Writing straight from the tick loop would mean one client's blocked socket stalls the
whole session, which over TCP is a thing a client can do on purpose. A queue that fills
is a client that is not keeping up with a reliable stream and never will: its queue is
dropped, it is told why, and it is closed.

**Frame-level failures.** A refusal that leaves the stream readable — an unknown field,
an out-of-range slot, a message in the wrong direction — is answered and the connection
continues. A refusal that means the stream itself cannot be trusted, which is a
malformed or oversized frame, is answered and then closed, because the next bytes on
that stream are no longer known to be a frame boundary.

**Connection lifetime.** A connection that ends for any reason — clean close, reset,
decode failure, overflow — reaches the same place: the session is told to drop it, which
discards its queued input and revokes its slot, and the run carries on.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Final

from battle_city_protocol import MessageError, RejectionCode, ServerChannel, ServerMessage
from battle_city_sim import SimulationState

from .clock import TickClock
from .config import SessionConfig
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
        "channel",
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
        self.writer: asyncio.Task[None] | None = None
        self.shutdown: asyncio.Task[None] | None = None


class SessionServer:
    """Runs one authoritative session over any number of connected channels."""

    def __init__(
        self, config: SessionConfig, *, flush_timeout: float = DEFAULT_FLUSH_TIMEOUT
    ) -> None:
        self._session = GameSession(config)
        self._connections: dict[int, _Connection] = {}
        self._flush_timeout = flush_timeout

    @property
    def session(self) -> GameSession:
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

    async def serve(self, channel: ServerChannel) -> None:
        """Serve one client until its channel ends. Intended to be run as a task."""
        connection = _Connection(
            self._session.connect(),
            channel,
            self._session.config.limits.max_outbound_messages,
        )
        self._connections[connection.identifier] = connection
        connection.writer = asyncio.create_task(self._write_loop(connection))
        try:
            await self._read_loop(connection)
        finally:
            await self._drop(connection)

    async def advance_tick(self) -> None:
        """Run one tick and queue everything it produced."""
        for reply in self._session.advance_tick():
            self._deliver(reply)
        await asyncio.sleep(0)

    async def run(self, clock: TickClock, *, ticks: int | None = None) -> None:
        """Advance ticks on ``clock`` until the session closes or ``ticks`` have run."""
        advanced = 0
        while not self._session.closed and (ticks is None or advanced < ticks):
            await clock.wait_for_tick(self._session.tick)
            await self.advance_tick()
            advanced += 1

    async def close(
        self, code: RejectionCode = RejectionCode.SERVER_SHUTDOWN, detail: str = ""
    ) -> None:
        """End the session and close every connection, telling each one why."""
        for reply in self._session.close(code, detail):
            self._deliver(reply)
        connections = list(self._connections.values())
        for connection in connections:
            connection.closing = True
            self._finish(connection)
        await asyncio.gather(*(self._close_later(connection) for connection in connections))

    async def _read_loop(self, connection: _Connection) -> None:
        while not connection.closing:
            try:
                message = await connection.channel.receive()
            except MessageError as error:
                self._deliver(
                    self._session.rejection(connection.identifier, error.code, error.detail)
                )
                if error.code in FATAL_FRAME_CODES:
                    connection.closing = True
                    return
                continue
            except OSError:
                return
            if message is None:
                return
            for reply in self._session.handle(connection.identifier, message):
                self._deliver(reply)

    async def _write_loop(self, connection: _Connection) -> None:
        while True:
            message = await connection.outbound.get()
            if message is None:
                return
            try:
                await connection.channel.send(message)
            except OSError, MessageError:
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
        self._session.disconnect(connection.identifier)
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
