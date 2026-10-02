"""Determinism: the same inputs produce the same campaign, byte for byte.

The acceptance signal the product specification states is that an identical seed, initial
state and fixed-tick input sequence produce identical simulation state hashes. A campaign
adds three things that could break it -- its own random draws, a per-stage seed, and a
driver -- so each is pinned here.
"""

from __future__ import annotations

from dataclasses import replace

from battle_city_client.campaign import (
    CAMPAIGN_SEEDING_VERSION,
    CampaignRules,
    CampaignRun,
    stage_simulation_seed,
    stage_spawn_rng,
)
from battle_city_client.intents import PlayerIntent
from battle_city_sim import TankVariant, state_hash
from campaign_helpers import (
    FIRING,
    QUICK_INTERVAL,
    TEST_SEED,
    BotEnemyDriver,
    advance_until,
    firing_range_stage,
    plan_of,
)

CELLS: tuple[tuple[int, int], ...] = ((7, 2), (2, 2), (12, 2))


def run(seed: int = TEST_SEED, *, stage_index: int = 0, **start: object) -> CampaignRun:
    rules = CampaignRules(spawn_interval_ticks=QUICK_INTERVAL)
    plan = plan_of(
        *(firing_range_stage(f"range-{index}", enemy_cells=CELLS) for index in range(3)),
        quota=3,
        rules=rules,
    )
    return CampaignRun.start(
        plan,
        seed=seed,
        rules=rules,
        stage_index=stage_index,
        **start,  # type: ignore[arg-type]
    )


def digest(campaign: CampaignRun) -> tuple[str, int, int]:
    """Everything a replay of a campaign has to reproduce."""
    return state_hash(campaign.session.state), campaign.score, campaign.lives


# -- the run ------------------------------------------------------------------


def test_one_seed_produces_one_campaign() -> None:
    first = advance_until(run(), limit=400)
    second = advance_until(run(), limit=400)
    assert digest(first) == digest(second)
    assert first.phase is second.phase


def test_a_different_seed_produces_a_different_one() -> None:
    """Not merely a different hash: the spawn draws must actually differ."""
    first = run().advance(3 * QUICK_INTERVAL, PlayerIntent())
    second = run(TEST_SEED + 1).advance(3 * QUICK_INTERVAL, PlayerIntent())
    first_cells = tuple(tank.position for tank in first.session.state.tanks)
    second_cells = tuple(tank.position for tank in second.session.state.tanks)
    assert first_cells != second_cells


def test_two_stages_of_one_campaign_draw_differently() -> None:
    """Otherwise every stage of a run would spawn in the same order on the same cells."""
    first = run(stage_index=0).advance(3 * QUICK_INTERVAL, PlayerIntent())
    second = run(stage_index=1).advance(3 * QUICK_INTERVAL, PlayerIntent())
    assert [tank.position for tank in first.session.state.tanks] != [
        tank.position for tank in second.session.state.tanks
    ]


def test_restarting_a_stage_replays_it() -> None:
    """The point of deriving a per-stage seed rather than threading one generator."""
    first = run().advance(2 * QUICK_INTERVAL, FIRING)
    restarted = first.restarted_stage()
    assert digest(restarted) == digest(run())
    assert digest(restarted.advance(2 * QUICK_INTERVAL, FIRING)) == digest(first)


def test_restarting_a_campaign_replays_it_from_the_beginning() -> None:
    later = advance_until(run(stage_index=2), limit=400)
    assert later.stage_index == 2
    assert digest(later.restarted()) == digest(run())
    assert later.restarted().stage_index == 0


def test_a_bot_driven_campaign_is_deterministic_too() -> None:
    """The seam does not leak nondeterminism: the bot's stream is the simulation's."""
    first = run(driver=BotEnemyDriver()).advance(400, FIRING)
    second = run(driver=BotEnemyDriver()).advance(400, FIRING)
    assert digest(first) == digest(second)


def test_advancing_in_one_call_matches_advancing_tick_by_tick() -> None:
    """A caller's frame pacing must never reach the simulation."""
    bulk = run().advance(240, FIRING)
    stepwise = run()
    for _ in range(240):
        stepwise = stepwise.advance(1, FIRING)
    assert digest(bulk) == digest(stepwise)


def test_advancing_does_not_mutate_the_run_it_was_given() -> None:
    before = run()
    snapshot = digest(before)
    before.advance(200, FIRING)
    assert digest(before) == snapshot


# -- the derivation -----------------------------------------------------------


def test_seed_derivation_is_a_pure_function_of_its_arguments() -> None:
    assert stage_simulation_seed(seed=7, stage_index=2) == stage_simulation_seed(
        seed=7, stage_index=2
    )
    assert stage_spawn_rng(seed=7, stage_index=2) == stage_spawn_rng(seed=7, stage_index=2)


def test_the_two_streams_of_one_stage_are_not_the_same_stream() -> None:
    """A shared position would correlate a stage's spawns with its simulation draws."""
    assert stage_spawn_rng(seed=7, stage_index=0).state != stage_simulation_seed(
        seed=7, stage_index=0
    )


def test_each_stage_and_each_seed_gets_its_own_position() -> None:
    positions = {
        (seed, index): stage_spawn_rng(seed=seed, stage_index=index).state
        for seed in (1, 2, 3)
        for index in range(4)
    }
    assert len(set(positions.values())) == len(positions)


def test_the_seeding_scheme_is_versioned() -> None:
    """Changing the derivation changes every spawn for an existing seed."""
    assert CAMPAIGN_SEEDING_VERSION == 1


# -- the rules a run was started with -----------------------------------------


def test_only_the_life_count_differs_between_campaign_and_stage_rules() -> None:
    """Lives reach the simulation through a per-stage rules copy and nothing else does."""
    campaign = run()
    assert replace(campaign.session.rules, starting_lives=campaign.sim_rules.starting_lives) == (
        campaign.sim_rules
    )
    assert campaign.session.rules.starting_lives == campaign.rules.starting_lives


def test_spawned_variants_stay_inside_the_rules() -> None:
    spawned = run().advance(3 * QUICK_INTERVAL, PlayerIntent())
    variants = {tank.variant for tank in spawned.session.state.tanks} - {TankVariant.PLAYER}
    assert variants <= set(spawned.rules.spawn_variants)
