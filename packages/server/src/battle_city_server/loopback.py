"""An in-process byte stream pair.

Loopback is a real transport adapter, not a test double: it is how a single-player or
listen-server client talks to the authoritative session in the same process, without a
socket, a port, a firewall prompt or a loopback interface. It is also what the server
tests drive, so those tests exercise the same framing, decoding and session code a TCP
client reaches.

The pair is deliberately strict about end of stream: closing one end makes the other
end's next read return short, which is what the framed channel reads as a clean
disconnect.
"""

from __future__ import annotations

import asyncio


class _Pipe:
    """One direction: bytes in at the writer's end, out at the reader's."""

    __slots__ = ("_buffer", "_closed", "_ready")

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._ready = asyncio.Event()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionResetError("loopback pipe is closed")
        self._buffer.extend(data)
        self._ready.set()

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count and not self._closed:
            # Nothing awaits between the length check and the clear, so a write cannot
            # slip in and be missed: the event is only cleared on a buffer we just read.
            self._ready.clear()
            await self._ready.wait()
        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken


class LoopbackStream:
    """One end of an in-process byte stream pair."""

    __slots__ = ("_inbox", "_outbox", "_peer")

    def __init__(self, inbox: _Pipe, outbox: _Pipe, peer: str) -> None:
        self._inbox = inbox
        self._outbox = outbox
        self._peer = peer

    @property
    def peer(self) -> str:
        return self._peer

    async def read_exactly(self, count: int) -> bytes:
        return await self._inbox.read_exactly(count)

    async def write(self, data: bytes) -> None:
        self._outbox.write(data)

    async def close(self) -> None:
        self._outbox.close()
        self._inbox.close()


def loopback_pair(
    *, left: str = "loopback-client", right: str = "loopback-server"
) -> tuple[LoopbackStream, LoopbackStream]:
    """Return two connected ends. What one writes, the other reads."""
    to_right = _Pipe()
    to_left = _Pipe()
    return (
        LoopbackStream(to_left, to_right, left),
        LoopbackStream(to_right, to_left, right),
    )
