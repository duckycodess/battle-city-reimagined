"""Hardened JSON decoding for untrusted content files.

``json.loads`` on its own accepts several documents that are not interchangeable JSON
and that would make loading non-deterministic or unbounded:

* duplicate object keys, where the last one silently wins;
* the ``NaN``, ``Infinity`` and ``-Infinity`` literals, which are not JSON, and overflow
  literals such as ``1e999`` that decode to a float infinity;
* a leading UTF-8 byte order mark, which JSON does not allow;
* arbitrarily large or arbitrarily nested input.

Every one of those is rejected here, before a document reaches a schema, so the schema
layer only ever sees a bounded, plain JSON value tree.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Final

from .errors import ContentValidationError

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
"""Any value ``json`` can decode once the hardening below has run."""

MAX_DOCUMENT_BYTES: Final[int] = 1 << 20
"""Largest content file this package will read. A 16x16 level is under two kilobytes."""

MAX_NESTING_DEPTH: Final[int] = 32
"""Deepest array/object nesting accepted. The level schema nests four levels deep."""

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


def read_json_object(path: Path) -> dict[str, JsonValue]:
    """Decode ``path`` as a bounded, strictly parsed JSON object.

    Raises :class:`ContentValidationError` naming ``path`` for every rejection, so a
    caller can report unreadable, oversized, malformed and merely invalid files the same
    way.
    """
    raw = _read_bytes(path)
    if raw.startswith(_UTF8_BOM):
        raise ContentValidationError(
            path=path,
            field="",
            message="starts with a UTF-8 byte order mark, which JSON does not allow",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContentValidationError(
            path=path,
            field="",
            message=f"is not valid UTF-8 at byte {error.start}: {error.reason}",
        ) from error

    depth = _max_nesting_depth(text)
    if depth > MAX_NESTING_DEPTH:
        raise ContentValidationError(
            path=path,
            field="",
            message=f"nests {depth} levels deep, more than the limit of {MAX_NESTING_DEPTH}",
        )

    try:
        document: JsonValue = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _RejectedConstantError as error:
        raise ContentValidationError(
            path=path,
            field="",
            message=f"uses the literal {error.constant}, which is not valid JSON",
        ) from error
    except _DuplicateKeyError as error:
        raise ContentValidationError(
            path=path, field="", message=f"declares the object key {error.key!r} twice"
        ) from error
    except json.JSONDecodeError as error:
        raise ContentValidationError(
            path=path,
            field="",
            message=f"is not valid JSON: {error.msg} (line {error.lineno} column {error.colno})",
        ) from error

    if not isinstance(document, dict):
        raise ContentValidationError(
            path=path, field="", message="must be a JSON object at the top level"
        )
    _reject_non_finite_numbers(path, document)
    return document


def _read_bytes(path: Path) -> bytes:
    """Read at most :data:`MAX_DOCUMENT_BYTES`, rejecting anything longer or unreadable."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as error:
        detail = error.strerror or type(error).__name__
        raise ContentValidationError(
            path=path, field="", message=f"cannot be read: {detail}"
        ) from error
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ContentValidationError(
            path=path,
            field="",
            message=f"is larger than the limit of {MAX_DOCUMENT_BYTES} bytes",
        )
    return raw


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


def _reject_non_finite_numbers(path: Path, document: JsonValue) -> None:
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
            raise ContentValidationError(path=path, field=field, message="is not a finite number")
        if isinstance(value, dict):
            pending.extend(
                (f"{field}.{key}" if field else key, item) for key, item in value.items()
            )
        elif isinstance(value, list):
            pending.extend((f"{field}[{index}]", item) for index, item in enumerate(value))


def _max_nesting_depth(text: str) -> int:
    """Return the deepest array/object nesting in ``text``, ignoring bracket characters
    inside strings.

    Depth is measured before parsing because ``json`` recurses while decoding: a deeply
    nested document would raise :class:`RecursionError` rather than a reportable
    validation error.
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
