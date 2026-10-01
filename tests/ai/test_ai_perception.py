"""Perception answers the engine's own questions, in pixels, with the engine's arithmetic."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from ai_helpers import PLAYER_TANK_ID, arena, game, with_enemy
from battle_city_ai import (
    SOLDIER,
    aim_candidates,
    clearance_ticks,
    hostiles_of,
    incoming_threat,
    max_flight_ticks,
    muzzle_of,
    predicted_pose,
    shot_target_id,
)
from battle_city_sim import (
    DEFAULT_RULES,
    DIRECTION_ORDER,
    Command,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    ProjectileFired,
    SimulationState,
    TankDestroyed,
    Tile,
    is_tank_concealed,
    step,
)
from battle_city_sim.inputs import TickInput

ENEMY_TANK_ID = 2
"""The first enemy spawned into a one-slot stage takes identifier 2."""


def _advance(state: SimulationState, commands: Sequence[Command]) -> SimulationState:
    """Step one tick with ``commands`` and return the resulting state."""
    return step(state, TickInput.from_iterable(state.tick, commands)).state


@pytest.mark.parametrize("direction", DIRECTION_ORDER, ids=lambda item: item.name)
@pytest.mark.parametrize(
    ("player_cell", "overrides"),
    [
        (GridPos(5, 5), {}),
        (GridPos(0, 0), {}),
        (GridPos(15, 14), {}),
        (GridPos(5, 5), {GridPos(5, 4): Tile.STONE, GridPos(6, 5): Tile.WATER}),
    ],
    ids=["open", "top-left-corner", "bottom-right-corner", "walled"],
)
def test_predicted_pose_matches_what_the_engine_actually_does(
    direction: Direction,
    player_cell: GridPos,
    overrides: dict[GridPos, Tile],
) -> None:
    state = game(arena(overrides=overrides, player_cell=player_cell))
    tank = state.tank(PLAYER_TANK_ID)
    predicted = predicted_pose(state, tank, direction)
    actual = _advance(state, [MoveCommand(tank_id=PLAYER_TANK_ID, direction=direction)]).tank(
        PLAYER_TANK_ID
    )
    assert predicted.position == actual.position
    assert predicted.facing == actual.facing


def test_a_move_command_turns_the_tank_even_when_the_step_is_refused() -> None:
    state = game(arena(overrides={GridPos(5, 4): Tile.STONE}, player_cell=GridPos(5, 5)))
    tank = state.tank(PLAYER_TANK_ID)
    predicted = predicted_pose(state, tank, Direction.UP)
    assert predicted.position == tank.position
    assert predicted.facing is Direction.UP


def test_prediction_accounts_for_another_tank_standing_in_the_way() -> None:
    state = with_enemy(game(arena(player_cell=GridPos(5, 5))), GridPos(5, 4))
    tank = state.tank(PLAYER_TANK_ID)
    assert predicted_pose(state, tank, Direction.UP).position == tank.position
    assert predicted_pose(state, tank, Direction.DOWN).position != tank.position


def test_clearance_counts_the_moves_the_engine_would_grant() -> None:
    # The tank starts at pixel row 80 with a 16px body and stone fills row 7 from pixel
    # 112, so the last legal top edge is 96: eight 2px steps, then nothing.
    state = game(arena(overrides={GridPos(5, 7): Tile.STONE}, player_cell=GridPos(5, 5)))
    tank = state.tank(PLAYER_TANK_ID)
    assert clearance_ticks(state, tank, Direction.DOWN, 8) == 8
    assert clearance_ticks(state, tank, Direction.DOWN, 16) == 8

    walked = state
    for _ in range(24):
        walked = _advance(walked, [MoveCommand(tank_id=PLAYER_TANK_ID, direction=Direction.DOWN)])
    travelled = walked.tank(PLAYER_TANK_ID).position.y - tank.position.y
    assert travelled == 8 * DEFAULT_RULES.tank_speed


def test_clearance_is_zero_at_the_world_edge() -> None:
    state = game(arena(player_cell=GridPos(0, 0)))
    tank = state.tank(PLAYER_TANK_ID)
    assert clearance_ticks(state, tank, Direction.UP, 8) == 0
    assert clearance_ticks(state, tank, Direction.LEFT, 8) == 0


def test_aim_candidates_need_the_target_on_the_named_side() -> None:
    state = with_enemy(game(), GridPos(12, 2))
    shooter = state.tank(PLAYER_TANK_ID)
    target = state.tank(ENEMY_TANK_ID)
    assert aim_candidates(shooter, target, 0) == (Direction.RIGHT,)
    assert aim_candidates(target, shooter, 0) == (Direction.LEFT,)


def test_aim_candidates_respect_the_tolerance() -> None:
    state = with_enemy(game(), GridPos(12, 3))
    shooter = state.tank(PLAYER_TANK_ID)
    target = state.tank(ENEMY_TANK_ID)
    assert aim_candidates(shooter, target, 8) == ()
    nudged = replace(target, position=replace(target.position, y=shooter.position.y + 8))
    assert aim_candidates(shooter, nudged, 8) == (Direction.RIGHT,)
    assert aim_candidates(shooter, nudged, 7) == ()


def test_a_clear_line_predicts_the_hit_the_engine_then_delivers() -> None:
    state = with_enemy(game(), GridPos(12, 2))
    shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
    assert (
        shot_target_id(
            state,
            origin=muzzle_of(shooter),
            direction=Direction.RIGHT,
            faction=shooter.faction,
        )
        == ENEMY_TANK_ID
    )

    current = _advance(
        state,
        [
            MoveCommand(tank_id=PLAYER_TANK_ID, direction=Direction.RIGHT),
            FireCommand(tank_id=PLAYER_TANK_ID),
        ],
    )
    for _ in range(max_flight_ticks(state)):
        result = step(current, TickInput.of(current.tick))
        current = result.state
        if any(isinstance(event, TankDestroyed) for event in result.events):
            break
    assert current.find_tank(ENEMY_TANK_ID) is None


@pytest.mark.parametrize(
    ("tile", "expected"),
    [
        (Tile.EMPTY, ENEMY_TANK_ID),
        (Tile.WATER, ENEMY_TANK_ID),
        (Tile.FOREST, ENEMY_TANK_ID),
        (Tile.BRICK, None),
        (Tile.CRACKED_BRICK, None),
        (Tile.STONE, None),
        (Tile.MIRROR_NE, None),
        (Tile.MIRROR_SE, None),
    ],
    ids=lambda item: item.name if isinstance(item, Tile) else str(item),
)
def test_terrain_between_shooter_and_target_decides_the_line(
    tile: Tile,
    expected: int | None,
) -> None:
    # Water and forest pass projectiles; brick and stone stop them. A mirror is reported
    # as no line at all rather than as a deflected one, so a bot never claims a shot it
    # cannot geometrically justify.
    state = with_enemy(game(arena(overrides={GridPos(7, 2): tile})), GridPos(12, 2))
    shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
    assert (
        shot_target_id(
            state,
            origin=muzzle_of(shooter),
            direction=Direction.RIGHT,
            faction=shooter.faction,
        )
        == expected
    )


def test_the_home_base_is_never_reported_as_a_firing_solution() -> None:
    state = with_enemy(game(arena(base=GridPos(7, 2))), GridPos(12, 2))
    shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
    assert (
        shot_target_id(
            state,
            origin=muzzle_of(shooter),
            direction=Direction.RIGHT,
            faction=shooter.faction,
        )
        is None
    )


def test_a_concealed_target_is_still_perceived() -> None:
    # ``battle_city_sim.visibility`` states that concealment has no gameplay effect until
    # a proposal defines one. Hiding a target from bots here would be inventing that rule.
    forest = {GridPos(x, y): Tile.FOREST for x in (11, 12, 13) for y in (1, 2, 3)}
    stage = arena(overrides=forest, enemy_cells=(GridPos(1, 14),))
    state = with_enemy(game(stage), GridPos(12, 2))
    target = state.tank(ENEMY_TANK_ID)
    assert is_tank_concealed(state.grid, target)

    shooter = predicted_pose(state, state.tank(PLAYER_TANK_ID), Direction.RIGHT)
    assert hostiles_of(state, shooter) == (target,)
    assert (
        shot_target_id(
            state,
            origin=muzzle_of(shooter),
            direction=Direction.RIGHT,
            faction=shooter.faction,
        )
        == ENEMY_TANK_ID
    )


def test_muzzle_offsets_match_the_projectiles_the_engine_spawns() -> None:
    for direction in DIRECTION_ORDER:
        state = game(arena(player_cell=GridPos(7, 7)))
        result = step(
            state,
            TickInput.of(
                state.tick,
                MoveCommand(tank_id=PLAYER_TANK_ID, direction=direction),
                FireCommand(tank_id=PLAYER_TANK_ID),
            ),
        )
        spawned = next(
            event for event in result.events if isinstance(event, ProjectileFired)
        ).position
        pose = predicted_pose(state, state.tank(PLAYER_TANK_ID), direction)
        dx, dy = direction.scaled(DEFAULT_RULES.projectile_speed)
        # The engine advances a projectile in the same tick it is fired, so the reported
        # spawn position is the muzzle and the first advance has not happened yet.
        assert muzzle_of(pose) == spawned
        assert (dx, dy) != (0, 0)


def test_an_incoming_hostile_shot_is_seen_and_a_friendly_one_is_not() -> None:
    state = with_enemy(game(), GridPos(12, 2), facing=Direction.LEFT)
    state = _advance(state, [FireCommand(tank_id=ENEMY_TANK_ID)])
    player = state.tank(PLAYER_TANK_ID)
    enemy = state.tank(ENEMY_TANK_ID)
    horizon = max_flight_ticks(state)
    assert incoming_threat(state, player, horizon) is not None
    assert incoming_threat(state, enemy, horizon) is None


def test_a_short_planning_horizon_sees_the_threat_too_late() -> None:
    state = with_enemy(game(), GridPos(12, 2), facing=Direction.LEFT)
    state = _advance(state, [FireCommand(tank_id=ENEMY_TANK_ID)])
    player = state.tank(PLAYER_TANK_ID)
    assert incoming_threat(state, player, SOLDIER.planning_horizon_ticks) is None
    assert incoming_threat(state, player, max_flight_ticks(state)) is not None


def test_terrain_stops_a_threat_before_it_arrives() -> None:
    state = with_enemy(
        game(arena(overrides={GridPos(7, 2): Tile.STONE})), GridPos(12, 2), facing=Direction.LEFT
    )
    state = _advance(state, [FireCommand(tank_id=ENEMY_TANK_ID)])
    player = state.tank(PLAYER_TANK_ID)
    assert incoming_threat(state, player, max_flight_ticks(state)) is None
