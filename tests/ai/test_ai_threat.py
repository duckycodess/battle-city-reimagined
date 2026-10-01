"""Threat response: a bounded trace, a perpendicular sidestep, and the trade it implies.

Threat awareness reuses the profile's planning horizon, which is what makes it a
difficulty setting rather than a reflex everyone shares: a bot that looks two ticks ahead
gets six pixels of warning and a bot that looks eight gets twenty-four.

The scenarios here plant a projectile whose owner is gone. That is a state the engine
produces on its own - a projectile outlives the tank that fired it - and it is the one
situation where a bot is under fire with nothing to shoot back at, so it isolates dodging
from the engagement logic that otherwise outranks it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from battle_city_ai import (
    ROOKIE,
    VETERAN,
    Bot,
    DifficultyProfile,
    decide,
    incoming_threat,
    seed_from_state,
)
from battle_city_sim import (
    Command,
    Direction,
    Faction,
    FireCommand,
    GridPos,
    MoveCommand,
    Projectile,
    SimulationState,
    TankDestroyed,
    TickInput,
    Vec2,
    step,
)
from conftest import PLAYER_TANK_ID, game, with_enemy

ORPHAN_PROJECTILE_ID = 50
ORPHAN_OWNER_ID = 99


def _under_fire(start_x: int = 71) -> SimulationState:
    """Return a state where a hostile shot is closing on the player tank from the right.

    The player sits at pixel ``(32, 32)``; the shot flies left along ``y = 39`` at 3px a
    tick. From ``x = 71`` impact lands eight ticks out, which is inside a veteran's
    horizon and well outside a rookie's.
    """
    state = game()
    threat = Projectile(
        entity_id=ORPHAN_PROJECTILE_ID,
        owner_id=ORPHAN_OWNER_ID,
        faction=Faction.ENEMY,
        position=Vec2(start_x, 39),
        direction=Direction.LEFT,
    )
    return replace(state, projectiles=(threat,), next_entity_id=ORPHAN_PROJECTILE_ID + 1)


def _run(state: SimulationState, bot: Bot | None, ticks: int) -> bool:
    """Step ``ticks`` ticks and report whether the player tank survived."""
    current = state
    driver = bot
    for _ in range(ticks):
        commands: Sequence[Command] = ()
        if driver is not None:
            decision = decide(driver, current)
            commands = decision.commands
            driver = decision.bot
        result = step(current, TickInput.from_iterable(current.tick, commands))
        current = result.state
        if any(isinstance(event, TankDestroyed) for event in result.events):
            return False
    return True


def test_the_planning_horizon_decides_whether_the_threat_is_seen_at_all() -> None:
    state = _under_fire()
    tank = state.tank(PLAYER_TANK_ID)
    assert incoming_threat(state, tank, VETERAN.planning_horizon_ticks) is not None
    assert incoming_threat(state, tank, ROOKIE.planning_horizon_ticks) is None


def test_a_bot_that_sees_the_shot_steps_out_of_its_path() -> None:
    state = _under_fire()
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    commands = decide(bot, state).commands
    assert len(commands) == 1
    move = commands[0]
    assert isinstance(move, MoveCommand)
    # Perpendicular only: a projectile covers 3px a tick and a tank 2px, so running along
    # the shot's own axis loses the race by a pixel a tick.
    assert move.direction in (Direction.UP, Direction.DOWN)


def test_the_sidestep_actually_saves_the_tank() -> None:
    state = _under_fire()
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    assert not _run(state, None, 12)
    assert _run(state, bot, 12)


def test_warning_shorter_than_the_escape_still_produces_a_legal_tick() -> None:
    # Three ticks of warning cannot clear a 16px body out of the line at 2px a tick. The
    # bot still sidesteps, it simply does not make it; the point is that an impossible
    # dodge degrades into a normal move rather than into a stuck or illegal one.
    state = _under_fire(start_x=56)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    commands = decide(bot, state).commands
    assert all(isinstance(command, MoveCommand) for command in commands)
    assert not _run(state, bot, 12)


def test_a_bot_that_cannot_see_the_shot_does_not_react_to_it() -> None:
    state = _under_fire()
    patient = replace(ROOKIE, name="rookie-under-fire")
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=patient, seed=seed_from_state(state))
    undisturbed = decide(bot, replace(state, projectiles=()))
    assert decide(bot, state).commands == undisturbed.commands


def test_a_firing_solution_outranks_a_dodge() -> None:
    # A documented trade: a bot with the shot takes it rather than stepping out of its own
    # line of fire. Asserting it keeps the precedence from drifting unnoticed.
    state = with_enemy(game(), GridPos(12, 2))
    threat = Projectile(
        entity_id=ORPHAN_PROJECTILE_ID,
        owner_id=ORPHAN_OWNER_ID,
        faction=Faction.ENEMY,
        position=Vec2(71, 39),
        direction=Direction.LEFT,
    )
    state = replace(state, projectiles=(threat,), next_entity_id=ORPHAN_PROJECTILE_ID + 1)
    # Reaction delay is zeroed so the shot lands on the first tick and the assertion is
    # about precedence rather than about how long the veteran waits.
    aggressive: DifficultyProfile = replace(VETERAN, name="veteran-engaged", reaction_delay_ticks=0)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=aggressive, seed=seed_from_state(state))

    threatened = incoming_threat(
        state, state.tank(PLAYER_TANK_ID), aggressive.planning_horizon_ticks
    )
    assert threatened is not None
    commands = decide(bot, state).commands
    assert any(isinstance(command, FireCommand) for command in commands)
    for command in commands:
        if isinstance(command, MoveCommand):
            assert command.direction is Direction.RIGHT


@pytest.mark.parametrize("start_x", [56, 64, 71, 80, 96])
def test_threat_handling_never_produces_an_illegal_tick(start_x: int) -> None:
    state = _under_fire(start_x)
    bot = Bot.create(tank_id=PLAYER_TANK_ID, profile=VETERAN, seed=seed_from_state(state))
    for _ in range(40):
        decision = decide(bot, state)
        state = step(state, TickInput.from_iterable(state.tick, decision.commands)).state
        bot = decision.bot
        if state.find_tank(PLAYER_TANK_ID) is None:
            break
