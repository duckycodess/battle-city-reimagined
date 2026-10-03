"""Tunable campaign constants for one campaign run.

A :class:`CampaignRules` value is to the campaign what
:class:`battle_city_sim.Rules` is to the rules engine: data a mode supplies, never a
global. The defaults are the historical runtime's numbers, read off
``duckycodess/Battle-City`` at revision ``5c9d81cd0de89a05f5946448d19c40fb343b0a2d`` and
accepted in ``openspec/changes/campaign-v1``.

Nothing here decides what a *simulation* does. Lives, score values and the damage ladder
belong to :class:`battle_city_sim.Rules`, which already carries the same historical
numbers; this module carries only the rules the simulation deliberately does not own:
how many enemies a stage releases and how often one arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from battle_city_sim import DIRECTION_ORDER, Direction, TankVariant


@dataclass(frozen=True, slots=True)
class CampaignRules:
    """Fixed integer constants governing one campaign."""

    starting_lives: int = 3
    """Lives the first stage of a campaign begins with. Historical ``BattleCity.lives``.

    Later stages inherit the count the player held when the previous stage was cleared,
    so this value is read once per campaign and not once per stage.
    """

    spawn_interval_ticks: int = 600
    """Ticks between one enemy entering play and the next attempt.

    The historical ``check_enemy_ai`` spawned on ``tick % 600 == 0``, and its tick counter
    started at zero, so the first enemy arrived on the stage's first frame. Measuring from
    the previous arrival rather than from an absolute schedule changes nothing while
    spawns succeed and avoids a burst after a blocked one; see the change's design note D3.
    """

    first_stage_enemies: int = 5
    """Enemy quota for the first stage of a pack when its level declares no waves."""

    stage_enemy_step: int = 2
    """Extra enemies each later stage releases when its level declares no waves.

    With the two defaults above this is the historical ``(level * 2) + 3`` for one-based
    levels: 5, 7 and 9 for the three classic stages.
    """

    spawn_variants: tuple[TankVariant, ...] = (
        TankVariant.ENEMY_NORMAL,
        TankVariant.ENEMY_SHIELDED,
    )
    """Variants a spawn may draw. Historical ``random.choice([0, 1])``.

    ``ENEMY_UNSHIELDED`` is deliberately absent: it is a damage state, reached by breaking
    a shielded enemy's shield, not something that enters the stage on its own.
    """

    spawn_facings: tuple[Direction, ...] = DIRECTION_ORDER
    """Facings a spawn may draw. Historical ``random.choice(['UP','DOWN','LEFT','RIGHT'])``."""

    def __post_init__(self) -> None:
        if self.starting_lives <= 0:
            raise ValueError("campaign.starting_lives must be positive")
        if self.spawn_interval_ticks <= 0:
            raise ValueError("campaign.spawn_interval_ticks must be positive")
        if self.first_stage_enemies <= 0:
            raise ValueError("campaign.first_stage_enemies must be positive")
        if self.stage_enemy_step < 0:
            raise ValueError("campaign.stage_enemy_step must not be negative")
        if not self.spawn_variants:
            raise ValueError("campaign.spawn_variants must not be empty")
        for variant in self.spawn_variants:
            if variant is TankVariant.PLAYER:
                raise ValueError("campaign.spawn_variants must not contain the player variant")
        if not self.spawn_facings:
            raise ValueError("campaign.spawn_facings must not be empty")


DEFAULT_CAMPAIGN_RULES: Final[CampaignRules] = CampaignRules()
"""The classic campaign. A pack or a mode overrides fields instead of mutating this."""
