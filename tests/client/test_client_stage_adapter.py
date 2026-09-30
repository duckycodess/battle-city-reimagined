"""The Level-to-Stage adapter: mechanical, validated, and free of I/O."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from battle_city_client.stage_adapter import (
    StageAdapterError,
    bundled_stage_catalog,
    cell_to_grid_pos,
    stage_catalog,
    stage_from_level,
)
from battle_city_content import GridCell, Wave, load_bundled_pack
from battle_city_sim import CLASSIC_GRID_SIZE, GridPos, Tile
from client_helpers import make_level, make_pack, rows_with

BUNDLED_IDS = ("classic-01", "classic-02", "classic-03")


def test_bundled_catalog_adapts_every_classic_stage() -> None:
    catalog = bundled_stage_catalog()
    assert tuple(entry.level_id for entry in catalog) == BUNDLED_IDS
    for entry in catalog:
        assert entry.stage.stage_id == entry.level_id
        assert entry.stage.grid.width == CLASSIC_GRID_SIZE
        assert entry.stage.grid.height == CLASSIC_GRID_SIZE


def test_adapter_preserves_the_level_it_was_given() -> None:
    """Every field the simulation stage carries comes straight from the level record."""
    for level in load_bundled_pack().levels:
        stage = stage_from_level(level)
        assert stage.grid.to_rows() == level.grid.rows
        assert stage.name == level.name
        assert stage.base_cell == cell_to_grid_pos(level.base_cell)
        assert stage.enemy_spawns == tuple(cell_to_grid_pos(cell) for cell in level.enemy_spawns)
        assert tuple(spawn.slot for spawn in stage.player_spawns) == tuple(
            spawn.slot for spawn in level.player_spawns
        )
        assert tuple(spawn.cell for spawn in stage.player_spawns) == tuple(
            cell_to_grid_pos(spawn.cell) for spawn in level.player_spawns
        )


def test_adapter_is_a_pure_function_of_its_argument() -> None:
    """Called twice on one record it produces equal stages and reads nothing outside."""
    level = make_level()
    assert stage_from_level(level) == stage_from_level(level)


def test_cell_conversion_keeps_the_coordinate_system() -> None:
    assert cell_to_grid_pos(GridCell(x=3, y=11)) == GridPos(x=3, y=11)


def test_declared_waves_do_not_reach_the_stage() -> None:
    """Wave data is carried by content and deliberately dropped here.

    The simulation has no wave scheduler and this phase does not add one, so a level that
    declares waves must adapt to exactly the same stage as one that does not. If that
    ever stops being true, someone has started scheduling enemies in the client.
    """
    plain = make_level()
    with_waves = make_level(waves=(Wave(enemies=4), Wave(enemies=6)))
    assert stage_from_level(with_waves) == stage_from_level(plain)


def test_a_level_the_simulation_rejects_names_itself_and_its_file() -> None:
    """Two home tiles load as content but cannot be a stage."""
    broken = make_level(
        rows=rows_with({(7, 15): Tile.HOME, (3, 3): Tile.HOME}),
        level_id="two-bases",
        origin=Path("/fixture/two-bases.json"),
    )
    with pytest.raises(StageAdapterError) as failure:
        stage_from_level(broken)
    message = str(failure.value)
    assert "two-bases" in message
    assert "two-bases.json" in message
    assert "home base" in message


def test_a_spawn_inside_terrain_is_rejected() -> None:
    """The simulation refuses a tank that would start inside a wall; so does the client."""
    broken = make_level(rows=rows_with({(7, 15): Tile.HOME, (7, 12): Tile.BRICK}))
    with pytest.raises(StageAdapterError):
        stage_from_level(broken)


def test_catalog_adaptation_is_all_or_nothing() -> None:
    """One unplayable level fails the pack rather than silently shortening the menu."""
    good = make_level(level_id="good")
    broken = replace(
        make_level(level_id="broken"),
        grid=make_level(rows=rows_with({})).grid,
    )
    with pytest.raises(StageAdapterError) as failure:
        stage_catalog(make_pack((good, broken)))
    assert "broken" in str(failure.value)


def test_catalog_keeps_manifest_order() -> None:
    pack = make_pack((make_level(level_id="second"), make_level(level_id="first")))
    assert tuple(entry.level_id for entry in stage_catalog(pack)) == ("second", "first")


def test_entry_reports_the_slots_a_stage_declares() -> None:
    catalog = stage_catalog(make_pack((make_level(player_cells=((7, 12), (9, 12))),)))
    assert catalog[0].player_slots == (1, 2)
