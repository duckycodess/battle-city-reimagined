"""Powerups, the fire gate, and the effect timers."""

from __future__ import annotations

from battle_city_sim import (
    DEFAULT_RULES,
    DespawnPowerupCommand,
    ExtraLifeGranted,
    FireCommand,
    GridPos,
    PowerupCollected,
    PowerupDespawned,
    PowerupExpired,
    PowerupKind,
    PowerupSpawned,
    ProjectileFired,
    SpawnPowerupCommand,
)
from helpers import events_of, make_state, only, run

PLAYER_ID = 1
FAR_CELL = GridPos(2, 2)


def test_a_spawned_powerup_waits_until_a_tank_reaches_it() -> None:
    state, events = run(
        make_state(), 5, commands={0: [SpawnPowerupCommand(FAR_CELL, PowerupKind.GATLING)]}
    )
    spawned = only(events, PowerupSpawned)
    assert (spawned.kind, spawned.cell) == (PowerupKind.GATLING, FAR_CELL)
    assert events_of(events, PowerupCollected) == ()
    assert len(state.powerups) == 1
    assert state.tank(PLAYER_ID).gatling_ticks == 0


def test_a_powerup_on_the_tank_is_collected_at_once() -> None:
    state, events = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.GATLING)]},
    )
    collected = only(events, PowerupCollected)
    assert (collected.kind, collected.tank_id, collected.slot) == (
        PowerupKind.GATLING,
        PLAYER_ID,
        1,
    )
    assert state.powerups == ()
    assert state.tank(PLAYER_ID).gatling_ticks == DEFAULT_RULES.powerup_duration_ticks


def test_an_extra_life_powerup_grants_a_life() -> None:
    state, events = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.EXTRA_LIFE)]},
    )
    assert only(events, ExtraLifeGranted).lives == DEFAULT_RULES.starting_lives + 1
    assert state.player(1).lives == DEFAULT_RULES.starting_lives + 1


def test_an_uncollected_powerup_can_be_despawned() -> None:
    state, _ = run(
        make_state(), 1, commands={0: [SpawnPowerupCommand(FAR_CELL, PowerupKind.GATLING)]}
    )
    powerup_id = state.powerups[0].entity_id
    after, events = run(state, 1, commands={state.tick: [DespawnPowerupCommand(powerup_id)]})
    assert only(events, PowerupDespawned).powerup_id == powerup_id
    assert after.powerups == ()


def test_gatling_and_invincibility_keep_separate_timers() -> None:
    state, _ = run(
        make_state(),
        2,
        commands={
            0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.GATLING)],
            1: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.INVINCIBILITY)],
        },
    )
    tank = state.tank(PLAYER_ID)
    assert tank.gatling_ticks == DEFAULT_RULES.powerup_duration_ticks - 1
    assert tank.invincible_ticks == DEFAULT_RULES.powerup_duration_ticks


def test_a_powerup_expires_after_its_declared_duration() -> None:
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.INVINCIBILITY)]},
    )
    almost, events = run(state, DEFAULT_RULES.powerup_duration_ticks - 1)
    assert almost.tank(PLAYER_ID).invincible_ticks == 1
    assert events_of(events, PowerupExpired) == ()

    after, events = run(almost, 1)
    assert only(events, PowerupExpired).kind is PowerupKind.INVINCIBILITY
    assert after.tank(PLAYER_ID).invincible_ticks == 0


def test_the_fire_gate_allows_one_live_projectile() -> None:
    state, events = run(
        make_state(),
        2,
        commands={0: [FireCommand(PLAYER_ID)], 1: [FireCommand(PLAYER_ID)]},
    )
    assert len(events_of(events, ProjectileFired)) == 1
    assert len(state.projectiles) == 1


def test_the_gate_reopens_once_the_projectile_is_gone() -> None:
    stage_state = make_state()
    state, events = run(
        stage_state,
        90,
        commands={0: [FireCommand(PLAYER_ID)], 60: [FireCommand(PLAYER_ID)]},
    )
    assert len(events_of(events, ProjectileFired)) == 2
    assert len(state.projectiles) == 1


def test_gatling_fires_on_its_own_cadence() -> None:
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.GATLING)]},
    )
    _, events = run(state, 20)
    fired = events_of(events, ProjectileFired)
    assert len(fired) == 20 // DEFAULT_RULES.gatling_interval_ticks


def test_gatling_bypasses_the_fire_gate() -> None:
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.GATLING)]},
    )
    after, events = run(
        state,
        3,
        commands={
            1: [FireCommand(PLAYER_ID)],
            2: [FireCommand(PLAYER_ID)],
            3: [FireCommand(PLAYER_ID)],
        },
    )
    assert len(events_of(events, ProjectileFired)) == 3
    assert len(after.projectiles) == 3


def test_a_tank_fires_at_most_once_per_tick() -> None:
    """Gatling auto-fire and an explicit command on the same tick produce one shot."""
    state, _ = run(
        make_state(),
        1,
        commands={0: [SpawnPowerupCommand(GridPos(8, 10), PowerupKind.GATLING)]},
    )
    _, events = run(state, 5, commands={5: [FireCommand(PLAYER_ID)]})
    fired_on_tick_five = [
        item for item in events_of(events, ProjectileFired) if item.owner_id == PLAYER_ID
    ]
    assert len(fired_on_tick_five) == 1
