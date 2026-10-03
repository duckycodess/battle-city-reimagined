"""Bot prediction on conveyor and teleport terrain.

A bot does not route through gimmick terrain -- there is no path planner here, and the
accepted change says so in as many words -- but it has to *predict* it, because the pose
it will hold when the fire phase runs is the pose the movement phase leaves it in. These
cases pin that prediction against the engine itself: the assertion is almost always that
``predicted_pose`` agrees with what a real tick produces.

The other half is the part a reader should care about more. On a stage with no gimmick
terrain every decision has to be byte-identical to what it was before this change, and
the last case here proves it by driving two bots over a long run and comparing the whole
command stream.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from ai_helpers import PLAYER_TANK_ID, arena, bots_for, drive, game, with_enemy
from battle_city_ai import clearance_ticks, decide, predicted_pose
from battle_city_ai.profiles import VETERAN
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    GridPos,
    MoveCommand,
    SimulationState,
    TankVariant,
    TickInput,
    Tile,
    Vec2,
    centre_cell,
    step,
)

SPEED = DEFAULT_RULES.tank_speed
TILE = DEFAULT_RULES.tile_size


def positioned(state: SimulationState, tank_id: int, position: Vec2) -> SimulationState:
    tanks = tuple(
        replace(tank, position=position) if tank.entity_id == tank_id else tank
        for tank in state.tanks
    )
    return replace(state, tanks=tanks)


def stepped(state: SimulationState, direction: Direction | None) -> SimulationState:
    commands = (
        () if direction is None else (MoveCommand(tank_id=PLAYER_TANK_ID, direction=direction),)
    )
    return step(state, TickInput(tick=state.tick, commands=commands)).state


def assert_predicts_the_engine(state: SimulationState, direction: Direction | None) -> None:
    """The one assertion worth repeating: prediction and the engine must not drift."""
    predicted = predicted_pose(state, state.tank(PLAYER_TANK_ID), direction)
    actual = stepped(state, direction).tank(PLAYER_TANK_ID)
    assert predicted.position == actual.position
    assert predicted.facing is actual.facing


# -- conveyors ----------------------------------------------------------------


@pytest.mark.parametrize(
    "tile", [Tile.CONVEYOR_N, Tile.CONVEYOR_E, Tile.CONVEYOR_S, Tile.CONVEYOR_W]
)
def test_an_idle_bot_predicts_the_push_it_is_about_to_receive(tile: Tile) -> None:
    """``None`` is "no command", not "no movement": a belt moves a tank that sits still."""
    belt = GridPos(8, 8)
    state = positioned(
        game(arena(overrides={belt: tile})), PLAYER_TANK_ID, Vec2(belt.x * TILE, belt.y * TILE)
    )
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), None)
    assert pose.position != state.tank(PLAYER_TANK_ID).position
    assert_predicts_the_engine(state, None)


@pytest.mark.parametrize(
    "direction", [Direction.UP, Direction.DOWN, Direction.LEFT, Direction.RIGHT]
)
def test_a_commanded_move_on_a_belt_predicts_both_displacements(direction: Direction) -> None:
    belt = GridPos(8, 8)
    state = positioned(
        game(arena(overrides={belt: Tile.CONVEYOR_E})),
        PLAYER_TANK_ID,
        Vec2(belt.x * TILE, belt.y * TILE),
    )
    assert_predicts_the_engine(state, direction)


def test_a_push_never_turns_the_predicted_tank() -> None:
    belt = GridPos(8, 8)
    state = positioned(
        game(arena(overrides={belt: Tile.CONVEYOR_S})),
        PLAYER_TANK_ID,
        Vec2(belt.x * TILE, belt.y * TILE),
    )
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), None)
    assert pose.facing is state.tank(PLAYER_TANK_ID).facing


def test_a_blocked_push_is_predicted_as_blocked() -> None:
    belt, wall = GridPos(8, 8), GridPos(9, 8)
    state = positioned(
        game(arena(overrides={belt: Tile.CONVEYOR_E, wall: Tile.STONE})),
        PLAYER_TANK_ID,
        Vec2(belt.x * TILE, belt.y * TILE),
    )
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), None)
    assert pose.position == state.tank(PLAYER_TANK_ID).position
    assert_predicts_the_engine(state, None)


def test_clearance_counts_a_belt_assisted_run() -> None:
    """A belt pushing the way the bot wants to go doubles how far it gets per tick."""
    belts = {GridPos(x, 8): Tile.CONVEYOR_E for x in range(2, 12)}
    state = positioned(game(arena(overrides=belts)), PLAYER_TANK_ID, Vec2(2 * TILE, 8 * TILE))
    tank = state.tank(PLAYER_TANK_ID)
    assert clearance_ticks(state, tank, Direction.RIGHT, 4) == 4
    pose = tank
    for _ in range(4):
        pose = predicted_pose(state, pose, Direction.RIGHT)
    assert pose.position.x == tank.position.x + 4 * 2 * SPEED


def test_clearance_is_zero_when_a_belt_pins_a_bot_against_a_wall() -> None:
    """Being carried east is not clearance to the west, however much the tank moved."""
    belt, wall = GridPos(8, 8), GridPos(7, 8)
    state = positioned(
        game(arena(overrides={belt: Tile.CONVEYOR_E, wall: Tile.STONE})),
        PLAYER_TANK_ID,
        Vec2(belt.x * TILE, belt.y * TILE),
    )
    assert clearance_ticks(state, state.tank(PLAYER_TANK_ID), Direction.LEFT, 4) == 0


# -- teleport pads ------------------------------------------------------------


def test_a_bot_predicts_the_arrival_it_is_about_to_make() -> None:
    source, partner = GridPos(4, 8), GridPos(12, 4)
    state = positioned(
        game(arena(overrides={source: Tile.TELEPORT_PAD, partner: Tile.TELEPORT_PAD})),
        PLAYER_TANK_ID,
        Vec2(source.x * TILE, source.y * TILE + TILE // 2),
    )
    assert centre_cell(state.tank(PLAYER_TANK_ID).position, DEFAULT_RULES) == GridPos(4, 9)
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.UP)
    assert centre_cell(pose.position, DEFAULT_RULES) == partner
    assert_predicts_the_engine(state, Direction.UP)


def test_an_occupied_exit_is_not_a_shortcut() -> None:
    """The arrival is collision-checked, so a bot cannot plan through a blocked pad."""
    source, partner = GridPos(4, 8), GridPos(12, 4)
    state = game(arena(overrides={source: Tile.TELEPORT_PAD, partner: Tile.TELEPORT_PAD}))
    state = with_enemy(state, partner, variant=TankVariant.ENEMY_NORMAL)
    state = positioned(state, PLAYER_TANK_ID, Vec2(source.x * TILE, source.y * TILE + TILE // 2))
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.UP)
    assert centre_cell(pose.position, DEFAULT_RULES) == source
    assert_predicts_the_engine(state, Direction.UP)


def test_sitting_on_a_pad_is_predicted_as_sitting_still() -> None:
    source, partner = GridPos(4, 8), GridPos(12, 4)
    state = positioned(
        game(arena(overrides={source: Tile.TELEPORT_PAD, partner: Tile.TELEPORT_PAD})),
        PLAYER_TANK_ID,
        Vec2(source.x * TILE, source.y * TILE),
    )
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), None)
    assert pose.position == state.tank(PLAYER_TANK_ID).position
    assert_predicts_the_engine(state, None)


def test_a_half_pair_grid_predicts_no_arrival_at_all() -> None:
    """A hand-built grid that never passed either validation gate simply has no teleports.

    Both gates refuse one pad, so this state cannot be reached through a stage or a level;
    predicting an arbitrary partner for it would be inventing a rule.
    """
    source = GridPos(4, 8)
    stage = arena()
    grid = stage.grid.with_tile(source, Tile.TELEPORT_PAD)
    state = replace(game(stage), grid=grid)
    state = positioned(state, PLAYER_TANK_ID, Vec2(source.x * TILE, source.y * TILE + TILE // 2))
    pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.UP)
    assert centre_cell(pose.position, DEFAULT_RULES) == source
    assert_predicts_the_engine(state, Direction.UP)


# -- the compatibility half ---------------------------------------------------


def test_a_bot_emits_only_commands_the_engine_accepts_on_gimmick_terrain() -> None:
    """Legality is the contract; a refused command aborts a whole tick for everyone."""
    belts = {GridPos(x, 8): Tile.CONVEYOR_E for x in range(2, 12)}
    belts[GridPos(6, 6)] = Tile.TELEPORT_PAD
    belts[GridPos(10, 11)] = Tile.TELEPORT_PAD
    state = game(arena(overrides=belts))
    state = with_enemy(state, GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: VETERAN, 2: VETERAN})
    session = drive(state, bots, 120)
    assert session.state.tick == state.tick + 120
    for tick_input in session.inputs:
        assert len(tick_input.commands) <= 2 * len(bots)


def test_decisions_on_a_gimmick_free_stage_are_unchanged() -> None:
    """The regression guard: no gimmick tiles, no difference in the command stream."""
    state = game(arena(overrides={GridPos(8, 6): Tile.BRICK, GridPos(4, 9): Tile.STONE}))
    state = with_enemy(state, GridPos(12, 2))
    bots = bots_for(state, {PLAYER_TANK_ID: VETERAN, 2: VETERAN})
    first = drive(state, bots, 150)
    second = drive(state, bots, 150)
    assert first.inputs == second.inputs
    assert [bot.memory for bot in first.bots] == [bot.memory for bot in second.bots]


def test_prediction_matches_the_engine_tick_for_tick_on_a_classic_stage() -> None:
    state = game(arena(overrides={GridPos(8, 6): Tile.BRICK}))
    tank = state.tank(PLAYER_TANK_ID)
    for direction in (Direction.UP, Direction.DOWN, Direction.LEFT, Direction.RIGHT, None):
        predicted = predicted_pose(state, tank, direction)
        actual = stepped(state, direction).tank(PLAYER_TANK_ID)
        assert (predicted.position, predicted.facing) == (actual.position, actual.facing)


def test_a_bot_on_a_classic_stage_never_sees_a_pad_scan() -> None:
    """Prediction is guarded by one cell lookup, so a classic grid costs nothing extra."""
    state = game(arena())
    tank = state.tank(PLAYER_TANK_ID)
    assert predicted_pose(state, tank, None) is tank
    assert (
        decide(bots_for(state, {PLAYER_TANK_ID: VETERAN})[0], state).commands
        == decide(bots_for(state, {PLAYER_TANK_ID: VETERAN})[0], state).commands
    )
