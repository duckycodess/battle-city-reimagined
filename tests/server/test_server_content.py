"""Where content, simulation and protocol meet.

The content package will not build a stage and the protocol package does not know what
a level is, so this mapping is the server's job and these tests are the only place the
three vocabularies are checked against each other.
"""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_content import load_bundled_pack
from battle_city_protocol import StateSnapshot
from battle_city_server import (
    GameSession,
    PlayerCredential,
    ServerConfigurationError,
    SessionConfig,
    SessionLimits,
    content_ref_for,
    grid_rows,
    rules_digest,
    stage_from_level,
)
from battle_city_sim import CLASSIC_GRID_SIZE, DEFAULT_RULES, Rules, Stage
from server_helpers import TOKENS, make_config, make_stage

CLASSIC_RULES_DIGEST = "b88237d8fbb36b0f5298255da19464073713a63499d46f0f4e4ecc573b3a5e89"


def test_every_bundled_level_builds_a_stage() -> None:
    pack = load_bundled_pack()
    for level in pack.levels:
        stage = stage_from_level(level)
        assert stage.stage_id == level.level_id
        assert stage.grid.width == CLASSIC_GRID_SIZE
        assert stage.grid.height == CLASSIC_GRID_SIZE
        assert stage.base_cell.x == level.base_cell.x
        assert stage.base_cell.y == level.base_cell.y
        assert [spawn.slot for spawn in stage.player_spawns] == [
            spawn.slot for spawn in level.player_spawns
        ]
        assert len(stage.enemy_spawns) == len(level.enemy_spawns)


def test_the_stage_grid_is_the_level_grid_unchanged() -> None:
    level = load_bundled_pack().level("classic-01")
    stage = stage_from_level(level)
    assert grid_rows_of(stage) == level.grid.rows


def grid_rows_of(stage: Stage) -> tuple[str, ...]:
    return tuple("".join(str(tile.value) for tile in row) for row in stage.grid.rows)


def test_a_content_reference_names_the_pack_version_level_and_schema() -> None:
    pack = load_bundled_pack()
    level = pack.level("classic-02")
    reference = content_ref_for(pack, level)
    assert reference.pack_id == pack.pack_id
    assert reference.pack_version == pack.version
    assert reference.level_id == "classic-02"
    assert reference.content_schema_version == level.schema_version


def test_a_session_built_from_bundled_content_broadcasts_that_terrain() -> None:
    pack = load_bundled_pack()
    level = pack.level("classic-01")
    stage = stage_from_level(level)
    config = SessionConfig(
        session_id="classic-session",
        stage=stage,
        content=content_ref_for(pack, level),
        credentials=(PlayerCredential(slot=1, token=TOKENS[1]),),
        seed=11,
        limits=SessionLimits(),
    )
    session = GameSession(config)
    connection = session.connect()
    snapshot = session.snapshot(keyframe=True)
    assert isinstance(snapshot, StateSnapshot)
    assert snapshot.grid == level.grid.rows
    assert grid_rows(session.state) == level.grid.rows
    assert session.info.content.level_id == "classic-01"
    assert connection == 1


def test_the_rules_digest_is_stable_for_the_classic_ruleset() -> None:
    """A pinned digest. A rule change must be a deliberate, visible compatibility break."""
    assert rules_digest(DEFAULT_RULES) == CLASSIC_RULES_DIGEST


def test_the_rules_digest_changes_with_the_rules() -> None:
    faster = dataclasses.replace(DEFAULT_RULES, tank_speed=DEFAULT_RULES.tank_speed + 1)
    assert rules_digest(faster) != rules_digest(DEFAULT_RULES)


def test_the_rules_digest_covers_every_field() -> None:
    """Every rule is in the fingerprint, so no tunable can drift between peers unseen."""
    baseline = rules_digest(DEFAULT_RULES)
    for field in dataclasses.fields(Rules()):
        value = getattr(DEFAULT_RULES, field.name)
        changed = dataclasses.replace(DEFAULT_RULES, **{field.name: value + 1})
        assert rules_digest(changed) != baseline, field.name


def test_a_credential_for_a_slot_the_stage_has_no_spawn_for_is_refused() -> None:
    with pytest.raises(ServerConfigurationError) as error:
        SessionConfig(
            session_id="session-1",
            stage=make_stage(slots=(1,)),
            content=make_config().content,
            credentials=(PlayerCredential(slot=2, token=TOKENS[2]),),
            seed=1,
        )
    assert "no spawn" in str(error.value)


def test_a_duplicate_credential_is_refused() -> None:
    with pytest.raises(ServerConfigurationError):
        SessionConfig(
            session_id="session-1",
            stage=make_stage(),
            content=make_config().content,
            credentials=(
                PlayerCredential(slot=1, token=TOKENS[1]),
                PlayerCredential(slot=1, token=TOKENS[2]),
            ),
            seed=1,
        )


def test_an_unusable_token_is_refused_without_quoting_it() -> None:
    with pytest.raises(ServerConfigurationError) as error:
        SessionConfig(
            session_id="session-1",
            stage=make_stage(),
            content=make_config().content,
            credentials=(PlayerCredential(slot=1, token="short"),),
            seed=1,
        )
    assert "slot 1" in str(error.value)
    assert "short" not in str(error.value)


def test_a_session_needs_at_least_one_credential() -> None:
    with pytest.raises(ServerConfigurationError):
        SessionConfig(
            session_id="session-1",
            stage=make_stage(),
            content=make_config().content,
            credentials=(),
            seed=1,
        )


def test_limits_must_be_positive() -> None:
    with pytest.raises(ServerConfigurationError):
        SessionLimits(max_pending_batches=0)
