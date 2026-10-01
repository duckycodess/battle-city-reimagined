"""The published event table: arity, names and bounds."""

from __future__ import annotations

import pytest
from battle_city_protocol import (
    EVENT_FIELDS,
    MAX_EVENTS_PER_MESSAGE,
    EventKind,
    GameEvent,
    MessageError,
    RejectionCode,
)
from protocol_helpers import tick_events


def test_every_kind_has_a_field_list() -> None:
    assert set(EVENT_FIELDS) == set(EventKind)


def test_no_kind_declares_a_duplicate_field() -> None:
    for kind, names in EVENT_FIELDS.items():
        assert len(set(names)) == len(names), kind


def test_no_kind_is_wider_than_the_limit() -> None:
    assert max(len(names) for names in EVENT_FIELDS.values()) <= 8


def test_fields_are_readable_by_name() -> None:
    event = GameEvent.of(EventKind.TILE_DAMAGED, 3, 7, 2, 0, 41)
    assert event.field("cell_x") == 3
    assert event.field("cell_y") == 7
    assert event.field("projectile_id") == 41
    assert event.as_mapping() == {
        "cell_x": 3,
        "cell_y": 7,
        "previous": 2,
        "current": 0,
        "projectile_id": 41,
    }


def test_an_unknown_field_name_is_a_key_error() -> None:
    event = GameEvent.of(EventKind.RUN_ENDED, 0)
    with pytest.raises(KeyError):
        event.field("slot")


def test_arity_is_enforced() -> None:
    with pytest.raises(MessageError) as error:
        GameEvent.of(EventKind.SHIELD_BROKEN, 1)
    assert error.value.code is RejectionCode.INVALID_FIELD
    assert "takes 2 values" in error.value.detail


def test_values_must_be_integers() -> None:
    with pytest.raises(MessageError):
        GameEvent(kind=EventKind.RUN_ENDED, values=("0",))  # type: ignore[arg-type]


def test_a_boolean_is_not_an_event_value() -> None:
    with pytest.raises(MessageError):
        GameEvent(kind=EventKind.RUN_ENDED, values=(True,))


def test_values_are_range_checked() -> None:
    with pytest.raises(MessageError):
        GameEvent.of(EventKind.RUN_ENDED, 1 << 40)


def test_a_tick_may_not_report_more_events_than_the_limit() -> None:
    event = GameEvent.of(EventKind.RUN_ENDED, 0)
    with pytest.raises(MessageError):
        tick_events(*([event] * (MAX_EVENTS_PER_MESSAGE + 1)))
