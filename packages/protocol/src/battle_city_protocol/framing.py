"""Message framing for a byte stream.

A frame is a four-byte big-endian unsigned length followed by exactly that many payload
bytes. The length is checked against :data:`~battle_city_protocol.limits.MAX_FRAME_BYTES`
*before* the payload is read, so a peer that declares a gigabyte costs a reader four
bytes and a closed connection rather than a gigabyte of buffer.

Framing is a stream concern, not a transport concern: a datagram transport that already
preserves message boundaries carries the payload alone and never calls this module.
"""

from __future__ import annotations

from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .limits import MAX_FRAME_BYTES

FRAME_HEADER_BYTES: Final[int] = 4
"""Width of the big-endian unsigned length prefix."""


def encode_frame(payload: bytes) -> bytes:
    """Return ``payload`` with its length prefix."""
    if len(payload) > MAX_FRAME_BYTES:
        raise MessageError(
            RejectionCode.FRAME_TOO_LARGE,
            f"frame is {len(payload)} bytes, over the limit of {MAX_FRAME_BYTES}",
        )
    return len(payload).to_bytes(FRAME_HEADER_BYTES, "big") + payload


def decode_frame_length(header: bytes) -> int:
    """Return the payload length a header declares, refusing an unusable declaration."""
    if len(header) != FRAME_HEADER_BYTES:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame header is {len(header)} bytes, expected {FRAME_HEADER_BYTES}",
        )
    length = int.from_bytes(header, "big")
    if length == 0:
        raise MessageError(RejectionCode.MALFORMED_FRAME, "frame declares an empty payload")
    if length > MAX_FRAME_BYTES:
        raise MessageError(
            RejectionCode.FRAME_TOO_LARGE,
            f"frame declares {length} bytes, over the limit of {MAX_FRAME_BYTES}",
        )
    return length
