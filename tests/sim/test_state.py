"""The state value: construction, accessors and immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    Faction,
    FireCommand,
    GridPos,
    PlayerSpawn,
    PowerupKind,
    Rng,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
    TickInput,
    Vec2,
    new_game,
    state_hash,
    step,
)
from helpers import make_stage, make_state, run

PLAYER_ID = 1


def test_a_new_game_starts_at_tick_zero_with_only_players() -> None:
    state = make_state(seed=12)
    assert state.tick == 0
    assert state.outcome is None
    assert not state.finished
    assert state.projectiles == ()
    assert state.powerups == ()
    assert len(state.tanks) == 1
    assert state.rng == Rng.from_seed(12)
    assert state.base.cell == GridPos(8, 15)
    assert not state.base.destroyed
    assert state.player(1).lives == DEFAULT_RULES.starting_lives


def test_a_new_game_may_seat_a_subset_of_slots() -> None:
    stage = make_stage(
        player_cells=[
            PlayerSpawn(slot=1, cell=GridPos(4, 4)),
            PlayerSpawn(slot=2, cell=GridPos(6, 4)),
        ]
    )
    both = new_game(stage, seed=1)
    assert [player.slot for player in both.players] == [1, 2]

    solo = new_game(stage, seed=1, player_slots=(2,))
    assert [player.slot for player in solo.players] == [2]
    assert len(solo.tanks) == 1
    assert solo.tanks[0].player_slot == 2


def test_a_new_game_needs_at_least_one_slot() -> None:
    with pytest.raises(ValueError, match="at least one player slot"):
        new_game(make_stage(), seed=1, player_slots=())


def test_the_world_is_the_declared_grid_in_pixels() -> None:
    assert make_state().world_size() == (256, 256)


def test_lookups_find_entities_and_report_unknown_ones() -> None:
    state, _ = run(
        make_state(),
        2,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(4, 4), TankVariant.ENEMY_SHIELDED),
                SpawnPowerupCommand(GridPos(2, 2), PowerupKind.GATLING),
            ],
            1: [FireCommand(PLAYER_ID)],
        },
    )
    assert state.tank(2).variant is TankVariant.ENEMY_SHIELDED
    assert state.find_tank(999) is None
    assert state.find_player(9) is None
    assert state.powerup(3).kind is PowerupKind.GATLING
    assert state.projectile(4).owner_id == PLAYER_ID
    assert state.projectiles_owned_by(PLAYER_ID) == state.projectiles
    assert [tank.entity_id for tank in state.tanks_of(Faction.ENEMY)] == [2]
    assert [tank.entity_id for tank in state.tanks_of(Faction.PLAYER)] == [PLAYER_ID]

    for lookup, key in ((state.tank, 999), (state.projectile, 999), (state.powerup, 999)):
        with pytest.raises(KeyError):
            lookup(key)
    with pytest.raises(KeyError):
        state.player(9)


def test_the_state_is_frozen() -> None:
    state = make_state()
    with pytest.raises(FrozenInstanceError):
        state.tick = 5  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.tanks[0].position = Vec2(0, 0)  # type: ignore[misc]


def test_a_step_result_exposes_the_canonical_hash() -> None:
    result = step(make_state(), TickInput.of(0, FireCommand(PLAYER_ID)))
    assert result.hash == state_hash(result.state)
    assert result.state.tick == 1


def test_a_stage_reports_an_unknown_player_slot() -> None:
    with pytest.raises(KeyError, match="unknown player slot"):
        make_stage().player_spawn_for(3)


def test_the_initial_facing_is_fixed_rather_than_random() -> None:
    facings = {new_game(make_stage(), seed=seed).tanks[0].facing for seed in range(8)}
    assert facings == {Direction.UP}
