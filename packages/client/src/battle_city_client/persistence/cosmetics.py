"""Unlockable badges: a word on this client's own screen, and nothing else.

A badge is a label. It is drawn on the local HUD and on the options screen of the machine
that earned it, and that is the entire extent of its existence. It is not a field of any
simulation state, it is not a field of any protocol message, it is not drawn on an online
screen, and it reaches no decision the game makes. The product specification's rule --
cosmetics must not alter competitive balance, and competitive cosmetics must not affect
simulation state or visibility -- is kept here by the badge having nowhere to go: the
selection lives in the local profile, the renderer reads it for one HUD line on a *local*
run, and no code path carries it towards :func:`battle_city_sim.step`, towards
:func:`battle_city_protocol.encode_message`, or towards the online HUD.
``tests/persistence`` asserts all three directions rather than trusting the description:
two profiles with different badges produce byte-identical per-tick state encodings, equal
per-tick hashes, and byte-identical outgoing messages across a whole online session.

Unlocks are derived, never stored
---------------------------------
A badge states the tally it needs, and whether it is unlocked is recomputed from
:class:`~battle_city_client.persistence.progress.CampaignProgress` every time it is asked.
Nothing records "this badge is unlocked", so there is no such record to edit: a save file
can name a badge, and if the counters do not support it the selection falls back to the
default. The counters themselves only move forward, and only when the campaign reports a
stage cleared or a campaign completed.

The requirements are deliberately small, and they are progression rather than payment:
there is no economy, no account and no purchase anywhere in this build, and the product
boundaries forbid introducing one without a separate approved proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .progress import DEFAULT_BADGE_ID, CampaignProgress


@dataclass(frozen=True, slots=True)
class Badge:
    """One cosmetic label and the progression that reveals it."""

    badge_id: str
    label: str
    stages_cleared: int = 0
    campaigns_completed: int = 0

    def unlocked_by(self, progress: CampaignProgress) -> bool:
        """Whether ``progress`` has met both requirements."""
        return (
            progress.stages_cleared >= self.stages_cleared
            and progress.campaigns_completed >= self.campaigns_completed
        )

    @property
    def requirement(self) -> str:
        """What a locked badge asks for, said in the words the options screen uses."""
        if self.campaigns_completed > 0:
            plural = "" if self.campaigns_completed == 1 else "S"
            return f"{self.campaigns_completed} CAMPAIGN{plural}"
        if self.stages_cleared > 0:
            plural = "" if self.stages_cleared == 1 else "S"
            return f"{self.stages_cleared} STAGE{plural}"
        return "FROM THE START"


BADGES: Final[tuple[Badge, ...]] = (
    Badge(badge_id=DEFAULT_BADGE_ID, label="RECRUIT"),
    Badge(badge_id="veteran", label="VETERAN", stages_cleared=1),
    Badge(badge_id="ace", label="ACE", campaigns_completed=1),
)
"""Every badge this build has, in display order. The first is always unlocked."""

BADGES_BY_ID: Final[dict[str, Badge]] = {badge.badge_id: badge for badge in BADGES}

DEFAULT_BADGE: Final[Badge] = BADGES[0]


def unlocked_badges(progress: CampaignProgress) -> tuple[Badge, ...]:
    """Every badge ``progress`` has earned, in display order. Never empty."""
    return tuple(badge for badge in BADGES if badge.unlocked_by(progress))


def selected_badge(progress: CampaignProgress) -> Badge:
    """The badge in effect: the selected one when it is earned, the default otherwise.

    A selection naming a badge this build does not have, or one the tally does not
    support, resolves to the default rather than raising. The file was already accepted
    as structurally valid by then; what it names is a preference, and an unearned
    preference is simply not honoured.
    """
    badge = BADGES_BY_ID.get(progress.selected_badge)
    if badge is None or not badge.unlocked_by(progress):
        return DEFAULT_BADGE
    return badge


def cycled_badge(progress: CampaignProgress, delta: int) -> Badge:
    """The badge ``delta`` places from the current one among the unlocked ones, wrapping."""
    options = unlocked_badges(progress)
    current = selected_badge(progress)
    index = options.index(current) if current in options else 0
    return options[(index + delta) % len(options)]
