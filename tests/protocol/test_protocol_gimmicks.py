"""What the wire did and did not change when gimmick terrain arrived.

The accepted change says the protocol does not move: ``PROTOCOL_VERSION`` stays 2,
``SNAPSHOT_VERSION`` stays 1, no message gained or lost a field, and the keyframe grid
check stays version-blind -- it bounds printable ASCII and nothing more, because this
package may not know what a tile code means. Compatibility for the new terrain rides on
``ContentRef.content_schema_version``, which this package already carried and already
compares.

These cases are the compatibility half of that statement, written down so a later edit
that quietly tightened or loosened the wire fails here rather than in a player's session.
"""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_protocol import (
    PROTOCOL_VERSION,
    SNAPSHOT_VERSION,
    ContentRef,
    MessageError,
    RejectionCode,
    StateSnapshot,
    decode_server_message,
    encode_message,
)
from battle_city_protocol.limits import MAX_GRID_DIMENSION
from protocol_helpers import GRID, content_ref, join_request, keyframe, snapshot

GIMMICK_ROW = "9ABCD" + "0" * 11
"""A row of the five codes level schema version 2 adds, padded to sixteen columns."""


# -- the versions did not move ------------------------------------------------


def test_the_published_versions_are_where_they_were() -> None:
    assert PROTOCOL_VERSION == 2
    assert SNAPSHOT_VERSION == 1
    assert snapshot().protocol_version == 2
    assert snapshot().snapshot_version == 1


def round_trip(frame: StateSnapshot) -> StateSnapshot:
    restored = decode_server_message(encode_message(frame))
    assert isinstance(restored, StateSnapshot)
    return restored


def test_a_classic_keyframe_still_encodes_and_decodes_unchanged() -> None:
    frame = keyframe()
    restored = round_trip(frame)
    assert restored == frame
    assert restored.grid == GRID


# -- the grid check stays version-blind ---------------------------------------


def test_a_keyframe_carrying_gimmick_codes_is_shaped_correctly() -> None:
    """The protocol bounds the shape of a row; it does not know what a tile code means.

    Reading the alphabet here would mean this package knowing the content vocabulary,
    which it may not. The agreed content version decides that, at the two ends that know
    it: the server session and the client's keyframe decode.
    """
    rows = (GIMMICK_ROW,) * 15 + ("0" * 8 + "8" + "0" * 7,)
    assert round_trip(dataclasses.replace(keyframe(), grid=rows)).grid == rows


def test_every_classic_row_is_still_accepted() -> None:
    """The v1 alphabet has to keep travelling, whatever else the check now sees."""
    rows = ("0123456780123456",) * 15 + ("0" * 8 + "8" + "0" * 7,)
    assert round_trip(dataclasses.replace(keyframe(), grid=rows)).grid == rows


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ((), "no rows at all"),
        (("0" * 16, "0" * 15), "ragged"),
        (("0" * (MAX_GRID_DIMENSION + 1),), "too wide"),
        (("\x00" * 16,) * 16, "not printable"),
        (("٣" * 16,) * 16, "not ascii"),
    ],
)
def test_a_malformed_grid_is_still_refused(rows: tuple[str, ...], reason: str) -> None:
    with pytest.raises(MessageError) as caught:
        dataclasses.replace(keyframe(), grid=rows)
    assert caught.value.code is RejectionCode.INVALID_FIELD, reason


# -- the compatibility signal -------------------------------------------------


def test_the_content_reference_carries_the_schema_version() -> None:
    reference = ContentRef(
        pack_id="gimmick-demo",
        pack_version="1.0.0",
        level_id="gimmick-demo-01",
        content_schema_version=2,
    )
    assert reference.content_schema_version == 2
    assert reference != content_ref()


def test_two_content_references_differing_only_in_schema_version_are_unequal() -> None:
    """Compatibility is an equality check on this record, so the version has to count."""
    first = content_ref()
    second = dataclasses.replace(first, content_schema_version=2)
    assert first != second
    assert dataclasses.replace(second, content_schema_version=1) == first


def test_a_join_request_round_trips_a_version_two_reference() -> None:
    from battle_city_protocol import decode_client_message

    request = dataclasses.replace(
        join_request(), content=dataclasses.replace(content_ref(), content_schema_version=2)
    )
    restored = decode_client_message(encode_message(request))
    assert restored == request


@pytest.mark.parametrize("version", [0, -1])
def test_an_impossible_content_schema_version_is_refused(version: int) -> None:
    with pytest.raises(MessageError):
        dataclasses.replace(content_ref(), content_schema_version=version)
