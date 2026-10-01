"""The transport seam.

Game logic talks to a :class:`MessageChannel`. Nothing above this line knows whether the
bytes travel over TCP, over a future loss-tolerant channel, or over an in-memory pipe in
a test, and nothing below it knows what a message means. That is the whole point of the
seam the architecture specification asks for: message semantics stay transport-neutral,
so adding a real-time channel later is an adapter rather than a rules change.

Two protocols and one adapter live here:

* :class:`ByteStream` is what a transport supplies: ordered bytes, in and out.
* :class:`MessageChannel` is what session code consumes: whole messages, in and out.
* :class:`FramedChannel` bridges the two with
  :mod:`~battle_city_protocol.framing` and :mod:`~battle_city_protocol.codec`.

A stream transport needs framing because TCP does not preserve message boundaries. A
datagram transport already does, so it would implement :class:`MessageChannel` directly
and skip :class:`FramedChannel` entirely; that is why framing is a separate module.

Reliability is the transport's business and its limits are the transport's limits. Over
TCP a peer that stops reading cannot be made to read: the kernel buffer fills, writes
block, and the only remaining move is to stop writing and close. Session code therefore
must treat a channel as something that can fail at any await, and must not assume that a
message it handed over was ever seen.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from .codec import decode_client_message, decode_server_message, encode_message
from .codes import RejectionCode
from .errors import MessageError
from .framing import FRAME_HEADER_BYTES, decode_frame_length, encode_frame
from .messages import ClientMessage, Message, ServerMessage


class ByteStream(Protocol):
    """An ordered, reliable byte pipe. One direction each way, no message boundaries."""

    @property
    def peer(self) -> str:
        """A short label for logs. Never a credential."""

    async def read_exactly(self, count: int) -> bytes:
        """Return exactly ``count`` bytes, or fewer only when the peer has finished.

        A short read is end of stream. Returning it rather than raising lets a caller
        tell a clean close (nothing read) from a truncated frame (something read).
        """

    async def write(self, data: bytes) -> None:
        """Send ``data``, applying whatever backpressure the transport has."""

    async def close(self) -> None:
        """Release the stream. Calling it twice is not an error."""


class MessageChannel[R: Message, S: Message](Protocol):
    """Whole messages in and out. ``R`` is what arrives; ``S`` is what may be sent."""

    @property
    def peer(self) -> str: ...

    async def receive(self) -> R | None:
        """Return the next message, or ``None`` once the peer has finished."""

    async def send(self, message: S) -> None: ...

    async def close(self) -> None: ...


type ServerChannel = MessageChannel[ClientMessage, ServerMessage]
"""A server's view of one connected client."""

type ClientChannel = MessageChannel[ServerMessage, ClientMessage]
"""A client's view of its server."""

type PayloadGuard = Callable[[Awaitable[bytes]], Awaitable[bytes]]
"""Wraps a read that must finish, so a half-sent frame cannot park a task for ever.

A peer that declares a frame and then goes quiet costs the reader nothing to detect and
everything to wait for, so the wait has to be bounded. The bound itself is a deployment
decision and needs a timer, which this package has no business owning: it imports no
event loop library. The caller therefore supplies the wrapper — ``asyncio.wait_for``,
or whatever its framework calls that — and this package decides only *which* reads it
applies to.

It is deliberately not applied to the read that waits for a frame to begin. A joined
client that sends nothing for an hour is idle, not stalled.
"""


class FramedChannel[R: Message, S: Message]:
    """A :class:`MessageChannel` over a :class:`ByteStream`.

    Decoding rejections surface as :class:`~battle_city_protocol.errors.MessageError`
    from :meth:`receive`, carrying the stable code the caller should answer with. The
    channel does not decide what to do about one: refusing a message and dropping a
    connection are session policy.
    """

    __slots__ = ("_closed", "_decode", "_guard", "_stream")

    def __init__(
        self,
        stream: ByteStream,
        decode: Callable[[bytes], R],
        *,
        payload_guard: PayloadGuard | None = None,
    ) -> None:
        self._stream = stream
        self._decode = decode
        self._guard = payload_guard
        self._closed = False

    @property
    def peer(self) -> str:
        return self._stream.peer

    @property
    def closed(self) -> bool:
        return self._closed

    async def receive(self) -> R | None:
        """Return the next message, or ``None`` once the peer has finished.

        The first byte is read on its own and without a guard. Waiting for a frame to
        start is waiting for a peer to have something to say, which is unbounded by
        design; once a frame *has* started, the rest of it is expected promptly, so the
        remaining header bytes and the payload go through :attr:`PayloadGuard` when one
        was supplied. Splitting the header read is what lets those two waits have
        different deadlines, and it costs one extra read per frame.
        """
        start = await self._stream.read_exactly(1)
        if not start:
            return None
        header = start + await self._guarded(FRAME_HEADER_BYTES - 1)
        length = decode_frame_length(header)
        payload = await self._guarded(length)
        if len(payload) != length:
            raise MessageError(
                RejectionCode.MALFORMED_FRAME,
                f"frame declared {length} bytes and carried {len(payload)}",
            )
        return self._decode(payload)

    async def _guarded(self, count: int) -> bytes:
        read = self._stream.read_exactly(count)
        if self._guard is None:
            return await read
        return await self._guard(read)

    async def send(self, message: S) -> None:
        await self._stream.write(encode_frame(encode_message(message)))

    async def close(self) -> None:
        self._closed = True
        await self._stream.close()


def server_channel(
    stream: ByteStream, *, payload_guard: PayloadGuard | None = None
) -> FramedChannel[ClientMessage, ServerMessage]:
    """Wrap ``stream`` as a server's channel: client messages in, server messages out."""
    return FramedChannel(stream, decode_client_message, payload_guard=payload_guard)


def client_channel(
    stream: ByteStream, *, payload_guard: PayloadGuard | None = None
) -> FramedChannel[ServerMessage, ClientMessage]:
    """Wrap ``stream`` as a client's channel: server messages in, client messages out."""
    return FramedChannel(stream, decode_server_message, payload_guard=payload_guard)
