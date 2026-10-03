"""The client's online controller, driven against a real authoritative server.

These tests run the client package's own code — :class:`ClientShell`,
:class:`OnlineSession`, the snapshot reader — against the real server over the real
loopback transport. No pygame is imported: the online path is deliberately free of it,
which is how a window-less test can prove the thing that matters most here, that an
online client runs no simulation of its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from battle_city_client.intents import IDLE_INTENT, Action, PlayerIntent
from battle_city_client.online import OnlineConfig, OnlinePhase, OnlineSession
from battle_city_client.session import StageSession
from battle_city_client.shell import ClientShell, Screen
from battle_city_client.stage_adapter import stage_from_level
from battle_city_content import Pack
from battle_city_protocol import (
    ClientChannel,
    EventKind,
    GameEvent,
    MatchMode,
    RejectionCode,
    TickEvents,
    client_channel,
)
from battle_city_server import SessionServer, loopback_pair
from battle_city_sim import Direction, Tile
from multiplayer_helpers import (
    LEVEL_ID,
    SESSION_ID,
    TICKETS,
    content_ref,
    level_of,
    make_server,
    write_pack,
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


class Peer:
    """One client shell wired to a server channel, pumped by hand.

    This is the loop in :mod:`battle_city_client.app` with the window taken out: poll
    the channel into the shell, let the shell offer what it wants to say, send it.
    """

    def __init__(
        self, shell: ClientShell, channel: ClientChannel, task: asyncio.Task[None]
    ) -> None:
        self.shell = shell
        self.channel = channel
        self.task = task

    @property
    def online(self) -> OnlineSession:
        session = self.shell.online
        assert session is not None
        return session

    async def pump(self, intent: PlayerIntent = IDLE_INTENT, rounds: int = 1) -> None:
        for _ in range(rounds):
            while True:
                try:
                    message = await asyncio.wait_for(self.channel.receive(), 0.05)
                except TimeoutError:
                    break
                if message is None:
                    self.shell.link_lost("SERVER CLOSED THE CONNECTION")
                    break
                self.shell.receive(message)
            if self.shell.drives_tank:
                self.shell.pump_online(intent)
            for outgoing in self.shell.take_outbox():
                await self.channel.send(outgoing)

    async def close(self) -> None:
        await self.channel.close()
        await self.task


def make_peer_shell(pack: Pack, ticket: str, name: str) -> ClientShell:
    shell = ClientShell(
        catalog=(),
        online_config=OnlineConfig(
            endpoint="loopback:0",
            session_id=SESSION_ID,
            ticket=ticket,
            display_name=name,
            content=content_ref(pack),
        ),
    )
    assert shell.open_online()
    return shell


async def attach(server: SessionServer, shell: ClientShell) -> Peer:
    client_end, server_end = loopback_pair()
    task = asyncio.create_task(server.serve(server.channel_for(server_end)))
    await asyncio.sleep(0)
    peer = Peer(shell, client_channel(client_end), task)
    await peer.pump()
    return peer


def local_session(pack: Pack) -> StageSession:
    """A plain offline run on the same stage, built through the client's own adapter."""
    return StageSession.start(stage_from_level(level_of(pack)))


async def coop_peers(server: SessionServer, pack: Pack) -> tuple[Peer, Peer]:
    """Seat two clients, agree through the client code, and let the host start."""
    host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
    guest = await attach(server, make_peer_shell(pack, TICKETS[2], "guest"))
    for peer in (host, guest):
        await peer.pump()
        peer.shell.handle(Action.ONLINE_READY)
        await peer.pump(rounds=2)
    await host.pump(rounds=2)
    assert host.online.can_start()
    host.shell.handle(Action.UI_CONFIRM)
    for _ in range(4):
        await host.pump()
        await guest.pump()
    return host, guest


# -- the lobby, from the client's side -----------------------------------------


def test_the_client_sees_the_roster_and_what_the_server_will_start(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
        guest = await attach(server, make_peer_shell(pack, TICKETS[2], "guest"))
        await host.pump()

        assert host.shell.screen is Screen.ONLINE_LOBBY
        assert host.online.phase is OnlinePhase.LOBBY
        assert host.online.slot == 1
        assert host.online.host
        assert not guest.online.host
        assert host.online.mode_playable(MatchMode.COOP)
        assert not host.online.mode_playable(MatchMode.FREE_FOR_ALL)

        lobby = host.online.lobby
        assert lobby is not None
        assert [member.display_name for member in lobby.members] == ["host", "guest"]
        assert lobby.blocked is RejectionCode.MEMBERS_NOT_READY
        assert not host.shell.consumes_ticks
        assert host.shell.session is None
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_a_guest_has_nothing_to_say_about_the_settings(tmp_path: Path) -> None:
    """The client refuses to compose a host's message rather than having it refused."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
        guest = await attach(server, make_peer_shell(pack, TICKETS[2], "guest"))
        await guest.pump()
        assert guest.online.configure_message(mode=MatchMode.TEAM_BATTLE) is None
        assert guest.online.start_message() is None
        assert not guest.online.can_start()
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_the_host_can_select_a_competitive_mode_and_is_told_it_will_not_start(
    tmp_path: Path,
) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
        await host.pump()
        message = host.online.configure_message(mode=MatchMode.FREE_FOR_ALL)
        assert message is not None
        await host.channel.send(message)
        await host.pump(rounds=2)

        lobby = host.online.lobby
        assert lobby is not None
        assert lobby.settings.mode is MatchMode.FREE_FOR_ALL
        assert lobby.settings.competitive
        assert lobby.blocked is RejectionCode.MODE_UNSUPPORTED
        assert not host.online.mode_playable(MatchMode.FREE_FOR_ALL)
        assert not host.online.can_start()

        start = host.online.start_message()
        assert start is not None
        await host.channel.send(start)
        await host.pump(rounds=2)
        assert "MODE UNSUPPORTED" in host.online.notice
        assert match.game is None
        assert host.shell.screen is Screen.ONLINE_LOBBY
        await server.close()
        await host.close()

    run(scenario)


# -- the handover and the run --------------------------------------------------


def test_the_client_joins_the_match_the_lobby_started(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)
        for peer in (host, guest):
            assert peer.online.phase is OnlinePhase.PLAYING
            assert peer.shell.screen is Screen.ONLINE_PLAY
            assert peer.online.settings is not None
            assert peer.online.settings.level_id == LEVEL_ID
            assert peer.online.board is not None
            assert peer.online.board.tick == 0
        assert host.online.slot == 1
        assert guest.online.slot == 2
        assert match.game is not None
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_an_online_client_never_advances_a_simulation(tmp_path: Path) -> None:
    """The board only ever moves when the server says so."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)
        assert host.shell.session is None
        assert not host.shell.consumes_ticks
        assert host.shell.drives_tank

        board = host.online.board
        assert board is not None
        # Many frames of held input, and not one server tick: the board must not move.
        for _ in range(10):
            await host.pump(PlayerIntent(direction=Direction.LEFT, fire=True))
        assert host.online.board is not None
        assert host.online.board.tick == board.tick
        assert match.state.tick == 0

        await server.advance_tick()
        await host.pump()
        assert host.online.board is not None
        assert host.online.board.tick == 1
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_one_input_batch_per_authoritative_tick(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)
        intent = PlayerIntent(direction=Direction.LEFT)
        first = host.online.input_batch(intent)
        assert first is not None
        assert first.slot == 1
        assert host.online.input_batch(intent) is None, "one batch per tick"

        await host.channel.send(first)
        await asyncio.sleep(0)
        await server.advance_tick()
        await host.pump()
        second = host.online.input_batch(intent)
        assert second is not None
        assert second.sequence > first.sequence
        assert match.state.tank(1).facing is Direction.LEFT
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_the_client_drives_its_tank_through_the_server(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)
        before = match.state.tank(1).position
        for _ in range(12):
            await host.pump(PlayerIntent(direction=Direction.UP))
            await guest.pump()
            await server.advance_tick()
        await host.pump()
        after = match.state.tank(1).position
        assert after != before
        assert match.state.tank(1).facing is Direction.UP

        board = host.online.board
        assert board is not None
        tank = board.tank_of(1)
        assert tank is not None
        assert (tank.x, tank.y) == (after.x, after.y)
        assert board.state_hash
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_two_clients_drive_their_own_tanks_and_see_the_same_board(tmp_path: Path) -> None:
    """Two real shells, one real server: the co-op acceptance criterion, end to end.

    Both clients hold a different direction for the same run of ticks. Each one drives
    only its own tank, each one's board matches the authoritative state, and each one
    can see the *other* player's tank where the server put it — which is what makes it
    one match rather than two clients each watching their own.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)
        assert host.shell.screen is Screen.ONLINE_PLAY
        assert guest.shell.screen is Screen.ONLINE_PLAY
        assert host.online.slot == 1
        assert guest.online.slot == 2

        before = {slot: match.state.tank(slot).position for slot in (1, 2)}
        for _ in range(12):
            await host.pump(PlayerIntent(direction=Direction.UP))
            await guest.pump(PlayerIntent(direction=Direction.LEFT))
            await server.advance_tick()
        await host.pump()
        await guest.pump()

        for slot, direction in ((1, Direction.UP), (2, Direction.LEFT)):
            assert match.state.tank(slot).facing is direction
            assert match.state.tank(slot).position != before[slot]

        boards = [host.online.board, guest.online.board]
        for board in boards:
            assert board is not None
            assert board.tick == match.state.tick
            for slot in (1, 2):
                tank = board.tank_of(slot)
                assert tank is not None, f"slot {slot} is missing from a client's board"
                authoritative = match.state.tank(slot).position
                assert (tank.x, tank.y) == (authoritative.x, authoritative.y)
        assert boards[0] is not None and boards[1] is not None
        assert boards[0].state_hash == boards[1].state_hash

        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_terrain_damage_arrives_as_an_event_and_is_corrected_by_a_keyframe(
    tmp_path: Path,
) -> None:
    """Presentation keeps up with authoritative events; the next keyframe is the truth."""
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest = await coop_peers(server, pack)
        board = host.online.board
        assert board is not None
        cell = board.base_cell
        assert board.grid.rows[cell.y][cell.x] is Tile.HOME

        host.shell.receive(
            TickEvents(
                session_id=SESSION_ID,
                tick=board.tick,
                events=(
                    GameEvent.of(
                        EventKind.TILE_DAMAGED, cell.x, cell.y, Tile.HOME.value, Tile.EMPTY.value, 9
                    ),
                ),
            )
        )
        damaged = host.online.board
        assert damaged is not None
        assert damaged.grid.rows[cell.y][cell.x] is Tile.EMPTY
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


# -- endings -------------------------------------------------------------------


def test_the_client_reports_the_servers_ending_and_invents_none(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest = await coop_peers(server, pack)
        await server.close(RejectionCode.SERVER_SHUTDOWN, "operator stopped the server")
        await host.pump(rounds=2)
        assert host.online.phase is OnlinePhase.ENDED
        assert host.online.closed_reason is RejectionCode.SERVER_SHUTDOWN
        assert host.online.outcome is None, "the client must not invent an outcome"
        await host.close()
        await guest.close()

    run(scenario)


def test_a_lost_link_ends_the_session_without_an_outcome(tmp_path: Path) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest = await coop_peers(server, pack)
        host.shell.link_lost("CONNECTION LOST")
        assert host.online.phase is OnlinePhase.ENDED
        assert host.online.closed_reason is None
        assert host.online.outcome is None
        assert host.online.notice == "CONNECTION LOST"
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_leaving_the_lobby_returns_to_the_menu_and_drops_the_session(
    tmp_path: Path,
) -> None:
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
        guest = await attach(server, make_peer_shell(pack, TICKETS[2], "guest"))
        await guest.pump()
        guest.shell.handle(Action.UI_CANCEL)
        await guest.pump()
        assert guest.shell.screen is Screen.MAIN_MENU
        assert guest.shell.online is None
        assert not guest.shell.wants_link

        await host.pump(rounds=2)
        lobby = host.online.lobby
        assert lobby is not None
        assert [member.slot for member in lobby.members] == [1]
        assert match.lobby.occupied_slots() == (1,)
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_starting_a_local_run_gives_up_the_online_seat(tmp_path: Path) -> None:
    """A local campaign and an online match are never both running in one shell.

    One board, one intent stream, one screen. If opening a local run left the online
    session in place, the shell would keep a seat it had stopped playing and would go on
    offering input for a match nobody was watching, so the seat is given up the same way
    leaving the lobby gives it up -- with the departure the roster can explain.
    """
    pack = write_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host = await attach(server, make_peer_shell(pack, TICKETS[1], "host"))
        guest = await attach(server, make_peer_shell(pack, TICKETS[2], "guest"))
        await guest.pump()
        # Read through a local: asserting on the attribute itself narrows it for the
        # rest of the test, and the next assertion is that it changed.
        seated = guest.shell.online
        assert seated is not None and seated.seated

        guest.shell.open_session(local_session(pack))
        assert guest.shell.online is None
        assert guest.shell.screen is Screen.PLAYING
        assert guest.shell.campaign is None
        await guest.pump()

        await host.pump(rounds=2)
        lobby = host.online.lobby
        assert lobby is not None
        assert [member.slot for member in lobby.members] == [1]
        assert match.lobby.occupied_slots() == (1,)
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)
