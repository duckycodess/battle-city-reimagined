"""A whole co-op match on gimmick terrain, from the lobby to the rendered board.

This is the end-to-end half of the compatibility story. The pieces are checked on their
own elsewhere -- the ordering in ``tests/sim``, the version gate in ``tests/server``, the
wire shape in ``tests/protocol`` -- and what is left is the question those cannot answer:
does a real client, joining a real lobby over a real loopback link, end up drawing the
terrain and the positions the server actually has?

The answer has to hold in both directions. A version 2 session carries the new codes all
the way to the client's board, and a client claiming version 1 is refused at the door with
the mismatch the networking specification already defines, rather than being seated and
sent rows it cannot read.

``Peer``, ``attach`` and ``run`` are imported from the sibling client module rather than
copied: a second loopback harness that drifted from the first would make a failure here
mean something different from a failure there.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from battle_city_client.online import OnlineConfig, OnlinePhase
from battle_city_client.remote import RemoteStateError, terrain_from_rows
from battle_city_client.shell import ClientShell
from battle_city_content import Pack
from battle_city_protocol import ContentRef, RejectionCode, client_channel
from battle_city_server import loopback_pair, stage_from_level
from battle_city_sim import DEFAULT_RULES, Direction, GridPos, Tile, tile_code_char
from multiplayer_helpers import (
    GIMMICK_BELT,
    GIMMICK_LEVEL_ID,
    GIMMICK_PARTNER_PAD,
    GIMMICK_SOURCE_PAD,
    SESSION_ID,
    TICKETS,
    content_ref,
    make_server,
    write_gimmick_pack,
    write_pack,
)
from test_multiplayer_client import Peer, coop_peers, run

from battle_city_client import PlayerIntent  # isort: skip -- after the helper imports

SPEED = DEFAULT_RULES.tank_speed
TILE = DEFAULT_RULES.tile_size
TICKS_ONTO_THE_CELL_BELOW = TILE // SPEED // 2
"""Ticks of held movement that carry a cell-aligned tank's centre into the next cell."""

EMPTY_ROW = tile_code_char(Tile.EMPTY) * 16
BASE_ROW = (
    tile_code_char(Tile.EMPTY) * 8 + tile_code_char(Tile.HOME) + tile_code_char(Tile.EMPTY) * 7
)
GIMMICK_ROW = "9ABCD" + tile_code_char(Tile.EMPTY) * 11


def claiming(pack: Pack, ticket: str, name: str, content: ContentRef) -> ClientShell:
    """A client that claims ``content``, whatever the lobby is actually running."""
    shell = ClientShell(
        catalog=(),
        online_config=OnlineConfig(
            endpoint="loopback:0",
            session_id=SESSION_ID,
            ticket=ticket,
            display_name=name,
            content=content,
        ),
    )
    assert shell.open_online()
    return shell


# -- the board reaches the client ---------------------------------------------


def test_a_client_draws_the_gimmick_terrain_the_server_sent(tmp_path: Path) -> None:
    pack = write_gimmick_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest = await coop_peers(server, pack)
        for peer in (host, guest):
            board = peer.online.board
            assert board is not None, peer.online.notice
            assert board.grid.at(GridPos(*GIMMICK_BELT)) is Tile.CONVEYOR_E
            assert board.grid.at(GridPos(*GIMMICK_SOURCE_PAD)) is Tile.TELEPORT_PAD
            assert board.grid.at(GridPos(*GIMMICK_PARTNER_PAD)) is Tile.TELEPORT_PAD
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_both_clients_agree_with_the_server_while_a_belt_carries_a_tank(
    tmp_path: Path,
) -> None:
    """The server decides; the clients render. Neither one ever infers a push."""
    pack = write_gimmick_pack(tmp_path)

    async def scenario() -> None:
        server, match = make_server(pack)
        host, guest = await coop_peers(server, pack)

        down = PlayerIntent(direction=Direction.DOWN)
        for _ in range(TICKS_ONTO_THE_CELL_BELOW + 3):
            await host.pump(down)
            await guest.pump()

        board = host.online.board
        assert board is not None
        tank = board.tank_of(1)
        assert tank is not None
        authoritative = match.state.tank(1).position
        assert (tank.x, tank.y) == (authoritative.x, authoritative.y)

        other = guest.online.board
        assert other is not None
        assert other.state_hash == board.state_hash

        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


def test_an_online_client_still_runs_no_simulation_on_gimmick_terrain(
    tmp_path: Path,
) -> None:
    """A belt is terrain the server applies; the client has no rules engine to apply it."""
    pack = write_gimmick_pack(tmp_path)

    async def scenario() -> None:
        server, _ = make_server(pack)
        host, guest = await coop_peers(server, pack)
        assert host.online.phase is OnlinePhase.PLAYING
        assert host.shell.session is None
        host.shell.advance(30, PlayerIntent())
        assert host.shell.session is None
        await server.close()
        await host.close()
        await guest.close()

    run(scenario)


# -- the version gate, from the client's side ---------------------------------


def test_a_client_claiming_version_one_is_refused_rather_than_seated(tmp_path: Path) -> None:
    pack = write_gimmick_pack(tmp_path)
    classic = write_pack(tmp_path / "classic")

    async def scenario() -> None:
        server, _ = make_server(pack)
        shell = claiming(pack, TICKETS[1], "host", content_ref(classic))
        client_end, server_end = loopback_pair()
        task = asyncio.create_task(server.serve(server.channel_for(server_end)))
        await asyncio.sleep(0)
        peer = Peer(shell, client_channel(client_end), task)
        await peer.pump(rounds=3)

        assert peer.online.board is None
        expected = RejectionCode.CONTENT_MISMATCH.value.replace("_", " ").upper()
        assert expected in peer.online.notice.upper()

        await server.close()
        await peer.close()

    run(scenario)


def test_a_version_one_decode_refuses_a_gimmick_keyframe() -> None:
    """The client's own half of the gate, asserted without a server to produce it.

    A build that read whatever the simulation can represent would happily draw a board the
    session never agreed to; the agreed version is the only thing that can say otherwise.
    """
    rows = (GIMMICK_ROW,) * 15 + (BASE_ROW,)
    grid = terrain_from_rows(rows, content_schema_version=2)
    assert grid.at(GridPos(0, 0)) is Tile.CONVEYOR_N
    assert grid.at(GridPos(4, 0)) is Tile.TELEPORT_PAD

    with pytest.raises(RemoteStateError, match="version 1 does not define"):
        terrain_from_rows(rows, content_schema_version=1)


@pytest.mark.parametrize("version", [1, 2])
def test_a_classic_keyframe_decodes_under_both_versions(version: int) -> None:
    rows = (EMPTY_ROW,) * 15 + (BASE_ROW,)
    assert terrain_from_rows(rows, content_schema_version=version).at(GridPos(8, 15)) is Tile.HOME


def test_an_unsupported_content_version_is_refused_by_the_client() -> None:
    with pytest.raises(RemoteStateError, match="this build reads 1, 2"):
        terrain_from_rows((EMPTY_ROW,) * 16, content_schema_version=99)


# -- the fixture itself -------------------------------------------------------


def test_the_sample_arena_is_reachable_from_its_spawns(tmp_path: Path) -> None:
    """A sanity check on the fixture: both gimmicks sit one step from a legal spawn."""
    pack = write_gimmick_pack(tmp_path)
    assert pack.content_schema_version == 2
    stage = stage_from_level(pack.level(GIMMICK_LEVEL_ID))
    assert stage.teleport_pads == (
        GridPos(*GIMMICK_PARTNER_PAD),
        GridPos(*GIMMICK_SOURCE_PAD),
    )
    assert stage.player_spawn_for(1).cell == GridPos(GIMMICK_BELT[0], GIMMICK_BELT[1] - 1)
    assert stage.player_spawn_for(2).cell == GridPos(
        GIMMICK_SOURCE_PAD[0], GIMMICK_SOURCE_PAD[1] - 1
    )
