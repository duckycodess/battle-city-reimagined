"""Per-tick work and per-bot state stay bounded, and a decision never touches the state.

The AI specification asks for bounded planning and for fairness and performance limits to
be stated. The limits are: one decision costs a fixed amount of look-ahead set by the
profile's planning horizon, plus one pass over the hostiles and one bounded trace per
live projectile. Nothing accumulates between ticks - the whole of a bot's memory is six
fields, three of which are counters that saturate at a profile bound.
"""

from __future__ import annotations

from collections.abc import Mapping

import battle_city_ai.perception as perception
import battle_city_ai.policy as policy
import pytest
from ai_helpers import PLAYER_TANK_ID, arena, bots_for, drive, game, with_enemy
from battle_city_ai import (
    PROFILE_ORDER,
    VETERAN,
    Bot,
    DifficultyProfile,
    decide,
    plan_tick,
    seed_from_state,
)
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    GridPos,
    Rules,
    SimulationState,
    Tank,
    TankVariant,
    TickInput,
    Tile,
    state_hash,
    step,
)

ENEMY_TANK_ID = 2

CLUTTER: Mapping[GridPos, Tile] = {
    GridPos(6, 6): Tile.BRICK,
    GridPos(8, 6): Tile.STONE,
    GridPos(9, 9): Tile.MIRROR_NE,
    GridPos(4, 11): Tile.FOREST,
}


def _crowd(state: SimulationState, count: int) -> SimulationState:
    """Spawn ``count`` enemies down the right-hand columns, one tick each."""
    current = state
    for index in range(count):
        cell = GridPos(13, 1 + index)
        current = with_enemy(current, cell, variant=TankVariant.ENEMY_NORMAL)
    return current


def _pose_calls(
    monkeypatch: pytest.MonkeyPatch,
    state: SimulationState,
    profile: DifficultyProfile,
) -> int:
    """Return how many movement predictions one decision costs."""
    calls = 0
    original = perception.predicted_pose

    def counting(
        inner_state: SimulationState,
        tank: Tank,
        direction: Direction | None,
        rules: Rules = DEFAULT_RULES,
    ) -> Tank:
        nonlocal calls
        calls += 1
        return original(inner_state, tank, direction, rules)

    monkeypatch.setattr(perception, "predicted_pose", counting)
    monkeypatch.setattr(policy, "predicted_pose", counting)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state))
    decide(bot, state)
    return calls


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_look_ahead_is_bounded_by_the_profiles_horizon(
    profile: DifficultyProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Four roaming candidates and two dodge candidates, each probed at most ``horizon``
    # steps, plus a handful of single-step projections. The constant is generous; the
    # point is that it is a constant times the horizon and not a search.
    state = with_enemy(game(arena(overrides=CLUTTER)), GridPos(12, 2))
    ceiling = 12 * profile.planning_horizon_ticks + 16
    assert _pose_calls(monkeypatch, state, profile) <= ceiling


def test_look_ahead_does_not_grow_with_the_number_of_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Adding tanks makes each collision test a little wider, but it must not add look-ahead
    # steps. A bot whose planning grew with the scene would stop being a fixed per-tick cost.
    sparse = with_enemy(game(arena(overrides=CLUTTER)), GridPos(12, 2))
    crowded = _crowd(sparse, 6)
    assert _pose_calls(monkeypatch, sparse, VETERAN) == _pose_calls(monkeypatch, crowded, VETERAN)


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_bot_memory_never_grows_past_its_profile_bounds(profile: DifficultyProfile) -> None:
    state = with_enemy(game(arena(overrides=CLUTTER), seed=17), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile})
    for _ in range(500):
        plan = plan_tick(bots, state)
        for bot in plan.bots:
            memory = bot.memory
            assert 0 <= memory.engaged_ticks <= profile.reaction_delay_ticks
            assert 0 <= memory.ticks_since_fire <= profile.fire_cooldown_ticks
            assert 0 <= memory.plan_ticks_left <= profile.plan_commit_ticks
        state = step(state, plan.tick_input).state
        bots = plan.bots


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_deciding_never_touches_the_state_it_was_given(profile: DifficultyProfile) -> None:
    state = with_enemy(game(arena(overrides=CLUTTER), seed=18), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile})
    for _ in range(100):
        before = state_hash(state)
        plan = plan_tick(bots, state)
        assert state_hash(state) == before
        state = step(state, plan.tick_input).state
        bots = plan.bots


def test_a_crowded_board_still_produces_only_legal_ticks() -> None:
    state = _crowd(game(arena(overrides=CLUTTER, enemy_cells=(GridPos(1, 14),)), seed=19), 6)
    assignments = {tank.entity_id: VETERAN for tank in state.tanks}
    session = drive(state, bots_for(state, assignments), 200)
    assert len(session.inputs) == 200


def test_a_bot_whose_tank_dies_goes_quiet_and_stays_quiet() -> None:
    # Bots are bound to a tank identifier, which the engine never reuses. A bot whose tank
    # is destroyed has nothing to drive; respawning is campaign policy, not a bot decision,
    # so the bot must simply stop rather than keep planning for a body that is gone.
    state = with_enemy(game(), GridPos(12, 2))
    seed = seed_from_state(state)
    hunter = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed)
    doomed = Bot.create(tank_id=ENEMY_TANK_ID, profile=VETERAN, seed=seed)

    # The doomed bot decides every tick but its commands are never submitted, so its tank
    # holds still and the duel resolves instead of running forever.
    for _ in range(200):
        decision = decide(hunter, state)
        doomed = decide(doomed, state).bot
        state = step(state, TickInput.from_iterable(state.tick, decision.commands)).state
        hunter = decision.bot
        if state.find_tank(ENEMY_TANK_ID) is None:
            break
    assert state.find_tank(ENEMY_TANK_ID) is None

    for _ in range(20):
        decision = decide(doomed, state)
        assert decision.commands == ()
        assert decision.bot.memory.engaged_ticks == 0
        assert decision.bot.memory.plan_direction is None
        assert decision.bot.memory.rng == doomed.memory.rng
        doomed = decision.bot
        state = step(state, TickInput.of(state.tick)).state
