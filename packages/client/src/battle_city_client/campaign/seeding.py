"""Deriving a stage's random streams from the campaign seed.

A campaign needs two seeded positions per stage: one for the simulation's own generator,
and one for the campaign's spawn draws. Both must be functions of ``(campaign seed, stage
index)`` and of nothing else, because that is what makes restarting a stage replay it
exactly and what keeps two stages from drawing the same spawn sequence.

The algorithm is the simulation's. :class:`battle_city_sim.rng.Rng` is the project's
seeded, versioned SplitMix64 value; nothing here implements a generator, and derivation
only chooses a starting position in that one.

Why this is not imported from ``battle_city_ai.seeding``
-------------------------------------------------------
That module does the same folding for bots, and the obvious move would be to call it. The
architecture specification allows ``client -> sim, content, protocol`` and does not allow
``client -> ai``, and the client manifest declares no such dependency, so the client may
not import it. The duplication is a dozen lines, it is recorded in both places, and
neither copy owns an algorithm: both call the simulation's generator. The two domain
separators differ, so a campaign draw can never land on a bot's stream position.

Changing anything here changes every spawn for an existing seed, which makes it a
replay-compatibility change. :data:`CAMPAIGN_SEEDING_VERSION` records the current scheme.
"""

from __future__ import annotations

from typing import Final

from battle_city_sim.rng import Rng

CAMPAIGN_SEEDING_VERSION: Final[int] = 1
"""Version of the derivation below. Bump it only alongside a compatibility note."""

CAMPAIGN_RNG_DOMAIN: Final[int] = 0x43_4D_50_47_4E_5F_56_31
"""Domain separator (ASCII ``CMPGN_V1``) folded in before anything else."""

STAGE_SIM_DOMAIN: Final[int] = 0x53_49_4D_5F_53_54_47_31
"""Domain separator (ASCII ``SIM_STG1``) for the simulation seed of one stage."""

STAGE_SPAWN_DOMAIN: Final[int] = 0x53_50_57_4E_5F_53_54_47
"""Domain separator (ASCII ``SPWN_STG``) for the spawn stream of one stage."""

_MASK64: Final[int] = (1 << 64) - 1


def fold(stream: Rng, ingredient: int) -> Rng:
    """Return a new stream position mixing ``ingredient`` into ``stream``.

    One SplitMix64 output round does the mixing, so the avalanche behaviour is the
    simulation's. The drawn value becomes the new position rather than being returned,
    because derivation produces a starting point and never a usable random value.
    """
    draw, _ = Rng.from_seed(stream.state ^ (ingredient & _MASK64)).next_u64()
    return Rng(state=draw)


def _stage_stream(*, seed: int, stage_index: int, domain: int) -> Rng:
    stream = Rng.from_seed(seed)
    stream = fold(stream, CAMPAIGN_RNG_DOMAIN)
    stream = fold(stream, domain)
    return fold(stream, stage_index)


def stage_simulation_seed(*, seed: int, stage_index: int) -> int:
    """The seed ``new_game`` is given for the stage at ``stage_index``.

    The rules engine carries its generator without drawing from it today, so this value
    is currently only a label on the run. It is derived rather than reused so that a
    future rule which *does* draw cannot make two stages of one campaign behave alike.
    """
    return _stage_stream(seed=seed, stage_index=stage_index, domain=STAGE_SIM_DOMAIN).state


def stage_spawn_rng(*, seed: int, stage_index: int) -> Rng:
    """The campaign's own stream for the stage at ``stage_index``.

    The campaign owns the successor of every draw it makes, exactly as a bot does: the
    rules engine copies ``state.rng`` from tick to tick without advancing it, so a caller
    that drew from the state would draw the same value forever.
    """
    return _stage_stream(seed=seed, stage_index=stage_index, domain=STAGE_SPAWN_DOMAIN)
