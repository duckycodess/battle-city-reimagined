"""Saving a campaign, picking it up again, and the rule that did not change.

The stages here are built for the test, not loaded from the bundled pack: an open board
with the enemy spawn in the player's line of fire, so a stage clears deterministically in
a few dozen ticks and a whole two-stage campaign fits in a test. The classic layouts are
mazes that need a pathfinder to clear, which is a fact about pathfinding rather than about
saving.

The claim this file exists to defend is the one that is easy to lose: **choosing a stage
from the list still starts it fresh.** The product specification records that a campaign
may be started at any stage in the pack, with the starting lives and a score of zero, and
adding a save does not quietly turn the stage list into a resume list. Resuming is a
separate action, asked for with its own key, and everything else about the list is
untouched -- every stage still selectable, every one still starting at zero.

Nothing here imports pygame.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from battle_city_client.campaign import CampaignPhase, CampaignRules
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.persistence import (
    CampaignProgress,
    LocalProfile,
    ProfileStore,
    StageCheckpoint,
)
from battle_city_client.session import DEFAULT_SEED
from battle_city_client.shell import (
    CHANGED_CONTENT_NOTICE,
    NO_CHECKPOINT_NOTICE,
    SAVED_BLOCK_NOTES,
    STALE_CHECKPOINT_NOTICE,
    ClientShell,
    Screen,
)
from battle_city_client.stage_adapter import StageEntry, stage_identity
from battle_city_protocol import JsonValue
from battle_city_sim import GridPos, PlayerSpawn, Stage, TankVariant, Tile, state_hash

FIRING = PlayerIntent(fire=True)
RANGE_RULES = CampaignRules(spawn_interval_ticks=5, spawn_variants=(TankVariant.ENEMY_NORMAL,))
CLEAR_BUDGET = 400
"""Ticks a stage of this shape needs to clear, with room to spare. It takes 28."""


def _rows() -> tuple[str, ...]:
    grid = [[Tile.EMPTY for _ in range(16)] for _ in range(16)]
    grid[15][7] = Tile.HOME
    return tuple("".join(str(tile.value) for tile in row) for row in grid)


def _entry(
    level_id: str,
    *,
    enemy_spawns: tuple[GridPos, ...] = (GridPos(7, 6),),
    waves: tuple[int, ...] = (1,),
) -> StageEntry:
    """One stage with a single enemy, spawning where the player is already aiming."""
    stage = Stage.create(
        stage_id=level_id,
        name=level_id.upper(),
        rows=_rows(),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(7, 12)),),
        enemy_spawns=enemy_spawns,
    )
    return StageEntry(level_id=level_id, name=stage.name, stage=stage, waves=waves)


CATALOG = (_entry("range-01"), _entry("range-02"))

SEEDED_CATALOG = (_entry("range-01", enemy_spawns=(GridPos(1, 1), GridPos(14, 1), GridPos(7, 6))),)
"""Three spawn cells, so which one a seed draws is visible in the state it produces."""

SEEDED_RULES = CampaignRules(spawn_interval_ticks=5)


def _checkpoint_of(shell: ClientShell) -> StageCheckpoint:
    checkpoint = shell.checkpoint
    assert checkpoint is not None
    return checkpoint


def _shell(profile: LocalProfile | None = None) -> ClientShell:
    return ClientShell(
        catalog=CATALOG,
        campaign_rules=RANGE_RULES,
        profile=profile if profile is not None else LocalProfile(),
    )


def _saved(
    *,
    level_id: str = "range-02",
    stage_index: int = 1,
    score: int = 900,
    lives: int = 1,
    seed: int = DEFAULT_SEED,
    catalog: tuple[StageEntry, ...] = CATALOG,
) -> LocalProfile:
    """A profile standing at a checkpoint, identified against ``catalog`` when it can be.

    An identifier that is not in ``catalog`` has no stage to be identified by, so the
    digest is a well-formed one belonging to nothing -- which is exactly the save a pack
    that was swapped leaves behind.
    """
    match = next((entry for entry in catalog if entry.level_id == level_id), None)
    identity = ABSENT_IDENTITY if match is None else stage_identity(match)
    return LocalProfile(
        progress=CampaignProgress(
            checkpoint=StageCheckpoint.of(
                level_id=level_id,
                stage_index=stage_index,
                score=score,
                lives=lives,
                seed=seed,
                stage_identity=identity,
            )
        )
    )


ABSENT_IDENTITY = "0" * 64
"""A syntactically valid identity no stage in this file produces."""


def _clear_the_stage(shell: ClientShell) -> None:
    """Drive the live stage to a clear, one tick at a time, and stop the moment it is."""
    for _ in range(CLEAR_BUDGET):
        shell.advance(1, FIRING)
        if shell.phase is not CampaignPhase.PLAYING:
            return
    raise AssertionError("the stage did not clear inside its tick budget")


# -- recording ----------------------------------------------------------------


def test_choosing_a_stage_records_the_boundary_it_opened_on() -> None:
    shell = _shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)

    assert _checkpoint_of(shell) == StageCheckpoint.of(
        level_id="range-02",
        stage_index=1,
        score=0,
        lives=RANGE_RULES.starting_lives,
        seed=shell.seed,
        stage_identity=stage_identity(CATALOG[1]),
    )


def test_clearing_a_stage_moves_the_checkpoint_on_and_counts_it() -> None:
    shell = _shell()
    assert shell.start_selected_stage()
    _clear_the_stage(shell)
    assert shell.phase is CampaignPhase.STAGE_CLEARED
    assert shell.profile.progress.stages_cleared == 1
    assert shell.profile.progress.best_score == 100

    shell.handle(Action.UI_CONFIRM)  # NEXT STAGE
    assert _checkpoint_of(shell) == StageCheckpoint.of(
        level_id="range-02",
        stage_index=1,
        score=100,
        lives=3,
        seed=shell.seed,
        stage_identity=stage_identity(CATALOG[1]),
    )


def test_completing_a_campaign_counts_the_last_stage_and_the_campaign() -> None:
    shell = _shell()
    assert shell.start_selected_stage()
    _clear_the_stage(shell)
    shell.handle(Action.UI_CONFIRM)
    _clear_the_stage(shell)

    assert shell.phase is CampaignPhase.COMPLETED
    assert shell.profile.progress.stages_cleared == 2
    assert shell.profile.progress.campaigns_completed == 1
    assert shell.profile.progress.best_score == 200


def test_a_checkpoint_records_a_boundary_and_never_a_live_simulation() -> None:
    """Twenty ticks into a stage, the saved record still describes its opening."""
    shell = _shell()
    assert shell.start_selected_stage()
    shell.advance(20, FIRING)
    assert shell.session is not None and shell.session.state.tick == 20

    checkpoint = shell.checkpoint
    assert checkpoint is not None
    assert (checkpoint.score, checkpoint.lives) == (0, RANGE_RULES.starting_lives)


# -- resuming -----------------------------------------------------------------


def test_resuming_picks_the_stage_up_with_the_anchors_it_opened_on() -> None:
    first = _shell()
    assert first.start_selected_stage()
    _clear_the_stage(first)
    first.handle(Action.UI_CONFIRM)

    resumed = _shell(LocalProfile(progress=first.profile.progress))
    assert resumed.can_resume
    assert resumed.resume_campaign()

    campaign = resumed.campaign
    assert campaign is not None
    assert (campaign.stage_index, campaign.score, campaign.lives) == (1, 100, 3)
    assert resumed.screen is Screen.PLAYING
    assert campaign.session.state.tick == 0


def test_a_resumed_stage_is_the_stage_a_restart_would_have_replayed() -> None:
    """Resuming uses the campaign's own stage anchors, so it rewinds to the same place."""
    started = _shell(_saved(score=100, lives=2))
    assert started.resume_campaign()
    campaign = started.campaign
    assert campaign is not None
    started.advance(10, FIRING)

    restarted = campaign.restarted_stage()
    assert (restarted.score, restarted.lives) == (100, 2)
    assert (campaign.stage_start_score, campaign.stage_start_lives) == (100, 2)


def test_resuming_with_nothing_saved_says_so_and_starts_nothing() -> None:
    shell = _shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.RESUME_SAVE)

    assert shell.screen is Screen.STAGE_SELECT
    assert shell.session is None
    assert "NO SAVED STAGE" in shell.notice


def test_a_save_naming_a_stage_this_pack_does_not_have_is_not_resumed() -> None:
    """A save outlives its content pack. Matching is by identifier, never by position."""
    shell = _shell(_saved(level_id="from-another-pack", score=400))
    shell.handle(Action.UI_CONFIRM)
    assert not shell.can_resume
    shell.handle(Action.RESUME_SAVE)

    assert shell.session is None
    assert "NOT IN THIS PACK" in shell.notice
    assert shell.checkpoint is not None  # refused, and never discarded


def test_a_moved_stage_is_resumed_where_it_now_sits() -> None:
    shell = _shell(_saved(level_id="range-01", stage_index=5, score=50))
    assert shell.resume_index == 0
    assert shell.resume_campaign()
    campaign = shell.campaign
    assert campaign is not None
    assert (campaign.stage_index, campaign.score, campaign.lives) == (0, 50, 1)


# -- the same campaign, not a similar one -------------------------------------


def _seeded_shell(seed: int, profile: LocalProfile | None = None) -> ClientShell:
    return ClientShell(
        catalog=SEEDED_CATALOG,
        campaign_rules=SEEDED_RULES,
        seed=seed,
        profile=profile if profile is not None else LocalProfile(),
    )


def _history(shell: ClientShell, ticks: int = 40) -> list[str]:
    """Every tick's state hash, which is what "the same stage" has to mean."""
    digests: list[str] = []
    for _ in range(ticks):
        shell.advance(1, FIRING)
        assert shell.session is not None
        digests.append(state_hash(shell.session.state))
    return digests


def test_a_resumed_stage_replays_the_seed_it_was_saved_with() -> None:
    """The launch's seed must not reach a resumed run: the saved campaign owns it."""
    saved_seed, other_seed = 12345, 999
    original = _seeded_shell(saved_seed)
    assert original.start_selected_stage()
    expected = _history(original)

    resumed = _seeded_shell(other_seed, LocalProfile(progress=original.profile.progress))
    assert resumed.seed == other_seed
    assert resumed.resume_campaign()
    assert resumed.campaign is not None
    assert resumed.campaign.seed == saved_seed
    assert _history(resumed) == expected


def test_the_seed_comparison_is_of_a_run_the_seed_actually_changes() -> None:
    """Without this, two seeds that happened to play alike would pass the test above.

    Not only different hashes -- a seed reaches the state's own generator, so those would
    differ even if nothing else did. These two seeds put a *different enemy on a
    different cell*, which is what makes resuming under the wrong one a different game.
    """
    one = _seeded_shell(12345)
    assert one.start_selected_stage()
    other = _seeded_shell(999)
    assert other.start_selected_stage()
    assert _history(one) != _history(other)
    assert _spawned(one) != _spawned(other)


def _spawned(shell: ClientShell) -> tuple[tuple[str, int, int], ...]:
    """The enemies on the board, as variant and position."""
    shell.advance(3, FIRING)
    assert shell.session is not None
    return tuple(
        (tank.variant.name, tank.position.x, tank.position.y)
        for tank in shell.session.state.tanks
        if tank.variant is not TankVariant.PLAYER
    )


def test_a_seed_survives_a_round_trip_through_a_file(tmp_path: Path) -> None:
    store = ProfileStore(lambda: tmp_path)
    played = _seeded_shell(12345, LocalProfile.load(store))
    assert played.start_selected_stage()
    expected = _history(played)

    reopened = _seeded_shell(999, LocalProfile.load(ProfileStore(lambda: tmp_path)))
    assert reopened.resume_campaign()
    assert _history(reopened) == expected


def test_a_stage_whose_data_changed_under_the_save_is_not_resumed() -> None:
    """Same pack, same level identifier, different stage. Content is identity."""
    played = _shell()
    assert played.start_selected_stage()

    edited = ClientShell(
        catalog=(_entry("range-01", waves=(9,)), CATALOG[1]),
        campaign_rules=RANGE_RULES,
        profile=LocalProfile(progress=played.profile.progress),
    )
    assert edited.checkpoint_index == 0
    assert edited.resume_index is None
    assert not edited.resume_campaign()

    assert "HAS CHANGED" in edited.notice
    assert edited.session is None
    assert edited.checkpoint == played.checkpoint  # refused, and never discarded


def test_a_stage_whose_spawns_moved_under_the_save_is_not_resumed() -> None:
    played = _shell()
    assert played.start_selected_stage()

    moved = ClientShell(
        catalog=(_entry("range-01", enemy_spawns=(GridPos(2, 2),)), CATALOG[1]),
        campaign_rules=RANGE_RULES,
        profile=LocalProfile(progress=played.profile.progress),
    )
    assert moved.resume_index is None
    assert moved.resume_notice == CHANGED_CONTENT_NOTICE


def test_the_same_identifier_in_another_pack_is_not_the_same_stage() -> None:
    """A level identifier is a name. Two packs are free to use the same one."""
    played = _shell()
    assert played.start_selected_stage()
    elsewhere = ClientShell(
        catalog=(_entry("range-01", enemy_spawns=(GridPos(1, 1), GridPos(14, 1))),),
        campaign_rules=RANGE_RULES,
        profile=LocalProfile(progress=played.profile.progress),
    )
    assert not elsewhere.can_resume
    assert elsewhere.resume_notice == CHANGED_CONTENT_NOTICE


def test_an_unchanged_pack_resumes() -> None:
    """The guard has to let the ordinary case through, or it is just a refusal."""
    played = _shell()
    assert played.start_selected_stage()
    again = _shell(LocalProfile(progress=played.profile.progress))
    assert again.resume_notice == ""
    assert again.resume_campaign()


def test_each_refusal_has_a_line_the_stage_list_has_room_for() -> None:
    """The long notice and the short one are the same table, so they cannot disagree."""
    for notice in (NO_CHECKPOINT_NOTICE, STALE_CHECKPOINT_NOTICE, CHANGED_CONTENT_NOTICE):
        assert notice in SAVED_BLOCK_NOTES
        assert all(len(line) <= 12 for line in SAVED_BLOCK_NOTES[notice])


# -- the rule that did not change ---------------------------------------------


def test_choosing_a_stage_from_the_list_still_starts_it_fresh() -> None:
    shell = _shell(_saved())
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)

    campaign = shell.campaign
    assert campaign is not None
    assert campaign.stage_index == 1
    assert campaign.score == 0
    assert campaign.lives == RANGE_RULES.starting_lives


def test_every_stage_in_the_pack_is_still_selectable_with_a_save_present() -> None:
    profile = _saved()
    for index, entry in enumerate(CATALOG):
        shell = _shell(profile)
        shell.stage_index = index
        assert shell.start_selected_stage()
        assert shell.session is not None
        assert shell.session.state.stage_id == entry.level_id
        assert shell.campaign is not None
        assert shell.campaign.score == 0


# -- through a real store -----------------------------------------------------


def test_a_campaign_survives_the_process_that_played_it(tmp_path: Path) -> None:
    store = ProfileStore(lambda: tmp_path / "profile")
    played = _shell(LocalProfile.load(store))
    assert played.start_selected_stage()
    _clear_the_stage(played)
    played.handle(Action.UI_CONFIRM)

    reopened = _shell(LocalProfile.load(ProfileStore(lambda: tmp_path / "profile")))
    assert _checkpoint_of(reopened) == _checkpoint_of(played)
    assert reopened.profile.recovery_notice == ""
    assert reopened.resume_campaign()


def test_a_shell_with_no_store_writes_nothing(tmp_path: Path) -> None:
    shell = _shell()
    assert shell.profile.store is None
    assert shell.start_selected_stage()
    _clear_the_stage(shell)
    assert not any(tmp_path.iterdir())


def test_nothing_is_written_when_nothing_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ProfileStore(lambda: tmp_path)
    profile = LocalProfile.load(store)
    writes = 0
    real = ProfileStore.save

    def counted(
        self: ProfileStore, kind: str, *, version: int, data: Mapping[str, JsonValue]
    ) -> str:
        nonlocal writes
        writes += 1
        return real(self, kind, version=version, data=data)

    monkeypatch.setattr(ProfileStore, "save", counted)
    shell = _shell(profile)
    assert shell.start_selected_stage()
    assert writes == 1

    shell.start_selected_stage()
    shell.start_selected_stage()
    assert writes == 1


def test_an_unreadable_campaign_file_is_left_alone_and_the_game_keeps_playing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "campaign.json"
    path.write_bytes(b"{ not a save")
    profile = LocalProfile.load(ProfileStore(lambda: tmp_path))
    assert "UNREADABLE" in profile.recovery_notice

    shell = _shell(profile)
    assert shell.start_selected_stage()
    _clear_the_stage(shell)

    assert path.read_bytes() == b"{ not a save"
    assert shell.profile.progress.stages_cleared == 1
    assert any("SESSION ONLY" in notice for notice in shell.profile.notices)
