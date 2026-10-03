"""Strict JSON for untrusted frames.

``json.loads`` on its own accepts several documents that are not interchangeable JSON
and that a peer must not be able to smuggle past a decoder:

* duplicate object keys, where the last one silently wins;
* the ``NaN``, ``Infinity`` and ``-Infinity`` literals, which are not JSON, and
  overflow literals such as ``1e999`` that decode to a float infinity;
* a leading UTF-8 byte order mark, which JSON does not allow;
* arbitrarily deep nesting, which recurses the decoder before any bound is consulted;
* a top-level array, string or number where a message object is expected.

Each of those is refused here, before a frame reaches the message decoders, so those
decoders only ever see a bounded plain-object value tree. Nothing is ever deserialised
into an executable object: the decoders build frozen dataclasses field by field.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .limits import MAX_FRAME_BYTES, MAX_JSON_DEPTH

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
"""Any value ``json`` can decode once the hardening below has run."""

_UTF8_BOM: Final[bytes] = b"\xef\xbb\xbf"


class _DuplicateKeyError(Exception):
    """Internal signal from the object hook; converted before it escapes this module."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _RejectedConstantError(Exception):
    """Internal signal for a non-JSON literal; converted before it escapes this module."""

    def __init__(self, constant: str) -> None:
        self.constant = constant
        super().__init__(constant)


def decode_json_object(payload: bytes, *, limit: int = MAX_FRAME_BYTES) -> dict[str, JsonValue]:
    """Decode ``payload`` as a bounded, strictly parsed JSON object.

    ``limit`` is the size bound in bytes and defaults to :data:`MAX_FRAME_BYTES`, which
    is the bound for a wire frame. A caller that is not reading a frame passes its own:
    :mod:`battle_city_protocol.replay` reads a stored document, which is larger than a
    packet and bounded by its own published constant. The hardening below is the same
    either way, which is the reason this takes a bound rather than being copied.

    Raises :class:`~battle_city_protocol.errors.MessageError` with
    :data:`~battle_city_protocol.codes.RejectionCode.FRAME_TOO_LARGE` or
    :data:`~battle_city_protocol.codes.RejectionCode.MALFORMED_FRAME`.
    """
    if len(payload) > limit:
        raise MessageError(
            RejectionCode.FRAME_TOO_LARGE,
            f"frame is {len(payload)} bytes, over the limit of {limit}",
        )
    if payload.startswith(_UTF8_BOM):
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            "frame starts with a UTF-8 byte order mark, which JSON does not allow",
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame is not valid UTF-8 at byte {error.start}",
        ) from error

    depth = _max_nesting_depth(text)
    if depth > MAX_JSON_DEPTH:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame nests {depth} levels deep, over the limit of {MAX_JSON_DEPTH}",
        )

    try:
        document: JsonValue = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _RejectedConstantError as error:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame uses the literal {error.constant}, which is not valid JSON",
        ) from error
    except _DuplicateKeyError as error:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame declares the object key {error.key!r} twice",
        ) from error
    except json.JSONDecodeError as error:
        raise MessageError(
            RejectionCode.MALFORMED_FRAME,
            f"frame is not valid JSON: {error.msg} (line {error.lineno} column {error.colno})",
        ) from error

    if not isinstance(document, dict):
        raise MessageError(
            RejectionCode.MALFORMED_FRAME, "frame must be a JSON object at the top level"
        )
    _reject_non_finite_numbers(document)
    return document


def encode_json_object(document: Mapping[str, JsonValue], *, limit: int = MAX_FRAME_BYTES) -> bytes:
    """Encode ``document`` as compact, key-sorted, bounded UTF-8 JSON.

    Keys are sorted and separators are fixed so one message always encodes to one byte
    string: two servers that agree on a snapshot agree on its bytes, which makes a
    frame comparable in a test and cacheable for a broadcast.

    ``limit`` is the size bound in bytes and defaults to :data:`MAX_FRAME_BYTES`, which
    is the bound for a wire frame. It is a parameter for the same reason
    :func:`decode_json_object` takes one: a caller that is not writing a frame has its
    own published bound, and a stored document that this function refused at the wire
    bound would be a document the matching decoder was willing to read.
    """
    text = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload = text.encode("utf-8")
    if len(payload) > limit:
        raise MessageError(
            RejectionCode.FRAME_TOO_LARGE,
            f"encoded message is {len(payload)} bytes, over the limit of {limit}",
        )
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """Build an object, refusing the silent last-one-wins merge of a repeated key."""
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(constant: str) -> JsonValue:
    """Refuse the non-JSON ``NaN``/``Infinity`` literals ``json`` accepts by default.

    ``json`` only calls this hook for those three literals, so every call is a rejection.
    """
    raise _RejectedConstantError(constant)


def _reject_non_finite_numbers(document: JsonValue) -> None:
    """Reject floats that decoded to an infinity, such as the literal ``1e999``.

    ``parse_constant`` never sees these: they are syntactically ordinary numbers that
    overflow during conversion.
    """
    pending: list[tuple[str, JsonValue]] = [("", document)]
    while pending:
        field, value = pending.pop()
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            name = field or "frame"
            raise MessageError(RejectionCode.MALFORMED_FRAME, f"{name} is not a finite number")
        if isinstance(value, dict):
            pending.extend(
                (f"{field}.{key}" if field else key, item) for key, item in value.items()
            )
        elif isinstance(value, list):
            pending.extend((f"{field}[{index}]", item) for index, item in enumerate(value))


def _max_nesting_depth(text: str) -> int:
    """Return the deepest array/object nesting in ``text``, ignoring brackets in strings.

    Depth is measured before parsing because ``json`` recurses while decoding: a deeply
    nested frame would raise :class:`RecursionError` rather than a reportable rejection.
    """
    depth = 0
    peak = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            peak = max(peak, depth)
        elif char in "]}":
            depth -= 1
    return peak
