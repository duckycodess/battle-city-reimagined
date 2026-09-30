"""Damage, lives, respawn and base destruction."""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_sim import (
    BaseDestroyed,
    Direction,
    FireCommand,
    GridPos,
    InvalidInputError,
    MoveCommand,
    PlayerLifeLost,
    PlayerRespawned,
    ProjectileEnded,
    ProjectileEndReason,
    RespawnCommand,
    RunEnded,
    RunOutcome,
    ScoreAwarded,
    ScoreReason,
    ShieldBroken,
    SimulationState,
    SpawnEnemyCommand,
    TankDestroyed,
    TankVariant,
    TickInput,
    Vec2,
    state_hash,
    step,
)
from helpers import events_of, make_stage, make_state, only, run

PLAYER_ID = 1
ENEMY_ID = 2


@pytest.mark.parametrize(
    ("variant", "points", "reason"),
    [
        (TankVariant.ENEMY_NORMAL, 100, ScoreReason.NORMAL_KILL),
        (TankVariant.ENEMY_UNSHIELDED, 200, ScoreReason.UNSHIELDED_KILL),
    ],
)
def test_a_player_shot_destroys_an_enemy_and_reports_points(
    variant: TankVariant, points: int, reason: ScoreReason
) -> None:
    state, events = run(
        make_state(),
        10,
        commands={0: [SpawnEnemyCommand(GridPos(8, 8), variant), FireCommand(PLAYER_ID)]},
    )
    destroyed = only(events, TankDestroyed)
    assert destroyed.tank_id == ENEMY_ID
    assert destroyed.variant is variant
    awarded = only(events, ScoreAwarded)
    assert (awarded.points, awarded.reason) == (points, reason)
    assert awarded.subject_tank_id == ENEMY_ID
    assert state.find_tank(ENEMY_ID) is None
    assert only(events, ProjectileEnded).reason is ProjectileEndReason.HIT_TANK


def test_a_shielded_enemy_loses_its_shield_before_it_dies() -> None:
    state, events = run(
        make_state(),
        20,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_SHIELDED),
                FireCommand(PLAYER_ID),
            ],
            10: [FireCommand(PLAYER_ID)],
        },
    )
    assert only(events, ShieldBroken).tank_id == ENEMY_ID
    awards = events_of(events, ScoreAwarded)
    assert [(item.points, item.reason) for item in awards] == [
        (50, ScoreReason.SHIELD_BROKEN),
        (200, ScoreReason.UNSHIELDED_KILL),
    ]
    assert only(events, TankDestroyed).variant is TankVariant.ENEMY_UNSHIELDED
    assert state.find_tank(ENEMY_ID) is None


def test_a_shield_break_leaves_the_enemy_alive_and_unshielded() -> None:
    state, _ = run(
        make_state(),
        10,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_SHIELDED),
                FireCommand(PLAYER_ID),
            ]
        },
    )
    assert state.tank(ENEMY_ID).variant is TankVariant.ENEMY_UNSHIELDED


def test_the_simulation_reports_points_but_keeps_no_total() -> None:
    state = make_state()
    assert not hasattr(state, "score")
    assert "score" not in {field for field in dir(state) if not field.startswith("_")}


def test_an_enemy_shot_costs_the_player_a_life() -> None:
    stage = make_stage(base=GridPos(1, 15))
    state, events = run(
        make_state(stage),
        10,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    lost = only(events, PlayerLifeLost)
    assert (lost.slot, lost.lives_remaining) == (1, 2)
    assert only(events, TankDestroyed).variant is TankVariant.PLAYER
    assert events_of(events, ScoreAwarded) == ()
    assert state.find_tank(PLAYER_ID) is None
    assert state.player(1).awaiting_respawn
    assert state.outcome is None


def test_an_explicit_respawn_returns_the_player_to_its_spawn() -> None:
    stage = make_stage(base=GridPos(1, 15))
    state, _ = run(
        make_state(stage),
        10,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    after, events = run(state, 1, commands={state.tick: [RespawnCommand(1)]})
    respawned = only(events, PlayerRespawned)
    assert respawned.slot == 1
    assert respawned.cell == GridPos(8, 10)
    tank = after.tank(respawned.tank_id)
    assert tank.position == Vec2(128, 160)
    assert tank.facing is Direction.UP
    assert tank.gatling_ticks == 0 and tank.invincible_ticks == 0
    assert after.player(1).lives == 2
    assert not after.player(1).awaiting_respawn


def test_losing_the_last_life_ends_the_run() -> None:
    stage = make_stage(base=GridPos(1, 15))
    start = make_state(stage)
    start = replace(start, players=(replace(start.player(1), lives=1),))
    state, events = run(
        start,
        10,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    assert only(events, PlayerLifeLost).lives_remaining == 0
    assert only(events, RunEnded).outcome is RunOutcome.PLAYERS_ELIMINATED
    assert state.outcome is RunOutcome.PLAYERS_ELIMINATED
    assert state.finished


def test_invincibility_makes_a_shot_pass_through_the_player() -> None:
    from battle_city_sim import PowerupKind, SpawnPowerupCommand

    stage = make_stage(base=GridPos(1, 15))
    state, events = run(
        make_state(stage),
        12,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_NORMAL, Direction.DOWN),
                SpawnPowerupCommand(GridPos(8, 10), PowerupKind.INVINCIBILITY),
            ],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    assert events_of(events, PlayerLifeLost) == ()
    assert events_of(events, TankDestroyed) == ()
    assert state.player(1).lives == 3
    assert state.tank(PLAYER_ID).invincible_ticks > 0


def test_a_hostile_shot_destroys_the_base_and_ends_the_run() -> None:
    state, events = run(
        make_state(),
        14,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 13), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    destroyed = only(events, BaseDestroyed)
    assert destroyed.cell == GridPos(8, 15)
    assert only(events, RunEnded).outcome is RunOutcome.BASE_DESTROYED
    assert state.base.destroyed
    assert state.outcome is RunOutcome.BASE_DESTROYED


def test_a_friendly_shot_is_absorbed_by_the_base() -> None:
    stage = make_stage(player_cell=GridPos(8, 13))
    state, events = run(
        make_state(stage),
        14,
        commands={
            0: [MoveCommand(PLAYER_ID, Direction.DOWN)],
            1: [FireCommand(PLAYER_ID)],
        },
    )
    assert events_of(events, BaseDestroyed) == ()
    assert not state.base.destroyed
    assert state.outcome is None
    assert only(events, ProjectileEnded).reason is ProjectileEndReason.TILE_BLOCKED


def test_a_finished_run_absorbs_further_ticks() -> None:
    state, _ = run(
        make_state(),
        14,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 13), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    assert state.finished
    before = state_hash(state)
    result = step(state, TickInput.of(state.tick, MoveCommand(PLAYER_ID, Direction.UP)))
    assert result.events == ()
    assert state_hash(result.state) == before
    assert result.state.tick == state.tick


def _awaiting_respawn_state() -> SimulationState:
    """Run until the player has lost a tank and is waiting for an explicit respawn."""
    stage = make_stage(base=GridPos(1, 15))
    state, _ = run(
        make_state(stage),
        10,
        commands={
            0: [SpawnEnemyCommand(GridPos(8, 8), TankVariant.ENEMY_NORMAL, Direction.DOWN)],
            1: [FireCommand(ENEMY_ID)],
        },
    )
    assert state.player(1).awaiting_respawn
    return state


def test_respawning_onto_an_occupied_spawn_is_rejected() -> None:
    """Placing a tank inside another one locks both in place, so the tick is refused.

    Reproduction from review: kill the player, park an enemy on the player spawn, then
    ask to respawn. Before the fix both tanks landed on the same pixels and twenty ticks
    of opposing move commands could not separate them.
    """
    state = _awaiting_respawn_state()
    occupied, _ = run(
        state,
        1,
        commands={
            state.tick: [SpawnEnemyCommand(GridPos(8, 10), TankVariant.ENEMY_NORMAL, Direction.UP)]
        },
    )
    blocker_id = occupied.tanks[-1].entity_id
    assert occupied.tank(blocker_id).position == Vec2(128, 160)

    before = state_hash(occupied)
    with pytest.raises(InvalidInputError, match="cannot respawn while its spawn cell is occupied"):
        step(occupied, TickInput.of(occupied.tick, RespawnCommand(1)))
    assert state_hash(occupied) == before
    assert occupied.player(1).awaiting_respawn
    assert occupied.player(1).tank_id is None


def test_a_later_respawn_succeeds_once_the_spawn_clears() -> None:
    state = _awaiting_respawn_state()
    occupied, _ = run(
        state,
        1,
        commands={
            state.tick: [SpawnEnemyCommand(GridPos(8, 10), TankVariant.ENEMY_NORMAL, Direction.UP)]
        },
    )
    blocker_id = occupied.tanks[-1].entity_id
    cleared, _ = run(
        occupied,
        8,
        commands=dict.fromkeys(
            range(occupied.tick, occupied.tick + 8),
            [MoveCommand(blocker_id, Direction.UP)],
        ),
    )
    assert cleared.tank(blocker_id).position == Vec2(128, 144)

    after, events = run(cleared, 1, commands={cleared.tick: [RespawnCommand(1)]})
    respawned = only(events, PlayerRespawned)
    assert respawned.slot == 1
    assert after.tank(respawned.tank_id).position == Vec2(128, 160)

    moved, _ = run(
        after,
        1,
        commands={
            after.tick: [
                MoveCommand(respawned.tank_id, Direction.DOWN),
                MoveCommand(blocker_id, Direction.LEFT),
            ]
        },
    )
    assert moved.tank(respawned.tank_id).position == Vec2(128, 162)
    assert moved.tank(blocker_id).position == Vec2(126, 144)


def test_a_respawn_is_refused_by_an_enemy_spawned_in_the_same_tick() -> None:
    """The verdict does not depend on where the commands sat in the tick input."""
    state = _awaiting_respawn_state()
    spawn_here = SpawnEnemyCommand(GridPos(8, 10), TankVariant.ENEMY_NORMAL, Direction.UP)
    for commands in (
        (spawn_here, RespawnCommand(1)),
        (RespawnCommand(1), spawn_here),
    ):
        with pytest.raises(
            InvalidInputError, match="cannot respawn while its spawn cell is occupied"
        ):
            step(state, TickInput(tick=state.tick, commands=commands))
    assert state.player(1).awaiting_respawn


def test_no_two_tanks_ever_share_a_position_through_a_respawn_cycle() -> None:
    state = _awaiting_respawn_state()
    after, _ = run(state, 1, commands={state.tick: [RespawnCommand(1)]})
    positions = [tank.position for tank in after.tanks]
    assert len(positions) == len(set(positions))
