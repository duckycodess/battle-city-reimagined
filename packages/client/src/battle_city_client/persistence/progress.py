"""The campaign document: where a run may be picked up, and what has been achieved.

Two different things live here and they are kept apart deliberately.

**A checkpoint is a stage boundary, not a saved game.** It records the stage a campaign
was standing at and the two anchors that stage began with -- the score and the lives it
opened on -- and nothing else about the run. Those two numbers are not invented here: the
campaign already keeps them, as
:attr:`~battle_city_client.campaign.CampaignRun.stage_start_score` and
:attr:`~battle_city_client.campaign.CampaignRun.stage_start_lives`, because restarting a
stage rewinds to exactly them. Resuming from a checkpoint therefore replays a stage from
the state the campaign would have replayed it from anyway, and no rule about score or
lives is changed, invented or rebalanced by saving it.

Two more fields are there so that "the same stage" means the same stage.

**The campaign seed.** Every random stream a stage draws from is derived from the
campaign seed and the stage index, so a campaign resumed under a different seed is a
different campaign wearing the same score. The seed the run was started with is therefore
part of the checkpoint, and a resume uses it in place of whatever this launch was told.
It is stored folded into the 64 bits :class:`battle_city_sim.rng.Rng` actually uses,
which is the position the derivation starts from either way, so the stored value and the
original produce the same streams and any integer seed is representable.

**The stage identity.** A level identifier names a stage; it does not identify one. The
same name lives in a different pack, and a pack is edited in place. The checkpoint
carries :func:`~battle_city_client.stage_adapter.stage_identity` -- a digest of the
grid, the base, the spawns and the declared waves -- and a resume that cannot match it
refuses and says so, rather than handing a player a score and a life count they earned in
content that is no longer there.

What is deliberately *not* here is a snapshot of a live simulation: no tick, no tank
positions, no terrain damage, no generator position. A mid-stage save would be a second
source of truth for the simulation's state and would have to be versioned against the
canonical encoding, which the architecture specification reserves to an accepted proposal.
A stage boundary needs none of that, and it is the granularity the historical game had.

**Progression is a tally.** Stages cleared, campaigns completed and the best score a run
reached are what cosmetic unlocks are derived from. They are counters, they only ever move
forward, and they are read by :mod:`~battle_city_client.persistence.cosmetics` -- never by
the simulation, the campaign rules or anything a match is played by.

The selected badge is stored as a plain identifier and is validated here only as one.
Whether it is *unlocked* is decided from the counters above every time it is read, so a
hand-edited file cannot grant a cosmetic: the worst it can do is name one, and a named
badge that the tally does not support falls back to the default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from battle_city_protocol import JsonValue

from .documents import (
    CorruptSave,
    read_int,
    read_optional_object,
    read_text,
    require_exact_keys,
)

PROGRESS_KIND: Final[str] = "campaign"
PROGRESS_SCHEMA_VERSION: Final[int] = 1
"""Bump when a field is added, removed or reinterpreted, and add the migration with it."""

MAX_LEVEL_ID_LENGTH: Final[int] = 64
MAX_STAGE_INDEX: Final[int] = 999
MAX_SCORE: Final[int] = 9_999_999
MAX_LIVES: Final[int] = 99
MAX_TALLY: Final[int] = 999_999
MAX_BADGE_ID_LENGTH: Final[int] = 32
IDENTITY_LENGTH: Final[int] = 64
"""Bounds every saved number and identifier is read against. Generous, and finite.

:data:`IDENTITY_LENGTH` mirrors
:data:`battle_city_client.stage_adapter.IDENTITY_LENGTH`; it is restated rather than
imported because the stage adapter reaches the filesystem and the content package, and a
record that validates a saved field should not pull either of those in to do it.
"""

MAX_SEED: Final[int] = (1 << 64) - 1
"""The widest seed a checkpoint can hold.

:meth:`battle_city_sim.rng.Rng.from_seed` folds any integer into these 64 bits before it
derives anything, so a seed stored folded starts every stream in exactly the same place
the original did. Storing it folded is what makes the field total: there is no seed a
player can launch with that cannot be written down.
"""

_IDENTITY: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
"""A stage identity, as :func:`~battle_city_client.stage_adapter.stage_identity` spells it."""

DEFAULT_BADGE_ID: Final[str] = "recruit"
"""The badge a profile starts on. Always unlocked; see :mod:`.cosmetics`."""

LEVEL_ID_FIELD: Final[str] = "level_id"
STAGE_INDEX_FIELD: Final[str] = "stage_index"
SCORE_FIELD: Final[str] = "score"
LIVES_FIELD: Final[str] = "lives"
SEED_FIELD: Final[str] = "seed"
STAGE_IDENTITY_FIELD: Final[str] = "stage_identity"

CHECKPOINT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        LEVEL_ID_FIELD,
        STAGE_INDEX_FIELD,
        SCORE_FIELD,
        LIVES_FIELD,
        SEED_FIELD,
        STAGE_IDENTITY_FIELD,
    }
)

CHECKPOINT_FIELD: Final[str] = "checkpoint"
STAGES_CLEARED_FIELD: Final[str] = "stages_cleared"
CAMPAIGNS_COMPLETED_FIELD: Final[str] = "campaigns_completed"
BEST_SCORE_FIELD: Final[str] = "best_score"
SELECTED_BADGE_FIELD: Final[str] = "selected_badge"

PROGRESS_FIELDS: Final[frozenset[str]] = frozenset(
    {
        CHECKPOINT_FIELD,
        STAGES_CLEARED_FIELD,
        CAMPAIGNS_COMPLETED_FIELD,
        BEST_SCORE_FIELD,
        SELECTED_BADGE_FIELD,
    }
)


@dataclass(frozen=True, slots=True)
class StageCheckpoint:
    """The stage a campaign may be picked up at, and what makes it that stage."""

    level_id: str
    stage_index: int
    score: int
    lives: int
    seed: int
    stage_identity: str

    def __post_init__(self) -> None:
        if not self.level_id or len(self.level_id) > MAX_LEVEL_ID_LENGTH:
            raise ValueError(f"checkpoint level id must be 1 to {MAX_LEVEL_ID_LENGTH} characters")
        if not 0 <= self.stage_index <= MAX_STAGE_INDEX:
            raise ValueError(f"checkpoint stage index is out of range: {self.stage_index}")
        if not 0 <= self.score <= MAX_SCORE:
            raise ValueError(f"checkpoint score is out of range: {self.score}")
        if not 1 <= self.lives <= MAX_LIVES:
            raise ValueError(f"checkpoint lives are out of range: {self.lives}")
        if not 0 <= self.seed <= MAX_SEED:
            raise ValueError(f"checkpoint seed must be folded into 64 bits: {self.seed}")
        if _IDENTITY.fullmatch(self.stage_identity) is None:
            raise ValueError("checkpoint stage identity must be a 64-character hex digest")

    @classmethod
    def of(
        cls,
        *,
        level_id: str,
        stage_index: int,
        score: int,
        lives: int,
        seed: int,
        stage_identity: str,
    ) -> StageCheckpoint:
        """Build a checkpoint, folding ``seed`` the way the generator folds it.

        The one constructor a caller holding a live campaign should use: a campaign seed
        is any integer a launch was given, and this is where it becomes a value a save can
        hold without changing a single stream it derives.
        """
        return cls(
            level_id=level_id,
            stage_index=stage_index,
            score=score,
            lives=lives,
            seed=seed & MAX_SEED,
            stage_identity=stage_identity,
        )

    def to_data(self) -> dict[str, JsonValue]:
        return {
            LEVEL_ID_FIELD: self.level_id,
            LIVES_FIELD: self.lives,
            SCORE_FIELD: self.score,
            SEED_FIELD: self.seed,
            STAGE_IDENTITY_FIELD: self.stage_identity,
            STAGE_INDEX_FIELD: self.stage_index,
        }


@dataclass(frozen=True, slots=True)
class CampaignProgress:
    """One player's local campaign record. Immutable; the profile replaces it wholesale."""

    checkpoint: StageCheckpoint | None = None
    stages_cleared: int = 0
    campaigns_completed: int = 0
    best_score: int = 0
    selected_badge: str = DEFAULT_BADGE_ID

    def __post_init__(self) -> None:
        for name, value in (
            (STAGES_CLEARED_FIELD, self.stages_cleared),
            (CAMPAIGNS_COMPLETED_FIELD, self.campaigns_completed),
        ):
            if not 0 <= value <= MAX_TALLY:
                raise ValueError(f"{name} is out of range: {value}")
        if not 0 <= self.best_score <= MAX_SCORE:
            raise ValueError(f"best score is out of range: {self.best_score}")
        if not self.selected_badge or len(self.selected_badge) > MAX_BADGE_ID_LENGTH:
            raise ValueError(f"badge id must be 1 to {MAX_BADGE_ID_LENGTH} characters")

    # -- forward-only updates --------------------------------------------------

    def at_checkpoint(self, checkpoint: StageCheckpoint | None) -> CampaignProgress:
        """The same record standing at ``checkpoint``."""
        return replace(self, checkpoint=checkpoint)

    def with_stage_cleared(self, score: int) -> CampaignProgress:
        """One more stage cleared, and the best score raised if this run beat it."""
        return replace(
            self,
            stages_cleared=min(self.stages_cleared + 1, MAX_TALLY),
            best_score=min(max(self.best_score, score), MAX_SCORE),
        )

    def with_campaign_completed(self, score: int) -> CampaignProgress:
        """One more campaign completed, and the best score raised if this run beat it."""
        return replace(
            self,
            campaigns_completed=min(self.campaigns_completed + 1, MAX_TALLY),
            best_score=min(max(self.best_score, score), MAX_SCORE),
        )

    def with_best_score(self, score: int) -> CampaignProgress:
        """The best score raised if ``score`` beat it. Never lowered."""
        return replace(self, best_score=min(max(self.best_score, score), MAX_SCORE))

    def with_badge(self, badge_id: str) -> CampaignProgress:
        """The same record with ``badge_id`` selected. Unlocking is decided elsewhere."""
        return replace(self, selected_badge=badge_id)

    def to_data(self) -> dict[str, JsonValue]:
        """The payload a campaign document carries."""
        checkpoint: JsonValue = None if self.checkpoint is None else self.checkpoint.to_data()
        return {
            BEST_SCORE_FIELD: self.best_score,
            CAMPAIGNS_COMPLETED_FIELD: self.campaigns_completed,
            CHECKPOINT_FIELD: checkpoint,
            SELECTED_BADGE_FIELD: self.selected_badge,
            STAGES_CLEARED_FIELD: self.stages_cleared,
        }


def checkpoint_from_data(data: Mapping[str, JsonValue]) -> StageCheckpoint:
    """Build a checkpoint from a decoded payload, refusing anything that is not one."""
    require_exact_keys(data, CHECKPOINT_FIELDS, "checkpoint")
    try:
        return StageCheckpoint(
            level_id=read_text(data, LEVEL_ID_FIELD, max_length=MAX_LEVEL_ID_LENGTH),
            stage_index=read_int(data, STAGE_INDEX_FIELD, minimum=0, maximum=MAX_STAGE_INDEX),
            score=read_int(data, SCORE_FIELD, minimum=0, maximum=MAX_SCORE),
            lives=read_int(data, LIVES_FIELD, minimum=1, maximum=MAX_LIVES),
            seed=read_int(data, SEED_FIELD, minimum=0, maximum=MAX_SEED),
            stage_identity=read_text(data, STAGE_IDENTITY_FIELD, max_length=IDENTITY_LENGTH),
        )
    except ValueError as error:
        raise CorruptSave(f"checkpoint is not usable: {error}") from error


def progress_from_data(data: Mapping[str, JsonValue]) -> CampaignProgress:
    """Build a campaign record from a decoded payload, refusing anything that is not one."""
    require_exact_keys(data, PROGRESS_FIELDS, "campaign progress")
    nested = read_optional_object(data, CHECKPOINT_FIELD)
    try:
        return CampaignProgress(
            checkpoint=None if nested is None else checkpoint_from_data(nested),
            stages_cleared=read_int(data, STAGES_CLEARED_FIELD, minimum=0, maximum=MAX_TALLY),
            campaigns_completed=read_int(
                data, CAMPAIGNS_COMPLETED_FIELD, minimum=0, maximum=MAX_TALLY
            ),
            best_score=read_int(data, BEST_SCORE_FIELD, minimum=0, maximum=MAX_SCORE),
            selected_badge=read_text(data, SELECTED_BADGE_FIELD, max_length=MAX_BADGE_ID_LENGTH),
        )
    except ValueError as error:
        raise CorruptSave(f"campaign progress is not usable: {error}") from error
