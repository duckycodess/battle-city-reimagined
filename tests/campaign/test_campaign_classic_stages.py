"""The campaign over the three converted classic layouts, loaded from the bundled pack.

These are the regression fixtures the content specification calls them, driven by the
campaign rather than inspected. What they assert is what only the real layouts can say:
that stage 1, 2 and 3 release 5, 7 and 9 enemies, one on tick 0 and one every 600 ticks
after, onto the enemy cells those files declare and no others.

Combat is asserted elsewhere, on stages built for it. Neither a bot-driven player nor
bot-driven enemies reliably find each other in these mazes -- 120,000 ticks of a veteran
bot driving the player on ``classic-01`` scores nothing -- and a campaign test that waited
on pathfinding would be measuring the wrong package. See ``campaign_helpers``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_client.campaign import (
    DEFAULT_CAMPAIGN_RULES,
    CampaignPhase,
    CampaignRules,
    CampaignRun,
    StagePlan,
    campaign_plan,
)
from battle_city_client.intents import PlayerIntent
from battle_city_client.stage_adapter import bundled_stage_catalog
from battle_city_sim import CLASSIC_GRID_SIZE, EnemySpawned, TankVariant
from campaign_helpers import (
    TEST_SEED,
    BotEnemyDriver,
    advance_while_spawning,
    observed,
)

CLASSIC_IDS: tuple[str, ...] = ("classic-01", "classic-02", "classic-03")
CLASSIC_QUOTAS: tuple[int, ...] = (5, 7, 9)
IDLE = PlayerIntent()


@pytest.fixture(scope="module")
def plan() -> tuple[StagePlan, ...]:
    """The bundled classic pack, adapted and planned exactly as the client does it."""
    return campaign_plan(bundled_stage_catalog())


# -- the plan -----------------------------------------------------------------


def test_the_bundled_pack_plans_the_three_classic_stages_in_order(
    plan: tuple[StagePlan, ...],
) -> None:
    assert tuple(stage.level_id for stage in plan) == CLASSIC_IDS
    assert tuple(stage.index for stage in plan) == (0, 1, 2)
    for stage in plan:
        assert stage.stage.grid.width == CLASSIC_GRID_SIZE
        assert stage.stage.grid.height == CLASSIC_GRID_SIZE


def test_the_classic_quotas_are_five_seven_and_nine(plan: tuple[StagePlan, ...]) -> None:
    """Historical ``(level * 2) + 3``. The converted levels declare no waves, so this is
    the fallback rule reproducing the source rather than data restating it."""
    assert tuple(stage.enemy_quota for stage in plan) == CLASSIC_QUOTAS
    for entry in bundled_stage_catalog():
        assert entry.waves == (), "the historical stage data carries no wave information"


# -- cadence on the real layouts ----------------------------------------------


@pytest.mark.parametrize(("index", "quota"), list(enumerate(CLASSIC_QUOTAS)))
def test_each_classic_stage_releases_its_quota_on_the_historical_cadence(
    plan: tuple[StagePlan, ...], index: int, quota: int
) -> None:
    """Ticks 0, 600, 1200, ... with the default rules, on the stage's own spawn cells.

    Driven with bot enemies because they vacate the spawn cells. The shipped idle driver
    does not, which is the subject of the next test.
    """
    run = CampaignRun.start(
        plan, seed=TEST_SEED, stage_index=index, driver=BotEnemyDriver(profile_name="soldier")
    )
    assert run.pending_enemies == quota

    arrivals: list[tuple[int, EnemySpawned]] = []
    for _ in range(quota * DEFAULT_CAMPAIGN_RULES.spawn_interval_ticks + 1):
        tick = run.session.state.tick
        run = run.advance(1, IDLE)
        arrivals.extend(
            (tick, event) for event in run.last_events if isinstance(event, EnemySpawned)
        )
        if run.pending_enemies == 0:
            break

    assert run.pending_enemies == 0
    assert len(arrivals) == quota
    assert [tick for tick, _ in arrivals] == [
        step * DEFAULT_CAMPAIGN_RULES.spawn_interval_ticks for step in range(quota)
    ]


@pytest.mark.parametrize("index", range(3))
def test_spawns_land_only_on_the_cells_the_level_declared(
    plan: tuple[StagePlan, ...], index: int
) -> None:
    stage = plan[index]
    run = CampaignRun.start(
        plan, seed=TEST_SEED, stage_index=index, driver=BotEnemyDriver(profile_name="soldier")
    )
    seen: list[EnemySpawned] = []
    for _ in range(stage.enemy_quota * DEFAULT_CAMPAIGN_RULES.spawn_interval_ticks + 1):
        run = run.advance(1, IDLE)
        seen.extend(event for event in run.last_events if isinstance(event, EnemySpawned))
        if run.pending_enemies == 0:
            break
    assert len(seen) == stage.enemy_quota
    assert {event.cell for event in seen} <= set(stage.stage.enemy_spawns)
    assert {event.variant for event in seen} <= set(DEFAULT_CAMPAIGN_RULES.spawn_variants)
    assert TankVariant.ENEMY_UNSHIELDED not in {event.variant for event in seen}


@pytest.mark.parametrize("index", range(3))
def test_the_shipped_driver_stalls_a_classic_stage_once_its_cells_are_full(
    plan: tuple[StagePlan, ...], index: int
) -> None:
    """The shipped build's honest limitation, pinned so it cannot change unnoticed.

    Enemies that never move never leave their spawn cell, so a classic stage releases
    exactly as many enemies as it has declared cells and then waits for the player. The
    quota is preserved, the campaign does not fail, and nothing illegal is submitted.
    """
    stage = plan[index]
    cells = len(stage.stage.enemy_spawns)
    assert cells < stage.enemy_quota, "otherwise this stage could not demonstrate a stall"

    run = advance_while_spawning(
        CampaignRun.start(plan, seed=TEST_SEED, stage_index=index), limit=4000
    )
    assert run.enemies_alive == cells
    assert run.pending_enemies == stage.enemy_quota - cells
    assert observed(run.phase) is CampaignPhase.PLAYING
    assert run.next_spawn_tick == run.session.state.tick


# -- progression across the three ---------------------------------------------


def test_a_campaign_runs_through_all_three_classic_stages(plan: tuple[StagePlan, ...]) -> None:
    """Carry, transition and completion over the real layouts.

    The quota is set to zero here, which is the one thing these stages cannot supply: a
    clear needs the enemies destroyed, and destroying them in three different mazes needs
    a pathfinder, not a campaign. What is under test is the progression -- that stage 2
    follows stage 1, that lives and score survive the transition, and that clearing the
    third completes the campaign rather than hanging, as the historical runtime did.
    """
    empty = tuple(replace(stage, enemy_quota=0) for stage in plan)
    run = CampaignRun.start(empty, seed=TEST_SEED)
    visited: list[str] = []
    for _ in range(len(empty)):
        visited.append(run.session.state.stage_id)
        run = run.advance(2, PlayerIntent())
        if run.phase is CampaignPhase.STAGE_CLEARED:
            assert run.lives == DEFAULT_CAMPAIGN_RULES.starting_lives
            run = run.advanced_stage()
    assert visited == list(CLASSIC_IDS)
    assert observed(run.phase) is CampaignPhase.COMPLETED
    assert run.outcome is None
    assert run.stage_number == 3


def test_a_campaign_may_begin_at_any_classic_stage(plan: tuple[StagePlan, ...]) -> None:
    """With no saved progress, the stage list is the checkpoint."""
    for index, level_id in enumerate(CLASSIC_IDS):
        run = CampaignRun.start(plan, seed=TEST_SEED, stage_index=index)
        assert run.session.state.stage_id == level_id
        assert run.pending_enemies == CLASSIC_QUOTAS[index]
        assert run.score == 0
        assert run.lives == DEFAULT_CAMPAIGN_RULES.starting_lives


def test_a_classic_campaign_is_reproducible(plan: tuple[StagePlan, ...]) -> None:
    first = CampaignRun.start(plan, seed=99).advance(1500, PlayerIntent(fire=True))
    second = CampaignRun.start(plan, seed=99).advance(1500, PlayerIntent(fire=True))
    assert first.session.state == second.session.state
    assert first.score == second.score


def test_the_classic_pack_honours_an_overridden_cadence(plan: tuple[StagePlan, ...]) -> None:
    """Rules are data: a pack or a mode may state its own without forking the campaign."""
    rules = CampaignRules(spawn_interval_ticks=30)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=BotEnemyDriver())
    arrivals = []
    for _ in range(90):
        tick = run.session.state.tick
        run = run.advance(1, IDLE)
        if any(isinstance(event, EnemySpawned) for event in run.last_events):
            arrivals.append(tick)
    assert arrivals == [0, 30, 60]
