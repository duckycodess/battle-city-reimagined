"""Input validation: an illegal tick is rejected whole, never applied in part."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from battle_city_sim import (
    Command,
    DespawnPowerupCommand,
    Direction,
    FireCommand,
    GridPos,
    InvalidInputError,
    MoveCommand,
    PowerupKind,
    RespawnCommand,
    SimulationState,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
    TickInput,
    Tile,
    state_hash,
    step,
)
from helpers import make_stage, make_state, run

PLAYER_ID = 1


def _reject(state: SimulationState, commands: Sequence[Command], match: str) -> None:
    before = state_hash(state)
    with pytest.raises(InvalidInputError, match=match):
        step(state, TickInput(tick=state.tick, commands=tuple(commands)))
    assert state_hash(state) == before, "a rejected tick must not touch the state"


def test_a_tick_input_for_the_wrong_tick_is_rejected() -> None:
    state = make_state()
    with pytest.raises(InvalidInputError, match="tick mismatch"):
        step(state, TickInput.of(7))
    assert state.tick == 0


def test_a_command_for_an_unknown_tank_is_rejected() -> None:
    state = make_state()
    _reject(state, [MoveCommand(99, Direction.UP)], "unknown tank 99")
    _reject(state, [FireCommand(99)], "unknown tank 99")


def test_two_move_commands_for_one_tank_are_rejected() -> None:
    state = make_state()
    _reject(
        state,
        [MoveCommand(PLAYER_ID, Direction.UP), MoveCommand(PLAYER_ID, Direction.DOWN)],
        "duplicate move command",
    )


def test_two_fire_commands_for_one_tank_are_rejected() -> None:
    state = make_state()
    _reject(state, [FireCommand(PLAYER_ID), FireCommand(PLAYER_ID)], "duplicate fire command")


def test_respawning_a_live_player_is_rejected() -> None:
    state = make_state()
    _reject(state, [RespawnCommand(1)], "not waiting to respawn")


def test_respawning_an_unknown_slot_is_rejected() -> None:
    state = make_state()
    _reject(state, [RespawnCommand(4)], "unknown player slot 4")


def test_spawning_a_player_variant_as_an_enemy_is_rejected() -> None:
    state = make_state()
    _reject(
        state,
        [SpawnEnemyCommand(GridPos(4, 4), TankVariant.PLAYER)],
        "not an enemy variant",
    )


def test_spawning_outside_the_grid_is_rejected() -> None:
    state = make_state()
    _reject(
        state,
        [SpawnEnemyCommand(GridPos(16, 4), TankVariant.ENEMY_NORMAL)],
        "outside the 16x16 grid",
    )


def test_spawning_into_terrain_is_rejected() -> None:
    state = make_state(make_stage(overrides={GridPos(4, 4): Tile.BRICK}))
    _reject(
        state,
        [SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_NORMAL)],
        "blocked by BRICK",
    )


def test_spawning_onto_a_tank_is_rejected() -> None:
    state = make_state()
    _reject(
        state,
        [SpawnEnemyCommand(GridPos(8, 10), TankVariant.ENEMY_NORMAL)],
        "overlaps a tank",
    )


def test_two_enemies_may_not_share_a_spawn_cell_in_one_tick() -> None:
    state = make_state()
    _reject(
        state,
        [
            SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_NORMAL),
            SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_SHIELDED),
        ],
        "overlaps a tank",
    )


def test_two_powerups_may_not_share_a_cell() -> None:
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(4, 4), PowerupKind.GATLING)]},
    )
    _reject(
        state,
        [SpawnPowerupCommand(GridPos(4, 4), PowerupKind.EXTRA_LIFE)],
        "a powerup already occupies",
    )


def test_despawning_an_unknown_powerup_is_rejected() -> None:
    state = make_state()
    _reject(state, [DespawnPowerupCommand(42)], "unknown powerup 42")


def test_a_rejected_tick_leaves_a_populated_state_untouched() -> None:
    state, _ = run(
        make_state(),
        4,
        commands={
            0: [SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_SHIELDED)],
            1: [FireCommand(PLAYER_ID)],
            2: [SpawnPowerupCommand(GridPos(6, 6), PowerupKind.GATLING)],
        },
    )
    _reject(
        state,
        [MoveCommand(PLAYER_ID, Direction.UP), MoveCommand(77, Direction.DOWN)],
        "unknown tank 77",
    )
