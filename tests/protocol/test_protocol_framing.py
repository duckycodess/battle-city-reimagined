"""Length-prefixed framing: a declaration is checked before a byte is buffered."""

from __future__ import annotations

import pytest
from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_BYTES,
    MessageError,
    RejectionCode,
    decode_frame_length,
    encode_frame,
)


def test_round_trips_a_payload() -> None:
    frame = encode_frame(b"hello")
    assert frame == b"\x00\x00\x00\x05hello"
    assert decode_frame_length(frame[:FRAME_HEADER_BYTES]) == 5


def test_rejects_an_oversized_payload() -> None:
    with pytest.raises(MessageError) as error:
        encode_frame(b"x" * (MAX_FRAME_BYTES + 1))
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE


def test_rejects_an_oversized_declaration_without_reading_it() -> None:
    header = (MAX_FRAME_BYTES + 1).to_bytes(FRAME_HEADER_BYTES, "big")
    with pytest.raises(MessageError) as error:
        decode_frame_length(header)
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE


def test_rejects_the_largest_declaration_a_header_can_hold() -> None:
    header = b"\xff\xff\xff\xff"
    with pytest.raises(MessageError) as error:
        decode_frame_length(header)
    assert error.value.code is RejectionCode.FRAME_TOO_LARGE


def test_rejects_an_empty_payload_declaration() -> None:
    with pytest.raises(MessageError) as error:
        decode_frame_length(b"\x00\x00\x00\x00")
    assert error.value.code is RejectionCode.MALFORMED_FRAME


@pytest.mark.parametrize("header", [b"", b"\x00", b"\x00\x00\x00\x00\x00"])
def test_rejects_a_header_of_the_wrong_width(header: bytes) -> None:
    with pytest.raises(MessageError) as error:
        decode_frame_length(header)
    assert error.value.code is RejectionCode.MALFORMED_FRAME
