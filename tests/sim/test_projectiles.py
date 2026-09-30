"""Projectile motion and terrain interaction.

These are the rules the historical runtime got wrong by accident, so each one is pinned
here: one advance per tick, one reflection per mirror entry, and a single tile lookup
rule for every owner.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    Faction,
    FireCommand,
    GridPos,
    Projectile,
    ProjectileEnded,
    ProjectileEndReason,
    ProjectileFired,
    ProjectileReflected,
    SpawnEnemyCommand,
    TankVariant,
    Tile,
    TileDamaged,
    Vec2,
)
from helpers import events_of, make_stage, make_state, only, run

PLAYER_ID = 1


def test_a_shot_leaves_the_muzzle_and_advances_once_per_tick() -> None:
    state, events = run(make_state(), 1, commands={0: [FireCommand(PLAYER_ID)]})
    fired = only(events, ProjectileFired)
    assert fired.position == Vec2(135, 163)
    assert fired.direction is Direction.UP
    assert state.projectiles[0].position == Vec2(135, 160)

    positions = [state.projectiles[0].position.y]
    for _ in range(4):
        state, _ = run(state, 1)
        positions.append(state.projectiles[0].position.y)
    assert positions == [160, 157, 154, 151, 148]
    deltas = {a - b for a, b in zip(positions, positions[1:], strict=False)}
    assert deltas == {DEFAULT_RULES.projectile_speed}


@pytest.mark.parametrize(
    ("facing", "expected"),
    [
        (Direction.UP, Vec2(135, 163)),
        (Direction.DOWN, Vec2(135, 173)),
        (Direction.LEFT, Vec2(131, 167)),
        (Direction.RIGHT, Vec2(141, 167)),
    ],
)
def test_muzzle_offsets_are_symmetric(facing: Direction, expected: Vec2) -> None:
    from battle_city_sim import MoveCommand

    stage = make_stage(
        overrides={
            GridPos(8, 9): Tile.STONE,
            GridPos(8, 11): Tile.STONE,
            GridPos(7, 10): Tile.STONE,
            GridPos(9, 10): Tile.STONE,
        }
    )
    _, events = run(
        make_state(stage),
        1,
        commands={0: [MoveCommand(PLAYER_ID, facing), FireCommand(PLAYER_ID)]},
    )
    assert only(events, ProjectileFired).position == expected


def test_a_shot_leaving_the_playfield_is_removed() -> None:
    stage = make_stage(player_cell=GridPos(8, 1))
    state, events = run(make_state(stage), 12, commands={0: [FireCommand(PLAYER_ID)]})
    ended = only(events, ProjectileEnded)
    assert ended.reason is ProjectileEndReason.OUT_OF_BOUNDS
    assert state.projectiles == ()


def test_stone_stops_a_shot_without_changing_the_tile() -> None:
    stage = make_stage(overrides={GridPos(8, 8): Tile.STONE})
    state, events = run(make_state(stage), 10, commands={0: [FireCommand(PLAYER_ID)]})
    assert only(events, ProjectileEnded).reason is ProjectileEndReason.TILE_BLOCKED
    assert events_of(events, TileDamaged) == ()
    assert state.grid.at(GridPos(8, 8)) is Tile.STONE
    assert state.projectiles == ()


def test_two_hits_take_brick_through_cracked_brick_to_empty() -> None:
    stage = make_stage(overrides={GridPos(8, 8): Tile.BRICK})
    state, events = run(
        make_state(stage),
        20,
        commands={0: [FireCommand(PLAYER_ID)], 10: [FireCommand(PLAYER_ID)]},
    )
    damage = events_of(events, TileDamaged)
    assert [(item.previous, item.current) for item in damage] == [
        (Tile.BRICK, Tile.CRACKED_BRICK),
        (Tile.CRACKED_BRICK, Tile.EMPTY),
    ]
    assert all(item.cell == GridPos(8, 8) for item in damage)
    assert state.grid.at(GridPos(8, 8)) is Tile.EMPTY
    ends = events_of(events, ProjectileEnded)
    assert {item.reason for item in ends} == {ProjectileEndReason.TILE_DESTROYED}


def test_water_lets_a_shot_pass() -> None:
    stage = make_stage(overrides={GridPos(8, 8): Tile.WATER})
    state, events = run(make_state(stage), 6, commands={0: [FireCommand(PLAYER_ID)]})
    assert events_of(events, ProjectileEnded) == ()
    assert events_of(events, TileDamaged) == ()
    assert len(state.projectiles) == 1
    assert state.grid.at(GridPos(8, 8)) is Tile.WATER


def test_forest_lets_a_shot_pass() -> None:
    stage = make_stage(overrides={GridPos(8, 8): Tile.FOREST})
    state, events = run(make_state(stage), 6, commands={0: [FireCommand(PLAYER_ID)]})
    assert events_of(events, ProjectileEnded) == ()
    assert len(state.projectiles) == 1


@pytest.mark.parametrize(
    ("tile", "outgoing"),
    [(Tile.MIRROR_NE, Direction.LEFT), (Tile.MIRROR_SE, Direction.RIGHT)],
)
def test_a_mirror_deflects_a_shot_once_per_entry(tile: Tile, outgoing: Direction) -> None:
    stage = make_stage(overrides={GridPos(8, 9): tile})
    state, events = run(make_state(stage), 6, commands={0: [FireCommand(PLAYER_ID)]})
    reflection = only(events, ProjectileReflected)
    assert reflection.cell == GridPos(8, 9)
    assert reflection.tile is tile
    assert reflection.incoming is Direction.UP
    assert reflection.outgoing is outgoing
    assert state.projectiles[0].direction is outgoing
    assert state.projectiles[0].position.y == 157


def test_a_shot_re_entering_a_mirror_cell_is_deflected_again() -> None:
    """Leaving the mirror cell clears the marker, so a later entry deflects."""
    stage = make_stage(overrides={GridPos(4, 4): Tile.MIRROR_NE})
    state = make_state(stage)
    approaching = Projectile(
        entity_id=99,
        owner_id=PLAYER_ID,
        faction=Faction.PLAYER,
        position=Vec2(61, 70),
        direction=Direction.RIGHT,
        reflected_cell=GridPos(3, 4),
    )
    state = replace(state, projectiles=(approaching,), next_entity_id=100)
    after, events = run(state, 1)
    assert only(events, ProjectileReflected).outgoing is Direction.DOWN
    assert after.projectiles[0].reflected_cell == GridPos(4, 4)


def test_a_shot_still_inside_its_mirror_cell_is_not_deflected_again() -> None:
    stage = make_stage(overrides={GridPos(4, 4): Tile.MIRROR_NE})
    state = make_state(stage)
    inside = Projectile(
        entity_id=99,
        owner_id=PLAYER_ID,
        faction=Faction.PLAYER,
        position=Vec2(64, 70),
        direction=Direction.RIGHT,
        reflected_cell=GridPos(4, 4),
    )
    state = replace(state, projectiles=(inside,), next_entity_id=100)
    after, events = run(state, 1)
    assert events_of(events, ProjectileReflected) == ()
    assert after.projectiles[0].direction is Direction.RIGHT


def test_opposing_shots_destroy_each_other() -> None:
    state, events = run(
        make_state(),
        20,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 6), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(PLAYER_ID), FireCommand(2)],
        },
    )
    collisions = [
        item
        for item in events_of(events, ProjectileEnded)
        if item.reason is ProjectileEndReason.HIT_PROJECTILE
    ]
    assert len(collisions) == 2
    assert {item.projectile_id for item in collisions} == {3, 4}
    assert state.projectiles == ()


def test_a_shot_passes_through_a_tank_of_its_own_faction() -> None:
    state, events = run(
        make_state(),
        14,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(8, 4), TankVariant.ENEMY_NORMAL, Direction.DOWN),
                SpawnEnemyCommand(GridPos(8, 6), TankVariant.ENEMY_NORMAL, Direction.DOWN),
            ],
            1: [FireCommand(2)],
        },
    )
    assert len(state.tanks) == 3
    assert events_of(events, ProjectileEnded) == ()
    assert len(state.projectiles) == 1
