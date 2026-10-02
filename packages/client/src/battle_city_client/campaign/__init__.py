"""The single-player campaign: stages in order, waves, score, lives, win and loss.

This package is the campaign phase's whole behaviour, and it is deliberately not in the
simulation and not in the content package. The simulation's documented position is that
wave cadence, stage-win timing and score accumulation are campaign policy; the content
package may depend on no other project package and so cannot step a simulation. The
architecture specification allows ``client -> sim, content, protocol``, which makes this
the only place the join can live.

Nothing here imports pygame, reads a clock, opens a file or touches a socket. A server
running a campaign would import this same module.

Using it
--------
::

    from battle_city_client.campaign import CampaignRun, campaign_plan
    from battle_city_client.stage_adapter import bundled_stage_catalog

    run = CampaignRun.start(campaign_plan(bundled_stage_catalog()), seed=7)
    run = run.advance(600, PlayerIntent(fire=True))
    run.score, run.lives, run.phase

What it owns, and what it does not
----------------------------------
It owns the rules the simulation deliberately leaves out: the enemy quota per stage, the
spawn cadence, the stage-clear condition, campaign victory, and what a restart rewinds. It
owns no rule the simulation already has -- lives, the damage ladder, the published score
values and both failure outcomes stay there, and the campaign reads them rather than
restating them.

It also does not steer enemies. Commands for the enemy tanks come from an injected
:class:`~battle_city_client.campaign.driver.EnemyCommandDriver`, and the default
:class:`~battle_city_client.campaign.driver.IdleEnemyDriver` issues none, so the shipped
client spawns enemies that hold position and never fire. The reason is the dependency
direction: bot behaviour is ``battle_city_ai``'s and the client may not depend on it. See
``driver.py`` for the whole of that argument, and issue #35 for the wiring.

The rules are accepted in ``openspec/changes/campaign-v1`` and recorded under "Campaign
rules" in the product specification.

Layout
------
``rules``       the campaign constants, as data
``plan``        the ordered stages and how each one's enemy quota is decided
``seeding``     per-stage random streams derived from the campaign seed
``driver``      the enemy-command seam, its default, and the legality filter
``controller``  :class:`CampaignRun`, the tick, the phases and the transitions
"""

from .controller import CampaignPhase, CampaignRun
from .driver import EnemyCommandDriver, IdleEnemyDriver, legal_enemy_commands
from .plan import StagePlan, campaign_plan, enemy_quota_for
from .rules import DEFAULT_CAMPAIGN_RULES, CampaignRules
from .seeding import (
    CAMPAIGN_RNG_DOMAIN,
    CAMPAIGN_SEEDING_VERSION,
    stage_simulation_seed,
    stage_spawn_rng,
)

__all__ = [
    "CAMPAIGN_RNG_DOMAIN",
    "CAMPAIGN_SEEDING_VERSION",
    "DEFAULT_CAMPAIGN_RULES",
    "CampaignPhase",
    "CampaignRules",
    "CampaignRun",
    "EnemyCommandDriver",
    "IdleEnemyDriver",
    "StagePlan",
    "campaign_plan",
    "enemy_quota_for",
    "legal_enemy_commands",
    "stage_simulation_seed",
    "stage_spawn_rng",
]
