"""One player's local profile: the settings, the campaign record, and what went wrong.

:class:`LocalProfile` is what the rest of the client talks to. It holds the two documents
as validated records, answers questions about them, and writes a document back when -- and
only when -- something in it actually changed. Every mutation is "replace the record, then
persist if it differs", so a frame that re-records the same checkpoint touches no disk, and
a session that changes nothing writes nothing at all.

A profile with no store is a profile in memory, and that is the default. A
:class:`~battle_city_client.shell.ClientShell` built without one therefore reads and writes
no files, which keeps every existing test, every headless run and every embedding of the
shell exactly as free of I/O as it was. Only :func:`battle_city_client.app.main` attaches a
:class:`~battle_city_client.persistence.store.ProfileStore`, because only a real launch has
a player whose preferences are worth keeping.

Failures are carried, not raised
--------------------------------
A save that cannot be read leaves the profile on its defaults and records a notice; a save
that cannot be written records a notice and keeps the change in memory for the rest of the
session. Nothing here raises into the game loop. The notices are short lines in the
client's own voice, and the renderer shows them on the main menu and on the options screen,
so a player whose profile did not load is told once, plainly, instead of discovering it
when their progress is gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .cosmetics import BADGES, Badge, cycled_badge, selected_badge, unlocked_badges
from .progress import (
    PROGRESS_KIND,
    PROGRESS_SCHEMA_VERSION,
    CampaignProgress,
    StageCheckpoint,
    progress_from_data,
)
from .settings import (
    SETTINGS_KIND,
    SETTINGS_SCHEMA_VERSION,
    LocalSettings,
    settings_from_data,
)
from .store import ProfileStore

MAX_NOTICES: int = 4
"""Enough to say what happened to both documents, and bounded so nothing accumulates."""


@dataclass(slots=True)
class LocalProfile:
    """The local profile, in memory, optionally backed by a store."""

    settings: LocalSettings = field(default_factory=LocalSettings)
    progress: CampaignProgress = field(default_factory=CampaignProgress)
    store: ProfileStore | None = None
    notices: tuple[str, ...] = ()
    settings_restored: bool = False
    """Whether :attr:`settings` came from a file rather than from the defaults.

    The launch reads it to decide whether a window scale was *chosen*: without a saved
    scale the client still picks one from the desktop size, which is a better answer than
    a default that happens to be the same number.
    """

    progress_restored: bool = False
    settings_writable: bool = True
    progress_writable: bool = True

    # -- construction ----------------------------------------------------------

    @classmethod
    def load(cls, store: ProfileStore) -> LocalProfile:
        """Read both documents through ``store``. Never raises; see the module docstring."""
        settings = store.load(
            SETTINGS_KIND, current_version=SETTINGS_SCHEMA_VERSION, parse=settings_from_data
        )
        progress = store.load(
            PROGRESS_KIND, current_version=PROGRESS_SCHEMA_VERSION, parse=progress_from_data
        )
        notices = tuple(notice for notice in (settings.notice, progress.notice) if notice)
        return cls(
            settings=settings.value or LocalSettings(),
            progress=progress.value or CampaignProgress(),
            store=store,
            notices=notices[:MAX_NOTICES],
            settings_restored=settings.value is not None,
            progress_restored=progress.value is not None,
            settings_writable=settings.writable,
            progress_writable=progress.writable,
        )

    # -- queries ---------------------------------------------------------------

    @property
    def recovery_notice(self) -> str:
        """The first thing that went wrong, or ``""`` when nothing did."""
        return self.notices[0] if self.notices else ""

    @property
    def checkpoint(self) -> StageCheckpoint | None:
        """The stage a campaign may be picked up at, if one was recorded."""
        return self.progress.checkpoint

    @property
    def badge(self) -> Badge:
        """The cosmetic badge in effect. Local presentation only; see :mod:`.cosmetics`."""
        return selected_badge(self.progress)

    def badge_options(self) -> tuple[tuple[Badge, bool], ...]:
        """Every badge with whether this profile has earned it, in display order."""
        earned = frozenset(badge.badge_id for badge in unlocked_badges(self.progress))
        return tuple((badge, badge.badge_id in earned) for badge in BADGES)

    # -- settings --------------------------------------------------------------

    def remember_settings(self, settings: LocalSettings) -> None:
        """Adopt ``settings`` and persist them if they differ from what is held."""
        if settings == self.settings:
            return
        self.settings = settings
        self.settings_restored = True
        self._save_settings()

    def adopt_settings(self, settings: LocalSettings) -> None:
        """Hold ``settings`` for this session without writing them.

        What a launch resolved -- a saved value, or the option that overrode it -- is what
        the client is running with, so it is what the profile should report and what a
        later write should record. It is not itself a change the player made, so it does
        not start one.
        """
        self.settings = settings

    def remember_scale(self, scale: int) -> None:
        """Record the window scale the player is now using."""
        self.remember_settings(self.settings.with_scale(scale))

    # -- campaign --------------------------------------------------------------

    def record_stage_start(
        self,
        *,
        level_id: str,
        stage_index: int,
        score: int,
        lives: int,
        seed: int,
        stage_identity: str,
    ) -> None:
        """Record the stage boundary a campaign has just opened on.

        The score and the lives are the campaign's own stage anchors, which is what makes
        this a checkpoint rather than a saved game: resuming replays the stage from
        exactly the state restarting it would have replayed it from. The seed and the
        stage identity are what make it the *same* stage when it is picked up again --
        the same random streams, and the same content.
        """
        try:
            checkpoint = StageCheckpoint.of(
                level_id=level_id,
                stage_index=stage_index,
                score=score,
                lives=lives,
                seed=seed,
                stage_identity=stage_identity,
            )
        except ValueError:
            # A campaign outside the bounds a save can hold is still perfectly playable;
            # it simply cannot be written down. Nothing is recorded and nothing is lost.
            return
        self._record(self.progress.at_checkpoint(checkpoint))

    def record_stage_cleared(self, score: int) -> None:
        """One more stage cleared. Raises the best score if this run beat it."""
        self._record(self.progress.with_stage_cleared(score))

    def record_campaign_completed(self, score: int) -> None:
        """One more campaign completed. Raises the best score if this run beat it."""
        self._record(self.progress.with_campaign_completed(score))

    def record_run_score(self, score: int) -> None:
        """Raise the best score if ``score`` beat it. Nothing else moves."""
        self._record(self.progress.with_best_score(score))

    def forget_checkpoint(self) -> None:
        """Drop the checkpoint, leaving the tally and the badge alone."""
        self._record(self.progress.at_checkpoint(None))

    # -- cosmetics -------------------------------------------------------------

    def select_badge(self, badge_id: str) -> None:
        """Select ``badge_id`` if this profile has earned it. A locked badge is ignored."""
        candidate = replace(self.progress, selected_badge=badge_id)
        if selected_badge(candidate).badge_id != badge_id:
            return
        self._record(self.progress.with_badge(badge_id))

    def cycle_badge(self, delta: int) -> None:
        """Move ``delta`` places through the earned badges, wrapping."""
        self._record(self.progress.with_badge(cycled_badge(self.progress, delta).badge_id))

    # -- persistence -----------------------------------------------------------

    def _record(self, progress: CampaignProgress) -> None:
        if progress == self.progress:
            return
        self.progress = progress
        self.progress_restored = True
        self._save_progress()

    def _save_settings(self) -> None:
        store = self.store
        if store is None:
            return
        if not self.settings_writable:
            self._note("SETTINGS KEPT FOR THIS SESSION ONLY")
            return
        notice = store.save(
            SETTINGS_KIND, version=SETTINGS_SCHEMA_VERSION, data=self.settings.to_data()
        )
        if notice:
            self.settings_writable = False
            self._note(notice)

    def _save_progress(self) -> None:
        store = self.store
        if store is None:
            return
        if not self.progress_writable:
            self._note("CAMPAIGN SAVE KEPT FOR THIS SESSION ONLY")
            return
        notice = store.save(
            PROGRESS_KIND, version=PROGRESS_SCHEMA_VERSION, data=self.progress.to_data()
        )
        if notice:
            self.progress_writable = False
            self._note(notice)

    def _note(self, notice: str) -> None:
        """Add a notice once. The same failure happening twice is still one thing to say."""
        if notice in self.notices or len(self.notices) >= MAX_NOTICES:
            return
        self.notices = (*self.notices, notice)
