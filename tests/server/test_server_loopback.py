"""End to end over a loopback transport: frames in, authoritative state out.

These exercise the whole path a TCP client takes — framing, strict decoding, session
authority, per-connection queues — without a socket, so they are deterministic and fast.
``test_server_tcp`` then proves the same path works over a real socket.
"""

from __future__ import annotations

import asyncio
import json

from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_BYTES,
    DirectionCode,
    InputAccepted,
    Rejected,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
    TickEvents,
    decode_server_message,
    encode_frame,
    encode_message,
)
from battle_city_server import ManualClock, SessionLimits, SessionServer
from battle_city_sim import Direction, state_hash
from server_helpers import (
    BlockedStream,
    connect,
    fire,
    join_request,
    joined,
    make_config,
    move,
    split_frames,
)


def test_a_client_joins_sends_input_and_sees_the_authoritative_result() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await connect(server)
        accepted, keyframe = await client.join(1)
        assert accepted.slot == 1
        assert keyframe.keyframe

        await client.send(move(1, 1, DirectionCode.LEFT))
        ack = await client.receive()
        assert isinstance(ack, InputAccepted)
        assert ack.tick == 0

        await server.advance_tick()
        snapshot = await client.receive()
        events = await client.receive()
        assert isinstance(snapshot, StateSnapshot)
        assert isinstance(events, TickEvents)
        assert snapshot.tick == 1
        assert snapshot.tick_rate == 60
        assert snapshot.grid is None
        assert snapshot.state_hash == state_hash(server.state)
        assert server.state.tank(1).facing is Direction.LEFT
        await client.close()

    asyncio.run(scenario())


def test_two_clients_receive_the_same_authoritative_snapshot() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        one = await joined(server, 1)
        two = await joined(server, 2)

        await one.send(move(1, 1, DirectionCode.LEFT))
        await two.send(move(2, 1, DirectionCode.RIGHT))
        await one.receive()
        await two.receive()

        await server.advance_tick()
        first = await one.receive()
        second = await two.receive()
        assert first == second
        assert isinstance(first, StateSnapshot)
        assert first.state_hash == state_hash(server.state)
        await one.close()
        await two.close()

    asyncio.run(scenario())


def test_a_client_may_not_submit_input_for_another_slot() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await client.send(move(2, 1, DirectionCode.UP))
        rejection = await client.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.WRONG_PLAYER

        await server.advance_tick()
        snapshot = await client.receive()
        assert isinstance(snapshot, StateSnapshot)
        assert server.state.tank(2).facing is Direction.UP
        await client.close()

    asyncio.run(scenario())


def test_a_recoverable_rejection_leaves_the_connection_usable() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)

        body = json.loads(encode_message(move(1, 1, DirectionCode.UP)))
        body["score"] = 9001
        await client.stream.write(encode_frame(json.dumps(body).encode()))
        rejection = await client.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.UNKNOWN_FIELD

        await client.send(fire(1, 2))
        assert isinstance(await client.receive(), InputAccepted)
        await client.close()

    asyncio.run(scenario())


def test_a_malformed_frame_is_answered_and_then_closed() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        await client.stream.write(encode_frame(b"this is not json"))

        rejection = await client.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.MALFORMED_FRAME
        assert await client.channel.receive() is None
        await asyncio.wait_for(client.task, 1.0)
        assert server.connection_count == 0

    asyncio.run(scenario())


def test_an_oversized_frame_is_refused_without_being_read() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        header = (MAX_FRAME_BYTES + 1).to_bytes(FRAME_HEADER_BYTES, "big")
        await client.stream.write(header + b"x" * 64)

        rejection = await client.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.FRAME_TOO_LARGE
        await asyncio.wait_for(client.task, 1.0)

    asyncio.run(scenario())


def test_a_snapshot_cannot_be_passed_off_as_client_input() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        snapshot = server.session.snapshot(keyframe=False)
        await client.stream.write(encode_frame(encode_message(snapshot)))

        rejection = await client.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.UNEXPECTED_MESSAGE
        await client.close()

    asyncio.run(scenario())


def test_a_disconnect_frees_nothing_and_the_run_carries_on() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        one = await joined(server, 1)
        two = await joined(server, 2)

        await one.send(move(1, 1, DirectionCode.LEFT, tick=0))
        await one.receive()
        await one.close()
        assert server.connection_count == 1

        await server.advance_tick()
        snapshot = await two.receive()
        assert isinstance(snapshot, StateSnapshot)
        assert snapshot.tick == 1
        assert server.state.tank(1).facing is Direction.UP

        rejoin = await connect(server)
        await rejoin.send(join_request(1))
        rejection = await rejoin.receive()
        assert isinstance(rejection, Rejected)
        assert rejection.code is RejectionCode.MEMBERSHIP_REVOKED
        await rejoin.close()
        await two.close()

    asyncio.run(scenario())


def test_a_client_that_stops_reading_is_dropped() -> None:
    async def scenario() -> None:
        limits = SessionLimits(max_outbound_messages=2, keyframe_interval=30)
        server = SessionServer(make_config(limits=limits), flush_timeout=0.05)
        stream = BlockedStream(encode_frame(encode_message(join_request(1))))
        task = asyncio.create_task(server.serve(server.channel_for(stream)))
        await asyncio.sleep(0)
        assert server.session.joined_slots() == (1,)

        for _ in range(8):
            await server.advance_tick()

        stream.released.set()
        await asyncio.wait_for(task, 2.0)
        assert server.connection_count == 0
        assert stream.closed
        assert server.session.joined_slots() == ()
        assert server.tick == 8

        last = decode_server_message(split_frames(bytes(stream.written))[-1])
        assert isinstance(last, SessionClosed)
        assert last.code is RejectionCode.QUEUE_OVERFLOW

    asyncio.run(scenario())


def test_the_server_tells_every_client_when_it_shuts_down() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        one = await joined(server, 1)
        two = await joined(server, 2)

        await server.close(RejectionCode.SERVER_SHUTDOWN, "going down")
        first = await one.receive()
        second = await two.receive()
        assert isinstance(first, SessionClosed)
        assert isinstance(second, SessionClosed)
        assert first.code is RejectionCode.SERVER_SHUTDOWN
        await asyncio.wait_for(one.task, 1.0)
        await asyncio.wait_for(two.task, 1.0)

    asyncio.run(scenario())


def test_a_run_advances_exactly_the_ticks_the_clock_releases() -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        client = await joined(server, 1)
        clock = ManualClock()
        runner = asyncio.create_task(server.run(clock, ticks=3))

        clock.release(3)
        await asyncio.wait_for(runner, 1.0)
        assert server.tick == 3

        snapshots = [await client.receive() for _ in range(3)]
        assert [snapshot.tick for snapshot in snapshots if isinstance(snapshot, StateSnapshot)] == [
            1,
            2,
            3,
        ]
        await client.close()

    asyncio.run(scenario())
