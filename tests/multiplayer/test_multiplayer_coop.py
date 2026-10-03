"""Two clients, one authoritative co-op run: start, play, disconnect, end.

Everything here goes over a real loopback channel through the real framing, the real
decoder and the real session, on a stage that was written as a content pack and loaded
through the content validator. Nothing is stubbed except the wire, and the wire is the
server package's own in-process transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from battle_city_content import Pack
from battle_city_protocol import (
    DirectionCode,
    JoinAccepted,
    LobbyState,
    MatchStarting,
    Rejected,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
)
from battle_city_server import MatchSession, SessionServer
from battle_city_sim import Direction, state_hash
from multiplayer_helpers import (
    LEVEL_ID,
    Client,
    close_all,
    connect,
    join_request,
    lobby_join,
    lobby_ready,
    lobby_start,
    make_server,
    move,
    write_pack,
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


async def seat(server: SessionServer, pack: Pack, slot: int, name: str) -> Client:
    client = await connect(server)
    await client.send(lobby_join(pack, slot, name=name))
    return client


async def start_coop(
    server: SessionServer, pack: Pack, slots: tuple[int, ...] = (1, 2)
) -> tuple[list[Client], dict[int, str]]:
    """Seat every slot, agree, start, and join the session the lobby created."""
    clients = [await seat(server, pack, slot, f"p{slot}") for slot in slots]
    for client in clients:
        await client.drain()
    revision = 0
    for client, slot in zip(clients, slots, strict=True):
        await client.send(lobby_ready(slot, revision=revision))
    for client in clients:
        await client.drain()
    await clients[0].send(lobby_start(slots[0], revision=revision))

    tokens: dict[int, str] = {}
    for client, slot in zip(clients, slots, strict=True):
        starting = await client.receive_until(MatchStarting)
        assert starting.slot == slot
        tokens[slot] = starting.token
    for client, slot in zip(clients, slots, strict=True):
        await client.send(join_request(pack, slot, tokens[slot]))
        accepted = await client.receive_until(JoinAccepted)
        assert accepted.slot == slot
        await client.receive_until(StateSnapshot)
    return clients, tokens


# -- the handover --------------------------------------------------------------


def test_two_clients_reach_one_authoritative_run(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        clients, tokens = await start_coop(server, pack)
        assert match.game is not None
        assert match.ticking
        assert match.game.joined_slots() == (1, 2)
        assert match.state.tick == 0
        assert len(match.state.tanks) == 2
        assert {tank.player_slot for tank in match.state.tanks} == {1, 2}
        assert match.state.stage_id == LEVEL_ID
        await close_all(server, *clients)

    run(scenario)


def test_each_member_is_given_a_different_credential(tmp_path: Path) -> None:
    """One token per slot, on that slot's connection only."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        clients, tokens = await start_coop(server, pack)
        assert len(set(tokens.values())) == len(tokens)
        await close_all(server, *clients)

    run(scenario)


def test_a_roster_never_carries_a_credential(tmp_path: Path) -> None:
    """Every broadcast the lobby produced, checked against the tokens it later minted."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host = await seat(server, pack, 1, "host")
        guest = await seat(server, pack, 2, "guest")
        seen = [*await host.drain(), *await guest.drain()]
        await host.send(lobby_ready(1))
        await guest.send(lobby_ready(2))
        seen += [*await host.drain(), *await guest.drain()]
        await host.send(lobby_start(1))

        tokens = set()
        for client in (host, guest):
            tokens.add((await client.receive_until(MatchStarting)).token)
        rosters = [message for message in seen if isinstance(message, LobbyState)]
        assert rosters
        encoded = repr(rosters)
        for token in tokens:
            assert token not in encoded
        await close_all(server, host, guest)

    run(scenario)


# -- playing -------------------------------------------------------------------


def test_both_clients_see_the_same_authoritative_tick(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        clients, _ = await start_coop(server, pack)
        one, two = clients
        await one.send(move(1, 1, DirectionCode.LEFT))
        await two.send(move(2, 1, DirectionCode.UP))
        await asyncio.sleep(0)
        await server.advance_tick()

        first = await one.receive_until(StateSnapshot)
        second = await two.receive_until(StateSnapshot)
        assert first.tick == second.tick == 1
        assert first.state_hash == second.state_hash == state_hash(match.state)
        assert match.state.tank(1).facing is Direction.LEFT
        assert match.state.tank(2).facing is Direction.UP
        await close_all(server, *clients)

    run(scenario)


def test_the_run_is_identical_for_the_same_seed_and_inputs(tmp_path: Path) -> None:
    """Determinism across two whole servers, driven only through the wire."""
    pack = write_pack(tmp_path)

    async def play() -> str:
        server, match = make_server(pack)
        clients, _ = await start_coop(server, pack)
        one, two = clients
        for sequence in range(1, 6):
            await one.send(move(1, sequence, DirectionCode.LEFT))
            await two.send(move(2, sequence, DirectionCode.UP))
            await asyncio.sleep(0)
            await server.advance_tick()
        digest = state_hash(match.state)
        await close_all(server, *clients)
        return digest

    async def scenario() -> None:
        assert await play() == await play()

    run(scenario)


# -- leaving -------------------------------------------------------------------


def test_a_disconnect_mid_run_leaves_the_other_client_playing(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        clients, _ = await start_coop(server, pack)
        one, two = clients
        await two.close()
        await asyncio.sleep(0)
        assert match.game is not None
        assert match.game.joined_slots() == (1,)

        await one.send(move(1, 1, DirectionCode.LEFT))
        await asyncio.sleep(0)
        await server.advance_tick()
        snapshot = await one.receive_until(StateSnapshot)
        assert snapshot.tick == 1
        assert not match.closed
        await close_all(server, one)

    run(scenario)


def test_a_disconnected_slot_cannot_come_back(tmp_path: Path) -> None:
    """No reconnect in this release: the credential is spent when the socket goes."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        clients, tokens = await start_coop(server, pack)
        one, two = clients
        await two.close()
        await asyncio.sleep(0)

        returning = await connect(server)
        await returning.send(join_request(pack, 2, tokens[2]))
        refusal = await returning.receive_until(Rejected)
        assert refusal.code is RejectionCode.MEMBERSHIP_REVOKED
        await close_all(server, one, returning)

    run(scenario)


def test_closing_the_server_tells_every_client_why(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        clients, _ = await start_coop(server, pack)
        await server.close(RejectionCode.SERVER_SHUTDOWN, "operator stopped the server")
        for client in clients:
            closed = await client.receive_until(SessionClosed)
            assert closed.code is RejectionCode.SERVER_SHUTDOWN
        for client in clients:
            await client.close()

    run(scenario)


# -- the clock -----------------------------------------------------------------


def test_the_tick_loop_waits_for_the_lobby_and_then_runs(tmp_path: Path) -> None:
    """A lobby does not tick; the same server ticks as soon as the match starts."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        from battle_city_server import ManualClock

        server, match = make_server(pack)
        clock = ManualClock()
        loop = asyncio.create_task(server.run(clock, ticks=3))
        await asyncio.sleep(0)
        clock.release(3)
        await asyncio.sleep(0)
        assert match.tick == 0, "a lobby must not advance a simulation"

        clients, _ = await start_coop(server, pack)
        await asyncio.wait_for(loop, timeout=2.0)
        assert match.tick == 3
        await close_all(server, *clients)

    run(scenario)


def test_a_match_session_is_the_server_authority(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)
    server, match = make_server(pack)
    assert isinstance(match, MatchSession)
    assert server.session is match
    assert match.session_id == match.lobby.config.session_id
    assert not match.ticking
