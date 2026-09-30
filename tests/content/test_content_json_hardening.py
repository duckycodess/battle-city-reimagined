"""Decoding rejections: what a content file may be before a schema ever sees it.

``json.loads`` accepts several documents that are not interchangeable JSON, and accepts
input of unbounded size and depth. Each is refused here as a content validation error
naming the file, so a caller reports a hostile file the same way it reports a typo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from battle_city_content import load_level, load_pack
from battle_city_content.jsonio import MAX_DOCUMENT_BYTES, MAX_NESTING_DEPTH
from content_helpers import rejection, synthetic_level, write_bytes, write_json, write_text


def reject_text(tmp_path: Path, text: str) -> tuple[str, str]:
    path = write_text(tmp_path / "level.json", text)
    error = rejection(lambda: load_level(path))
    assert error.path == path
    return error.field, error.message


def reject_bytes(tmp_path: Path, raw: bytes) -> tuple[str, str]:
    path = write_bytes(tmp_path / "level.json", raw)
    error = rejection(lambda: load_level(path))
    assert error.path == path
    return error.field, error.message


def test_a_duplicate_object_key_is_rejected(tmp_path: Path) -> None:
    field, message = reject_text(tmp_path, '{"schema_version": 1, "schema_version": 2}')
    assert field == ""
    assert message == "declares the object key 'schema_version' twice"


def test_a_duplicate_key_in_a_nested_object_is_rejected(tmp_path: Path) -> None:
    text = '{"grid": {"width": 16, "width": 17}}'
    assert reject_text(tmp_path, text)[1] == "declares the object key 'width' twice"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_json_numeric_literal_is_rejected(tmp_path: Path, literal: str) -> None:
    field, message = reject_text(tmp_path, '{"schema_version": ' + literal + "}")
    assert field == ""
    assert message == f"uses the literal {literal}, which is not valid JSON"


@pytest.mark.parametrize("literal", ["1e999", "-1e999", "1E400"])
def test_a_number_that_overflows_to_infinity_is_rejected(tmp_path: Path, literal: str) -> None:
    field, message = reject_text(tmp_path, '{"schema_version": ' + literal + "}")
    assert field == "schema_version"
    assert message == "is not a finite number"


def test_an_overflowing_number_deep_in_the_document_is_rejected(tmp_path: Path) -> None:
    text = '{"grid": {"rows": [1e999]}}'
    field, message = reject_text(tmp_path, text)
    assert field == "grid.rows[0]"
    assert message == "is not a finite number"


def test_a_byte_order_mark_is_rejected(tmp_path: Path) -> None:
    field, message = reject_bytes(tmp_path, b"\xef\xbb\xbf{}")
    assert field == ""
    assert message == "starts with a UTF-8 byte order mark, which JSON does not allow"


def test_invalid_utf8_is_rejected(tmp_path: Path) -> None:
    field, message = reject_bytes(tmp_path, b'{"name": "\xff\xfe"}')
    assert field == ""
    assert message.startswith("is not valid UTF-8 at byte ")


def test_valid_non_ascii_utf8_survives_decoding(tmp_path: Path) -> None:
    # Rejection is about encoding, not about alphabet: a non-ASCII name is legal.
    document = synthetic_level(name="Stage é中\U0001f600")
    level = load_level(write_json(tmp_path / "level.json", document))
    assert level.name == "Stage é中\U0001f600"


def test_nesting_beyond_the_limit_is_rejected(tmp_path: Path) -> None:
    depth = MAX_NESTING_DEPTH + 4
    text = '{"a":' + "[" * depth + "]" * depth + "}"
    field, message = reject_text(tmp_path, text)
    assert field == ""
    assert message == (f"nests {depth + 1} levels deep, more than the limit of {MAX_NESTING_DEPTH}")


def test_nesting_at_the_limit_is_decoded(tmp_path: Path) -> None:
    depth = MAX_NESTING_DEPTH - 1
    text = '{"a":' + "[" * depth + "]" * depth + "}"
    # Decoding succeeds; the schema then rejects the document on its own terms, which is
    # a missing required field rather than anything about depth.
    assert reject_text(tmp_path, text) == ("schema_version", "is required")


def test_brackets_inside_strings_do_not_count_as_nesting(tmp_path: Path) -> None:
    document = synthetic_level(name="[[[{{{" * 10 + '"]]]')
    path = write_json(tmp_path / "level.json", document)
    assert load_level(path).name == "[[[{{{" * 10 + '"]]]'


def test_a_document_larger_than_the_limit_is_rejected(tmp_path: Path) -> None:
    padding = "x" * (MAX_DOCUMENT_BYTES + 1)
    field, message = reject_text(tmp_path, '{"name": "' + padding + '"}')
    assert field == ""
    assert message == f"is larger than the limit of {MAX_DOCUMENT_BYTES} bytes"


@pytest.mark.parametrize("text", ["[1, 2, 3]", '"a string"', "17", "null", "true"])
def test_a_document_that_is_not_an_object_is_rejected(tmp_path: Path, text: str) -> None:
    field, message = reject_text(tmp_path, text)
    assert field == ""
    assert message == "must be a JSON object at the top level"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{",
        '{"a": 1},',
        '{"a": 1}{"b": 2}',
        "{'a': 1}",
        '{"a": 1,}',
        '{"a": 1} // comment',
        '// comment\n{"a": 1}',
    ],
)
def test_malformed_json_is_rejected(tmp_path: Path, text: str) -> None:
    field, message = reject_text(tmp_path, text)
    assert field == ""
    assert message.startswith("is not valid JSON: ")


def test_a_directory_in_place_of_a_file_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "level.json"
    directory.mkdir()
    error = rejection(lambda: load_level(directory))
    assert error.field == ""
    assert "cannot be read" in error.message


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    error = rejection(lambda: load_level(tmp_path / "absent.json"))
    assert error.field == ""
    assert error.message == "cannot be read: No such file or directory"


def test_hardening_applies_to_pack_manifests_too(tmp_path: Path) -> None:
    path = write_text(tmp_path / "pack.json", '{"id": "a", "id": "b"}')
    error = rejection(lambda: load_pack(path))
    assert error.path == path
    assert error.message == "declares the object key 'id' twice"
