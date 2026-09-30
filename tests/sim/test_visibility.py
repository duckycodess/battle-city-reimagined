"""The explicit forest concealment rule."""

from __future__ import annotations

from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    GridPos,
    MoveCommand,
    Tile,
    concealed_tank_ids,
    is_tank_concealed,
)
from helpers import make_stage, make_state, run

PLAYER_ID = 1


def test_a_tank_on_open_ground_is_visible() -> None:
    state = make_state()
    assert not is_tank_concealed(state.grid, state.tank(PLAYER_ID), DEFAULT_RULES)
    assert concealed_tank_ids(state) == ()


def test_a_tank_fully_inside_forest_is_concealed() -> None:
    state = make_state(make_stage(overrides={GridPos(8, 9): Tile.FOREST}))
    state, _ = run(
        state, 8, commands=dict.fromkeys(range(8), [MoveCommand(PLAYER_ID, Direction.UP)])
    )
    assert state.tank(PLAYER_ID).position.y == 144
    assert is_tank_concealed(state.grid, state.tank(PLAYER_ID), DEFAULT_RULES)
    assert concealed_tank_ids(state) == (PLAYER_ID,)


def test_a_tank_straddling_a_forest_edge_is_visible() -> None:
    state = make_state(make_stage(overrides={GridPos(8, 9): Tile.FOREST}))
    state, _ = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert state.tank(PLAYER_ID).position.y == 158
    assert concealed_tank_ids(state) == ()


def test_a_tank_moving_between_two_forest_cells_stays_concealed() -> None:
    stage = make_stage(overrides={GridPos(8, 9): Tile.FOREST, GridPos(8, 8): Tile.FOREST})
    state, _ = run(
        make_state(stage),
        9,
        commands=dict.fromkeys(range(9), [MoveCommand(PLAYER_ID, Direction.UP)]),
    )
    assert state.tank(PLAYER_ID).position.y == 142
    assert concealed_tank_ids(state) == (PLAYER_ID,)


def test_concealment_does_not_change_the_simulation() -> None:
    """Concealment is derived, so it is absent from the canonical state."""
    from battle_city_sim import state_hash

    open_ground = make_state()
    forested = make_state(make_stage(overrides={GridPos(12, 12): Tile.FOREST}))
    assert state_hash(open_ground) != state_hash(forested)
    assert concealed_tank_ids(forested) == ()
