"""The reliable TCP adapter.

This is the first transport, and it is deliberately the simplest one that can carry a
playable session: asyncio stream sockets, one connection per client, length-prefixed
frames. Nothing above :class:`~battle_city_protocol.transport.ByteStream` knows it is
here, so a later loss-tolerant channel is a second adapter rather than a protocol or
rules change.

What TCP gives and what it costs
--------------------------------
It gives ordered, reliable, de-duplicated delivery, which is why lobby and control
messages can be written once and assumed to arrive, and why the server needs no
acknowledgement or resend logic of its own.

It costs head-of-line blocking and an unbounded cost for a slow reader. A lost packet
stalls every later message behind it until it is retransmitted, so a client on a lossy
link sees state arrive in bursts; interpolation between authoritative snapshots hides
some of that and a real-time transport is the actual fix. A client that stops reading
cannot be made to read: the kernel buffer fills, writes stop draining, and the only
remaining move is to drop the queue and close the connection, which is what
:class:`~battle_city_server.server.SessionServer` does once its outbound queue is full.

Session loss
------------
A connection that drops takes its slot with it. The session keeps running without that
player, their queued input is discarded, and their credential is revoked: this release
has no reconnect and no host migration, so there is no safe way to let someone back
into a run that moved on without them. If the server process itself is lost, the
session is lost; the networking specification allows that for the first release and a
future proposal owns the alternative.
"""

from __future__ import annotations

import asyncio
import contextlib

from battle_city_protocol import server_channel

from .server import SessionServer


class TcpStream:
    """An :class:`~battle_city_protocol.transport.ByteStream` over an asyncio socket."""

    __slots__ = ("_closed", "_peer", "_reader", "_writer")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        peername = writer.get_extra_info("peername")
        self._peer = _describe(peername)

    @property
    def peer(self) -> str:
        return self._peer

    async def read_exactly(self, count: int) -> bytes:
        try:
            return await self._reader.readexactly(count)
        except asyncio.IncompleteReadError as error:
            # A short read is end of stream. The channel decides whether that was a
            # clean close between frames or a truncated one inside a frame.
            return error.partial
        except ConnectionError, OSError:
            return b""

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self._writer.wait_closed()


async def serve_tcp(server: SessionServer, host: str, port: int) -> asyncio.Server:
    """Start accepting TCP clients for ``server``.

    Returns the :class:`asyncio.Server` so the caller owns its lifetime; binding to port
    ``0`` and reading the assigned port back is how a test gets an ephemeral port.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = TcpStream(reader, writer)
        await server.serve(server_channel(stream))

    return await asyncio.start_server(handle, host, port)


def _describe(peername: object) -> str:
    """Render a peer address for logs. Addresses are not secrets; tokens are."""
    if isinstance(peername, tuple) and len(peername) >= 2:
        return f"{peername[0]}:{peername[1]}"
    return "unknown"
