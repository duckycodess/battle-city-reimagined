"""The save envelope: what it accepts, what it refuses, and why it refuses it.

Every test here is about untrusted bytes. A save file is edited by hand, truncated by a
full disk, written by a build nobody has any more, or pointed at by mistake, and the
decoder's job is to tell those apart from a document it can use -- without ever applying
half of one.

There is deliberately no ``conftest.py`` and no shared helper module in this directory.
``tests/client`` already has a ``conftest`` and the repository type-checks with ``mypy
packages tests``, which rejects a second module by that name; ``tests/campaign`` and
``tests/tools`` record the same reason. Nothing here imports pygame at module scope
either, so collecting this directory loads no display library -- only
``test_persistence_screenshots`` asks for one, inside the functions that draw.
"""

from __future__ import annotations

import pytest
from battle_city_client.persistence import (
    FORMAT_TAG,
    MAX_DOCUMENT_BYTES,
    SETTINGS_KIND,
    SETTINGS_SCHEMA_VERSION,
    CorruptSave,
    Document,
    FutureSchema,
    LocalSettings,
    SaveError,
    decode_document,
    encode_document,
)
from battle_city_protocol import encode_json_object

CURRENT = SETTINGS_SCHEMA_VERSION


def _document(**overrides: object) -> bytes:
    """A structurally valid settings document, with ``overrides`` applied to the envelope."""
    envelope: dict[str, object] = {
        "data": LocalSettings().to_data(),
        "format": FORMAT_TAG,
        "kind": SETTINGS_KIND,
        "schema_version": CURRENT,
    }
    envelope.update(overrides)
    return encode_json_object(envelope)  # type: ignore[arg-type]


def _decode(payload: bytes) -> Document:
    return decode_document(payload, kind=SETTINGS_KIND, current_version=CURRENT)


# -- the happy path -----------------------------------------------------------


def test_a_document_round_trips_through_its_own_encoding() -> None:
    original = Document(
        kind=SETTINGS_KIND, schema_version=CURRENT, data=LocalSettings(scale=4).to_data()
    )
    decoded = _decode(encode_document(original))
    assert decoded == original


def test_encoding_is_byte_stable_for_one_document() -> None:
    """Sorted keys and fixed separators, so an unchanged save rewrites to the same bytes."""
    first = encode_document(
        Document(kind=SETTINGS_KIND, schema_version=CURRENT, data=LocalSettings().to_data())
    )
    second = encode_document(
        Document(kind=SETTINGS_KIND, schema_version=CURRENT, data=LocalSettings().to_data())
    )
    assert first == second
    assert b'"format":"' in first


def test_the_envelope_names_the_game_the_document_and_the_layout() -> None:
    decoded = _decode(_document())
    assert (decoded.kind, decoded.schema_version) == (SETTINGS_KIND, CURRENT)


# -- refusals -----------------------------------------------------------------


def test_an_unknown_envelope_field_is_refused_rather_than_ignored() -> None:
    with pytest.raises(CorruptSave, match="unknown"):
        _decode(_document(note="hello"))


@pytest.mark.parametrize("field", ["data", "format", "kind", "schema_version"])
def test_a_missing_envelope_field_is_refused(field: str) -> None:
    envelope = {
        "data": LocalSettings().to_data(),
        "format": FORMAT_TAG,
        "kind": SETTINGS_KIND,
        "schema_version": CURRENT,
    }
    del envelope[field]
    with pytest.raises(CorruptSave, match="missing"):
        _decode(encode_json_object(envelope))  # type: ignore[arg-type]


def test_a_document_from_another_format_is_refused() -> None:
    with pytest.raises(CorruptSave):
        _decode(_document(format="some-other-game/save"))


def test_a_document_of_another_kind_is_refused() -> None:
    """Reading the campaign file as settings must fail, not produce empty settings."""
    with pytest.raises(CorruptSave, match="kind"):
        _decode(_document(kind="campaign"))


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None])
def test_a_schema_version_that_is_not_a_positive_integer_is_refused(version: object) -> None:
    with pytest.raises(CorruptSave, match="schema version"):
        _decode(_document(schema_version=version))


def test_a_newer_schema_version_is_reported_as_such_and_not_as_damage() -> None:
    """The two recoveries differ, so the two failures have to be distinguishable."""
    with pytest.raises(FutureSchema) as caught:
        _decode(_document(schema_version=CURRENT + 5))
    assert caught.value.found == CURRENT + 5
    assert caught.value.current == CURRENT
    assert isinstance(caught.value, SaveError)
    assert not isinstance(caught.value, CorruptSave)


def test_data_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(CorruptSave, match="object"):
        _decode(_document(data=[1, 2, 3]))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json at all",
        b"[1, 2, 3]",
        b'"a string"',
        b'\xef\xbb\xbf{"format": "x"}',
        b'{"kind": "settings", "kind": "settings"}',
        b'{"data": NaN}',
        b"\xff\xfe\x00",
    ],
    ids=["empty", "garbage", "array", "string", "bom", "duplicate-key", "nan", "not-utf8"],
)
def test_bytes_that_are_not_a_json_object_are_refused(payload: bytes) -> None:
    with pytest.raises(CorruptSave):
        _decode(payload)


def test_an_oversized_file_is_refused_before_it_is_parsed() -> None:
    with pytest.raises(CorruptSave, match="over the limit"):
        _decode(b" " * (MAX_DOCUMENT_BYTES + 1))


def test_a_document_too_large_to_write_is_refused_by_the_encoder() -> None:
    """The bound holds in both directions, so nothing is written that cannot be read."""
    with pytest.raises(SaveError, match="over the local limit"):
        encode_document(
            Document(
                kind=SETTINGS_KIND,
                schema_version=CURRENT,
                data={"display_name": "x" * MAX_DOCUMENT_BYTES, "frame_cap": 1, "scale": 1},
            )
        )
