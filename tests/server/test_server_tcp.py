"""The same session over a real TCP socket.

Loopback proves the session logic; this proves the adapter: a real listener, a real
ephemeral port, real framing across a kernel buffer, and a real close.
"""

from __future__ import annotations

import asyncio

from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_BYTES,
    ClientChannel,
    DirectionCode,
    InputAccepted,
    JoinAccepted,
    Rejected,
    RejectionCode,
    StateSnapshot,
    client_channel,
    encode_frame,
    encode_message,
)
from battle_city_server import SessionServer, TcpStream, serve_tcp
from battle_city_sim import Direction, state_hash
from server_helpers import join_request, make_config, move


async def open_client(port: int) -> tuple[ClientChannel, TcpStream]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    stream = TcpStream(reader, writer)
    return client_channel(stream), stream


def test_a_tcp_client_plays_a_tick() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, _ = await open_client(port)

        await channel.send(join_request(1))
        accepted = await channel.receive()
        keyframe = await channel.receive()
        assert isinstance(accepted, JoinAccepted)
        assert isinstance(keyframe, StateSnapshot)
        assert keyframe.grid is not None

        await channel.send(move(1, 1, DirectionCode.RIGHT))
        assert isinstance(await channel.receive(), InputAccepted)

        await server.advance_tick()
        snapshot = await channel.receive()
        assert isinstance(snapshot, StateSnapshot)
        assert snapshot.tick == 1
        assert snapshot.state_hash == state_hash(server.state)
        assert server.state.tank(1).facing is Direction.RIGHT

        await channel.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())


def test_an_oversized_frame_over_tcp_is_refused_and_closed() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, stream = await open_client(port)

        await channel.send(join_request(1))
        await channel.receive()
        await channel.receive()

        await stream.write((MAX_FRAME_BYTES + 1).to_bytes(FRAME_HEADER_BYTES, "big") + b"x" * 16)
        rejection = await channel.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.FRAME_TOO_LARGE
        assert await channel.receive() is None

        await channel.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())


def test_a_dropped_tcp_connection_leaves_the_session_running() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, _ = await open_client(port)

        await channel.send(join_request(1))
        await channel.receive()
        await channel.receive()
        await channel.close()

        for _ in range(100):
            await asyncio.sleep(0)
            if server.connection_count == 0:
                break
        assert server.connection_count == 0
        assert server.session.joined_slots() == ()

        await server.advance_tick()
        assert server.tick == 1

        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())


def test_a_frame_split_across_writes_is_reassembled() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, stream = await open_client(port)

        frame = encode_frame(encode_message(join_request(1)))
        await stream.write(frame[:3])
        await asyncio.sleep(0)
        await stream.write(frame[3:])

        assert isinstance(await channel.receive(), JoinAccepted)
        await channel.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())
