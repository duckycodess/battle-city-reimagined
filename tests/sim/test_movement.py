"""Tank movement: speed, facing, terrain blocking, tank blocking and world bounds."""

from __future__ import annotations

import pytest
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    GridPos,
    MoveCommand,
    SpawnEnemyCommand,
    TankMoveBlocked,
    TankMoved,
    TankVariant,
    Tile,
    Vec2,
)
from helpers import events_of, make_stage, make_state, only, run

PLAYER_ID = 1


def test_player_starts_cell_aligned_and_facing_up() -> None:
    state = make_state()
    tank = state.tank(PLAYER_ID)
    assert tank.position == Vec2(128, 160)
    assert tank.facing is Direction.UP
    assert tank.variant is TankVariant.PLAYER
    assert tank.player_slot == 1


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (Direction.UP, Vec2(128, 158)),
        (Direction.DOWN, Vec2(128, 162)),
        (Direction.LEFT, Vec2(126, 160)),
        (Direction.RIGHT, Vec2(130, 160)),
    ],
)
def test_a_move_advances_exactly_tank_speed(direction: Direction, expected: Vec2) -> None:
    state, events = run(make_state(), 1, commands={0: [MoveCommand(PLAYER_ID, direction)]})
    assert state.tank(PLAYER_ID).position == expected
    assert state.tank(PLAYER_ID).facing is direction
    moved = only(events, TankMoved)
    assert moved.origin == Vec2(128, 160)
    assert moved.position == expected


def test_a_tank_without_a_command_does_not_drift() -> None:
    state, events = run(make_state(), 10)
    assert state.tank(PLAYER_ID).position == Vec2(128, 160)
    assert events == ()


@pytest.mark.parametrize(
    "tile", [Tile.STONE, Tile.BRICK, Tile.CRACKED_BRICK, Tile.WATER, Tile.MIRROR_NE, Tile.HOME]
)
def test_solid_terrain_blocks_a_tank(tile: Tile) -> None:
    base = GridPos(8, 9) if tile is Tile.HOME else GridPos(8, 15)
    stage = make_stage(overrides={GridPos(8, 9): tile}, base=base)
    state, events = run(make_state(stage), 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert state.tank(PLAYER_ID).position == Vec2(128, 160)
    blocked = only(events, TankMoveBlocked)
    assert blocked.facing is Direction.UP


def test_a_blocked_tank_still_turns() -> None:
    stage = make_stage(overrides={GridPos(8, 9): Tile.STONE})
    state, _ = run(make_state(stage), 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert state.tank(PLAYER_ID).facing is Direction.UP
    assert state.tank(PLAYER_ID).position == Vec2(128, 160)


def test_forest_is_an_overlay_and_does_not_block() -> None:
    stage = make_stage(overrides={GridPos(8, 9): Tile.FOREST})
    state, events = run(make_state(stage), 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert state.tank(PLAYER_ID).position == Vec2(128, 158)
    assert events_of(events, TankMoveBlocked) == ()


def test_the_world_edge_stops_a_tank() -> None:
    stage = make_stage(player_cell=GridPos(0, 0))
    state, events = run(
        make_state(stage), 1, commands={0: [MoveCommand(PLAYER_ID, Direction.LEFT)]}
    )
    assert state.tank(PLAYER_ID).position == Vec2(0, 0)
    assert only(events, TankMoveBlocked).position == Vec2(0, 0)


def test_the_far_world_edge_stops_a_tank() -> None:
    stage = make_stage(player_cell=GridPos(15, 0))
    size = DEFAULT_RULES.tile_size * 16 - DEFAULT_RULES.tank_size
    state, _ = run(
        make_state(stage),
        4,
        commands=dict.fromkeys(range(4), [MoveCommand(PLAYER_ID, Direction.RIGHT)]),
    )
    assert state.tank(PLAYER_ID).position == Vec2(size, 0)


def test_tanks_do_not_overlap_each_other() -> None:
    state, _ = run(
        make_state(),
        1,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 9), TankVariant.ENEMY_NORMAL)],
        },
    )
    after, events = run(state, 1, commands={1: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert after.tank(PLAYER_ID).position == Vec2(128, 160)
    assert only(events, TankMoveBlocked).tank_id == PLAYER_ID


def test_movement_resolves_in_ascending_identifier_order() -> None:
    """The lower identifier moves first, so it sees the higher one's pre-move body.

    This is the ordering guarantee, not an accident: both tanks want the same tick, and
    the result must not depend on how the command list happened to be built.
    """
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnEnemyCommand(GridPos(8, 9), TankVariant.ENEMY_NORMAL)]},
    )
    enemy_id = 2
    assert state.tank(enemy_id).position == Vec2(128, 144)
    after, events = run(
        state,
        1,
        commands={
            1: [
                MoveCommand(enemy_id, Direction.UP),
                MoveCommand(PLAYER_ID, Direction.UP),
            ]
        },
    )
    assert after.tank(PLAYER_ID).position == Vec2(128, 160)
    assert after.tank(enemy_id).position == Vec2(128, 142)
    assert only(events, TankMoveBlocked).tank_id == PLAYER_ID
    assert only(events, TankMoved).tank_id == enemy_id
