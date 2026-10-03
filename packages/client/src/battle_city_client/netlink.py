"""Getting messages to and from a server without blocking the frame loop.

The client's loop is synchronous: it pumps window events, advances a frame and draws,
sixty to a hundred and twenty times a second, and it must not wait on a socket in the
middle of that. The protocol's channel is asynchronous. :class:`NetworkLink` is the
join between them — whole decoded messages in a queue the loop drains, and whole
messages out — so that :mod:`battle_city_client.online` can be a pure state machine and
:mod:`battle_city_client.app` can stay a pump.

Why a thread
------------
A frame budget is sixteen milliseconds and a round trip is not, so the socket work
happens on its own thread running its own event loop, and the two threads meet at two
bounded queues. The alternative — stepping an event loop a little each frame — makes
every network deadline depend on the frame rate, which is exactly the coupling the
fixed-tick design spends its time avoiding.

Nothing here understands a message. The link does not inspect, reorder, filter or retry
anything; it moves frames and reports failure. What a message *means* is decided by
:class:`~battle_city_client.online.OnlineSession`, and what is authoritative is decided
by the server.

Bounds and failure
------------------
Both queues are bounded. A full inbound queue means the frame loop stopped draining —
the window is wedged, not merely slow — and the link closes rather than growing without
limit; a full outbound queue means the same thing about the socket. Every failure, from
a refused connection to a closed stream, ends the same way: :attr:`NetworkLink.failure`
carries a short reason, :meth:`NetworkLink.open` goes false, and the session is told its
link is gone. No reconnect is attempted. The first release has no session resumption, so
a quietly re-dialled socket would be a new connection with no claim on the old slot.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any, Final, Protocol

from battle_city_protocol import (
    ClientChannel,
    ClientMessage,
    MessageError,
    ServerMessage,
    client_channel,
)

DEFAULT_QUEUE_SIZE: Final[int] = 256
"""Messages either direction may have waiting. A tick's worth is one or two."""

DEFAULT_CONNECT_TIMEOUT: Final[float] = 5.0
"""Seconds to wait for a server to accept the connection before giving up."""

CLOSE_TIMEOUT: Final[float] = 2.0
"""Seconds the network thread is given to wind down before the client stops waiting."""


class NetworkLink(Protocol):
    """Whole messages in and out, from the frame loop's point of view."""

    @property
    def open(self) -> bool:
        """Whether the link is still usable."""

    @property
    def failure(self) -> str | None:
        """A short reason the link ended, or ``None`` while it has not."""

    def poll(self) -> tuple[ServerMessage, ...]:
        """Take everything that has arrived since the last call. Never blocks."""

    def send(self, message: ClientMessage) -> None:
        """Queue ``message``. Never blocks; a full queue ends the link."""

    def close(self) -> None:
        """Shut the link down. Calling it twice is not an error."""


class TcpLink:
    """A :class:`NetworkLink` over an asyncio TCP connection on its own thread."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._inbound: queue.Queue[ServerMessage] = queue.Queue(maxsize=queue_size)
        self._outbound: queue.Queue[ClientMessage] = queue.Queue(maxsize=queue_size)
        self._connect_timeout = connect_timeout
        self._failure: str | None = None
        self._open = True
        self._lock = threading.Lock()
        self._wakeup: Callable[[], None] | None = None
        self._thread = threading.Thread(
            target=self._thread_main, name="battle-city-netlink", daemon=True
        )
        self._thread.start()

    # -- the frame loop's side -------------------------------------------------

    @property
    def open(self) -> bool:
        with self._lock:
            return self._open

    @property
    def failure(self) -> str | None:
        with self._lock:
            return self._failure

    def poll(self) -> tuple[ServerMessage, ...]:
        arrived: list[ServerMessage] = []
        while True:
            try:
                arrived.append(self._inbound.get_nowait())
            except queue.Empty:
                return tuple(arrived)

    def send(self, message: ClientMessage) -> None:
        if not self.open:
            return
        try:
            self._outbound.put_nowait(message)
        except queue.Full:
            self._fail("OUTBOUND QUEUE FULL")
            return
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup()

    def close(self) -> None:
        self._fail(None)
        self._thread.join(timeout=CLOSE_TIMEOUT)

    # -- the network thread's side ---------------------------------------------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as error:  # noqa: BLE001 - a thread that dies silently is worse
            self._fail(_reason(error))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        pending: asyncio.Event = asyncio.Event()
        # Set from the start: a caller may queue the first message — the seat request —
        # before this thread has a loop to be woken on, and that message must not sit in
        # the queue until something else happens to wake the writer.
        pending.set()

        def wake() -> None:
            # The loop is gone once the link has wound down, and a late wake-up is an
            # ordinary thing then: the frame loop may call close() long after the
            # network thread finished. It is nothing to report and nothing to raise on.
            try:
                loop.call_soon_threadsafe(pending.set)
            except RuntimeError:
                return

        self._wakeup = wake
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._connect_timeout
            )
        except (OSError, TimeoutError) as error:
            self._fail(_reason(error))
            return
        stream = _AsyncioStream(reader, writer, f"{self._host}:{self._port}")
        channel = client_channel(stream)
        reading = asyncio.create_task(self._read_loop(channel))
        writing = asyncio.create_task(self._write_loop(channel, pending))
        try:
            await _first_to_finish(reading, writing)
        finally:
            self._fail(None)
            for task in (reading, writing):
                task.cancel()
            await asyncio.gather(reading, writing, return_exceptions=True)
            await channel.close()
            self._wakeup = None

    async def _read_loop(self, channel: ClientChannel) -> None:
        while True:
            try:
                message = await channel.receive()
            except (OSError, MessageError) as error:
                self._fail(_reason(error))
                return
            if message is None:
                self._fail("SERVER CLOSED THE CONNECTION")
                return
            try:
                self._inbound.put_nowait(message)
            except queue.Full:
                self._fail("INBOUND QUEUE FULL")
                return

    async def _write_loop(self, channel: ClientChannel, pending: asyncio.Event) -> None:
        while True:
            await pending.wait()
            pending.clear()
            while True:
                try:
                    message = self._outbound.get_nowait()
                except queue.Empty:
                    break
                try:
                    await channel.send(message)
                except (OSError, MessageError) as error:
                    self._fail(_reason(error))
                    return
            if not self.open:
                return

    def _fail(self, reason: str | None) -> None:
        """Record the first reason the link ended and mark it closed.

        First reason wins: a socket that is torn down produces several, and the one that
        explains what happened is the one that happened first.
        """
        with self._lock:
            if self._failure is None and reason is not None:
                self._failure = reason
            self._open = False
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup()


class _AsyncioStream:
    """A :class:`~battle_city_protocol.ByteStream` over an asyncio connection.

    The server package has its own; this one is the client's, because the client may not
    import the server. The behaviour that matters is identical and is the protocol's
    contract: a short read is end of stream, not an exception.
    """

    __slots__ = ("_peer", "_reader", "_writer")

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: str
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._peer = peer

    @property
    def peer(self) -> str:
        return self._peer

    async def read_exactly(self, count: int) -> bytes:
        try:
            return await self._reader.readexactly(count)
        except asyncio.IncompleteReadError as partial:
            return partial.partial

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer.is_closing():
            return
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            return


def open_tcp_link(endpoint: str, **options: Any) -> TcpLink:
    """Open a link to ``host:port``.

    The endpoint is parsed here rather than on the command line so that one spelling
    rule covers every caller, and so that a malformed one fails with a message about the
    endpoint rather than about a port that turned out to be a string.
    """
    host, port = parse_endpoint(endpoint)
    return TcpLink(host, port, **options)


def parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Split ``host:port``. Raises :class:`ValueError` with what was wrong."""
    host, separator, raw_port = endpoint.rpartition(":")
    if not separator or not host:
        raise ValueError(f"expected host:port, found {endpoint!r}")
    try:
        port = int(raw_port)
    except ValueError:
        raise ValueError(f"{raw_port!r} is not a port number") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be 1 to 65535, found {port}")
    return host.strip("[]"), port


async def _first_to_finish(*tasks: asyncio.Task[None]) -> None:
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)


def _reason(error: BaseException) -> str:
    """A short, printable reason for a player, without a stack trace in it."""
    if isinstance(error, MessageError):
        return error.code.value.replace("_", " ").upper()
    if isinstance(error, TimeoutError):
        return "CONNECTION TIMED OUT"
    return type(error).__name__.upper()
