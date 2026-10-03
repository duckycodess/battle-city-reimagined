"""One client must not be able to act as another, in the lobby or in the run.

Every test here has the same shape: a real connected client sends a well-formed,
in-bounds message that claims somebody else's slot, team or credential, and the server
refuses it with a stable code and changes nothing. They are written against the wire
rather than against the session object, because the thing being proved is that there is
no path from a socket to another player's authority — not that one function checks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from battle_city_content import Pack
from battle_city_protocol import (
    ActionKind,
    DirectionCode,
    InputBatch,
    JoinAccepted,
    LobbyState,
    MatchMode,
    MatchStarting,
    PlayerAction,
    Rejected,
    RejectionCode,
    StateSnapshot,
    TeamAssignment,
)
from battle_city_server import SessionServer
from multiplayer_helpers import (
    SESSION_ID,
    Client,
    close_all,
    connect,
    join_request,
    lobby_configure,
    lobby_join,
    lobby_leave,
    lobby_ready,
    lobby_start,
    make_server,
    move,
    write_pack,
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


async def seat(server: SessionServer, pack: Pack, slot: int) -> Client:
    client = await connect(server)
    await client.send(lobby_join(pack, slot, name=f"p{slot}"))
    await client.drain()
    return client


async def started(server: SessionServer, pack: Pack) -> tuple[Client, Client, dict[int, str]]:
    host = await seat(server, pack, 1)
    guest = await seat(server, pack, 2)
    await host.send(lobby_ready(1))
    await guest.send(lobby_ready(2))
    await host.drain()
    await guest.drain()
    await host.send(lobby_start(1))
    tokens: dict[int, str] = {}
    for client, slot in ((host, 1), (guest, 2)):
        starting = await client.receive_until(MatchStarting)
        tokens[slot] = starting.token
        await client.send(join_request(pack, slot, starting.token))
        await client.receive_until(JoinAccepted)
        await client.receive_until(StateSnapshot)
    return host, guest, tokens


# -- in the lobby --------------------------------------------------------------


def test_a_member_cannot_ready_another_slot(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await seat(server, pack, 1)
        guest = await seat(server, pack, 2)
        await guest.send(lobby_ready(1, ready=True))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.WRONG_PLAYER
        roster = [message for message in await host.drain() if isinstance(message, LobbyState)]
        assert all(not member.ready for state in roster for member in state.members)
        await close_all(server, host, guest)

    run(scenario)


def test_a_member_cannot_start_in_the_hosts_name(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await seat(server, pack, 1)
        guest = await seat(server, pack, 2)
        await host.send(lobby_ready(1))
        await guest.send(lobby_ready(2))
        await host.drain()
        await guest.drain()
        await guest.send(lobby_start(1))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.WRONG_PLAYER
        assert match.game is None
        await close_all(server, host, guest)

    run(scenario)


def test_a_member_cannot_assign_teams_in_the_hosts_name(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await seat(server, pack, 1)
        guest = await seat(server, pack, 2)
        await guest.send(
            lobby_configure(
                1,
                mode=MatchMode.TEAM_BATTLE,
                teams=(TeamAssignment(slot=1, team=2), TeamAssignment(slot=2, team=1)),
            )
        )
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.WRONG_PLAYER
        assert match.lobby.mode is MatchMode.COOP
        assert match.lobby.revision == 0
        await close_all(server, host, guest)

    run(scenario)


def test_a_member_cannot_evict_another_by_leaving_for_them(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await seat(server, pack, 1)
        guest = await seat(server, pack, 2)
        await guest.send(lobby_leave(1))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.WRONG_PLAYER
        assert match.lobby.occupied_slots() == (1, 2)
        await close_all(server, host, guest)

    run(scenario)


def test_a_connection_with_no_seat_cannot_act(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host = await seat(server, pack, 1)
        stranger = await connect(server)
        await stranger.send(lobby_ready(1))
        refusal = await stranger.receive_until(Rejected)
        assert refusal.code is RejectionCode.NOT_JOINED
        await close_all(server, host, stranger)

    run(scenario)


def test_a_ticket_cannot_be_reused_while_its_seat_is_held(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host = await seat(server, pack, 1)
        thief = await connect(server)
        await thief.send(lobby_join(pack, 1))
        refusal = await thief.receive_until(Rejected)
        assert refusal.code is RejectionCode.SLOT_OCCUPIED
        await close_all(server, host, thief)

    run(scenario)


# -- in the run ----------------------------------------------------------------


def test_a_player_cannot_submit_input_for_another_slot(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest, _ = await started(server, pack)
        await guest.send(move(1, 1, DirectionCode.LEFT))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.WRONG_PLAYER
        assert refusal.sequence == 1

        await server.advance_tick()
        assert match.state.tank(1).facing is not None
        assert match.state.tick == 1
        await close_all(server, host, guest)

    run(scenario)


def test_a_player_cannot_join_with_another_slots_token(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest, tokens = await started(server, pack)
        stranger = await connect(server)
        await stranger.send(join_request(pack, 1, tokens[2]))
        refusal = await stranger.receive_until(Rejected)
        assert refusal.code is RejectionCode.INVALID_TOKEN
        await close_all(server, host, guest, stranger)

    run(scenario)


def test_an_unknown_slot_is_refused_without_a_token_hint(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest, tokens = await started(server, pack)
        stranger = await connect(server)
        await stranger.send(join_request(pack, 3, tokens[1]))
        refusal = await stranger.receive_until(Rejected)
        assert refusal.code is RejectionCode.UNKNOWN_SLOT
        assert tokens[1] not in refusal.detail
        await close_all(server, host, guest, stranger)

    run(scenario)


def test_a_message_for_another_session_is_refused(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest, _ = await started(server, pack)
        assert SESSION_ID != "someone-elses-session"
        await host.send(
            InputBatch(
                session_id="someone-elses-session",
                slot=1,
                sequence=1,
                actions=(PlayerAction(kind=ActionKind.FIRE),),
            )
        )
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.UNKNOWN_SESSION
        await close_all(server, host, guest)

    run(scenario)
