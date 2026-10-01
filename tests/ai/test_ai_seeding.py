"""Bot randomness is the simulation's generator, derived per bot, free of hidden inputs."""

from __future__ import annotations

import os
import subprocess
import sys

from ai_helpers import arena, bots_for, drive, game, with_enemy
from battle_city_ai import (
    PROFILE_ORDER,
    ROOKIE,
    SOLDIER,
    VETERAN,
    Bot,
    decide,
    derive_bot_rng,
    profile_code,
)
from battle_city_sim import Direction, GridPos, Rng, Tile


def test_derivation_is_a_pure_function_of_its_three_inputs() -> None:
    first = derive_bot_rng(seed=1234, tank_id=2, profile_name="veteran")
    second = derive_bot_rng(seed=1234, tank_id=2, profile_name="veteran")
    assert first == second
    assert isinstance(first, Rng)


def test_each_seed_tank_and_profile_gets_its_own_stream() -> None:
    base = derive_bot_rng(seed=1234, tank_id=2, profile_name="veteran")
    assert derive_bot_rng(seed=1235, tank_id=2, profile_name="veteran") != base
    assert derive_bot_rng(seed=1234, tank_id=3, profile_name="veteran") != base
    assert derive_bot_rng(seed=1234, tank_id=2, profile_name="rookie") != base


def test_neighbouring_seeds_do_not_produce_neighbouring_streams() -> None:
    # Folding exists so that a session seed of 1 and a session seed of 2 are unrelated.
    # Two bots whose first draws agreed would make "different seed" a meaningless knob.
    draws = {
        derive_bot_rng(seed=seed, tank_id=2, profile_name="soldier").below(100)[0]
        for seed in range(32)
    }
    assert len(draws) > 8


def test_streams_are_distinct_across_a_whole_population() -> None:
    positions = {
        derive_bot_rng(seed=seed, tank_id=tank_id, profile_name=profile.name)
        for seed in range(8)
        for tank_id in range(1, 5)
        for profile in PROFILE_ORDER
    }
    assert len(positions) == 8 * 4 * len(PROFILE_ORDER)


def test_the_profile_code_does_not_depend_on_the_interpreter_hash_seed() -> None:
    # ``hash("veteran")`` is randomised per process. If derivation ever reached for it,
    # a replay would stop reproducing as soon as it ran in a different shell.
    script = (
        "from battle_city_ai import profile_code;"
        "print(profile_code('veteran'), profile_code('rookie'))"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": str(value)},
        ).stdout.strip()
        for value in (0, 1, 4242)
    }
    assert len(outputs) == 1
    assert outputs == {f"{profile_code('veteran')} {profile_code('rookie')}"}


def test_a_bot_derives_its_stream_once_and_then_threads_it() -> None:
    bot = Bot.create(tank_id=2, profile=VETERAN, seed=99)
    assert bot.memory.rng == derive_bot_rng(seed=99, tank_id=2, profile_name="veteran")

    state = with_enemy(game(seed=99), GridPos(12, 2))
    moved = bot
    seen = {moved.memory.rng}
    for _ in range(40):
        moved = decide(moved, state).bot
        seen.add(moved.memory.rng)
    # The stream advances rather than restarting from the derivation.
    assert len(seen) > 1
    assert derive_bot_rng(seed=99, tank_id=2, profile_name="veteran") in seen


def test_driving_bots_never_advances_the_simulations_own_generator() -> None:
    # ``step`` copies ``state.rng`` through untouched, which is exactly why a bot may not
    # draw from it. Asserting the invariant keeps a future bot from quietly relying on it.
    state = with_enemy(game(seed=5), GridPos(12, 2))
    before = state.rng
    session = drive(state, bots_for(state, {1: SOLDIER, 2: ROOKIE}), 60)
    assert session.state.rng == before


def test_two_bots_on_one_seed_do_not_share_a_decision_stream() -> None:
    stage = arena(overrides={GridPos(7, 7): Tile.BRICK})
    state = with_enemy(game(stage, seed=11), GridPos(12, 2), facing=Direction.LEFT)
    session = drive(state, bots_for(state, {1: SOLDIER, 2: SOLDIER}), 80)
    first, second = session.bots
    assert first.memory.rng != second.memory.rng
