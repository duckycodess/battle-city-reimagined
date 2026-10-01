"""Strict JSON: the documents that are JSON-shaped but must not decode."""

from __future__ import annotations

import json

import pytest
from battle_city_protocol import (
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MessageError,
    RejectionCode,
    decode_json_object,
    encode_json_object,
)


def test_decodes_a_plain_object() -> None:
    assert decode_json_object(b'{"a":1,"b":[1,2]}') == {"a": 1, "b": [1, 2]}


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_json_float_literals(literal: str) -> None:
    payload = f'{{"value":{literal}}}'.encode()
    with pytest.raises(MessageError) as error:
        decode_json_object(payload)
    assert error.value.code is RejectionCode.MALFORMED_FRAME
    assert literal in error.value.detail


def test_rejects_overflow_literal_that_decodes_to_infinity() -> None:
    """``1e999`` is syntactically a number, so ``parse_constant`` never sees it."""
    assert json.loads('{"value":1e999}')["value"] == float("inf")
    with pytest.raises(MessageError) as error:
        decode_json_object(b'{"value":1e999}')
    assert error.value.code is RejectionCode.MALFORMED_FRAME
    assert "finite" in error.value.detail


def test_rejects_nested_overflow_literal() -> None:
    with pytest.raises(MessageError) as error:
        decode_json_object(b'{"outer":{"inner":[0,-1e999]}}')
    assert "outer.inner[1]" in error.value.detail


def test_rejects_duplicate_keys_instead_of_last_one_wins() -> None:
    assert json.loads('{"slot":1,"slot":2}')["slot"] == 2
    with pytest.raises(MessageError) as error:
        decode_json_object(b'{"slot":1,"slot":2}')
    assert error.value.code is RejectionCode.MALFORMED_FRAME
    assert "'slot'" in error.value.detail


@pytest.mark.parametrize("payload", [b"[]", b'"text"', b"7", b"null", b"true"])
def test_rejects_a_top_level_value_that_is_not_an_object(payload: bytes) -> None:
    with pytest.raises(MessageError) as error:
        decode_json_object(payload)
    assert error.value.code is RejectionCode.MALFORMED_FRAME


def test_rejects_a_byte_order_mark() -> None:
    with pytest.raises(MessageError) as error:
        decode_json_object(b"\xef\xbb\xbf{}")
    assert "byte order mark" in error.value.detail


def test_rejects_invalid_utf8() -> None:
    with pytest.raises(MessageError) as error:
        decode_json_object(b'{"a":"\xff\xfe"}')
    assert error.value.code is RejectionCode.MALFORMED_FRAME
    assert "UTF-8" in error.value.detail


def test_rejects_truncated_json() -> None:
    with pytest.raises(MessageError) as error:
        decode_json_object(b'{"a":')
    assert error.value.code is RejectionCode.MALFORMED_FRAME


def test_rejects_an_oversized_payload() -> None:
    payload = b'{"a":"' + b"x" * MAX_FRAME_BYTES + b'"}'
    with pytest.raises(MessageError) as error:
        decode_json_object(payload)
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE


def test_rejects_nesting_deeper_than_the_limit() -> None:
    depth = MAX_JSON_DEPTH + 1
    payload = ('{"a":' * depth) + "1" + ("}" * depth)
    with pytest.raises(MessageError) as error:
        decode_json_object(payload.encode())
    assert error.value.code is RejectionCode.MALFORMED_FRAME
    assert "nests" in error.value.detail


def test_accepts_nesting_at_the_limit() -> None:
    depth = MAX_JSON_DEPTH
    payload = ('{"a":' * depth) + "1" + ("}" * depth)
    assert decode_json_object(payload.encode())


def test_brackets_inside_strings_do_not_count_as_nesting() -> None:
    payload = '{"a":"' + "[" * (MAX_JSON_DEPTH * 4) + '"}'
    assert decode_json_object(payload.encode())["a"].count("[") == MAX_JSON_DEPTH * 4  # type: ignore[union-attr]


def test_encoding_is_key_sorted_and_compact() -> None:
    assert encode_json_object({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'


def test_encoding_refuses_a_non_finite_number() -> None:
    with pytest.raises(ValueError):
        encode_json_object({"value": float("nan")})


def test_encoding_refuses_an_oversized_document() -> None:
    with pytest.raises(MessageError) as error:
        encode_json_object({"value": "x" * (MAX_FRAME_BYTES + 1)})
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE
