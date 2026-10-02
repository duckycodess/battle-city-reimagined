"""The enemy-command seam: its contract, its guardrail, and what actually ships.

Two things are being shown. First, that the seam carries a real bot -- a driver backed by
``battle_city_ai``, which the client itself may not import -- so the campaign's enemy
behaviour is injected rather than absent by design. Second, that a driver cannot do
anything the campaign did not sanction, because what it returns is filtered rather than
trusted.
"""

from __future__ import annotations

from dataclasses import replace

from battle_city_client.campaign import (
    CampaignPhase,
    CampaignRules,
    CampaignRun,
    EnemyCommandDriver,
    IdleEnemyDriver,
    legal_enemy_commands,
)
from battle_city_client.intents import PlayerIntent
from battle_city_sim import (
    Faction,
    FireCommand,
    GridPos,
    MoveCommand,
    PowerupKind,
    RespawnCommand,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
)
from campaign_helpers import (
    FIRING,
    QUICK_INTERVAL,
    TEST_SEED,
    BotEnemyDriver,
    CountingDriver,
    OutlawDriver,
    firing_range_stage,
    observed,
    plan_of,
)

IDLE = PlayerIntent()
CELLS: tuple[tuple[int, int], ...] = ((7, 2), (2, 2), (12, 2))


def campaign(driver: EnemyCommandDriver | None = None, *, stages: int = 2) -> CampaignRun:
    rules = CampaignRules(spawn_interval_ticks=QUICK_INTERVAL)
    plan = plan_of(
        *(firing_range_stage(f"range-{index}", enemy_cells=CELLS) for index in range(stages)),
        quota=3,
        rules=rules,
    )
    return CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=driver)


# -- what ships ---------------------------------------------------------------


def test_the_default_driver_commands_nobody() -> None:
    """The shipped client's enemies hold position and never fire. Stated, not implied."""
    run = campaign().advance(3 * QUICK_INTERVAL, IDLE)
    assert isinstance(run.driver, IdleEnemyDriver)
    enemies = run.session.state.tanks_of(Faction.ENEMY)
    assert enemies, "they are still spawned; the campaign's cadence is real"
    assert run.session.state.projectiles == (), "and none of them has fired"

    positions = {tank.entity_id: tank.position for tank in enemies}
    later = run.advance(200, IDLE)
    for tank in later.session.state.tanks_of(Faction.ENEMY):
        assert later.session.state.tank(tank.entity_id).position == positions[tank.entity_id]


# -- the contract -------------------------------------------------------------


def test_a_driver_is_told_once_per_stage_and_once_per_tick() -> None:
    run = campaign(CountingDriver())
    assert isinstance(run.driver, CountingDriver)
    assert run.driver.stages == 1
    assert run.driver.ticks == 0

    run = run.advance(5, IDLE)
    assert isinstance(run.driver, CountingDriver)
    assert run.driver.ticks == 5
    assert run.driver.stages == 1


def test_a_restart_gives_the_driver_a_fresh_stage() -> None:
    """Per-stage memory must not survive an attempt that was discarded."""
    run = campaign(CountingDriver()).advance(5, IDLE).restarted_stage()
    assert isinstance(run.driver, CountingDriver)
    assert run.driver.stages == 2
    assert run.driver.ticks == 0


def test_a_driver_is_threaded_rather_than_mutated() -> None:
    """Advancing returns a successor; the run it was called on keeps its own driver."""
    before = campaign(CountingDriver())
    before.advance(10, IDLE)
    assert isinstance(before.driver, CountingDriver)
    assert before.driver.ticks == 0


# -- the guardrail ------------------------------------------------------------


def test_an_outlaw_driver_cannot_take_the_run_down() -> None:
    """Respawns, spawns, powerups and the player's own tank are all dropped silently."""
    run = campaign(OutlawDriver()).advance(3 * QUICK_INTERVAL, IDLE)
    assert observed(run.phase) is CampaignPhase.PLAYING
    assert run.session.state.powerups == ()
    assert run.session.state.tanks_of(Faction.ENEMY), "only the campaign's spawns landed"
    assert len(run.session.state.tanks_of(Faction.ENEMY)) == 3


def test_an_outlaw_driver_cannot_drive_the_player() -> None:
    run = campaign(OutlawDriver())
    start = run.session.player_tank
    assert start is not None
    moved = run.advance(60, IDLE)
    after = moved.session.player_tank
    assert after is not None
    assert after.position == start.position
    assert moved.session.state.projectiles == (), "nor fire for them"


def test_the_filter_keeps_one_move_and_one_fire_for_each_live_enemy() -> None:
    run = campaign().advance(2 * QUICK_INTERVAL, IDLE)
    state = run.session.state
    enemy = state.tanks_of(Faction.ENEMY)[0]
    player = state.tanks_of(Faction.PLAYER)[0]
    kept = legal_enemy_commands(
        (
            MoveCommand(tank_id=enemy.entity_id, direction=enemy.facing),
            MoveCommand(tank_id=enemy.entity_id, direction=enemy.facing),
            FireCommand(tank_id=enemy.entity_id),
            FireCommand(tank_id=enemy.entity_id),
            MoveCommand(tank_id=player.entity_id, direction=player.facing),
            FireCommand(tank_id=player.entity_id),
            MoveCommand(tank_id=9999, direction=enemy.facing),
            RespawnCommand(slot=1),
            SpawnEnemyCommand(cell=GridPos(x=1, y=1), variant=TankVariant.ENEMY_NORMAL),
            SpawnPowerupCommand(cell=GridPos(x=1, y=2), kind=PowerupKind.GATLING),
        ),
        state,
    )
    assert kept == (
        MoveCommand(tank_id=enemy.entity_id, direction=enemy.facing),
        FireCommand(tank_id=enemy.entity_id),
    )


def test_the_filter_keeps_submitted_order() -> None:
    """Order is not significant to the simulation, but a filter that reordered would hide
    a driver's intent from anyone reading a recorded tick."""
    run = campaign().advance(2 * QUICK_INTERVAL, IDLE)
    state = run.session.state
    enemies = state.tanks_of(Faction.ENEMY)
    assert len(enemies) >= 2
    submitted = tuple(FireCommand(tank_id=tank.entity_id) for tank in reversed(enemies))
    assert legal_enemy_commands(submitted, state) == submitted


def test_an_empty_suggestion_stays_empty() -> None:
    run = campaign()
    assert legal_enemy_commands((), run.session.state) == ()


# -- a real bot through the seam ----------------------------------------------


def test_a_bot_driven_enemy_moves_and_shoots() -> None:
    """``tests`` may import every package; the client may not. That is the whole point."""
    run = campaign(BotEnemyDriver()).advance(3 * QUICK_INTERVAL, IDLE)
    first = {tank.entity_id: tank.position for tank in run.session.state.tanks_of(Faction.ENEMY)}
    later = run.advance(300, IDLE)
    moved = any(
        tank.position != first.get(tank.entity_id)
        for tank in later.session.state.tanks_of(Faction.ENEMY)
    )
    assert moved, "bots steer their tanks"


def test_a_bot_driven_campaign_can_be_lost() -> None:
    """The limitation the shipped build has, removed by injecting a driver that fights."""
    rules = CampaignRules(spawn_interval_ticks=QUICK_INTERVAL)
    plan = plan_of(firing_range_stage("range-a", enemy_cells=CELLS), quota=6, rules=rules)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=BotEnemyDriver())
    for _ in range(6000):
        if run.phase is not CampaignPhase.PLAYING:
            break
        run = run.advance(1, IDLE)
    assert observed(run.phase) is CampaignPhase.FAILED
    assert run.outcome is not None


def test_a_bot_never_fires_at_the_base() -> None:
    """A fairness rule of the AI package, relied on by the campaign's failure test."""
    rules = CampaignRules(spawn_interval_ticks=QUICK_INTERVAL)
    stage = firing_range_stage("range-a", player_cell=(2, 12), enemy_cells=((7, 2),))
    plan = plan_of(stage, quota=2, rules=rules)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=BotEnemyDriver())
    run = run.advance(2000, IDLE)
    assert not run.session.state.base.destroyed


def test_a_driver_may_be_swapped_between_stages() -> None:
    """Nothing in the run holds the driver other than the field, so a mode may change it."""
    run = campaign(BotEnemyDriver(), stages=2).advance(10, IDLE)
    swapped = replace(run, driver=IdleEnemyDriver()).advance(10, IDLE)
    assert isinstance(swapped.driver, IdleEnemyDriver)
    assert observed(swapped.phase) is CampaignPhase.PLAYING


def test_the_shipped_default_is_the_idle_driver() -> None:
    assert isinstance(
        CampaignRun.start(plan_of(firing_range_stage(), quota=1)).driver, IdleEnemyDriver
    )


def test_drivers_satisfy_the_published_protocol() -> None:
    for driver in (IdleEnemyDriver(), BotEnemyDriver(), CountingDriver(), OutlawDriver()):
        assert isinstance(driver, EnemyCommandDriver)


def test_one_bot_in_an_open_column_beats_a_player_who_only_fires() -> None:
    """The seam's effect, stated as a number rather than as a feeling.

    The same stage the shipped build makes trivially winnable -- the player holds fire and
    the quota walks into it -- is lost in 223 ticks once something is steering. Pinning the
    tick makes the assertion a regression test rather than a mood.
    """
    rules = CampaignRules(
        spawn_interval_ticks=QUICK_INTERVAL, spawn_variants=(TankVariant.ENEMY_NORMAL,)
    )
    plan = plan_of(firing_range_stage("range-a"), quota=1, rules=rules)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=BotEnemyDriver())
    for _ in range(600):
        if run.phase is not CampaignPhase.PLAYING:
            break
        run = run.advance(1, FIRING)
    assert observed(run.phase) is CampaignPhase.FAILED
    assert run.session.state.tick == 223
    assert run.lives == 0
