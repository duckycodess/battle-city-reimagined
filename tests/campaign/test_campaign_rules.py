"""The campaign constants and the rule that turns a pack into a list of quotas."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from battle_city_client.campaign import (
    DEFAULT_CAMPAIGN_RULES,
    CampaignRules,
    StagePlan,
    campaign_plan,
    enemy_quota_for,
)
from battle_city_sim import DIRECTION_ORDER, TankVariant
from campaign_helpers import entry_for, firing_range_stage

# -- the constants ------------------------------------------------------------


def test_the_defaults_are_the_historical_numbers() -> None:
    """Every one of these was read off the historical runtime; see the change's design."""
    rules = DEFAULT_CAMPAIGN_RULES
    assert rules.starting_lives == 3
    assert rules.spawn_interval_ticks == 600
    assert rules.first_stage_enemies == 5
    assert rules.stage_enemy_step == 2
    assert rules.spawn_facings == DIRECTION_ORDER


def test_the_classic_quotas_fall_out_of_the_defaults() -> None:
    """``(level * 2) + 3`` for one-based levels 1, 2 and 3."""
    rules = DEFAULT_CAMPAIGN_RULES
    quotas = [rules.first_stage_enemies + rules.stage_enemy_step * index for index in range(3)]
    assert quotas == [5, 7, 9]


def test_an_unshielded_enemy_is_never_spawned_outright() -> None:
    """It is a damage state, reached by breaking a shield, not an arrival."""
    assert TankVariant.ENEMY_UNSHIELDED not in DEFAULT_CAMPAIGN_RULES.spawn_variants
    assert set(DEFAULT_CAMPAIGN_RULES.spawn_variants) == {
        TankVariant.ENEMY_NORMAL,
        TankVariant.ENEMY_SHIELDED,
    }


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("starting_lives", lambda: CampaignRules(starting_lives=0)),
        ("spawn_interval_ticks", lambda: CampaignRules(spawn_interval_ticks=0)),
        ("first_stage_enemies", lambda: CampaignRules(first_stage_enemies=0)),
        ("stage_enemy_step", lambda: CampaignRules(stage_enemy_step=-1)),
        ("spawn_variants", lambda: CampaignRules(spawn_variants=())),
        ("spawn_facings", lambda: CampaignRules(spawn_facings=())),
    ],
)
def test_nonsense_rules_are_refused_at_construction(
    field: str, build: Callable[[], CampaignRules]
) -> None:
    """Each case is written out rather than built from a name, so the type checker sees
    the same call a caller would write."""
    with pytest.raises(ValueError, match=field):
        build()


def test_the_player_variant_cannot_be_spawned_as_an_enemy() -> None:
    with pytest.raises(ValueError, match="player variant"):
        CampaignRules(spawn_variants=(TankVariant.PLAYER,))


# -- the quota rule -----------------------------------------------------------


def test_declared_waves_decide_the_quota() -> None:
    entry = entry_for(firing_range_stage(), waves=(4, 6))
    assert enemy_quota_for(entry, 0) == 10
    assert enemy_quota_for(entry, 2) == 10, "a declared quota does not drift with position"


def test_a_level_without_waves_falls_back_to_its_position() -> None:
    entry = entry_for(firing_range_stage())
    assert [enemy_quota_for(entry, index) for index in range(3)] == [5, 7, 9]


def test_the_fallback_follows_the_rules_it_is_given() -> None:
    entry = entry_for(firing_range_stage())
    rules = CampaignRules(first_stage_enemies=2, stage_enemy_step=3)
    assert [enemy_quota_for(entry, index, rules) for index in range(3)] == [2, 5, 8]


# -- the plan -----------------------------------------------------------------


def test_the_plan_keeps_catalog_order_and_numbers_from_zero() -> None:
    entries = [entry_for(firing_range_stage(f"range-{index}")) for index in range(3)]
    plan = campaign_plan(entries)
    assert [stage.level_id for stage in plan] == ["range-0", "range-1", "range-2"]
    assert [stage.index for stage in plan] == [0, 1, 2]


def test_the_plan_mixes_declared_and_derived_quotas() -> None:
    """A pack may declare waves for some levels and not others; both are honoured."""
    plan = campaign_plan(
        [
            entry_for(firing_range_stage("range-0")),
            entry_for(firing_range_stage("range-1"), waves=(1,)),
            entry_for(firing_range_stage("range-2")),
        ]
    )
    assert [stage.enemy_quota for stage in plan] == [5, 1, 9]


def test_an_empty_catalog_plans_nothing() -> None:
    assert campaign_plan([]) == ()


def test_a_stage_plan_refuses_impossible_values() -> None:
    stage = firing_range_stage()
    with pytest.raises(ValueError, match="index"):
        StagePlan(index=-1, level_id="x", name="X", stage=stage, enemy_quota=1)
    with pytest.raises(ValueError, match="quota"):
        StagePlan(index=0, level_id="x", name="X", stage=stage, enemy_quota=-1)
