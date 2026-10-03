"""The lobby's rules: seating, agreement, who may change what, and what will not start."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from battle_city_content import Pack
from battle_city_protocol import (
    ContentRef,
    LobbyJoin,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchStarting,
    Rejected,
    RejectionCode,
    SessionClosed,
    TeamAssignment,
)
from battle_city_server import (
    PLAYABLE_MODES,
    LobbyConfig,
    LobbyNotStartedError,
    LobbyTicket,
    ServerConfigurationError,
    SessionServer,
)
from multiplayer_helpers import (
    LEVEL_ID,
    SECOND_LEVEL_ID,
    SESSION_ID,
    TICKETS,
    Client,
    close_all,
    connect,
    join_request,
    lobby_configure,
    lobby_join,
    lobby_leave,
    lobby_ready,
    lobby_start,
    make_lobby_config,
    make_server,
    write_pack,
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


async def seated(
    server: SessionServer, pack: Pack, slot: int, name: str = "player"
) -> tuple[Client, LobbyWelcome]:
    client = await connect(server)
    await client.send(lobby_join(pack, slot, name=name))
    welcome = await client.receive_until(LobbyWelcome)
    await client.receive_until(LobbyState)
    return client, welcome


async def latest_roster(client: Client) -> LobbyState:
    """The most recent roster this client has been sent.

    A roster is broadcast on every change, so a client that has been sitting in the
    lobby has several waiting. A test that reads the first one is reading the state
    before the thing it just did.
    """
    rosters = [message for message in await client.drain() if isinstance(message, LobbyState)]
    assert rosters, "no roster arrived"
    return rosters[-1]


# -- seating -------------------------------------------------------------------


def test_a_ticket_claims_its_seat_and_the_roster_is_broadcast(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, welcome = await seated(server, pack, 1, "host")
        assert welcome.slot == 1
        assert welcome.host
        assert welcome.lobby.playable_modes == (MatchMode.COOP,)

        guest, guest_welcome = await seated(server, pack, 2, "guest")
        assert guest_welcome.slot == 2
        assert not guest_welcome.host

        roster = await host.receive_until(LobbyState)
        assert [member.slot for member in roster.members] == [1, 2]
        assert [member.display_name for member in roster.members] == ["host", "guest"]
        assert roster.host_slot == 1
        await close_all(server, host, guest)

    run(scenario)


def test_a_wrong_ticket_is_refused_without_naming_a_seat(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        client = await connect(server)
        await client.send(lobby_join(pack, 1, ticket="not-the-right-ticket"))
        refusal = await client.receive_until(Rejected)
        assert refusal.code is RejectionCode.INVALID_TOKEN
        assert "ticket" not in refusal.detail
        await close_all(server, client)

    run(scenario)


def test_a_seat_that_is_taken_is_refused(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        impostor = await connect(server)
        await impostor.send(lobby_join(pack, 1))
        refusal = await impostor.receive_until(Rejected)
        assert refusal.code is RejectionCode.SLOT_OCCUPIED
        await close_all(server, host, impostor)

    run(scenario)


def test_a_capacity_smaller_than_the_ticket_list_is_enforced(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack, slots=(1, 2, 3), capacity=2)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        third = await connect(server)
        await third.send(lobby_join(pack, 3))
        refusal = await third.receive_until(Rejected)
        assert refusal.code is RejectionCode.LOBBY_FULL
        await close_all(server, host, guest, third)

    run(scenario)


def test_a_different_content_pack_is_refused_at_the_lobby(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        client = await connect(server)
        await client.send(
            LobbyJoin(
                session_id=SESSION_ID,
                ticket=TICKETS[1],
                display_name="host",
                content=ContentRef(
                    pack_id="someone-elses-pack",
                    pack_version="9.9.9",
                    level_id=LEVEL_ID,
                    content_schema_version=1,
                ),
            )
        )
        refusal = await client.receive_until(Rejected)
        assert refusal.code is RejectionCode.CONTENT_MISMATCH
        await close_all(server, client)

    run(scenario)


# -- agreement -----------------------------------------------------------------


def test_readiness_is_agreement_to_one_revision(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        await host.send(lobby_ready(1, revision=0))
        roster = await host.receive_until(LobbyState)
        assert roster.members[0].ready

        # The host changes the stage: the revision moves and consent is withdrawn.
        await host.send(lobby_configure(1, revision=0, level_id=SECOND_LEVEL_ID))
        changed = await host.receive_until(LobbyState)
        assert changed.revision == 1
        assert not changed.members[0].ready

        await host.send(lobby_ready(1, revision=0))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.SETTINGS_STALE
        assert match.lobby.revision == 1
        await close_all(server, host)

    run(scenario)


def test_only_the_host_configures_or_starts(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)

        await guest.send(lobby_configure(2, level_id=SECOND_LEVEL_ID))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.NOT_HOST

        await guest.send(lobby_start(2))
        refusal = await guest.receive_until(Rejected)
        assert refusal.code is RejectionCode.NOT_HOST
        await close_all(server, host, guest)

    run(scenario)


def test_start_waits_for_every_connected_member(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.send(lobby_ready(1))
        roster = await host.receive_until(LobbyState)
        assert not roster.startable
        assert roster.blocked is RejectionCode.MEMBERS_NOT_READY

        await host.send(lobby_start(1))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.MEMBERS_NOT_READY
        await close_all(server, host, guest)

    run(scenario)


def test_teams_are_recorded_for_a_team_mode_and_cleared_outside_one(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.drain()
        await host.send(
            lobby_configure(
                1,
                mode=MatchMode.TEAM_BATTLE,
                teams=(TeamAssignment(slot=1, team=1), TeamAssignment(slot=2, team=2)),
            )
        )
        roster = await latest_roster(host)
        assert roster.settings.mode is MatchMode.TEAM_BATTLE
        assert [member.team for member in roster.members] == [1, 2]

        await host.send(lobby_configure(1, revision=roster.revision, mode=MatchMode.COOP))
        back = await latest_roster(host)
        assert back.settings.mode is MatchMode.COOP
        assert [member.team for member in back.members] == [None, None]
        await close_all(server, host, guest)

    run(scenario)


def test_a_team_cannot_be_assigned_to_an_empty_seat(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        await host.send(
            lobby_configure(1, mode=MatchMode.TEAM_BATTLE, teams=(TeamAssignment(slot=2, team=2),))
        )
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.INVALID_FIELD
        await close_all(server, host)

    run(scenario)


# -- competitive modes ---------------------------------------------------------


@pytest.mark.parametrize("mode", [MatchMode.FREE_FOR_ALL, MatchMode.TEAM_BATTLE])
def test_a_competitive_match_is_configurable_and_refuses_to_start(
    tmp_path: Path, mode: MatchMode
) -> None:
    """The setting is real, versioned and broadcast. The match does not begin.

    The shared simulation has one player faction, no player-versus-player damage and no
    competitive result. Starting under those rules and reporting a duel would be the
    server lying about what it ran, so it refuses by name and says so in the roster
    before anyone presses start.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        teams = (TeamAssignment(slot=1, team=1),) if mode is MatchMode.TEAM_BATTLE else ()
        await host.drain()
        await host.send(lobby_configure(1, mode=mode, teams=teams))
        roster = await latest_roster(host)
        assert roster.settings.mode is mode
        assert roster.settings.competitive
        assert not roster.startable
        assert roster.blocked is RejectionCode.MODE_UNSUPPORTED

        await host.send(lobby_ready(1, revision=roster.revision))
        ready_roster = await latest_roster(host)
        assert ready_roster.members[0].ready
        assert ready_roster.blocked is RejectionCode.MODE_UNSUPPORTED

        await host.send(lobby_start(1, revision=roster.revision))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.MODE_UNSUPPORTED
        assert "competitive" in refusal.detail

        assert match.game is None
        assert not match.ticking
        with pytest.raises(LobbyNotStartedError):
            _ = match.state
        await close_all(server, host)

    run(scenario)


def test_the_build_declares_exactly_which_modes_it_will_start() -> None:
    assert {MatchMode.COOP} == PLAYABLE_MODES


def test_a_stage_that_cannot_seat_the_roster_refuses_to_start(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.drain()
        await host.send(lobby_configure(1, level_id=SECOND_LEVEL_ID))
        roster = await latest_roster(host)
        for client, slot in ((host, 1), (guest, 2)):
            await client.send(lobby_ready(slot, revision=roster.revision))
        ready_roster = await latest_roster(host)
        assert ready_roster.blocked is RejectionCode.STAGE_UNSUPPORTED
        assert not ready_roster.startable

        await host.send(lobby_start(1, revision=roster.revision))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.STAGE_UNSUPPORTED
        await close_all(server, host, guest)

    run(scenario)


def test_an_unoffered_level_is_refused(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        await host.send(lobby_configure(1, level_id="some-other-level"))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.INVALID_FIELD
        await close_all(server, host)

    run(scenario)


def test_every_refusal_leaves_the_lobby_usable_and_the_lobby_still_starts(
    tmp_path: Path,
) -> None:
    """Four refusals in a row, then a clean start on the same lobby.

    Each lobby refusal is a ``Rejected``, which by contract leaves the connection open.
    The individual rules are tested above; what is checked here is that they compose —
    that a host can pick an unrunnable mode, pick an unseatable stage, start too early
    and agree to a revision that has moved on, be told exactly which rule it broke each
    time, and still get the co-op match it was entitled to. A lobby that had to be
    rebuilt after a mistyped setting would be unusable, and a refusal that silently
    dropped the connection would look like a crash.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.drain()
        await guest.drain()
        observed: list[RejectionCode] = []

        # A mode this build will not run.
        await host.send(lobby_configure(1, revision=0, mode=MatchMode.FREE_FOR_ALL))
        roster = await latest_roster(host)
        await host.send(lobby_start(1, revision=roster.revision))
        observed.append((await host.receive_until(Rejected)).code)

        # A stage that cannot seat two players.
        await host.send(lobby_configure(1, revision=roster.revision, level_id=SECOND_LEVEL_ID))
        roster = await latest_roster(host)
        await host.send(lobby_start(1, revision=roster.revision))
        observed.append((await host.receive_until(Rejected)).code)

        # Back to something runnable, and started before anyone agreed to it.
        await host.send(lobby_configure(1, revision=roster.revision, level_id=LEVEL_ID))
        roster = await latest_roster(host)
        await host.send(lobby_start(1, revision=roster.revision))
        observed.append((await host.receive_until(Rejected)).code)

        # An agreement to a revision the lobby has already moved past.
        await host.send(lobby_ready(1, revision=roster.revision - 1))
        observed.append((await host.receive_until(Rejected)).code)

        assert observed == [
            RejectionCode.MODE_UNSUPPORTED,
            RejectionCode.STAGE_UNSUPPORTED,
            RejectionCode.MEMBERS_NOT_READY,
            RejectionCode.SETTINGS_STALE,
        ]
        assert match.game is None, "nothing may have started during any of that"

        # The same lobby, the same two connections, now doing it correctly. The start
        # waits for the roster that says so: two agreements arriving on two connections
        # are two events, and asking to start between them is the race, not the rule.
        for client, slot in ((host, 1), (guest, 2)):
            await client.send(lobby_ready(slot, revision=roster.revision))
        agreed = await latest_roster(host)
        assert agreed.startable
        assert agreed.blocked is None
        await host.send(lobby_start(1, revision=agreed.revision))
        for client, slot in ((host, 1), (guest, 2)):
            starting = await client.receive_until(MatchStarting)
            assert starting.slot == slot
            assert starting.settings.mode is MatchMode.COOP
            assert starting.settings.level_id == LEVEL_ID
        await close_all(server, host, guest)

    run(scenario)


# -- leaving -------------------------------------------------------------------


def test_leaving_frees_the_seat_and_tells_the_others(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.receive_until(LobbyState)
        await guest.send(lobby_leave(2))
        roster = await host.receive_until(LobbyState)
        assert [member.slot for member in roster.members] == [1]
        await close_all(server, host, guest)

    run(scenario)


def test_a_host_that_leaves_ends_the_lobby(tmp_path: Path) -> None:
    """There is no host migration in this release, so the lobby ends rather than stalls."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        guest, _ = await seated(server, pack, 2)
        await host.send(lobby_leave(1))
        closed = await guest.receive_until(SessionClosed)
        assert closed.code is RejectionCode.SESSION_CLOSED
        assert "host" in closed.detail
        assert match.closed
        await close_all(server, host, guest)

    run(scenario)


# -- the tick ------------------------------------------------------------------


def test_no_tick_runs_before_a_match_starts(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        assert not match.ticking
        assert match.tick == 0
        await server.advance_tick()
        await server.advance_tick()
        assert match.tick == 0
        assert match.game is None
        await close_all(server, host)

    run(scenario)


def test_gameplay_messages_are_refused_before_the_match_starts(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, _ = await seated(server, pack, 1)
        await host.send(join_request(pack, 1, "token-that-was-never-issued"))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.UNEXPECTED_MESSAGE
        await close_all(server, host)

    run(scenario)


def test_lobby_messages_are_refused_once_the_match_has_started(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, _ = await seated(server, pack, 1)
        await host.drain()
        await host.send(lobby_ready(1))
        roster = await latest_roster(host)
        assert roster.startable
        await host.send(lobby_start(1, revision=roster.revision))
        await host.receive_until(MatchStarting)

        await host.send(lobby_ready(1, revision=roster.revision))
        refusal = await host.receive_until(Rejected)
        assert refusal.code is RejectionCode.UNEXPECTED_MESSAGE
        assert match.game is not None
        await close_all(server, host)

    run(scenario)


# -- configuration -------------------------------------------------------------


def test_a_lobby_needs_exactly_one_host_ticket(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)
    config = make_lobby_config(pack)
    with pytest.raises(ServerConfigurationError):
        LobbyConfig(
            session_id=SESSION_ID,
            tickets=tuple(
                LobbyTicket(slot=ticket.slot, ticket=ticket.ticket) for ticket in config.tickets
            ),
            levels=config.levels,
            seed=1,
        )


def test_a_lobby_refuses_an_unusable_ticket(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)
    config = make_lobby_config(pack)
    with pytest.raises(ServerConfigurationError) as error:
        LobbyConfig(
            session_id=SESSION_ID,
            tickets=(LobbyTicket(slot=1, ticket="tiny", host=True),),
            levels=config.levels,
            seed=1,
        )
    assert "tiny" not in str(error.value)
