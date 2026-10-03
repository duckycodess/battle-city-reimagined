"""The replay document: what it accepts, what it refuses, and what it is not.

A replay is a stored artifact rather than a wire message. These tests hold both halves
of that: the document round-trips through its own entrypoints, and nothing about it is
reachable from the message decoders.
"""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_protocol import (
    MAX_COMMANDS_PER_TICK,
    MAX_FRAME_BYTES,
    MAX_REPLAY_BYTES,
    MAX_REPLAY_TICKS,
    REPLAY_FORMAT,
    REPLAY_VERSION,
    ActionKind,
    DirectionCode,
    MessageError,
    MessageType,
    RejectionCode,
    ReplayCommand,
    ReplayCommandKind,
    ReplayDespawnPowerup,
    ReplayDocument,
    ReplayError,
    ReplayFire,
    ReplayHash,
    ReplayMove,
    ReplayRejection,
    ReplayRespawn,
    ReplaySpawnEnemy,
    ReplaySpawnPowerup,
    ReplayTick,
    command_kind_of,
    decode_client_message,
    decode_replay,
    encode_replay,
)
from replay_helpers import as_json, make_metadata, minimal_document, to_bytes

DIGEST = "a" * 64


def every_command() -> tuple[ReplayCommand, ...]:
    """One of each of the six recorded command variants."""
    return (
        ReplayMove(tank_id=1, direction=DirectionCode.UP),
        ReplayFire(tank_id=1),
        ReplayRespawn(slot=2),
        ReplaySpawnEnemy(cell_x=2, cell_y=1, variant=2, facing=DirectionCode.DOWN),
        ReplaySpawnPowerup(cell_x=3, cell_y=4, powerup=1),
        ReplayDespawnPowerup(powerup_id=9),
    )


def test_a_document_round_trips_through_its_own_entrypoints() -> None:
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(ReplayTick(tick=0, commands=every_command()), ReplayTick(tick=1)),
        hashes=(ReplayHash(tick=0, digest=DIGEST),),
    )
    assert decode_replay(encode_replay(document)) == document


def test_encoding_is_deterministic() -> None:
    """One document is one byte string, so a recording's digest is stable."""
    document = minimal_document()
    assert encode_replay(document) == encode_replay(document)


def test_every_command_variant_is_covered_by_the_kind_table() -> None:
    """A seventh simulation command must not be able to land here unnamed."""
    kinds = {command_kind_of(command) for command in every_command()}
    assert kinds == set(ReplayCommandKind)
    assert len(every_command()) == len(ReplayCommandKind)


def test_every_command_variant_survives_a_round_trip() -> None:
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=(ReplayTick(tick=0, commands=every_command()),),
    )
    assert decode_replay(encode_replay(document)).ticks[0].commands == every_command()


# -- refusals ------------------------------------------------------------------------


def test_malformed_bytes_are_refused() -> None:
    with pytest.raises(ReplayError) as error:
        decode_replay(b'{"format":')
    assert error.value.code is ReplayRejection.MALFORMED_REPLAY


def test_a_top_level_array_is_refused() -> None:
    with pytest.raises(ReplayError) as error:
        decode_replay(b"[]")
    assert error.value.code is ReplayRejection.MALFORMED_REPLAY


def test_an_oversized_document_is_refused_on_its_length() -> None:
    with pytest.raises(ReplayError) as error:
        decode_replay(b"x" * (MAX_REPLAY_BYTES + 1))
    assert error.value.code is ReplayRejection.REPLAY_TOO_LARGE


def test_an_oversized_document_is_refused_while_being_written_too() -> None:
    """A recording nothing could read back is refused at the point it is produced."""
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=tuple(
            ReplayTick(
                tick=tick,
                commands=tuple(
                    ReplayMove(tank_id=index + 1, direction=DirectionCode.UP)
                    for index in range(MAX_COMMANDS_PER_TICK)
                ),
            )
            for tick in range(MAX_REPLAY_TICKS)
        ),
    )
    with pytest.raises(ReplayError) as error:
        encode_replay(document)
    assert error.value.code is ReplayRejection.REPLAY_TOO_LARGE


def test_a_document_from_another_format_is_refused() -> None:
    body = as_json(minimal_document())
    body["format"] = "battle-city-reimagined/local-save"
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.UNKNOWN_DOCUMENT


def test_a_newer_replay_version_is_refused_rather_than_migrated() -> None:
    body = as_json(minimal_document())
    body["replay_version"] = REPLAY_VERSION + 1
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.REPLAY_VERSION_UNSUPPORTED
    assert str(REPLAY_VERSION) in error.value.detail


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    body = as_json(minimal_document())
    body["extra"] = 1
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.UNKNOWN_FIELD


def test_a_missing_field_is_refused() -> None:
    body = as_json(minimal_document())
    del body["metadata"]["seed"]
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.INVALID_FIELD


def test_an_unknown_command_kind_is_refused() -> None:
    body = as_json(minimal_document())
    body["ticks"][0]["commands"][0]["kind"] = "award_score"
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.UNKNOWN_COMMAND


def test_a_command_carrying_a_field_of_another_kind_is_refused() -> None:
    body = as_json(minimal_document())
    body["ticks"][0]["commands"][0]["tank_id"] = 1
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.UNKNOWN_FIELD


def test_ticks_out_of_order_are_refused() -> None:
    with pytest.raises(ReplayError) as error:
        ReplayDocument(
            metadata=make_metadata(),
            ticks=(ReplayTick(tick=1), ReplayTick(tick=0)),
        )
    assert error.value.code is ReplayRejection.INVALID_FIELD


def test_a_repeated_tick_is_refused() -> None:
    with pytest.raises(ReplayError) as error:
        ReplayDocument(metadata=make_metadata(), ticks=(ReplayTick(tick=3), ReplayTick(tick=3)))
    assert error.value.code is ReplayRejection.INVALID_FIELD


def test_duplicate_slots_are_refused() -> None:
    with pytest.raises(ReplayError) as error:
        make_metadata(slots=(1, 1))
    assert error.value.code is ReplayRejection.INVALID_FIELD


def test_a_digest_that_is_not_one_is_refused() -> None:
    with pytest.raises(ReplayError) as error:
        ReplayHash(tick=0, digest="not-a-digest")
    assert error.value.code is ReplayRejection.INVALID_FIELD


# -- credentials ---------------------------------------------------------------------


@pytest.mark.parametrize("key", ["token", "ticket", "credentials", "password", "authorization"])
def test_a_credential_key_is_refused_by_name_at_the_top_level(key: str) -> None:
    body = as_json(minimal_document())
    body[key] = "anything"
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.CREDENTIAL_FIELD


def test_a_credential_key_is_refused_however_deeply_it_is_buried() -> None:
    body = as_json(minimal_document())
    body["metadata"]["content"]["token"] = "secret-value"
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.CREDENTIAL_FIELD
    assert "secret-value" not in error.value.detail


def test_a_credential_key_inside_an_array_is_refused() -> None:
    body = as_json(minimal_document())
    body["ticks"][0]["commands"].append({"kind": "fire", "tank_id": 1, "token": "x"})
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.CREDENTIAL_FIELD


def test_a_credential_key_is_refused_whatever_its_case() -> None:
    body = as_json(minimal_document())
    body["metadata"]["Token"] = "x"
    with pytest.raises(ReplayError) as error:
        decode_replay(to_bytes(body))
    assert error.value.code is ReplayRejection.CREDENTIAL_FIELD


def test_the_metadata_record_has_no_field_a_credential_could_travel_in() -> None:
    names = {field.name for field in dataclasses.fields(make_metadata())}
    assert not names & {"token", "tokens", "credential", "credentials", "ticket", "secret"}


# -- a replay is not a message -------------------------------------------------------


def test_no_message_type_names_a_replay() -> None:
    assert REPLAY_FORMAT not in {kind.value for kind in MessageType}
    assert "replay" not in {kind.value for kind in MessageType}


def test_a_replay_document_is_not_accepted_as_a_client_message() -> None:
    with pytest.raises(MessageError) as error:
        decode_client_message(encode_replay(minimal_document()))
    assert error.value.code is RejectionCode.UNKNOWN_MESSAGE_TYPE


def test_the_spawn_commands_have_no_client_action_to_arrive_in() -> None:
    """A client may move, fire and respawn. A replay also records what the server did."""
    recorded = {kind.value for kind in ReplayCommandKind}
    allowed = {kind.value for kind in ActionKind}
    assert recorded - allowed == {"spawn_enemy", "spawn_powerup", "despawn_powerup"}


@pytest.mark.parametrize("kind", ["spawn_enemy", "spawn_powerup", "despawn_powerup"])
def test_an_input_batch_naming_a_recorded_only_command_is_refused(kind: str) -> None:
    payload = to_bytes(
        {
            "type": "input_batch",
            "protocol_version": 2,
            "session_id": "replay-session",
            "slot": 1,
            "sequence": 1,
            "target_tick": None,
            "actions": [{"kind": kind, "direction": None}],
        }
    )
    with pytest.raises(MessageError) as error:
        decode_client_message(payload)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_document_well_over_the_wire_frame_bound_round_trips() -> None:
    """A replay is a file, not a packet: the encoder is bounded by the replay limit.

    Encoding at the frame bound would refuse to write documents :func:`decode_replay`
    is perfectly willing to read, which is the worst of both bounds.
    """
    document = ReplayDocument(
        metadata=make_metadata(),
        ticks=tuple(
            ReplayTick(
                tick=tick,
                commands=(
                    ReplayMove(tank_id=1, direction=DirectionCode.UP),
                    ReplayFire(tank_id=1),
                ),
            )
            for tick in range(1100)
        ),
    )
    payload = encode_replay(document)
    assert MAX_FRAME_BYTES < len(payload) <= MAX_REPLAY_BYTES
    assert len(payload) > 100_000
    assert decode_replay(payload) == document
