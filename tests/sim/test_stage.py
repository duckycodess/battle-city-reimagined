"""Stage validation: the simulation refuses terrain it cannot run deterministically."""

from __future__ import annotations

import pytest
from battle_city_sim import (
    CLASSIC_GRID_SIZE,
    Direction,
    GridPos,
    MoveCommand,
    PlayerSpawn,
    Stage,
    StageValidationError,
    Tile,
)
from helpers import BASE_CELL, build_rows, make_stage, make_state, run


def test_valid_stage_exposes_grid_spawns_and_base() -> None:
    stage = make_stage()
    assert stage.grid.width == CLASSIC_GRID_SIZE
    assert stage.grid.height == CLASSIC_GRID_SIZE
    assert stage.base_cell == BASE_CELL
    assert stage.player_spawn_for(1).cell == GridPos(8, 10)
    assert stage.enemy_spawns == (GridPos(2, 1),)


def test_tile_codes_round_trip_through_the_grid() -> None:
    overrides = {
        GridPos(0, 0): Tile.STONE,
        GridPos(1, 0): Tile.BRICK,
        GridPos(2, 0): Tile.MIRROR_NE,
        GridPos(3, 0): Tile.MIRROR_SE,
        GridPos(4, 0): Tile.WATER,
        GridPos(5, 0): Tile.CRACKED_BRICK,
        GridPos(6, 0): Tile.FOREST,
    }
    rows = build_rows(overrides)
    stage = make_stage(overrides=overrides)
    assert stage.grid.to_rows() == rows
    assert rows[0].startswith("1234567")


def test_ragged_grid_is_rejected() -> None:
    with pytest.raises(StageValidationError, match=r"grid.rows\[1\]"):
        Stage.create(
            stage_id="bad",
            name="Bad",
            rows=["0" * 16, "0" * 15] + ["0" * 16] * 13 + ["0" * 8 + "8" + "0" * 7],
            player_spawns=[PlayerSpawn(slot=1, cell=GridPos(0, 0))],
            enemy_spawns=[GridPos(1, 0)],
        )


def test_unknown_tile_code_is_rejected() -> None:
    rows = list(build_rows())
    rows[3] = "9" + rows[3][1:]
    with pytest.raises(StageValidationError, match=r"grid.rows\[3\]\[0\]"):
        Stage.create(
            stage_id="bad",
            name="Bad",
            rows=rows,
            player_spawns=[PlayerSpawn(slot=1, cell=GridPos(0, 0))],
            enemy_spawns=[GridPos(1, 0)],
        )


def test_non_classic_dimensions_are_rejected_by_default() -> None:
    rows = ["0" * 8] * 7 + ["0" * 3 + "8" + "0" * 4]
    with pytest.raises(StageValidationError, match="classic stages are 16x16"):
        Stage.create(
            stage_id="bad",
            name="Bad",
            rows=rows,
            player_spawns=[PlayerSpawn(slot=1, cell=GridPos(0, 0))],
            enemy_spawns=[GridPos(1, 0)],
        )


def test_non_classic_dimensions_need_an_explicit_opt_out() -> None:
    rows = ["0" * 8] * 7 + ["0" * 3 + "8" + "0" * 4]
    stage = Stage.create(
        stage_id="small",
        name="Small",
        rows=rows,
        player_spawns=[PlayerSpawn(slot=1, cell=GridPos(0, 0))],
        enemy_spawns=[GridPos(1, 0)],
        require_classic_grid=False,
    )
    assert (stage.grid.width, stage.grid.height) == (8, 8)


@pytest.mark.parametrize("base_count", [0, 2])
def test_stage_needs_exactly_one_home_base(base_count: int) -> None:
    cells = [["0"] * 16 for _ in range(16)]
    for index in range(base_count):
        cells[15][index] = "8"
    with pytest.raises(StageValidationError, match="exactly one home base"):
        Stage.create(
            stage_id="bad",
            name="Bad",
            rows=["".join(row) for row in cells],
            player_spawns=[PlayerSpawn(slot=1, cell=GridPos(8, 10))],
            enemy_spawns=[GridPos(2, 1)],
        )


def test_spawn_outside_the_grid_is_rejected() -> None:
    with pytest.raises(StageValidationError, match="outside the 16x16 grid"):
        make_stage(player_cell=GridPos(16, 0))


def test_spawn_on_terrain_is_rejected() -> None:
    with pytest.raises(StageValidationError, match="must be empty ground, found BRICK"):
        make_stage(overrides={GridPos(8, 10): Tile.BRICK})


def test_duplicate_player_slots_are_rejected() -> None:
    with pytest.raises(StageValidationError, match="duplicate slot 1"):
        make_stage(
            player_cells=[
                PlayerSpawn(slot=1, cell=GridPos(4, 4)),
                PlayerSpawn(slot=1, cell=GridPos(5, 4)),
            ]
        )


def test_duplicate_enemy_spawns_are_rejected() -> None:
    with pytest.raises(StageValidationError, match="duplicate spawn at \\(2, 1\\)"):
        make_stage(enemy_cells=[GridPos(2, 1), GridPos(2, 1)])


def test_duplicate_player_spawn_cells_are_rejected() -> None:
    """Two slots on one cell would start the run with two tanks exactly coincident.

    Movement tests a target rect against the other tank's current body, so coincident
    tanks can never step apart. The stage has to be refused before the run begins.
    """
    with pytest.raises(StageValidationError, match=r"spawns.players\[slot=2\]: duplicate spawn"):
        make_stage(
            player_cells=[
                PlayerSpawn(slot=1, cell=GridPos(8, 10)),
                PlayerSpawn(slot=2, cell=GridPos(8, 10)),
            ]
        )


def test_distinct_player_spawn_cells_are_accepted() -> None:
    stage = make_stage(
        player_cells=[
            PlayerSpawn(slot=1, cell=GridPos(8, 10)),
            PlayerSpawn(slot=2, cell=GridPos(6, 10)),
        ]
    )
    assert [spawn.cell for spawn in stage.player_spawns] == [GridPos(8, 10), GridPos(6, 10)]


def test_player_and_enemy_spawns_may_not_share_a_cell() -> None:
    with pytest.raises(StageValidationError, match="share cell \\(8, 10\\)"):
        make_stage(enemy_cells=[GridPos(8, 10)])


def test_stage_requires_at_least_one_spawn_of_each_kind() -> None:
    with pytest.raises(StageValidationError, match="spawns.players"):
        make_stage(player_cells=[])
    with pytest.raises(StageValidationError, match="spawns.enemies"):
        make_stage(enemy_cells=[])


def test_two_slots_on_distinct_cells_can_actually_separate() -> None:
    """The counterpart to the duplicate-cell rejection: legal spawns are not stuck."""
    stage = make_stage(
        player_cells=[
            PlayerSpawn(slot=1, cell=GridPos(8, 10)),
            PlayerSpawn(slot=2, cell=GridPos(6, 10)),
        ]
    )
    state, _ = run(
        make_state(stage),
        4,
        commands=dict.fromkeys(
            range(4),
            [MoveCommand(1, Direction.RIGHT), MoveCommand(2, Direction.LEFT)],
        ),
    )
    assert state.tank(1).position.x == 136
    assert state.tank(2).position.x == 88
