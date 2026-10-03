"""The two records a profile holds: what they accept, and what they will not carry.

Two claims matter more than the rest and are asserted directly rather than described.

**A settings file cannot hold a secret.** The record has three fields, the reader refuses
a fourth, and a payload carrying a lobby ticket or a server address is rejected outright
rather than being loaded and ignored.

**A saved roster name is a name a lobby will take.** The settings record restates the
protocol's roster rule, so the test puts a saved name through a real
:class:`~battle_city_protocol.LobbyJoin`: if the two rules ever drift, this fails.
"""

from __future__ import annotations

import pytest
from battle_city_client import theme
from battle_city_client.persistence import (
    DEFAULT_BADGE_ID,
    MAX_FRAME_CAP,
    MAX_SEED,
    MIN_FRAME_CAP,
    CampaignProgress,
    CorruptSave,
    LocalSettings,
    StageCheckpoint,
    progress_from_data,
    settings_from_data,
)
from battle_city_protocol import ContentRef, JsonValue, LobbyJoin
from battle_city_sim.rng import Rng

CONTENT = ContentRef(
    pack_id="classic", pack_version="1.0.0", level_id="classic-01", content_schema_version=1
)
IDENTITY = "a" * 64
"""A stage identity's shape, with no stage behind it. See ``test_persistence_resume``."""


def _checkpoint(
    *, level_id: str = "classic-01", stage_index: int = 0, score: int = 0, lives: int = 3
) -> StageCheckpoint:
    return StageCheckpoint(
        level_id=level_id,
        stage_index=stage_index,
        score=score,
        lives=lives,
        seed=7,
        stage_identity=IDENTITY,
    )


# -- settings -----------------------------------------------------------------


def test_settings_round_trip_through_their_payload() -> None:
    original = LocalSettings(scale=5, frame_cap=30, display_name="ducky")
    assert settings_from_data(original.to_data()) == original


def test_a_settings_payload_holds_exactly_three_fields_and_no_credential() -> None:
    assert set(LocalSettings().to_data()) == {"display_name", "frame_cap", "scale"}


@pytest.mark.parametrize("secret", ["ticket", "server", "session_id", "token"])
def test_a_settings_payload_carrying_a_credential_is_refused(secret: str) -> None:
    """Not ignored: a file with a ticket in it is not a settings file this build wrote."""
    payload = {**LocalSettings().to_data(), secret: "ticket-aaaaaaaaaaaa"}
    with pytest.raises(CorruptSave, match="unknown"):
        settings_from_data(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scale", theme.MIN_SCALE - 1),
        ("scale", theme.MAX_SCALE + 1),
        ("scale", "3"),
        ("scale", True),
        ("frame_cap", MIN_FRAME_CAP - 1),
        ("frame_cap", MAX_FRAME_CAP + 1),
        ("frame_cap", 12.5),
        ("display_name", ""),
        ("display_name", "has a space"),
        ("display_name", "x" * 64),
        ("display_name", 7),
    ],
)
def test_a_settings_value_outside_its_bound_is_refused_not_clamped(
    field: str, value: JsonValue
) -> None:
    with pytest.raises(CorruptSave):
        settings_from_data({**LocalSettings().to_data(), field: value})


def test_a_control_character_in_a_saved_name_is_refused() -> None:
    with pytest.raises(CorruptSave):
        settings_from_data({**LocalSettings().to_data(), "display_name": "duck\x07y"})


def test_a_saved_name_is_a_name_a_lobby_will_accept() -> None:
    settings = settings_from_data(LocalSettings(display_name="ducky-1").to_data())
    joined = LobbyJoin(
        session_id="session-1",
        ticket="ticket-host-aaaaaaaa",
        display_name=settings.display_name,
        content=CONTENT,
    )
    assert joined.display_name == "ducky-1"


def test_the_window_scale_is_clamped_only_where_a_window_reports_one() -> None:
    """``with_scale`` takes what the presenter settled on, which is already bounded."""
    assert LocalSettings().with_scale(theme.MAX_SCALE + 4).scale == theme.MAX_SCALE
    assert LocalSettings().with_scale(theme.MIN_SCALE - 4).scale == theme.MIN_SCALE


# -- campaign progress --------------------------------------------------------


def test_progress_round_trips_with_and_without_a_checkpoint() -> None:
    empty = CampaignProgress()
    assert progress_from_data(empty.to_data()) == empty

    standing = CampaignProgress(
        checkpoint=_checkpoint(level_id="classic-02", stage_index=1, score=900, lives=2),
        stages_cleared=1,
        campaigns_completed=0,
        best_score=900,
        selected_badge="veteran",
    )
    assert progress_from_data(standing.to_data()) == standing


def test_a_checkpoint_holds_a_stage_boundary_and_nothing_from_a_live_run() -> None:
    """No tick, no tanks, no terrain: the save is a boundary, not a simulation snapshot."""
    assert set(_checkpoint().to_data()) == {
        "level_id",
        "lives",
        "score",
        "seed",
        "stage_identity",
        "stage_index",
    }


def test_a_checkpoint_folds_a_seed_the_way_the_generator_does() -> None:
    """Any integer a launch can carry is a seed a save can hold, and the same stream."""
    folded = StageCheckpoint.of(
        level_id="classic-01", stage_index=0, score=0, lives=3, seed=-1, stage_identity=IDENTITY
    )
    assert folded.seed == MAX_SEED
    assert Rng.from_seed(-1) == Rng.from_seed(folded.seed)

    huge = StageCheckpoint.of(
        level_id="classic-01",
        stage_index=0,
        score=0,
        lives=3,
        seed=MAX_SEED + 7,
        stage_identity=IDENTITY,
    )
    assert Rng.from_seed(MAX_SEED + 7) == Rng.from_seed(huge.seed)


def test_a_seed_that_was_not_folded_is_refused_by_the_record() -> None:
    """The constructor folds; the record itself will not hold anything else."""
    with pytest.raises(ValueError, match="folded"):
        StageCheckpoint(
            level_id="classic-01",
            stage_index=0,
            score=0,
            lives=3,
            seed=MAX_SEED + 1,
            stage_identity=IDENTITY,
        )


@pytest.mark.parametrize(
    "identity", ["", "abc", IDENTITY.upper(), IDENTITY[:-1] + "g", IDENTITY + "0"]
)
def test_an_identity_that_is_not_a_digest_is_refused(identity: str) -> None:
    """A checkpoint that could not be matched is worse than no checkpoint."""
    with pytest.raises(ValueError, match="identity"):
        StageCheckpoint(
            level_id="classic-01",
            stage_index=0,
            score=0,
            lives=3,
            seed=1,
            stage_identity=identity,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("stages_cleared", -1),
        ("campaigns_completed", -1),
        ("best_score", -1),
        ("best_score", "900"),
        ("selected_badge", ""),
        ("selected_badge", 3),
        ("checkpoint", 5),
        ("checkpoint", []),
    ],
)
def test_a_progress_value_outside_its_bound_is_refused(field: str, value: JsonValue) -> None:
    with pytest.raises(CorruptSave):
        progress_from_data({**CampaignProgress().to_data(), field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage_index", -1),
        ("score", -1),
        ("lives", 0),
        ("level_id", ""),
        ("lives", "3"),
        ("seed", -1),
        ("seed", MAX_SEED + 1),
        ("seed", "1"),
        ("stage_identity", ""),
        ("stage_identity", "not-a-digest"),
    ],
)
def test_a_checkpoint_value_outside_its_bound_is_refused(field: str, value: JsonValue) -> None:
    checkpoint = _checkpoint()
    payload = {**CampaignProgress().to_data(), "checkpoint": {**checkpoint.to_data(), field: value}}
    with pytest.raises(CorruptSave):
        progress_from_data(payload)


def test_an_unknown_progress_field_is_refused() -> None:
    with pytest.raises(CorruptSave, match="unknown"):
        progress_from_data({**CampaignProgress().to_data(), "unlocked_badges": ["ace"]})


def test_tallies_only_ever_move_forward() -> None:
    progress = CampaignProgress(stages_cleared=2, best_score=1200)
    raised = progress.with_stage_cleared(500)
    assert raised.stages_cleared == 3
    assert raised.best_score == 1200
    assert progress.with_best_score(50).best_score == 1200
    assert progress.with_best_score(5000).best_score == 5000


def test_a_fresh_profile_starts_on_the_default_badge() -> None:
    assert CampaignProgress().selected_badge == DEFAULT_BADGE_ID
