"""Message bounds, the client action allowlist, and the version contract."""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_protocol import (
    ALLOWED_ACTION_KINDS,
    CLIENT_MESSAGE_TYPES,
    MAX_ACTIONS_PER_BATCH,
    MAX_SLOT,
    MAX_TICK_RATE,
    PROTOCOL_VERSION,
    SERVER_MESSAGE_TYPES,
    SNAPSHOT_VERSION,
    ActionKind,
    ClientMessage,
    DirectionCode,
    InputBatch,
    MessageError,
    MessageType,
    PlayerAction,
    Rejected,
    RejectionCode,
    SessionClosed,
    TankSnapshot,
)
from protocol_helpers import (
    GRID,
    SESSION_ID,
    input_batch,
    join_request,
    keyframe,
    session_info,
    snapshot,
)


def test_message_type_sets_partition_the_enum() -> None:
    assert set(MessageType) == CLIENT_MESSAGE_TYPES | SERVER_MESSAGE_TYPES
    assert not CLIENT_MESSAGE_TYPES & SERVER_MESSAGE_TYPES


def test_client_messages_are_only_join_and_input() -> None:
    """The union is the allowlist. Widening it is a deliberate, reviewable edit."""
    assert {MessageType.JOIN_REQUEST, MessageType.INPUT_BATCH} == CLIENT_MESSAGE_TYPES


def test_action_allowlist_excludes_authority() -> None:
    assert {kind.value for kind in ALLOWED_ACTION_KINDS} == {"move", "fire", "respawn"}
    forbidden = {"spawn", "spawn_enemy", "spawn_powerup", "score", "seed", "state", "tile"}
    assert not {kind.value for kind in ActionKind} & forbidden


def test_client_messages_carry_no_authoritative_field() -> None:
    """No client message may name score, seed, state, damage or entity placement."""
    forbidden = {"score", "seed", "rng", "state", "state_hash", "damage", "grid", "tanks"}
    for message_type in (join_request(), input_batch()):
        names = {field.name for field in dataclasses.fields(message_type)}
        assert not names & forbidden


def test_rejection_code_values_are_stable() -> None:
    """These strings are matched on by peers and logs; changing one is a wire break."""
    assert RejectionCode.WRONG_PLAYER.value == "wrong_player"
    assert RejectionCode.SEQUENCE_NOT_MONOTONIC.value == "sequence_not_monotonic"
    assert RejectionCode.CONTENT_MISMATCH.value == "content_mismatch"
    assert RejectionCode.ILLEGAL_COMMAND.value == "illegal_command"
    assert RejectionCode.QUEUE_OVERFLOW.value == "queue_overflow"
    assert len({code.value for code in RejectionCode}) == len(RejectionCode)


def test_move_needs_a_direction() -> None:
    with pytest.raises(MessageError) as error:
        PlayerAction(kind=ActionKind.MOVE)
    assert error.value.code is RejectionCode.INVALID_FIELD


@pytest.mark.parametrize("kind", [ActionKind.FIRE, ActionKind.RESPAWN])
def test_non_move_actions_reject_a_direction(kind: ActionKind) -> None:
    with pytest.raises(MessageError) as error:
        PlayerAction(kind=kind, direction=DirectionCode.UP)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_move_and_fire_in_one_batch_is_legal() -> None:
    batch = input_batch()
    assert {action.kind for action in batch.actions} == {ActionKind.MOVE, ActionKind.FIRE}


def test_a_batch_may_carry_one_of_each_kind() -> None:
    batch = InputBatch(
        session_id=SESSION_ID,
        slot=1,
        sequence=1,
        actions=(
            PlayerAction(kind=ActionKind.MOVE, direction=DirectionCode.LEFT),
            PlayerAction(kind=ActionKind.FIRE),
            PlayerAction(kind=ActionKind.RESPAWN),
        ),
    )
    assert len(batch.actions) == MAX_ACTIONS_PER_BATCH


def test_a_batch_rejects_two_actions_of_one_kind() -> None:
    with pytest.raises(MessageError) as error:
        InputBatch(
            session_id=SESSION_ID,
            slot=1,
            sequence=1,
            actions=(
                PlayerAction(kind=ActionKind.MOVE, direction=DirectionCode.LEFT),
                PlayerAction(kind=ActionKind.MOVE, direction=DirectionCode.RIGHT),
            ),
        )
    assert error.value.code is RejectionCode.DUPLICATE_ACTION


def test_a_batch_rejects_more_actions_than_the_limit() -> None:
    actions = (PlayerAction(kind=ActionKind.FIRE),) * (MAX_ACTIONS_PER_BATCH + 1)
    with pytest.raises(MessageError) as error:
        InputBatch(session_id=SESSION_ID, slot=1, sequence=1, actions=actions)
    assert error.value.code is RejectionCode.INVALID_FIELD


@pytest.mark.parametrize("slot", [0, -1, MAX_SLOT + 1])
def test_slot_bounds(slot: int) -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(input_batch(), slot=slot)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_boolean_is_not_an_integer() -> None:
    """``bool`` subclasses ``int``; a flag and a count must not be interchangeable."""
    with pytest.raises(MessageError) as error:
        dataclasses.replace(input_batch(), sequence=True)
    assert error.value.code is RejectionCode.INVALID_FIELD


@pytest.mark.parametrize(
    "session_id",
    ["", " ", "has space", "trailing-", "session\n", "x" * 65, "ünicode"],
)
def test_identifier_charset(session_id: str) -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(input_batch(), session_id=session_id)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_identifier_rejects_a_trailing_newline() -> None:
    """A pattern anchored with ``$`` would accept this; ``fullmatch`` does not."""
    with pytest.raises(MessageError):
        dataclasses.replace(join_request(), session_id="session-1\n")


@pytest.mark.parametrize("token", ["", "short", "x" * 129, "has space", "tab\there"])
def test_token_bounds(token: str) -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(join_request(), token=token)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_a_rejection_never_has_to_quote_a_token() -> None:
    """Validation failures name the field, never the value, because details reach logs."""
    secret = "supersecrettoken"
    with pytest.raises(MessageError) as error:
        dataclasses.replace(join_request(), token=secret + " ")
    assert secret not in error.value.detail


def test_protocol_version_mismatch_is_named_as_such() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(join_request(), protocol_version=PROTOCOL_VERSION + 1)
    assert error.value.code is RejectionCode.PROTOCOL_VERSION_UNSUPPORTED


def test_snapshot_version_is_separate_from_the_protocol_version() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(snapshot(), snapshot_version=SNAPSHOT_VERSION + 1)
    assert error.value.code is RejectionCode.PROTOCOL_VERSION_UNSUPPORTED
    assert "snapshot version" in error.value.detail


def test_a_session_declares_content_rules_and_both_versions() -> None:
    info = session_info()
    assert info.content.pack_id == "classic"
    assert info.content.level_id == "classic-01"
    assert info.content.content_schema_version == 1
    assert len(info.rules_digest) == 64
    assert info.state_version == 1
    assert info.snapshot_version == SNAPSHOT_VERSION


def test_keyframe_must_carry_the_grid() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(keyframe(), grid=None)
    assert "keyframe snapshot needs a grid" in error.value.detail


def test_a_plain_snapshot_must_not_carry_the_grid() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(snapshot(), grid=GRID)
    assert "keyframe" in error.value.detail


def test_a_ragged_grid_is_refused() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(keyframe(), grid=("0000", "000"))
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_snapshot_entity_counts_are_bounded() -> None:
    tank = snapshot().tanks[0]
    with pytest.raises(MessageError) as error:
        dataclasses.replace(snapshot(), tanks=(tank,) * 65)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_entity_identifiers_start_at_one() -> None:
    with pytest.raises(MessageError):
        TankSnapshot(
            entity_id=0,
            variant=0,
            x=0,
            y=0,
            facing=0,
            slot=None,
            gatling_ticks=0,
            invincible_ticks=0,
        )


def test_tick_rate_is_bounded() -> None:
    with pytest.raises(MessageError):
        dataclasses.replace(snapshot(), tick_rate=MAX_TICK_RATE + 1)


def test_state_hash_must_be_lowercase_hex() -> None:
    with pytest.raises(MessageError) as error:
        dataclasses.replace(snapshot(), state_hash="B" * 64)
    assert error.value.code is RejectionCode.INVALID_FIELD


def test_detail_stays_printable_and_bounded() -> None:
    with pytest.raises(MessageError):
        Rejected(session_id=SESSION_ID, code=RejectionCode.WRONG_PLAYER, detail="line\nbreak")
    with pytest.raises(MessageError):
        SessionClosed(session_id=SESSION_ID, code=RejectionCode.SESSION_CLOSED, detail="x" * 201)


def test_client_message_union_is_what_the_helpers_build() -> None:
    messages: list[ClientMessage] = [join_request(), input_batch()]
    assert len(messages) == 2
