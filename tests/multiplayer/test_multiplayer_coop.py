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
from battle_city_server import MatchSession, SessionLimits, SessionServer
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
    server: SessionServer,
    pack: Pack,
    slots: tuple[int, ...] = (1, 2),
    *,
    settle: float = 0.0,
) -> tuple[list[Client], dict[int, str]]:
    """Seat every slot, agree, start, and join the session the lobby created."""
    clients = [await seat(server, pack, slot, f"p{slot}") for slot in slots]
    for client in clients:
        await client.drain()
    if settle:
        # Time spent in the lobby, on purpose. See the deadline test below.
        await asyncio.sleep(settle)
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


def test_a_lobby_older_than_the_join_deadline_still_hands_over(tmp_path: Path) -> None:
    """Deciding slowly must not cost the match the clients were deciding about.

    The join deadline bounds how long a stranger may hold a connection before proving
    membership, and it is measured from when the socket was accepted. The handover makes
    every connection a stranger again -- the lobby seat stops counting and the session
    membership has not been proved yet -- so a lobby that took longer to agree than the
    deadline allows would hand out credentials nobody could spend: the first read after
    the handover would time out before the client could answer.

    The deadline here is a fifth of a second and the lobby spends twice that, which is
    the same shape as a real lobby spending half a minute under the shipped ten. Both
    clients must still join the match the lobby started.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack, limits=SessionLimits(join_deadline_seconds=0.2))
        clients, _ = await start_coop(server, pack, settle=0.4)
        assert match.game is not None
        assert match.game.joined_slots() == (1, 2)
        assert server.connection_count == 2

        # And the budget is a budget, not a removal: the run goes on from here.
        await server.advance_tick()
        for client in clients:
            assert (await client.receive_until(StateSnapshot)).tick == 1
        await close_all(server, *clients)

    run(scenario)


def test_a_connection_that_never_joins_the_started_match_is_still_closed(
    tmp_path: Path,
) -> None:
    """The re-armed deadline is still a deadline.

    A client that takes its credential and then says nothing is holding a slot the match
    is waiting on. It is given the join budget again from the moment it became a
    stranger, and closed when that budget is spent -- which is what keeps the re-arm from
    being an unbounded wait wearing a timer's clothes.
    """

    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack, limits=SessionLimits(join_deadline_seconds=0.2))
        clients = [await seat(server, pack, slot, f"p{slot}") for slot in (1, 2)]
        for client in clients:
            await client.drain()
        for client, slot in zip(clients, (1, 2), strict=True):
            await client.send(lobby_ready(slot, revision=0))
        for client in clients:
            await client.drain()
        await clients[0].send(lobby_start(1))
        for client in clients:
            await client.receive_until(MatchStarting)

        # Neither client answers with its JoinRequest. The host's reader comes back for
        # the next message and is bounded there; the guest's is parked in a read that
        # will never return, and is bounded by the per-tick sweep instead. Both paths
        # have to end the same way, which is why both clients are checked here.
        await asyncio.sleep(0.4)
        await server.advance_tick()
        closed = [await client.receive_until(SessionClosed) for client in clients]
        assert [notice.code for notice in closed] == [RejectionCode.JOIN_TIMEOUT] * 2
        assert match.game is not None
        assert match.game.joined_slots() == ()
        for client in clients:
            await client.close()
        await server.close()

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


def test_the_host_dropping_mid_match_does_not_end_the_other_players_run(
    tmp_path: Path,
) -> None:
    """The host has no special standing once the match is running.

    A host that leaves ends a *lobby*, because there is no migration and nobody else
    could configure or start it. A started match has nothing left to host: the settings
    are agreed, the stage is loaded and the simulation is the server's. So the host
    dropping is one player dropping, exactly as any other player dropping is, and the
    guest keeps playing the run it joined.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        clients, _ = await start_coop(server, pack)
        host, guest = clients
        assert match.game is not None
        assert match.game.joined_slots() == (1, 2)

        await host.close()
        await asyncio.sleep(0)
        assert not match.closed
        assert match.game.joined_slots() == (2,)
        assert not any(isinstance(message, SessionClosed) for message in await guest.drain())

        await guest.send(move(2, 1, DirectionCode.LEFT))
        await asyncio.sleep(0)
        await server.advance_tick()
        snapshot = await guest.receive_until(StateSnapshot)
        assert snapshot.tick == 1
        assert match.state.tank(2).facing is Direction.LEFT
        await close_all(server, guest)

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
