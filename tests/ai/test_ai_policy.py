"""The policy emits legal commands, obeys the engine's own gates, and respects its profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest
from ai_helpers import PLAYER_TANK_ID, Session, arena, bots_for, drive, game, with_enemy
from battle_city_ai import (
    PROFILE_ORDER,
    ROOKIE,
    SOLDIER,
    VETERAN,
    Bot,
    BotDecision,
    DifficultyProfile,
    TargetPreference,
    clearance_ticks,
    decide,
    muzzle_of,
    plan_tick,
    predicted_pose,
    seed_from_state,
    shot_target_id,
)
from battle_city_sim import (
    DEFAULT_RULES,
    DIRECTION_ORDER,
    Command,
    FireCommand,
    GridPos,
    MoveCommand,
    ProjectileFired,
    RunOutcome,
    SimulationState,
    TankDestroyed,
    TankMoved,
    TickInput,
    Tile,
    step,
)

ENEMY_TANK_ID = 2

CLUTTER: Mapping[GridPos, Tile] = {
    GridPos(6, 6): Tile.BRICK,
    GridPos(7, 6): Tile.BRICK,
    GridPos(8, 6): Tile.STONE,
    GridPos(6, 7): Tile.WATER,
    GridPos(9, 9): Tile.MIRROR_NE,
    GridPos(10, 9): Tile.MIRROR_SE,
    GridPos(4, 11): Tile.FOREST,
    GridPos(5, 11): Tile.CRACKED_BRICK,
}
"""One of every tile behaviour, so a run exercises blocking, passing and reflecting."""


def duel(profile: DifficultyProfile, *, seed: int, ticks: int = 400) -> Session:
    """Run a bot-against-bot duel on the cluttered arena."""
    state = with_enemy(game(arena(overrides=CLUTTER), seed=seed), GridPos(12, 2))
    return drive(state, bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile}), ticks)


def _moves(commands: Sequence[Command], tank_id: int) -> tuple[MoveCommand, ...]:
    return tuple(
        command
        for command in commands
        if isinstance(command, MoveCommand) and command.tank_id == tank_id
    )


def _fires(commands: Sequence[Command], tank_id: int) -> tuple[FireCommand, ...]:
    return tuple(
        command
        for command in commands
        if isinstance(command, FireCommand) and command.tank_id == tank_id
    )


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_every_tick_a_bot_produces_is_accepted_by_the_engine(
    profile: DifficultyProfile,
) -> None:
    # ``step`` raises ``InvalidInputError`` on any illegal command, so a clean 400-tick
    # run over terrain that includes every tile behaviour is the legality assertion.
    session = duel(profile, seed=3)
    assert len(session.inputs) == 400
    assert session.inputs[-1].tick == session.state.tick - 1


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_a_bot_commands_only_its_own_tank_and_only_the_two_player_commands(
    profile: DifficultyProfile,
) -> None:
    session = duel(profile, seed=4)
    for tick_input in session.inputs:
        for command in tick_input.commands:
            assert isinstance(command, MoveCommand | FireCommand)
            assert command.tank_id in (PLAYER_TANK_ID, ENEMY_TANK_ID)
        for tank_id in (PLAYER_TANK_ID, ENEMY_TANK_ID):
            assert len(_moves(tick_input.commands, tank_id)) <= 1
            assert len(_fires(tick_input.commands, tank_id)) <= 1


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_a_bot_never_fires_without_a_hostile_at_the_end_of_the_line(
    profile: DifficultyProfile,
) -> None:
    # Re-derived per tick from the pose the move phase will actually produce. This is the
    # invariant behind three separate promises: no blind shots, no shot the engine would
    # resolve into terrain, and no shot aimed at the home base, whose tile swallows a
    # friendly projectile and ends the run on a hostile one.
    state = with_enemy(game(arena(overrides=CLUTTER), seed=6), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile})
    for _ in range(400):
        plan = plan_tick(bots, state)
        for command in plan.tick_input.commands:
            if not isinstance(command, FireCommand):
                continue
            tank = state.tank(command.tank_id)
            moves = _moves(plan.tick_input.commands, command.tank_id)
            pose = predicted_pose(state, tank, moves[0].direction if moves else None)
            assert (
                shot_target_id(
                    state,
                    origin=muzzle_of(pose),
                    direction=pose.facing,
                    faction=pose.faction,
                )
                is not None
            ), f"tank {command.tank_id} fired into nothing at tick {state.tick}"
        state = step(state, plan.tick_input).state
        bots = plan.bots


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_a_bot_never_spends_a_fire_command_the_engine_would_drop(
    profile: DifficultyProfile,
) -> None:
    # ``step._phase_fire`` silently refuses a tank that already owns its projectile
    # allowance. A bot that issued the command anyway would look busy and achieve nothing.
    state = with_enemy(game(arena(overrides=CLUTTER), seed=8), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile})
    for _ in range(400):
        plan = plan_tick(bots, state)
        for command in plan.tick_input.commands:
            if isinstance(command, FireCommand):
                live = len(state.projectiles_owned_by(command.tank_id))
                assert live < DEFAULT_RULES.max_projectiles_per_tank
        state = step(state, plan.tick_input).state
        bots = plan.bots


@pytest.mark.parametrize("commitment", [1, 2, 3, 7])
def test_a_commitment_of_n_ticks_spans_exactly_n_ticks(commitment: int) -> None:
    # The counter is read after it is decremented, so the tick that sets a commitment is
    # the first of its ``n`` ticks rather than a free extra one on top of them.
    state = game()
    profile = replace(_metronome(), name=f"committed-{commitment}", plan_commit_ticks=commitment)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state))
    for tick in range(4 * commitment):
        decision = decide(bot, state)
        assert decision.bot.memory.plan_ticks_left == commitment - (tick % commitment)
        state = step(state, TickInput.from_iterable(state.tick, decision.commands)).state
        bot = decision.bot


@pytest.mark.parametrize("profile", PROFILE_ORDER, ids=lambda item: item.name)
def test_shots_from_one_bot_stay_inside_the_profiles_cadence(
    profile: DifficultyProfile,
) -> None:
    state = with_enemy(game(arena(overrides=CLUTTER), seed=9), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: profile})
    last_fired: dict[int, int] = {}
    for _ in range(600):
        result = step(state, plan_tick(bots, state).tick_input)
        bots = plan_tick(bots, state).bots
        for event in result.events:
            if not isinstance(event, ProjectileFired):
                continue
            previous = last_fired.get(event.owner_id)
            if previous is not None:
                assert state.tick - previous >= profile.fire_cooldown_ticks
            last_fired[event.owner_id] = state.tick
        state = result.state


def test_a_bot_with_no_tank_issues_nothing_and_keeps_its_stream() -> None:
    state = game()
    bot = Bot.create(tank_id=99, profile=VETERAN, seed=seed_from_state(state))
    decision = decide(bot, state)
    assert decision.commands == ()
    assert decision.bot.memory.rng == bot.memory.rng


def test_a_finished_run_absorbs_every_bot() -> None:
    # ``step`` makes a terminal state absorbing. A bot that kept issuing commands into one
    # would be burning its random stream on ticks that can never happen.
    state = with_enemy(game(), GridPos(12, 2))
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    assert decide(bot, state).commands != ()

    ended = replace(state, outcome=RunOutcome.BASE_DESTROYED)
    decision = decide(bot, ended)
    assert decision.commands == ()
    assert decision.bot.memory.rng == bot.memory.rng


def test_plan_tick_is_ordered_by_tank_and_refuses_a_shared_tank() -> None:
    state = with_enemy(game(), GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: SOLDIER, ENEMY_TANK_ID: ROOKIE})
    forward = plan_tick(bots, state)
    reversed_order = plan_tick(tuple(reversed(bots)), state)
    assert forward.tick_input == reversed_order.tick_input
    assert tuple(bot.tank_id for bot in forward.bots) == (PLAYER_TANK_ID, ENEMY_TANK_ID)

    seen: list[int] = []
    for command in forward.tick_input.commands:
        assert isinstance(command, MoveCommand | FireCommand)
        if command.tank_id not in seen:
            seen.append(command.tank_id)
    assert seen == sorted(seen)

    with pytest.raises(ValueError, match="two bots drive tank"):
        plan_tick((bots[0], replace(bots[1], tank_id=PLAYER_TANK_ID)), state)


def _metronome(
    *,
    reaction_delay_ticks: int = 1,
    aggression_percent: int = 0,
    fire_cooldown_ticks: int = 1,
) -> DifficultyProfile:
    """A profile with every random knob pinned, so a timing test measures one thing.

    ``miss_chance_percent`` is zero and ``aim_tolerance_px`` is zero, so neither the
    accuracy roll nor a sloppy alignment can blur the tick a shot lands on.
    """
    return DifficultyProfile(
        name="metronome",
        reaction_delay_ticks=reaction_delay_ticks,
        plan_commit_ticks=1,
        planning_horizon_ticks=4,
        aim_tolerance_px=0,
        miss_chance_percent=0,
        aggression_percent=aggression_percent,
        fire_cooldown_ticks=fire_cooldown_ticks,
        target_preference=TargetPreference.NEAREST,
    )


def _first_shot_tick(
    profile: DifficultyProfile,
    *,
    seed: int = 7,
    ticks: int = 400,
) -> int | None:
    """Return how many ticks a lone bot takes to shoot a stationary, aligned target."""
    state = with_enemy(game(seed=seed), GridPos(12, 2))
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state))
    start = state.tick
    for _ in range(ticks):
        decision = decide(bot, state)
        result = step(state, TickInput.from_iterable(state.tick, decision.commands))
        if any(isinstance(event, ProjectileFired) for event in result.events):
            return state.tick - start
        state = result.state
        bot = decision.bot
    return None


@pytest.mark.parametrize("delay", [0, 1, 2, 5, 13])
def test_reaction_delay_is_the_number_of_ticks_a_solution_must_hold(delay: int) -> None:
    # The gate counts ticks of waiting, so a delay of ``n`` puts the shot ``n`` ticks after
    # the solution appears and every setting is a distinct cadence. Counting the current
    # tick instead would make a one-tick reflex indistinguishable from no reflex at all.
    assert _first_shot_tick(_metronome(reaction_delay_ticks=delay)) == delay


def test_a_cautious_bot_holds_its_line_and_an_aggressive_one_closes() -> None:
    def advance_events(aggression: int) -> int:
        state = with_enemy(game(), GridPos(12, 2))
        profile = _metronome(aggression_percent=aggression, fire_cooldown_ticks=240)
        bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=profile, seed=seed_from_state(state))
        advances = 0
        for _ in range(40):
            decision = decide(bot, state)
            result = step(state, TickInput.from_iterable(state.tick, decision.commands))
            advances += sum(
                1
                for event in result.events
                if isinstance(event, TankMoved) and event.tank_id == PLAYER_TANK_ID
            )
            state = result.state
            bot = decision.bot
        return advances

    # One turn toward the target counts as a move for both; after that the cautious bot
    # stops and the aggressive one keeps walking down its own firing line.
    assert advance_events(0) <= 1
    assert advance_events(100) > 20


def test_a_lone_bot_with_no_hostile_still_produces_only_legal_ticks() -> None:
    state = game()
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    for _ in range(200):
        decision = decide(bot, state)
        assert all(isinstance(command, MoveCommand) for command in decision.commands)
        state = step(state, TickInput.from_iterable(state.tick, decision.commands)).state
        bot = decision.bot


def test_a_boxed_in_bot_issues_no_command_rather_than_an_illegal_one() -> None:
    walls = {
        GridPos(5, 4): Tile.STONE,
        GridPos(5, 6): Tile.STONE,
        GridPos(4, 5): Tile.STONE,
        GridPos(6, 5): Tile.STONE,
    }
    state = game(arena(overrides=walls, player_cell=GridPos(5, 5)))
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    decision: BotDecision = decide(bot, state)
    assert decision.commands == ()


def test_a_harder_profile_takes_an_offered_shot_sooner() -> None:
    # Same stage, same seed, same stationary target already inside every profile's aim
    # tolerance: only reaction delay and the accuracy roll differ. If difficulty did not
    # show up here it would not be a difficulty setting.
    latencies = [
        [_first_shot_tick(profile, seed=seed) for seed in range(12)] for profile in PROFILE_ORDER
    ]
    assert all(value is not None for row in latencies for value in row)
    totals = [sum(value for value in row if value is not None) for row in latencies]
    assert totals == sorted(totals, reverse=True)
    assert totals[0] > totals[-1] * 4


def test_the_easiest_profile_is_measurably_the_slowest_to_win() -> None:
    # Outcome, not opportunity: how long each profile needs to remove the same opponent.
    # Only the rookie is asserted against the rest. Aggression trades safety for pressure,
    # so the two harder profiles are not required to order against each other in a duel,
    # and pinning an ordering they do not have by design would be overfitting the test to
    # one stage.
    def ticks_to_kill(profile: DifficultyProfile, cap: int = 400) -> int:
        total = 0
        for seed in range(12):
            state = with_enemy(game(arena(overrides=CLUTTER), seed=seed), GridPos(12, 2))
            bots = bots_for(state, {PLAYER_TANK_ID: profile, ENEMY_TANK_ID: ROOKIE})
            elapsed = cap
            for tick in range(cap):
                plan = plan_tick(bots, state)
                result = step(state, plan.tick_input)
                if any(
                    isinstance(event, TankDestroyed) and event.tank_id == ENEMY_TANK_ID
                    for event in result.events
                ):
                    elapsed = tick
                    break
                state = result.state
                bots = plan.bots
            total += elapsed
        return total

    rookie = ticks_to_kill(ROOKIE)
    assert rookie > ticks_to_kill(SOLDIER)
    assert rookie > ticks_to_kill(VETERAN)


def test_a_bot_never_commits_to_a_direction_the_engine_would_refuse() -> None:
    # A stone between the bot and its target, which bounded roaming has no way around:
    # the horizon is a straight-line probe, not a path search, so the bot patrols in front
    # of the obstacle rather than rounding it. What it must never do is grind: every move
    # it commits to has somewhere to go at the moment it is issued, and the run keeps
    # producing movement rather than settling against the wall.
    state: SimulationState = game(
        arena(overrides={GridPos(5, 4): Tile.STONE}, player_cell=GridPos(5, 5))
    )
    state = with_enemy(state, GridPos(5, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: VETERAN})

    visited = {state.tank(PLAYER_TANK_ID).position}
    for _ in range(120):
        plan = plan_tick(bots, state)
        for command in plan.tick_input.commands:
            if not isinstance(command, MoveCommand):
                continue
            tank = state.tank(command.tank_id)
            assert clearance_ticks(state, tank, command.direction, 1) == 1, (
                f"committed to a refused step at tick {state.tick}"
            )
        state = step(state, plan.tick_input).state
        bots = plan.bots
        visited.add(state.tank(PLAYER_TANK_ID).position)

    assert len(visited) > 1
    assert state.tank(PLAYER_TANK_ID).facing in DIRECTION_ORDER
