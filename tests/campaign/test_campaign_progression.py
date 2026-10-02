"""The campaign's own rules: cadence, score, clear, victory, defeat, carry and restart.

Every run here is on a stage built for the assertion -- an open column with the enemy spawn
in the player's line of fire -- so a kill is a function of the tick count and nothing else.
The classic layouts are exercised in :mod:`test_campaign_classic_stages`, which asserts
what only they can.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from battle_city_client.campaign import CampaignPhase, CampaignRules, CampaignRun
from battle_city_client.intents import PlayerIntent
from battle_city_sim import (
    DEFAULT_RULES,
    EnemySpawned,
    RunOutcome,
    ScoreAwarded,
    ScoreReason,
    TankVariant,
)
from campaign_helpers import (
    FIRING,
    QUICK_INTERVAL,
    TEST_SEED,
    ScriptedEnemyDriver,
    advance_until,
    firing_range_stage,
    observed,
    plan_of,
    session_with_outcome,
)

IDLE = PlayerIntent()


def rules_for(variant: TankVariant = TankVariant.ENEMY_NORMAL) -> CampaignRules:
    """Short cadence and one spawnable variant, so a score is a number a test can state."""
    return CampaignRules(spawn_interval_ticks=QUICK_INTERVAL, spawn_variants=(variant,))


ONE_CELL: tuple[tuple[int, int], ...] = ((7, 2),)
"""One spawn cell, in the player's line of fire. An idle enemy standing on it blocks it."""

OPEN_CELLS: tuple[tuple[int, int], ...] = ((7, 2), (2, 2), (12, 2))
"""Three spawn cells, so an idle enemy on one does not stall the stage's cadence."""


def run_of(
    *stage_ids: str,
    quota: int = 2,
    variant: TankVariant = TankVariant.ENEMY_NORMAL,
    cells: tuple[tuple[int, int], ...] = ONE_CELL,
    **start: object,
) -> CampaignRun:
    rules = rules_for(variant)
    stages = tuple(firing_range_stage(stage_id, enemy_cells=cells) for stage_id in stage_ids)
    plan = plan_of(*stages, quota=quota, rules=rules)
    return CampaignRun.start(plan, seed=TEST_SEED, rules=rules, **start)  # type: ignore[arg-type]


# -- cadence ------------------------------------------------------------------


def test_the_first_enemy_arrives_on_tick_zero() -> None:
    """Historical: the spawn test was ``tick % 600 == 0`` and the counter started at zero."""
    run = run_of("range-a", quota=3).advance(1, IDLE)
    assert [event for event in run.last_events if isinstance(event, EnemySpawned)]
    assert run.enemies_alive == 1
    assert run.pending_enemies == 2


def test_enemies_then_arrive_one_interval_apart() -> None:
    run = run_of("range-a", quota=3, cells=OPEN_CELLS)
    arrivals: list[int] = []
    for _ in range(3 * QUICK_INTERVAL):
        tick = run.session.state.tick
        run = run.advance(1, IDLE)
        if any(isinstance(event, EnemySpawned) for event in run.last_events):
            arrivals.append(tick)
    assert arrivals == [0, QUICK_INTERVAL, 2 * QUICK_INTERVAL]


def test_the_quota_is_spent_and_no_more() -> None:
    run = run_of("range-a", quota=2, cells=OPEN_CELLS)
    total = 0
    for _ in range(10 * QUICK_INTERVAL):
        run = run.advance(1, IDLE)
        total += sum(1 for event in run.last_events if isinstance(event, EnemySpawned))
    assert total == 2
    assert run.pending_enemies == 0


def test_a_blocked_spawn_keeps_its_quota_and_retries_every_tick() -> None:
    """The stage declares one spawn cell, and an idle enemy never leaves it.

    This is the shipped build's honest behaviour, not a contrived case: the default driver
    commands nobody, so the second enemy of a one-cell stage waits for the player to clear
    the doorway. What must not happen is the quota being spent on a spawn that never
    landed.
    """
    run = run_of("range-a", quota=3).advance(10 * QUICK_INTERVAL, IDLE)
    assert run.enemies_alive == 1
    assert run.pending_enemies == 2, "a refused spawn must not be counted as one"
    assert run.next_spawn_tick == run.session.state.tick, "and it must be retried at once"
    assert observed(run.phase) is CampaignPhase.PLAYING


def test_a_spawn_lands_only_on_a_declared_cell_with_a_spawnable_variant() -> None:
    stage = firing_range_stage("range-a", enemy_cells=((7, 2), (2, 2), (12, 2)))
    rules = rules_for()
    plan = plan_of(stage, quota=3, rules=replace(rules, spawn_variants=DEFAULT_SPAWNABLE))
    run = CampaignRun.start(
        plan, seed=TEST_SEED, rules=replace(rules, spawn_variants=DEFAULT_SPAWNABLE)
    )
    seen: list[EnemySpawned] = []
    for _ in range(4 * QUICK_INTERVAL):
        run = run.advance(1, IDLE)
        seen.extend(event for event in run.last_events if isinstance(event, EnemySpawned))
    assert len(seen) == 3
    assert all(event.cell in stage.enemy_spawns for event in seen)
    assert all(event.variant in DEFAULT_SPAWNABLE for event in seen)


DEFAULT_SPAWNABLE = (TankVariant.ENEMY_NORMAL, TankVariant.ENEMY_SHIELDED)


# -- score --------------------------------------------------------------------


def test_the_score_is_the_sum_of_what_the_simulation_reported() -> None:
    """The campaign adds up ``ScoreAwarded``; it never re-derives a point value."""
    run = advance_until(run_of("range-a", quota=2), limit=600)
    awarded = [event for event in run.last_events if isinstance(event, ScoreAwarded)]
    assert run.score == 200
    assert {event.reason for event in awarded} == {ScoreReason.NORMAL_KILL}


def test_a_shielded_enemy_is_worth_fifty_then_two_hundred() -> None:
    """250 across two hits, which is the historical total and the simulation's values."""
    run = advance_until(run_of("range-a", quota=1, variant=TankVariant.ENEMY_SHIELDED), limit=600)
    assert run.score == DEFAULT_RULES.score_shield_break + DEFAULT_RULES.score_unshielded_kill
    assert run.score == 250


def test_the_score_survives_a_stage_transition() -> None:
    run = advance_until(run_of("range-a", "range-b", quota=2), limit=600)
    assert observed(run.phase) is CampaignPhase.STAGE_CLEARED
    assert run.score == 200
    second = run.advanced_stage()
    assert second.stage_index == 1
    assert second.score == 200
    assert advance_until(second, limit=600).score == 400


# -- clearing a stage ---------------------------------------------------------


def test_a_stage_clears_when_nothing_is_queued_alive_or_in_flight() -> None:
    run = advance_until(run_of("range-a", quota=2), limit=600)
    assert observed(run.phase) is CampaignPhase.COMPLETED
    assert run.pending_enemies == 0
    assert run.enemies_alive == 0
    assert run.session.state.projectiles == ()


def test_the_players_own_shot_holds_the_stage_open() -> None:
    """The third term of the historical condition, and the one that reads like a bug.

    With two shots allowed in flight the last enemy dies to the leading one while the
    trailing one is still travelling. The stage must not end there: that shot can still
    reach the base.
    """
    rules = rules_for()
    plan = plan_of(firing_range_stage("range-a"), quota=1, rules=rules)
    run = CampaignRun.start(
        plan,
        seed=TEST_SEED,
        rules=rules,
        sim_rules=replace(DEFAULT_RULES, max_projectiles_per_tank=2),
    )
    while run.enemies_remaining > 0 and run.session.state.tick < 600:
        run = run.advance(1, FIRING)

    assert run.enemies_remaining == 0
    assert run.session.state.projectiles != (), "the trailing shot is still travelling"
    assert observed(run.phase) is CampaignPhase.PLAYING

    run = advance_until(run, intent=IDLE, limit=600)
    assert observed(run.phase) is CampaignPhase.COMPLETED
    assert run.session.state.projectiles == ()


# -- victory and defeat -------------------------------------------------------


def test_clearing_the_last_stage_completes_the_campaign() -> None:
    """The historical win state is unreachable; this is the documented deviation."""
    run = advance_until(run_of("range-a", "range-b", quota=1), limit=600)
    assert observed(run.phase) is CampaignPhase.STAGE_CLEARED
    run = advance_until(run.advanced_stage(), limit=600)
    assert observed(run.phase) is CampaignPhase.COMPLETED
    assert run.outcome is None, "victory is a campaign phase, never a simulation outcome"


def test_a_cleared_campaign_does_not_advance_past_its_last_stage() -> None:
    run = advance_until(run_of("range-a", quota=1), limit=600)
    assert observed(run.phase) is CampaignPhase.COMPLETED
    assert run.advanced_stage() is run


def test_spending_the_last_life_fails_the_campaign() -> None:
    run = advance_until(
        run_of("range-a", quota=3, driver=ScriptedEnemyDriver()), intent=IDLE, limit=4000
    )
    assert observed(run.phase) is CampaignPhase.FAILED
    assert run.outcome is RunOutcome.PLAYERS_ELIMINATED
    assert run.lives == 0


def test_losing_the_base_fails_the_campaign() -> None:
    """Driven by a scripted driver: a fair bot never fires at the base, by its own spec."""
    rules = rules_for()
    stage = firing_range_stage("range-a", player_cell=(2, 12), enemy_cells=((7, 2),))
    plan = plan_of(stage, quota=3, rules=rules)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=ScriptedEnemyDriver())
    run = advance_until(run, intent=IDLE, limit=4000)
    assert observed(run.phase) is CampaignPhase.FAILED
    assert run.outcome is RunOutcome.BASE_DESTROYED
    assert run.session.state.base.destroyed


def test_a_finished_campaign_absorbs_further_ticks() -> None:
    run = advance_until(run_of("range-a", quota=1), limit=600)
    tick = run.session.state.tick
    assert run.advance(500, FIRING).session.state.tick == tick


# -- lives --------------------------------------------------------------------


def test_a_campaign_begins_with_the_rules_starting_lives() -> None:
    rules = replace(rules_for(), starting_lives=5)
    plan = plan_of(firing_range_stage("range-a"), quota=1, rules=rules)
    assert CampaignRun.start(plan, seed=TEST_SEED, rules=rules).lives == 5


def test_lives_carry_into_the_next_stage() -> None:
    """Historical: ``init_positions`` without ``restart`` does not touch the life count."""
    rules = rules_for()
    plan = plan_of(
        firing_range_stage("range-a"), firing_range_stage("range-b"), quota=1, rules=rules
    )
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=ScriptedEnemyDriver())
    # Let the scripted enemy take exactly one life, then stop it and clear the stage.
    while run.lives == rules.starting_lives and run.session.state.tick < 2000:
        run = run.advance(1, IDLE)
    assert run.lives == rules.starting_lives - 1
    run = replace(run, driver=ScriptedEnemyDriver(direction=None, fire=False))
    run = advance_until(run, limit=2000)
    assert observed(run.phase) is CampaignPhase.STAGE_CLEARED
    assert run.advanced_stage().lives == rules.starting_lives - 1


# -- restart ------------------------------------------------------------------


def test_restarting_a_stage_rewinds_the_score_it_began_with() -> None:
    run = advance_until(run_of("range-a", "range-b", quota=2), limit=600).advanced_stage()
    assert run.score == 200
    scored = run.advance(QUICK_INTERVAL * 2, FIRING)
    assert scored.score > 200
    restarted = scored.restarted_stage()
    assert restarted.score == 200, "the abandoned attempt leaves nothing behind"
    assert restarted.stage_index == 1
    assert restarted.session.state.tick == 0


def test_restarting_the_campaign_clears_the_score_and_returns_to_the_first_stage() -> None:
    """The documented deviation: the historical restart left the score where it was."""
    run = advance_until(run_of("range-a", "range-b", quota=2), limit=600).advanced_stage()
    assert run.score == 200
    restarted = run.restarted()
    assert restarted.score == 0
    assert restarted.stage_index == 0
    assert restarted.lives == restarted.rules.starting_lives
    assert observed(restarted.phase) is CampaignPhase.PLAYING


def test_restarting_a_stage_restores_the_lives_it_began_with() -> None:
    rules = rules_for()
    plan = plan_of(firing_range_stage("range-a"), quota=1, rules=rules)
    run = CampaignRun.start(plan, seed=TEST_SEED, rules=rules, driver=ScriptedEnemyDriver())
    while run.lives == rules.starting_lives and run.session.state.tick < 2000:
        run = run.advance(1, IDLE)
    assert run.lives < rules.starting_lives
    assert run.restarted_stage().lives == rules.starting_lives


# -- adopting a session decided elsewhere --------------------------------------
#
# A campaign owns the session it produces, but it does not own the only way one can be
# produced: a caller may assemble a state and hand it over, which is what makes
# ``ClientShell.session`` writable. ``with_session`` is where that is taken seriously.


def test_an_adopted_session_decides_the_phase() -> None:
    run = run_of("range-a", quota=2).advance(10, IDLE)
    assert run.phase is CampaignPhase.PLAYING

    adopted = run.with_session(session_with_outcome(run.session, RunOutcome.BASE_DESTROYED))

    assert adopted.phase is CampaignPhase.FAILED
    assert adopted.outcome is RunOutcome.BASE_DESTROYED


def test_adopting_the_running_session_changes_nothing() -> None:
    """Identity is the test, so the ordinary hand-back is free and is not a transition."""
    run = run_of("range-a", quota=2).advance(10, IDLE)
    assert run.with_session(run.session) is run


def test_adoption_rewrites_nothing_but_the_session_and_the_phase() -> None:
    """The quota, the cadence and the generator belong to the stage, not to the state."""
    run = run_of("range-a", quota=2).advance(QUICK_INTERVAL + 5, FIRING)
    adopted = run.with_session(session_with_outcome(run.session, RunOutcome.PLAYERS_ELIMINATED))

    assert adopted == replace(run, session=adopted.session, phase=CampaignPhase.FAILED), (
        "adoption touched a field it does not own"
    )


def test_an_adopted_session_can_also_report_a_clear() -> None:
    """``with_session`` re-reads the phase; it does not only look for a failure."""
    run = run_of("range-a", quota=1)
    cleared = advance_until(run)
    assert cleared.phase is CampaignPhase.COMPLETED

    reopened = cleared.with_session(replace(cleared.session, last_events=()))

    assert reopened.phase is CampaignPhase.COMPLETED


# -- construction -------------------------------------------------------------


def test_a_campaign_needs_a_stage() -> None:
    with pytest.raises(ValueError, match="at least one stage"):
        CampaignRun.start(())


def test_a_campaign_cannot_begin_outside_its_plan() -> None:
    plan = plan_of(firing_range_stage("range-a"), quota=1)
    with pytest.raises(ValueError, match="outside"):
        CampaignRun.start(plan, stage_index=3)


def test_a_campaign_may_begin_at_a_later_stage() -> None:
    """The whole checkpoint policy, until saved progress exists."""
    run = run_of("range-a", "range-b", "range-c", quota=1, stage_index=2)
    assert run.stage_index == 2
    assert run.stage_number == 3
    assert run.stage_count == 3
    assert run.score == 0
    assert run.lives == run.rules.starting_lives
    assert run.session.state.stage_id == "range-c"


def test_negative_ticks_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        run_of("range-a", quota=1).advance(-1)
