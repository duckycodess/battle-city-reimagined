"""What a stranger can cost the server before it has proved anything.

Every test here is a resource an unauthenticated peer could otherwise hold for free: a
connection slot, a task parked on a frame that never finishes, an indefinite hold
before joining, or unlimited guesses at a token.
"""

from __future__ import annotations

import asyncio

from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    DirectionCode,
    InputAccepted,
    Rejected,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
)
from battle_city_server import SessionLimits, SessionServer, serve_tcp
from server_helpers import connect, join_request, joined, make_config, move, open_tcp_client

TIGHT = SessionLimits(join_deadline_seconds=0.05, frame_deadline_seconds=0.05)


def test_the_server_refuses_more_connections_than_it_will_hold() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=SessionLimits(max_connections=1)))
        first = await connect(server)
        assert server.connection_count == 1

        second = await connect(server)
        refusal = await second.receive()
        assert isinstance(refusal, SessionClosed)
        assert refusal.code is RejectionCode.TOO_MANY_CONNECTIONS
        assert await second.channel.receive() is None
        assert server.connection_count == 1
        assert server.session.joined_slots() == ()

        await asyncio.wait_for(second.task, 1.0)
        await first.close()

    asyncio.run(scenario())


def test_a_refused_connection_is_never_registered_with_the_session() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=SessionLimits(max_connections=1)))
        first = await joined(server, 1)
        second = await connect(server)
        await second.receive()
        await asyncio.wait_for(second.task, 1.0)

        # The slot the refused peer never reached is still joinable by someone else.
        await first.close()
        third = await connect(server)
        await third.send(join_request(2))
        accepted = await third.receive()
        assert not isinstance(accepted, SessionClosed)
        await third.close()

    asyncio.run(scenario())


def test_a_connection_that_never_joins_is_closed() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        client = await connect(server)

        closed = await asyncio.wait_for(client.receive(), 1.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.JOIN_TIMEOUT
        await asyncio.wait_for(client.task, 1.0)
        assert server.connection_count == 0

    asyncio.run(scenario())


def test_the_join_deadline_is_absolute_not_per_message() -> None:
    """Trickling refused messages must not keep renewing a stranger's welcome."""

    async def scenario() -> None:
        limits = SessionLimits(join_deadline_seconds=0.08, max_join_attempts=50)
        server = SessionServer(make_config(limits=limits))
        client = await connect(server)

        for sequence in range(1, 4):
            await client.send(move(1, sequence, DirectionCode.UP))
            rejection = await client.receive()
            assert isinstance(rejection, Rejected)
            assert rejection.code is RejectionCode.NOT_JOINED
            await asyncio.sleep(0.03)

        closed = await asyncio.wait_for(client.receive(), 1.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.JOIN_TIMEOUT
        await asyncio.wait_for(client.task, 1.0)

    asyncio.run(scenario())


def test_a_joined_connection_may_stay_quiet_for_longer_than_the_join_deadline() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        client = await joined(server, 1)

        await asyncio.sleep(TIGHT.join_deadline_seconds * 3)
        assert server.connection_count == 1

        await client.send(move(1, 1, DirectionCode.LEFT))
        assert isinstance(await client.receive(), InputAccepted)
        await client.close()

    asyncio.run(scenario())


def test_a_frame_that_starts_and_stops_is_closed() -> None:
    """A declared payload that never arrives would otherwise park a reader for ever."""

    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        client = await joined(server, 1)
        await client.stream.write((1024).to_bytes(FRAME_HEADER_BYTES, "big") + b"x" * 8)

        closed = await asyncio.wait_for(client.receive(), 1.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.FRAME_TIMEOUT
        await asyncio.wait_for(client.task, 1.0)
        assert server.connection_count == 0

    asyncio.run(scenario())


def test_a_header_that_starts_and_stops_is_closed() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        client = await joined(server, 1)
        await client.stream.write(b"\x00\x00")

        closed = await asyncio.wait_for(client.receive(), 1.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.FRAME_TIMEOUT
        await asyncio.wait_for(client.task, 1.0)

    asyncio.run(scenario())


def test_the_run_carries_on_after_a_stalled_connection_is_cut() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        stalled = await joined(server, 1)
        healthy = await joined(server, 2)
        await stalled.stream.write((4096).to_bytes(FRAME_HEADER_BYTES, "big"))
        await asyncio.wait_for(stalled.task, 1.0)

        await server.advance_tick()
        snapshot = await healthy.receive()
        assert isinstance(snapshot, StateSnapshot)
        assert snapshot.tick == 1
        assert server.tick == 1
        await healthy.close()

    asyncio.run(scenario())


def test_a_connection_spends_its_budget_of_failed_joins() -> None:
    async def scenario() -> None:
        limits = SessionLimits(max_join_attempts=2, join_deadline_seconds=5.0)
        server = SessionServer(make_config(limits=limits))
        client = await connect(server)

        await client.send(join_request(1, token="wrong-token-aaaaaaaa"))
        first = await client.receive()
        assert isinstance(first, Rejected)
        assert first.code is RejectionCode.INVALID_TOKEN

        await client.send(join_request(1, token="wrong-token-bbbbbbbb"))
        second = await client.receive()
        assert isinstance(second, Rejected)
        closed = await client.receive()
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.TOO_MANY_ATTEMPTS

        await asyncio.wait_for(client.task, 1.0)
        assert server.connection_count == 0
        assert server.session.joined_slots() == ()

    asyncio.run(scenario())


def test_a_successful_join_does_not_spend_the_budget() -> None:
    async def scenario() -> None:
        limits = SessionLimits(max_join_attempts=2)
        server = SessionServer(make_config(limits=limits))
        client = await connect(server)

        await client.send(join_request(1, token="wrong-token-aaaaaaaa"))
        assert isinstance(await client.receive(), Rejected)

        await client.join(1)
        for sequence in range(1, 6):
            await client.send(move(2, sequence, DirectionCode.UP))
            rejection = await client.receive()
            assert isinstance(rejection, Rejected)
            assert rejection.code is RejectionCode.WRONG_PLAYER
        assert server.connection_count == 1
        await client.close()

    asyncio.run(scenario())


def test_a_tcp_client_that_stalls_mid_frame_is_cut() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, stream = await open_tcp_client(port)

        await channel.send(join_request(1))
        await channel.receive()
        await channel.receive()
        await stream.write((2048).to_bytes(FRAME_HEADER_BYTES, "big"))

        closed = await asyncio.wait_for(channel.receive(), 2.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.FRAME_TIMEOUT
        assert await channel.receive() is None

        await channel.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())


def test_a_tcp_client_that_never_joins_is_cut() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config(limits=TIGHT))
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        channel, _ = await open_tcp_client(port)

        closed = await asyncio.wait_for(channel.receive(), 2.0)
        assert isinstance(closed, SessionClosed)
        assert closed.code is RejectionCode.JOIN_TIMEOUT
        assert await channel.receive() is None

        await channel.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())


def test_tcp_connections_are_capped() -> None:
    async def scenario() -> None:
        limits = SessionLimits(max_connections=1, join_deadline_seconds=5.0)
        server = SessionServer(make_config(limits=limits))
        listener = await serve_tcp(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]

        first, _ = await open_tcp_client(port)
        await first.send(join_request(1))
        await first.receive()
        await first.receive()

        second, _ = await open_tcp_client(port)
        refusal = await asyncio.wait_for(second.receive(), 2.0)
        assert isinstance(refusal, SessionClosed)
        assert refusal.code is RejectionCode.TOO_MANY_CONNECTIONS
        assert server.connection_count == 1

        await first.close()
        await second.close()
        listener.close()
        await listener.wait_closed()

    asyncio.run(scenario())
