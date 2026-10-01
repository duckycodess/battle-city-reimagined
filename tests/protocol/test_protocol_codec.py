"""Encoding and decoding: round trips, direction, and everything that is refused."""

from __future__ import annotations

import json
from typing import Any

import pytest
from battle_city_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ClientMessage,
    EventKind,
    GameEvent,
    InputAccepted,
    InputBatch,
    Message,
    MessageError,
    MessageType,
    Rejected,
    RejectionCode,
    ServerMessage,
    SessionClosed,
    decode_client_message,
    decode_server_message,
    encode_message,
    message_type_of,
)
from protocol_helpers import (
    SESSION_ID,
    input_batch,
    join_accepted,
    join_request,
    keyframe,
    snapshot,
    tick_events,
)


def document(message: Message) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(encode_message(message))
    return decoded


def reencode(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode()


CLIENT_MESSAGES: list[ClientMessage] = [join_request(), input_batch()]
SERVER_MESSAGES: list[ServerMessage] = [
    join_accepted(),
    InputAccepted(session_id=SESSION_ID, slot=1, sequence=4, tick=12),
    snapshot(),
    keyframe(),
    tick_events(
        GameEvent.of(EventKind.TANK_MOVED, 1, 128, 160, 128, 158, 0),
        GameEvent.of(EventKind.PROJECTILE_FIRED, 2, 1, 0, 131, 150, 0),
    ),
    Rejected(session_id=SESSION_ID, code=RejectionCode.WRONG_PLAYER, detail="slot 2", sequence=4),
    SessionClosed(session_id=SESSION_ID, code=RejectionCode.QUEUE_OVERFLOW, detail="slow"),
]


@pytest.mark.parametrize("message", CLIENT_MESSAGES, ids=lambda m: type(m).__name__)
def test_client_messages_round_trip(message: ClientMessage) -> None:
    assert decode_client_message(encode_message(message)) == message


@pytest.mark.parametrize("message", SERVER_MESSAGES, ids=lambda m: type(m).__name__)
def test_server_messages_round_trip(message: ServerMessage) -> None:
    assert decode_server_message(encode_message(message)) == message


def test_every_message_type_is_covered_by_the_round_trip_set() -> None:
    covered = {message_type_of(message) for message in CLIENT_MESSAGES + SERVER_MESSAGES}
    assert covered == set(MessageType)


def test_encoding_is_deterministic() -> None:
    assert encode_message(snapshot()) == encode_message(snapshot())


def test_every_message_declares_type_version_and_session() -> None:
    for message in CLIENT_MESSAGES + SERVER_MESSAGES:
        body = document(message)
        assert body["type"] == message_type_of(message).value
        assert body["protocol_version"] == PROTOCOL_VERSION
        assert body["session_id"] == SESSION_ID


def test_a_worst_case_keyframe_fits_in_a_frame() -> None:
    assert len(encode_message(keyframe())) < MAX_FRAME_BYTES


def test_a_server_message_is_refused_as_client_input() -> None:
    with pytest.raises(MessageError) as error:
        decode_client_message(encode_message(snapshot()))
    assert error.value.code is RejectionCode.UNEXPECTED_MESSAGE


def test_a_client_message_is_refused_as_server_output() -> None:
    with pytest.raises(MessageError) as error:
        decode_server_message(encode_message(input_batch()))
    assert error.value.code is RejectionCode.UNEXPECTED_MESSAGE


@pytest.mark.parametrize("payload", [b"{}", b'{"type":"nope"}', b'{"type":7}'])
def test_an_unusable_type_is_refused(payload: bytes) -> None:
    with pytest.raises(MessageError) as error:
        decode_client_message(payload)
    assert error.value.code is RejectionCode.UNKNOWN_MESSAGE_TYPE


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    body = document(input_batch())
    body["score"] = 9001
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.UNKNOWN_FIELD
    assert "score" in error.value.detail


def test_an_unknown_nested_field_is_refused() -> None:
    body = document(join_request())
    body["content"]["seed"] = 5
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.UNKNOWN_FIELD
    assert "content.seed" in error.value.detail


def test_a_missing_field_is_refused() -> None:
    body = document(input_batch())
    del body["sequence"]
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD
    assert "sequence is missing" in error.value.detail


def test_a_wrongly_typed_field_is_refused() -> None:
    body = document(input_batch())
    body["sequence"] = "1"
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_boolean_is_not_accepted_where_an_integer_belongs() -> None:
    body = document(input_batch())
    body["sequence"] = True
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_the_version_is_checked_before_the_rest_of_the_message() -> None:
    body = document(input_batch())
    body["protocol_version"] = PROTOCOL_VERSION + 1
    body["sequence"] = "not an integer"
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.PROTOCOL_VERSION_UNSUPPORTED


def test_an_unknown_action_kind_is_refused() -> None:
    body = document(input_batch())
    body["actions"][0]["kind"] = "spawn_enemy"
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD
    assert "ActionKind" in error.value.detail


def test_an_unknown_direction_is_refused() -> None:
    body = document(input_batch())
    body["actions"][0]["direction"] = "sideways"
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_duplicated_action_kind_is_refused_on_decode() -> None:
    body = document(input_batch())
    body["actions"] = [
        {"kind": "move", "direction": "up"},
        {"kind": "move", "direction": "down"},
    ]
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.DUPLICATE_ACTION


def test_too_many_actions_are_refused_before_they_are_read() -> None:
    body = document(input_batch())
    body["actions"] = [{"kind": "fire"}] * 50
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_an_action_that_is_not_an_object_is_refused() -> None:
    body = document(input_batch())
    body["actions"] = ["fire"]
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_an_out_of_range_slot_is_refused_on_decode() -> None:
    body = document(input_batch())
    body["slot"] = 99
    with pytest.raises(MessageError) as error:
        decode_client_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_an_omitted_target_tick_means_the_next_tick() -> None:
    batch = input_batch()
    assert batch.target_tick is None
    assert document(batch)["target_tick"] is None
    decoded = decode_client_message(encode_message(batch))
    assert isinstance(decoded, InputBatch)
    assert decoded.target_tick is None


def test_an_event_with_the_wrong_arity_is_refused() -> None:
    body = document(tick_events(GameEvent.of(EventKind.SHIELD_BROKEN, 1, 2)))
    body["events"][0]["values"] = [1]
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_an_unknown_event_kind_is_refused() -> None:
    body = document(tick_events(GameEvent.of(EventKind.SHIELD_BROKEN, 1, 2)))
    body["events"][0]["kind"] = "cheat_activated"
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_keyframe_grid_round_trips_as_rows() -> None:
    decoded = decode_server_message(encode_message(keyframe()))
    assert isinstance(decoded, type(keyframe()))
    assert decoded.grid is not None
    assert len(decoded.grid) == 16


def test_a_grid_row_that_is_not_a_string_is_refused() -> None:
    body = document(keyframe())
    body["grid"][0] = 0
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_an_unknown_rejection_code_is_refused() -> None:
    body = document(Rejected(session_id=SESSION_ID, code=RejectionCode.WRONG_PLAYER))
    body["code"] = "invented_reason"
    with pytest.raises(MessageError) as error:
        decode_server_message(reencode(body))
    assert error.value.code is RejectionCode.INVALID_FIELD
