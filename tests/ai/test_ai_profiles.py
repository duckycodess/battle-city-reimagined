"""Difficulty profiles are bounded data, and "harder" is a property of that data."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest
from battle_city_ai import (
    AIM_TOLERANCE_BOUNDS,
    FIRE_COOLDOWN_BOUNDS,
    MAX_HITTING_OFFSET_PX,
    PERCENT_BOUNDS,
    PLAN_COMMIT_BOUNDS,
    PLANNING_HORIZON_BOUNDS,
    PROFILE_ORDER,
    PROFILES,
    REACTION_DELAY_BOUNDS,
    ROOKIE,
    SOLDIER,
    VETERAN,
    DifficultyProfile,
    profile_named,
)
from battle_city_sim import DEFAULT_RULES

BOUNDS = {
    "reaction_delay_ticks": REACTION_DELAY_BOUNDS,
    "plan_commit_ticks": PLAN_COMMIT_BOUNDS,
    "planning_horizon_ticks": PLANNING_HORIZON_BOUNDS,
    "aim_tolerance_px": AIM_TOLERANCE_BOUNDS,
    "miss_chance_percent": PERCENT_BOUNDS,
    "aggression_percent": PERCENT_BOUNDS,
    "fire_cooldown_ticks": FIRE_COOLDOWN_BOUNDS,
}


def test_at_least_three_profiles_are_selectable_by_name() -> None:
    assert len(PROFILE_ORDER) >= 3
    assert PROFILE_ORDER == (ROOKIE, SOLDIER, VETERAN)
    assert sorted(PROFILES) == sorted(profile.name for profile in PROFILE_ORDER)
    for profile in PROFILE_ORDER:
        assert profile_named(profile.name) is profile


def test_an_unknown_profile_name_names_the_known_profiles() -> None:
    with pytest.raises(KeyError) as error:
        profile_named("impossible")
    message = str(error.value)
    for profile in PROFILE_ORDER:
        assert profile.name in message


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_every_built_in_profile_sits_inside_its_published_bounds(
    profile: DifficultyProfile,
) -> None:
    for field, (low, high) in BOUNDS.items():
        value = getattr(profile, field)
        assert low <= value <= high, f"{profile.name}.{field} = {value} escapes [{low}, {high}]"


def _soldier_with(field: str, value: int) -> DifficultyProfile:
    """Rebuild the reference profile with one field overridden."""
    arguments = {item.name: getattr(SOLDIER, item.name) for item in fields(SOLDIER)}
    arguments[field] = value
    return DifficultyProfile(**arguments)


@pytest.mark.parametrize("field", sorted(BOUNDS))
def test_a_value_outside_its_bound_is_refused_at_construction(field: str) -> None:
    low, high = BOUNDS[field]
    for offending in (low - 1, high + 1):
        with pytest.raises(ValueError, match=field):
            _soldier_with(field, offending)


def test_a_profile_must_be_named() -> None:
    with pytest.raises(ValueError, match="name"):
        replace(SOLDIER, name="")


def test_the_built_in_order_is_monotonically_harder() -> None:
    falling = (
        "reaction_delay_ticks",
        "plan_commit_ticks",
        "miss_chance_percent",
        "fire_cooldown_ticks",
    )
    rising = ("planning_horizon_ticks", "aggression_percent")
    for easier, harder in zip(PROFILE_ORDER, PROFILE_ORDER[1:], strict=False):
        for field in falling:
            assert getattr(harder, field) < getattr(easier, field), field
        for field in rising:
            assert getattr(harder, field) > getattr(easier, field), field
        assert harder.aim_tolerance_px <= easier.aim_tolerance_px


def test_the_aim_tolerance_cap_is_derived_from_the_simulation_geometry() -> None:
    # A shot leaves the muzzle offset from the body centre line and inflates by its
    # radius, so this is the largest misalignment that can still overlap a target body.
    # Guessing a different number here would let a profile aim at geometry it cannot hit.
    reach = DEFAULT_RULES.muzzle_offset + DEFAULT_RULES.projectile_radius
    assert reach == MAX_HITTING_OFFSET_PX
    assert AIM_TOLERANCE_BOUNDS == (0, MAX_HITTING_OFFSET_PX)
