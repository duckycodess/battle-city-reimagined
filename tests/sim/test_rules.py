"""Rules are data a session hands to the engine, not globals."""

from __future__ import annotations

import pytest
from battle_city_sim import DEFAULT_RULES, Direction, MoveCommand, Rules, Vec2
from helpers import make_state, run

PLAYER_ID = 1


def test_the_defaults_reproduce_the_historical_numbers() -> None:
    assert DEFAULT_RULES.tile_size == 16
    assert DEFAULT_RULES.tank_size == 16
    assert DEFAULT_RULES.tank_speed == 2
    assert DEFAULT_RULES.projectile_speed == 3
    assert DEFAULT_RULES.starting_lives == 3
    assert DEFAULT_RULES.powerup_duration_ticks == 300
    assert DEFAULT_RULES.gatling_interval_ticks == 5
    assert (
        DEFAULT_RULES.score_shield_break,
        DEFAULT_RULES.score_normal_kill,
        DEFAULT_RULES.score_unshielded_kill,
    ) == (50, 100, 200)


def test_rules_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        DEFAULT_RULES.tank_speed = 4  # type: ignore[misc]


def test_a_mode_may_override_a_constant() -> None:
    fast = Rules(tank_speed=4)
    state, _ = run(
        make_state(),
        1,
        commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]},
        rules=fast,
    )
    assert state.tank(PLAYER_ID).position == Vec2(128, 156)


@pytest.mark.parametrize(
    "overrides",
    [
        {"tile_size": 0},
        {"tank_speed": -1},
        {"projectile_speed": 0},
        {"gatling_interval_ticks": 0},
        {"powerup_duration_ticks": -5},
        {"starting_lives": 0},
        {"max_projectiles_per_tank": 0},
        {"projectile_radius": -1},
    ],
)
def test_nonsense_rules_are_rejected(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Rules(**overrides)


def test_a_projectile_may_not_outrun_a_tile() -> None:
    with pytest.raises(ValueError, match="smaller than rules.tile_size"):
        Rules(projectile_speed=16)
