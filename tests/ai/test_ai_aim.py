"""The aim model: a tolerance the engine's geometry justifies, and a declared miss rate.

Aim error is modelled as two separate, bounded knobs rather than as a wrong facing. A
bot either accepts a sloppier alignment (``aim_tolerance_px``) or declines a shot it
could have taken (``miss_chance_percent``). Both leave every emitted command one a human
player could also have emitted, which is what keeps "legal inputs" literally true.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from ai_helpers import PLAYER_TANK_ID, game, with_enemy
from battle_city_ai import (
    MAX_HITTING_OFFSET_PX,
    PROFILE_ORDER,
    Bot,
    DifficultyProfile,
    TargetPreference,
    aim_candidates,
    decide,
    muzzle_of,
    predicted_pose,
    shot_target_id,
)
from battle_city_sim import (
    DEFAULT_RULES,
    Command,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    ProjectileFired,
    SimulationState,
    TankDestroyed,
    TickInput,
    step,
)

ENEMY_TANK_ID = 2


def _advance(state: SimulationState, commands: Sequence[Command]) -> SimulationState:
    return step(state, TickInput.from_iterable(state.tick, commands)).state


def _offset_arena(offset: int) -> SimulationState:
    """Return a duel where ``shooter.y - target.y == offset``.

    Both tanks start tile-aligned and move in ``rules.tank_speed`` steps, so every
    reachable offset is even; the odd edge of the hit window is unreachable in practice
    and the published cap is the symmetric, even-safe one.
    """
    assert offset % DEFAULT_RULES.tank_speed == 0
    state = with_enemy(game(), GridPos(12, 2))
    mover = PLAYER_TANK_ID if offset > 0 else ENEMY_TANK_ID
    for _ in range(abs(offset) // DEFAULT_RULES.tank_speed):
        state = _advance(state, [MoveCommand(tank_id=mover, direction=Direction.DOWN)])
    return state


def _shot_connects(offset: int) -> bool:
    """Fire for real at the given misalignment and report whether the target dies."""
    state = _offset_arena(offset)
    state = _advance(
        state,
        [
            MoveCommand(tank_id=PLAYER_TANK_ID, direction=Direction.RIGHT),
            FireCommand(tank_id=PLAYER_TANK_ID),
        ],
    )
    for _ in range(80):
        result = step(state, TickInput.of(state.tick))
        state = result.state
        if any(isinstance(event, TankDestroyed) for event in result.events):
            return True
        if not state.projectiles:
            return False
    return False


@pytest.mark.parametrize("offset", [-8, -6, -2, 0, 2, 6, 8])
def test_every_offset_inside_the_published_cap_really_connects(offset: int) -> None:
    assert abs(offset) <= MAX_HITTING_OFFSET_PX
    assert _shot_connects(offset)


@pytest.mark.parametrize("offset", [-12, -10, 10, 12])
def test_every_offset_outside_the_published_cap_really_misses(offset: int) -> None:
    assert abs(offset) > MAX_HITTING_OFFSET_PX
    assert not _shot_connects(offset)


@pytest.mark.parametrize("offset", [-12, -10, -8, 0, 8, 10, 12])
def test_the_tolerance_agrees_with_the_line_check_at_every_offset(offset: int) -> None:
    # Alignment and the line check are separate questions, and both have to agree with
    # what the engine then does. A tolerance that claimed an unhittable offset would send
    # bots into a loop of shots that cannot land.
    state = _offset_arena(offset)
    shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
    target = state.tank(ENEMY_TANK_ID)
    aligned = Direction.RIGHT in aim_candidates(shooter, target, MAX_HITTING_OFFSET_PX)
    reached = (
        shot_target_id(
            state,
            origin=muzzle_of(shooter),
            direction=Direction.RIGHT,
            faction=shooter.faction,
        )
        == ENEMY_TANK_ID
    )
    assert aligned == (abs(offset) <= MAX_HITTING_OFFSET_PX)
    assert reached == aligned


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_no_profile_can_aim_at_geometry_it_cannot_hit(profile: DifficultyProfile) -> None:
    for offset in (-12, -10, 10, 12):
        state = _offset_arena(offset)
        shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
        target = state.tank(ENEMY_TANK_ID)
        assert aim_candidates(shooter, target, profile.aim_tolerance_px) == ()


def _marksman(miss_chance_percent: int) -> DifficultyProfile:
    """A profile that shoots the instant it is lined up, so only the miss roll varies."""
    return DifficultyProfile(
        name=f"marksman-{miss_chance_percent}",
        reaction_delay_ticks=0,
        plan_commit_ticks=1,
        planning_horizon_ticks=4,
        aim_tolerance_px=0,
        miss_chance_percent=miss_chance_percent,
        aggression_percent=0,
        fire_cooldown_ticks=1,
        target_preference=TargetPreference.NEAREST,
    )


SEED_STRIDE = 0x9E3779B97F4A7C15
"""Golden-ratio stride used to spread a sample of session seeds across the 64-bit space.

Sampling ``range(n)`` would measure one thin corner of the seed space, and a corner is
exactly where a sample's noise is easiest to mistake for a bias in the knob under test.
Striding is only how this test picks its sample; derivation itself takes any integer.
"""


def _sample_seeds(population: int) -> tuple[int, ...]:
    return tuple((index * SEED_STRIDE) % (1 << 64) for index in range(population))


def _fire_rate(miss_chance_percent: int, population: int = 600) -> float:
    """Fraction of a seeded population that takes the first shot it is offered."""
    state = with_enemy(game(), GridPos(12, 2))
    profile = _marksman(miss_chance_percent)
    taken = sum(
        1
        for seed in _sample_seeds(population)
        if any(
            isinstance(command, FireCommand)
            for command in decide(
                Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed), state
            ).commands
        )
    )
    return taken / population


def test_a_perfect_profile_never_declines_a_clear_shot() -> None:
    assert _fire_rate(0) == 1.0


def test_a_hopeless_profile_never_takes_one() -> None:
    assert _fire_rate(100) == 0.0


@pytest.mark.parametrize("miss_chance_percent", [25, 50, 75])
def test_the_realised_miss_rate_matches_the_declared_one(miss_chance_percent: int) -> None:
    # The knob is a promise to whoever tunes difficulty: a profile that says it declines a
    # quarter of its shots has to decline about a quarter of them over a population.
    expected = (100 - miss_chance_percent) / 100
    assert abs(_fire_rate(miss_chance_percent) - expected) < 0.06


PATIENT_COOLDOWN = 20
"""Fire cooldown for the cadence sample, long enough that a spent opportunity is visible.

A declined shot costs one cooldown, so with a miss chance of ``m`` a bot waits
``PATIENT_COOLDOWN * m / (100 - m)`` ticks on average before its first shot leaves: zero
at ``m = 0``, one cooldown at ``m = 50``, three at ``m = 75``.
"""


def _patient_marksman(miss_chance_percent: int) -> DifficultyProfile:
    """A marksman whose only delay is the miss roll and the cooldown each miss costs."""
    return DifficultyProfile(
        name=f"patient-{miss_chance_percent}",
        reaction_delay_ticks=0,
        plan_commit_ticks=1,
        planning_horizon_ticks=4,
        aim_tolerance_px=0,
        miss_chance_percent=miss_chance_percent,
        aggression_percent=0,
        fire_cooldown_ticks=PATIENT_COOLDOWN,
        target_preference=TargetPreference.NEAREST,
    )


def _ticks_to_first_shot(profile: DifficultyProfile, seed: int, cap: int) -> int | None:
    """Return how many ticks one seeded bot takes to put a projectile on the board."""
    state = with_enemy(game(), GridPos(12, 2))
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed)
    start = state.tick
    for _ in range(cap):
        decision = decide(bot, state)
        result = step(state, TickInput.from_iterable(state.tick, decision.commands))
        if any(isinstance(event, ProjectileFired) for event in result.events):
            return state.tick - start
        state = result.state
        bot = decision.bot
    return None


def _mean_ticks_to_first_shot(
    miss_chance_percent: int,
    population: int = 150,
    cap: int = 1500,
) -> float:
    """Mean first-shot latency over a seeded population, in ticks."""
    profile = _patient_marksman(miss_chance_percent)
    latencies = [_ticks_to_first_shot(profile, seed, cap) for seed in _sample_seeds(population)]
    assert all(value is not None for value in latencies), "a bot never took its shot"
    return sum(value for value in latencies if value is not None) / population


def test_a_declined_shot_costs_a_cooldown_and_not_merely_a_tick() -> None:
    # The multi-tick counterpart to the single-tick rate tests above, and the one that
    # makes ``miss_chance_percent`` a cadence dial rather than a rounding error. A bot
    # that re-rolled on the very next tick would reach its first shot in a tick or two at
    # every setting here, so every bound below would fail by an order of magnitude.
    never = _mean_ticks_to_first_shot(0)
    even = _mean_ticks_to_first_shot(50)
    steep = _mean_ticks_to_first_shot(75)

    assert never == 0.0
    assert never < even < steep

    # One spent cooldown on average at 50 percent, three at 75: a real difference in how
    # often the bot shoots, not a delay before it shoots anyway.
    assert PATIENT_COOLDOWN * 0.6 < even < PATIENT_COOLDOWN * 1.5
    assert PATIENT_COOLDOWN * 1.8 < steep < PATIENT_COOLDOWN * 4.5
