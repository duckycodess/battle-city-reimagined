"""Same profile, seed, state and tick history: same inputs, every time, everywhere.

This is the AI specification's central promise, and it is what lets a server replay a bot
run and a client predict one. The tests here assert it at three levels: the single
decision, the whole recorded input stream, and the canonical state hash that stream
produces.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import pytest
from ai_helpers import PLAYER_TANK_ID, arena, bots_for, drive, game, with_enemy
from battle_city_ai import (
    PROFILE_ORDER,
    ROOKIE,
    SOLDIER,
    VETERAN,
    Bot,
    DifficultyProfile,
    decide,
    plan_tick,
    seed_from_state,
)
from battle_city_sim import GridPos, SimulationState, TickInput, Tile, run_ticks, state_hash

ENEMY_TANK_ID = 2

TERRAIN = {
    GridPos(6, 6): Tile.BRICK,
    GridPos(7, 6): Tile.STONE,
    GridPos(9, 9): Tile.MIRROR_NE,
    GridPos(4, 11): Tile.FOREST,
    GridPos(5, 11): Tile.WATER,
}


def duel_state(seed: int) -> SimulationState:
    return with_enemy(game(arena(overrides=TERRAIN), seed=seed), GridPos(12, 2))


def duel_inputs(
    seed: int,
    profiles: tuple[DifficultyProfile, DifficultyProfile],
    ticks: int = 300,
) -> tuple[tuple[TickInput, ...], str]:
    state = duel_state(seed)
    first, second = profiles
    session = drive(state, bots_for(state, {PLAYER_TANK_ID: first, ENEMY_TANK_ID: second}), ticks)
    return session.inputs, state_hash(session.state)


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_one_decision_is_a_pure_function_of_bot_and_state(
    profile: DifficultyProfile,
) -> None:
    state = duel_state(seed=21)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state))
    first = decide(bot, state)
    second = decide(bot, state)
    assert first == second
    assert (
        bot.memory
        == Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state)).memory
    )


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_the_same_seed_and_profile_replay_command_for_command(
    profile: DifficultyProfile,
) -> None:
    first_inputs, first_hash = duel_inputs(31, (profile, profile))
    second_inputs, second_hash = duel_inputs(31, (profile, profile))
    assert first_inputs == second_inputs
    assert first_hash == second_hash


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_a_recorded_bot_run_replays_through_the_engine_alone(
    profile: DifficultyProfile,
) -> None:
    # The recorded inputs must stand on their own: a replay has no bots, only the stream
    # they produced. If a decision had leaked a hidden input, the hashes would part here.
    inputs, expected = duel_inputs(32, (profile, profile))
    replayed, _ = run_ticks(duel_state(seed=32), inputs)
    assert state_hash(replayed) == expected


def test_different_seeds_spread_a_population_of_bots() -> None:
    hashes = {duel_inputs(seed, (SOLDIER, SOLDIER), ticks=200)[1] for seed in range(24)}
    # Seeded variation has to be real variation; a derivation that collapsed would make
    # every session of a stage play out identically.
    assert len(hashes) > 12


def test_different_profiles_on_one_seed_play_differently() -> None:
    streams = {
        profile.name: duel_inputs(41, (profile, ROOKIE), ticks=200)[0] for profile in PROFILE_ORDER
    }
    assert len({tuple(stream) for stream in streams.values()}) == len(PROFILE_ORDER)


def test_only_the_profile_name_needs_to_change_to_change_the_stream() -> None:
    # The profile name is mixed into the derivation, so two profiles with identical
    # numbers still make independent draws. That is deliberate and is documented as a
    # replay-compatibility consequence of renaming a profile.
    renamed = dataclasses.replace(SOLDIER, name="soldier-copy")
    assert duel_inputs(42, (SOLDIER, ROOKIE))[0] != duel_inputs(42, (renamed, ROOKIE))[0]


def test_the_order_bots_are_listed_in_cannot_change_a_tick() -> None:
    state = duel_state(seed=43)
    bots = bots_for(state, {PLAYER_TANK_ID: VETERAN, ENEMY_TANK_ID: ROOKIE})
    forward = plan_tick(bots, state)
    backward = plan_tick(tuple(reversed(bots)), state)
    assert forward.tick_input == backward.tick_input
    assert forward.bots == backward.bots


def test_a_bot_run_is_reproducible_from_an_intermediate_snapshot() -> None:
    # Bot memory is a value, so holding tick 120's bots and state and continuing from
    # them has to land exactly where the uninterrupted run did.
    state = duel_state(seed=44)
    bots: Sequence[Bot] = bots_for(state, {PLAYER_TANK_ID: VETERAN, ENEMY_TANK_ID: SOLDIER})
    whole = drive(state, bots, 240)
    first_half = drive(state, bots, 120)
    second_half = drive(first_half.state, first_half.bots, 120)
    assert first_half.inputs + second_half.inputs == whole.inputs
    assert state_hash(second_half.state) == state_hash(whole.state)
