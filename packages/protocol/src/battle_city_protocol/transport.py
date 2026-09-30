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

from collections.abc import Callable
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


class FramedChannel[R: Message, S: Message]:
    """A :class:`MessageChannel` over a :class:`ByteStream`.

    Decoding rejections surface as :class:`~battle_city_protocol.errors.MessageError`
    from :meth:`receive`, carrying the stable code the caller should answer with. The
    channel does not decide what to do about one: refusing a message and dropping a
    connection are session policy.
    """

    __slots__ = ("_closed", "_decode", "_stream")

    def __init__(self, stream: ByteStream, decode: Callable[[bytes], R]) -> None:
        self._stream = stream
        self._decode = decode
        self._closed = False

    @property
    def peer(self) -> str:
        return self._stream.peer

    @property
    def closed(self) -> bool:
        return self._closed

    async def receive(self) -> R | None:
        header = await self._stream.read_exactly(FRAME_HEADER_BYTES)
        if not header:
            return None
        length = decode_frame_length(header)
        payload = await self._stream.read_exactly(length)
        if len(payload) != length:
            raise MessageError(
                RejectionCode.MALFORMED_FRAME,
                f"frame declared {length} bytes and carried {len(payload)}",
            )
        return self._decode(payload)

    async def send(self, message: S) -> None:
        await self._stream.write(encode_frame(encode_message(message)))

    async def close(self) -> None:
        self._closed = True
        await self._stream.close()


def server_channel(stream: ByteStream) -> FramedChannel[ClientMessage, ServerMessage]:
    """Wrap ``stream`` as a server's channel: client messages in, server messages out."""
    return FramedChannel(stream, decode_client_message)


def client_channel(stream: ByteStream) -> FramedChannel[ServerMessage, ClientMessage]:
    """Wrap ``stream`` as a client's channel: server messages in, client messages out."""
    return FramedChannel(stream, decode_server_message)
