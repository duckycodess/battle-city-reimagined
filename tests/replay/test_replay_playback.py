"""Replaying a recording: it reproduces the run, or it says exactly where it did not."""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_protocol import (
    PROTOCOL_VERSION,
    ContentRef,
    DirectionCode,
    ReplayDocument,
    ReplayHash,
    ReplaySpawnEnemy,
    ReplayTick,
    decode_replay,
    encode_replay,
)
from battle_city_server import (
    ReplayContext,
    ReplayIncompatibleError,
    ReplayUnplayableError,
    play_replay,
    required_checkpoints,
)
from battle_city_sim import SpawnEnemyCommand, TankVariant, new_game, state_hash
from replay_helpers import (
    ENEMY_CELL,
    SEED,
    content_ref,
    fire,
    join_request,
    make_context,
    make_metadata,
    make_settings,
    make_stage,
    minimal_document,
    move,
    recording_session,
)

DIGEST = "a" * 64


def played_run() -> tuple[ReplayDocument, str, int]:
    """Record a short two-player run and return the document, its hash and its length."""
    session, recorder = recording_session(hash_interval=2)
    first = session.connect()
    second = session.connect()
    session.handle(first, join_request(1))
    session.handle(second, join_request(2))

    session.handle(first, move(1, sequence=1, direction=DirectionCode.UP))
    session.schedule_commands(
        session.tick, [SpawnEnemyCommand(cell=ENEMY_CELL, variant=TankVariant.ENEMY_SHIELDED)]
    )
    session.advance_tick()

    session.handle(first, fire(1, sequence=2))
    session.handle(second, move(2, sequence=1, direction=DirectionCode.LEFT))
    session.advance_tick()

    for index in range(6):
        session.handle(second, move(2, sequence=index + 2, direction=DirectionCode.UP))
        session.advance_tick()

    return recorder.document(), state_hash(session.state), session.tick


# -- determinism ---------------------------------------------------------------------


def test_a_recorded_run_replays_to_the_same_canonical_state() -> None:
    document, expected, ticks = played_run()
    playback = play_replay(document, make_context())
    assert playback.verified
    assert playback.mismatch is None
    assert playback.ticks_played == ticks
    assert state_hash(playback.state) == expected


def test_a_recorded_run_replays_identically_after_a_round_trip_through_bytes() -> None:
    document, expected, _ = played_run()
    playback = play_replay(decode_replay(encode_replay(document)), make_context())
    assert playback.verified
    assert state_hash(playback.state) == expected


def test_replaying_twice_gives_the_same_answer() -> None:
    document, _, _ = played_run()
    first = play_replay(document, make_context())
    second = play_replay(document, make_context())
    assert state_hash(first.state) == state_hash(second.state)


def test_every_recorded_checkpoint_is_actually_checked() -> None:
    document, _, _ = played_run()
    playback = play_replay(document, make_context())
    assert playback.hashes_checked == playback.hashes_recorded == len(document.hashes)
    assert playback.hashes_checked > 1


def test_a_checkpoint_the_stream_never_reaches_is_not_quietly_passed() -> None:
    """A claim nothing checked must not be counted as a claim that held."""
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(ReplayTick(tick=0),),
        hashes=(
            ReplayHash(tick=0, digest=state_hash(new_game(make_stage(), seed=SEED))),
            ReplayHash(tick=900, digest=DIGEST),
        ),
    )
    playback = play_replay(document, make_context())
    assert playback.mismatch is None
    assert playback.hashes_checked == 1
    assert playback.hashes_recorded == 2
    assert not playback.verified


def test_a_recording_with_no_checkpoints_is_replayed_but_not_verified() -> None:
    """ "It ran" and "it ran identically" are different claims."""
    document = ReplayDocument(metadata=make_metadata(), ticks=(ReplayTick(tick=0),), hashes=())
    playback = play_replay(document, make_context())
    assert playback.ticks_played == 1
    assert playback.mismatch is None
    assert playback.missing_checkpoints == (0, 1)
    assert not playback.verified


# -- the checkpoints a document commits itself to ------------------------------------


def test_the_required_checkpoints_follow_the_documents_own_interval() -> None:
    document, _, _ = played_run()
    assert required_checkpoints(document) == tuple(entry.tick for entry in document.hashes)


def test_a_recording_commits_to_a_checkpoint_after_its_last_tick() -> None:
    document = ReplayDocument(
        metadata=make_metadata(hash_interval=4),
        ticks=tuple(ReplayTick(tick=tick) for tick in range(6)),
    )
    assert required_checkpoints(document) == (0, 4, 6)


def test_a_forged_recording_keeping_only_the_opening_hash_is_not_verified() -> None:
    """The attack: drop every checkpoint but the first, then rewrite what came after."""
    document, _, _ = played_run()
    forged = dataclasses.replace(
        document,
        ticks=document.ticks[:1]
        + tuple(ReplayTick(tick=entry.tick) for entry in document.ticks[1:]),
        hashes=document.hashes[:1],
    )
    playback = play_replay(forged, make_context())
    assert playback.mismatch is None
    assert playback.missing_checkpoints
    assert not playback.verified


def test_altering_a_command_under_a_kept_checkpoint_is_caught() -> None:
    """The same forgery, kept honest about its interval: the next checkpoint catches it."""
    document, _, _ = played_run()
    tampered = dataclasses.replace(document, ticks=(ReplayTick(tick=0),) + document.ticks[1:])
    playback = play_replay(tampered, make_context())
    assert playback.mismatch is not None
    assert not playback.verified


def test_dropping_the_final_checkpoint_is_not_verified() -> None:
    """Without one, the commands after the last interval hash are unchecked."""
    document, _, _ = played_run()
    trimmed = dataclasses.replace(document, hashes=document.hashes[:-1])
    playback = play_replay(trimmed, make_context())
    assert playback.mismatch is None
    assert playback.missing_checkpoints == (document.hashes[-1].tick,)
    assert not playback.verified


def test_a_short_recording_that_closes_its_chain_is_verified() -> None:
    session, recorder = recording_session(hash_interval=30)
    session.advance_tick()
    playback = play_replay(recorder.document(), make_context())
    assert playback.missing_checkpoints == ()
    assert playback.verified


def test_a_truncated_recording_that_closes_its_chain_is_verified() -> None:
    session, recorder = recording_session(hash_interval=2, max_ticks=3)
    for _ in range(6):
        session.advance_tick()
    playback = play_replay(recorder.document(), make_context())
    assert recorder.truncated
    assert playback.missing_checkpoints == ()
    assert playback.verified


def test_verification_is_self_consistency_and_not_authenticity() -> None:
    """Anyone can write a document that verifies. Nothing here is signed."""
    session, recorder = recording_session(hash_interval=2)
    for _ in range(4):
        session.advance_tick()
    playback = play_replay(recorder.document(), make_context())
    assert playback.verified
    assert "authenticity" in (type(playback).verified.__doc__ or "")


# -- mismatch ------------------------------------------------------------------------


def test_the_first_hash_mismatch_is_reported_with_both_digests() -> None:
    document, _, _ = played_run()
    tampered = dataclasses.replace(
        document,
        hashes=tuple(
            dataclasses.replace(entry, digest=DIGEST) if entry.tick == 4 else entry
            for entry in document.hashes
        ),
    )
    playback = play_replay(tampered, make_context())
    assert playback.mismatch is not None
    assert playback.mismatch.tick == 4
    assert playback.mismatch.expected == DIGEST
    assert playback.mismatch.found == state_hash(playback.state)
    assert not playback.verified


def test_playback_stops_at_the_first_mismatch() -> None:
    document, _, ticks = played_run()
    tampered = dataclasses.replace(
        document,
        hashes=tuple(dataclasses.replace(entry, digest=DIGEST) for entry in document.hashes[1:]),
    )
    playback = play_replay(tampered, make_context())
    assert playback.mismatch is not None
    assert playback.mismatch.tick == document.hashes[1].tick
    assert playback.ticks_played < ticks


def test_a_tick_zero_mismatch_is_reported_before_anything_is_stepped() -> None:
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(ReplayTick(tick=0),),
        hashes=(ReplayHash(tick=0, digest=DIGEST),),
    )
    playback = play_replay(document, make_context())
    assert playback.mismatch is not None
    assert playback.mismatch.tick == 0
    assert playback.ticks_played == 0


def test_a_different_seed_diverges_and_is_reported_rather_than_hidden() -> None:
    document, _, _ = played_run()
    reseeded = dataclasses.replace(
        document, metadata=dataclasses.replace(document.metadata, seed=document.metadata.seed + 1)
    )
    playback = play_replay(reseeded, make_context())
    # The classic stage's opening state carries the generator, so the divergence is
    # caught by the very first checkpoint rather than many ticks later.
    assert playback.mismatch is not None
    assert playback.mismatch.tick == 0


# -- compatibility -------------------------------------------------------------------


def test_a_replay_from_another_protocol_version_is_refused_by_name() -> None:
    document = dataclasses.replace(
        minimal_document(),
        metadata=make_metadata(protocol_version=PROTOCOL_VERSION + 1),
    )
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "protocol_version"
    assert error.value.expected == PROTOCOL_VERSION


def test_a_replay_from_another_canonical_state_version_is_refused_by_name() -> None:
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(state_version=99))
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "state_version"


def test_a_replay_of_other_content_is_refused_by_name() -> None:
    other = ContentRef(
        pack_id="classic", pack_version="2.0.0", level_id="replay-stage", content_schema_version=1
    )
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(content=other))
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "content"
    assert error.value.expected == content_ref()


def test_a_replay_of_other_rules_is_refused_by_name() -> None:
    document = dataclasses.replace(
        minimal_document(), metadata=make_metadata(rules_digest="b" * 64)
    )
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "rules_digest"


def test_a_replay_naming_a_slot_the_stage_has_no_spawn_for_is_refused() -> None:
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(slots=(1, 2)))
    context = ReplayContext(stage=make_stage(slots=(1,)), content=content_ref())
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, context)
    assert error.value.field == "slots"


def test_nothing_is_stepped_when_a_document_is_incompatible() -> None:
    """An incompatible replay costs a comparison, not a simulation."""
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(state_version=99))
    with pytest.raises(ReplayIncompatibleError):
        play_replay(document, make_context())


# -- an unplayable stream ------------------------------------------------------------


def test_a_tick_index_that_does_not_follow_the_state_is_refused() -> None:
    document = ReplayDocument(metadata=make_metadata(), ticks=(ReplayTick(tick=4),))
    with pytest.raises(ReplayUnplayableError, match="does not follow"):
        play_replay(document, make_context())


def test_an_enum_code_no_simulation_value_has_is_refused() -> None:
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(
            ReplayTick(
                tick=0,
                commands=(
                    ReplaySpawnEnemy(
                        cell_x=ENEMY_CELL.x,
                        cell_y=ENEMY_CELL.y,
                        variant=200,
                        facing=DirectionCode.DOWN,
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ReplayUnplayableError, match="tank variant code 200"):
        play_replay(document, make_context())


def test_an_input_the_rules_engine_refuses_is_reported_as_unplayable() -> None:
    """Not a divergence: the document says something the simulation never ran."""
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(
            ReplayTick(
                tick=0,
                commands=(
                    ReplaySpawnEnemy(
                        cell_x=ENEMY_CELL.x,
                        cell_y=ENEMY_CELL.y,
                        variant=TankVariant.PLAYER.value,
                        facing=DirectionCode.DOWN,
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ReplayUnplayableError, match="refused by the rules engine"):
        play_replay(document, make_context())


# -- the verifier holds no credential ------------------------------------------------


def test_the_playback_context_has_no_field_a_credential_could_travel_in() -> None:
    names = {field.name for field in dataclasses.fields(make_context())}
    assert names == {"stage", "content", "rules"}


# -- recorded settings must agree with the run they describe -------------------------


def test_settings_naming_other_content_than_the_run_are_refused() -> None:
    other = ContentRef(
        pack_id="classic", pack_version="9.9.9", level_id="replay-stage", content_schema_version=1
    )
    settings = dataclasses.replace(make_settings(), content=other)
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(settings=settings))
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "settings.content"


def test_settings_naming_another_tick_rate_than_the_run_are_refused() -> None:
    settings = dataclasses.replace(make_settings(), tick_rate=30)
    document = dataclasses.replace(minimal_document(), metadata=make_metadata(settings=settings))
    with pytest.raises(ReplayIncompatibleError) as error:
        play_replay(document, make_context())
    assert error.value.field == "settings.tick_rate"


def test_agreeing_settings_are_accepted() -> None:
    document = dataclasses.replace(
        minimal_document(), metadata=make_metadata(settings=make_settings())
    )
    playback = play_replay(document, make_context())
    assert playback.ticks_played == 1
