"""Conveyor pushes and paired-pad teleports, as deterministic state transitions.

Every case states its own terrain and drives whole ticks through ``step``, so the
assertions are about the published contract -- positions, events, ordering -- rather than
about the private helpers that produce them.

The arithmetic worth holding while reading: a tile is 16px, a tank is 16px and a tank
moves 2px a tick, so a tank is tile-aligned only on even multiples of its own speed. The
tile a tank is *on* is the one holding the centre of its body -- ``position + 8`` on both
axes -- which is why a tank whose top-left is ``(8, 160)`` counts as standing in column 1.

Spawn cells stay on empty ground, as the stage contract requires, so a case that needs a
tank standing on gimmick terrain positions it with :func:`positioned` instead of declaring
a spawn there. That is a statement about a *state*, not a relaxation of the spawn rule.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    GridPos,
    MoveCommand,
    SimulationState,
    SpawnEnemyCommand,
    Stage,
    StageValidationError,
    TankMoveBlocked,
    TankMoved,
    TankVariant,
    Tile,
    Vec2,
    centre_cell,
    new_game,
    state_hash,
    teleport_pads,
)
from helpers import events_of, make_stage, make_state, only, run

PLAYER_ID = 1
TILE = DEFAULT_RULES.tile_size
SPEED = DEFAULT_RULES.tank_speed

SOURCE_PAD = GridPos(4, 10)
PARTNER_PAD = GridPos(12, 4)
SPAWN_CELL = GridPos(1, 13)
"""Empty ground well clear of every fixture, so a spawn never lands on gimmick terrain."""


def cell_pixel(cell: GridPos) -> Vec2:
    return Vec2(cell.x * TILE, cell.y * TILE)


def positioned(state: SimulationState, tank_id: int, position: Vec2) -> SimulationState:
    """Return ``state`` with ``tank_id`` standing at ``position``.

    Used where a case needs a body part-way across a tile, which no spawn can produce:
    spawns are cell-aligned and have to stand on empty ground.
    """
    tanks = tuple(
        replace(tank, position=position) if tank.entity_id == tank_id else tank
        for tank in state.tanks
    )
    return replace(state, tanks=tanks)


def belt_state(tile: Tile, cell: GridPos, position: Vec2 | None = None) -> SimulationState:
    """A state with one conveyor at ``cell`` and the player standing on it."""
    stage = make_stage(overrides={cell: tile}, player_cell=SPAWN_CELL)
    state = make_state(stage)
    return positioned(state, PLAYER_ID, position if position is not None else cell_pixel(cell))


def pad_stage(
    *,
    extra: dict[GridPos, Tile] | None = None,
    partner: GridPos = PARTNER_PAD,
) -> Stage:
    overrides: dict[GridPos, Tile] = {SOURCE_PAD: Tile.TELEPORT_PAD, partner: Tile.TELEPORT_PAD}
    overrides.update(extra or {})
    return make_stage(overrides=overrides)


def pad_state(position: Vec2, **kwargs: object) -> SimulationState:
    stage = pad_stage(**kwargs)  # type: ignore[arg-type]
    return positioned(make_state(stage), PLAYER_ID, position)


# -- centre-cell activation ---------------------------------------------------


def test_the_active_cell_is_the_one_holding_the_body_centre() -> None:
    """A 16px body never fits inside one 16px tile unless it is exactly aligned.

    The superseded draft of this change asked for whole-body containment after a 2px
    push, which no tank can satisfy. The accepted rule reads one point instead.
    """
    assert centre_cell(Vec2(0, 0), DEFAULT_RULES) == GridPos(0, 0)
    assert centre_cell(Vec2(8, 160), DEFAULT_RULES) == GridPos(1, 10)
    assert centre_cell(Vec2(14, 160), DEFAULT_RULES) == GridPos(1, 10)
    assert centre_cell(Vec2(TILE, 160), DEFAULT_RULES) == GridPos(1, 10)


# -- conveyors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tile", "delta"),
    [
        (Tile.CONVEYOR_N, (0, -SPEED)),
        (Tile.CONVEYOR_E, (SPEED, 0)),
        (Tile.CONVEYOR_S, (0, SPEED)),
        (Tile.CONVEYOR_W, (-SPEED, 0)),
    ],
)
def test_an_idle_tank_is_pushed_one_step_in_the_arrow_direction(
    tile: Tile, delta: tuple[int, int]
) -> None:
    """A conveyor moves a tank that issued no command at all."""
    cell = GridPos(8, 10)
    state = belt_state(tile, cell)
    start = state.tank(PLAYER_ID).position
    after, events = run(state, 1)
    assert after.tank(PLAYER_ID).position == start.translated(*delta)
    moved = only(events, TankMoved)
    assert moved.origin == start
    assert moved.position == after.tank(PLAYER_ID).position


def test_a_push_never_turns_the_tank() -> None:
    """A conveyor moves a body sideways; it does not aim the barrel."""
    state = belt_state(Tile.CONVEYOR_E, GridPos(8, 10))
    assert state.tank(PLAYER_ID).facing is Direction.UP
    after, events = run(state, 1)
    assert after.tank(PLAYER_ID).facing is Direction.UP
    assert only(events, TankMoved).facing is Direction.UP


def test_a_command_and_a_push_both_land_in_one_tick() -> None:
    """Riding a belt the way you are driving covers two steps, which is the point."""
    state = belt_state(Tile.CONVEYOR_S, GridPos(8, 10))
    start = state.tank(PLAYER_ID).position
    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.DOWN)]})
    assert after.tank(PLAYER_ID).position == start.translated(0, 2 * SPEED)
    assert [move.position for move in events_of(events, TankMoved)] == [
        start.translated(0, SPEED),
        start.translated(0, 2 * SPEED),
    ]


def test_a_push_against_the_command_cancels_the_commanded_step() -> None:
    """Both displacements happen; they simply happen to sum to nothing."""
    state = belt_state(Tile.CONVEYOR_N, GridPos(8, 10))
    start = state.tank(PLAYER_ID).position
    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.DOWN)]})
    assert after.tank(PLAYER_ID).position == start
    assert len(events_of(events, TankMoved)) == 2
    assert after.tank(PLAYER_ID).facing is Direction.DOWN


def test_the_cell_that_activates_is_the_one_the_tick_started_on() -> None:
    """Driving onto a belt buys no push until the next tick begins on it."""
    belt = GridPos(8, 11)
    state = belt_state(Tile.CONVEYOR_E, belt, position=cell_pixel(GridPos(8, 10)))
    start = state.tank(PLAYER_ID).position

    arriving, events = run(
        state, 4, commands=dict.fromkeys(range(4), [MoveCommand(PLAYER_ID, Direction.DOWN)])
    )
    assert arriving.tank(PLAYER_ID).position == start.translated(0, 4 * SPEED)
    assert centre_cell(arriving.tank(PLAYER_ID).position, DEFAULT_RULES) == belt
    assert len(events_of(events, TankMoved)) == 4

    riding, _ = run(arriving, 1)
    assert riding.tank(PLAYER_ID).position == start.translated(SPEED, 4 * SPEED)


def test_leaving_one_belt_for_another_does_not_chain() -> None:
    """One push per tick, decided by the starting cell. Two belts are still one push."""
    stage = make_stage(
        overrides={GridPos(8, 10): Tile.CONVEYOR_E, GridPos(9, 10): Tile.CONVEYOR_E},
        player_cell=SPAWN_CELL,
    )
    state = positioned(make_state(stage), PLAYER_ID, Vec2(134, 160))
    assert centre_cell(Vec2(134, 160), DEFAULT_RULES) == GridPos(8, 10)

    after, _ = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.RIGHT)]})
    assert centre_cell(Vec2(136, 160), DEFAULT_RULES) == GridPos(9, 10)
    assert after.tank(PLAYER_ID).position == Vec2(134 + 2 * SPEED, 160)


def test_a_blocked_push_is_reported_and_leaves_the_commanded_step_standing() -> None:
    """A wall cancels the push, never the move the player asked for."""
    stage = make_stage(
        overrides={GridPos(8, 10): Tile.CONVEYOR_E, GridPos(9, 10): Tile.STONE},
        player_cell=SPAWN_CELL,
    )
    state = positioned(make_state(stage), PLAYER_ID, cell_pixel(GridPos(8, 10)))
    start = state.tank(PLAYER_ID).position

    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert after.tank(PLAYER_ID).position == start.translated(0, -SPEED)
    assert only(events, TankMoved).position == start.translated(0, -SPEED)
    blocked = only(events, TankMoveBlocked)
    assert blocked.position == start.translated(0, -SPEED)
    assert blocked.facing is Direction.UP


def test_a_push_into_another_tank_is_cancelled_rather_than_shoving_it() -> None:
    state = belt_state(Tile.CONVEYOR_E, GridPos(8, 10))
    after, events = run(
        state,
        1,
        commands={0: [SpawnEnemyCommand(cell=GridPos(9, 10), variant=TankVariant.ENEMY_NORMAL)]},
    )
    neighbour = max(tank.entity_id for tank in after.tanks)
    assert after.tank(PLAYER_ID).position == cell_pixel(GridPos(8, 10))
    assert after.tank(neighbour).position == cell_pixel(GridPos(9, 10))
    assert only(events, TankMoveBlocked).tank_id == PLAYER_ID


def test_a_push_at_the_world_edge_is_reported_blocked_not_as_a_move_to_here() -> None:
    cell = GridPos(0, 10)
    state = belt_state(Tile.CONVEYOR_W, cell)
    start = state.tank(PLAYER_ID).position
    after, events = run(state, 1)
    assert after.tank(PLAYER_ID).position == start
    assert not events_of(events, TankMoved)
    assert only(events, TankMoveBlocked).position == start


def test_two_riders_contend_for_one_destination_by_ascending_identifier() -> None:
    """The lower identifier takes the pixels and the higher one is simply refused."""
    stage = make_stage(
        overrides={GridPos(7, 10): Tile.CONVEYOR_E, GridPos(8, 10): Tile.CONVEYOR_W},
        player_cell=SPAWN_CELL,
    )
    state = positioned(make_state(stage), PLAYER_ID, Vec2(130, 160))
    assert centre_cell(Vec2(130, 160), DEFAULT_RULES) == GridPos(8, 10)

    after, events = run(
        state,
        1,
        commands={0: [SpawnEnemyCommand(cell=GridPos(7, 10), variant=TankVariant.ENEMY_NORMAL)]},
    )
    follower = max(tank.entity_id for tank in after.tanks)
    assert after.tank(PLAYER_ID).position == Vec2(128, 160)
    assert after.tank(follower).position == cell_pixel(GridPos(7, 10))
    assert only(events, TankMoveBlocked).tank_id == follower


# -- teleport pads ------------------------------------------------------------


def test_a_stage_reports_its_pair_in_row_major_order() -> None:
    stage = make_stage(
        overrides={GridPos(9, 3): Tile.TELEPORT_PAD, GridPos(2, 3): Tile.TELEPORT_PAD}
    )
    assert stage.teleport_pads == (GridPos(2, 3), GridPos(9, 3))
    assert teleport_pads(stage.grid) == (GridPos(2, 3), GridPos(9, 3))


@pytest.mark.parametrize("count", [1, 3])
def test_a_stage_with_a_broken_pair_is_refused(count: int) -> None:
    overrides = {GridPos(index, 3): Tile.TELEPORT_PAD for index in range(count)}
    with pytest.raises(StageValidationError, match="teleport pads"):
        make_stage(overrides=overrides)


def test_a_stage_may_declare_no_pads_at_all() -> None:
    assert make_stage().teleport_pads is None


def test_entering_a_pad_transfers_at_the_same_offset() -> None:
    """The pixel offset inside the cell survives the jump, sign and all."""
    state = pad_state(Vec2(64, 168))
    assert centre_cell(Vec2(64, 168), DEFAULT_RULES) == GridPos(4, 11)

    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    entry = Vec2(64, 166)
    assert centre_cell(entry, DEFAULT_RULES) == SOURCE_PAD
    offset_y = entry.y - SOURCE_PAD.y * TILE
    assert offset_y == 6
    arrival = Vec2(PARTNER_PAD.x * TILE, PARTNER_PAD.y * TILE + offset_y)
    assert after.tank(PLAYER_ID).position == arrival
    assert [move.position for move in events_of(events, TankMoved)] == [entry, arrival]


def test_a_negative_offset_is_carried_rather_than_clamped() -> None:
    """A body straddling the pad's upper edge arrives straddling the partner's."""
    state = pad_state(Vec2(64, 150))
    assert centre_cell(Vec2(64, 150), DEFAULT_RULES) == GridPos(4, 9)

    after, _ = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.DOWN)]})
    assert after.tank(PLAYER_ID).position == Vec2(PARTNER_PAD.x * TILE, PARTNER_PAD.y * TILE - 8)


def test_standing_on_a_pad_does_not_jump_again() -> None:
    """Arrival is not an entry: a tank parked on a pad stays parked."""
    state = pad_state(cell_pixel(SOURCE_PAD))
    after, events = run(state, 3)
    assert after.tank(PLAYER_ID).position == cell_pixel(SOURCE_PAD)
    assert not events


def test_an_arrival_does_not_chain_into_a_second_jump() -> None:
    state = pad_state(Vec2(64, 168))
    arrived, _ = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    landed = arrived.tank(PLAYER_ID).position
    assert centre_cell(landed, DEFAULT_RULES) == PARTNER_PAD

    rested, events = run(arrived, 1)
    assert rested.tank(PLAYER_ID).position == landed
    assert not events


def test_leaving_and_re_entering_a_pad_jumps_again() -> None:
    state = pad_state(Vec2(64, 168))
    arrived, _ = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})

    off, _ = run(arrived, 1, commands={1: [MoveCommand(PLAYER_ID, Direction.DOWN)]})
    assert centre_cell(off.tank(PLAYER_ID).position, DEFAULT_RULES) == GridPos(12, 5)

    back, _ = run(off, 1, commands={2: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert centre_cell(back.tank(PLAYER_ID).position, DEFAULT_RULES) == SOURCE_PAD
    assert back.tank(PLAYER_ID).position == Vec2(64, 166)


def test_an_arrival_onto_blocking_terrain_leaves_the_tank_at_the_entry() -> None:
    state = pad_state(Vec2(64, 168), extra={GridPos(12, 5): Tile.STONE})
    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.UP)]})
    assert after.tank(PLAYER_ID).position == Vec2(64, 166)
    assert only(events, TankMoveBlocked).position == Vec2(64, 166)


def test_an_occupied_destination_leaves_the_tank_at_the_entry() -> None:
    state = pad_state(Vec2(64, 168))
    after, events = run(
        state,
        1,
        commands={
            0: [
                SpawnEnemyCommand(cell=PARTNER_PAD, variant=TankVariant.ENEMY_NORMAL),
                MoveCommand(PLAYER_ID, Direction.UP),
            ]
        },
    )
    assert after.tank(PLAYER_ID).position == Vec2(64, 166)
    assert only(events, TankMoveBlocked).tank_id == PLAYER_ID


def test_an_arrival_that_would_leave_the_playfield_is_refused() -> None:
    """An offset is never clamped, so a pad at the edge simply refuses the jump."""
    state = pad_state(Vec2(64, 150), partner=GridPos(15, 0))
    after, events = run(state, 1, commands={0: [MoveCommand(PLAYER_ID, Direction.DOWN)]})
    assert centre_cell(after.tank(PLAYER_ID).position, DEFAULT_RULES) == SOURCE_PAD
    assert only(events, TankMoveBlocked).facing is Direction.DOWN


def test_a_conveyor_can_feed_a_pad_in_one_tick() -> None:
    """Push then transport, in that order, for the same tank on the same tick."""
    belt = GridPos(4, 11)
    state = pad_state(Vec2(64, 168), extra={belt: Tile.CONVEYOR_N})
    assert centre_cell(Vec2(64, 168), DEFAULT_RULES) == belt

    after, events = run(state, 1)
    assert centre_cell(after.tank(PLAYER_ID).position, DEFAULT_RULES) == PARTNER_PAD
    assert [move.position for move in events_of(events, TankMoved)] == [
        Vec2(64, 166),
        Vec2(PARTNER_PAD.x * TILE, PARTNER_PAD.y * TILE + 6),
    ]


def test_a_spawn_onto_a_pad_does_not_teleport() -> None:
    """Phase 5 records its start cell after the spawn phases, so a spawn is not an entry."""
    state = make_state(pad_stage())
    after, _ = run(
        state,
        1,
        commands={0: [SpawnEnemyCommand(cell=SOURCE_PAD, variant=TankVariant.ENEMY_NORMAL)]},
    )
    spawned = max(after.tanks, key=lambda tank: tank.entity_id)
    assert spawned.position == cell_pixel(SOURCE_PAD)

    rested, _ = run(after, 1)
    assert rested.tank(spawned.entity_id).position == cell_pixel(SOURCE_PAD)


# -- what gimmick terrain must not touch --------------------------------------


def test_a_classic_stage_is_unchanged_by_the_new_phase_structure() -> None:
    """The regression that matters: no gimmick tiles, no behavioural difference."""
    state = make_state(make_stage(overrides={GridPos(8, 8): Tile.BRICK}))
    after, events = run(
        state, 6, commands=dict.fromkeys(range(6), [MoveCommand(PLAYER_ID, Direction.UP)])
    )
    assert after.tank(PLAYER_ID).position == Vec2(128, 160 - 6 * SPEED)
    assert len(events_of(events, TankMoved)) == 6
    assert not events_of(events, TankMoveBlocked)


def test_an_idle_classic_tick_still_produces_nothing() -> None:
    state = make_state()
    after, events = run(state, 4)
    assert not events
    assert after.tank(PLAYER_ID).position == state.tank(PLAYER_ID).position


def test_a_gimmick_run_replays_to_the_same_hash() -> None:
    """Same stage, same seed, same inputs: same canonical state, twice."""
    belt = GridPos(4, 11)
    stage = pad_stage(extra={belt: Tile.CONVEYOR_N})
    commands = {tick: [MoveCommand(PLAYER_ID, Direction.LEFT)] for tick in range(0, 12, 3)}
    first, _ = run(new_game(stage, seed=7), 12, commands=commands)
    second, _ = run(new_game(stage, seed=7), 12, commands=commands)
    assert state_hash(first) == state_hash(second)
    assert first.tick == 12
