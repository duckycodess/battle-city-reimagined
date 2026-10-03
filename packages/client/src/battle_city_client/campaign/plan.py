"""The ordered stages of a campaign and the enemy quota each one releases.

A :class:`StagePlan` is a stage plus the one campaign fact a stage needs: how many enemy
tanks it releases before it can be cleared. Building the plan is separated from running it
so the quota rule is readable in one place and so a test, a menu or a future save can hold
the plan without holding a run.

Where a quota comes from
------------------------
A level may declare ``waves`` in its content file, and a wave states only how many enemies
it releases. The quota is the sum of those counts. A level that declares none -- which is
all three converted classic levels, because the historical stage data carries no wave
information -- falls back to the classic arithmetic: ``first_stage_enemies +
stage_enemy_step * index`` over the stage's zero-based position in the pack. With the
default rules that is 5, 7 and 9, the historical ``(level * 2) + 3`` for levels 1 to 3.

The fallback is indexed by position rather than by level identifier so that a pack of any
length gets a rising quota and the classic pack reproduces its source without wave data
being written into converted files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from battle_city_sim import Stage

from ..stage_adapter import StageEntry
from .rules import DEFAULT_CAMPAIGN_RULES, CampaignRules


@dataclass(frozen=True, slots=True)
class StagePlan:
    """One stage of a campaign, with the labels a menu shows and its enemy quota."""

    index: int
    level_id: str
    name: str
    stage: Stage
    enemy_quota: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"stage index must not be negative, found {self.index}")
        if self.enemy_quota < 0:
            raise ValueError(f"enemy quota must not be negative, found {self.enemy_quota}")


def enemy_quota_for(
    entry: StageEntry, index: int, rules: CampaignRules = DEFAULT_CAMPAIGN_RULES
) -> int:
    """How many enemies the stage at ``index`` releases. See the module docstring."""
    if entry.waves:
        return sum(entry.waves)
    return rules.first_stage_enemies + rules.stage_enemy_step * index


def campaign_plan(
    catalog: Sequence[StageEntry], rules: CampaignRules = DEFAULT_CAMPAIGN_RULES
) -> tuple[StagePlan, ...]:
    """Build the plan for ``catalog``, in the order the pack manifest declared.

    Manifest order is campaign order: the content package states that
    :attr:`battle_city_content.Pack.level_ids` is "in manifest order, which is campaign
    order", and the adapter preserves it.
    """
    return tuple(
        StagePlan(
            index=index,
            level_id=entry.level_id,
            name=entry.name,
            stage=entry.stage,
            enemy_quota=enemy_quota_for(entry, index, rules),
        )
        for index, entry in enumerate(catalog)
    )
