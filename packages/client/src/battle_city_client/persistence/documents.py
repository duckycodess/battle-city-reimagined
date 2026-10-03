"""The local save envelope, and the strict readers every saved field goes through.

One saved document is one JSON object with four fields and nothing else::

    {"data": {...}, "format": "battle-city-reimagined/local-save",
     "kind": "settings", "schema_version": 1}

The envelope is self-describing on purpose. ``format`` says the file belongs to this
game, ``kind`` says which document it is, and ``schema_version`` says which layout
``data`` is in, so a file that is opened by the wrong reader, truncated, hand-edited or
written by a newer build is *recognised* rather than silently half-applied.

Why the protocol's JSON and not ``json.loads``
----------------------------------------------
A save file is untrusted input in the same sense a frame off a socket is: it is outside
the program, it can be edited, and a decoder that accepts more than it should is where
the damage starts. :mod:`battle_city_protocol.jsonio` already refuses duplicate keys,
``NaN``/``Infinity``, a byte order mark, unbounded nesting and a non-object at the top
level, and it encodes with sorted keys and fixed separators so one document has one byte
string. The architecture specification allows ``client -> protocol``, so this module uses
that hardened pair instead of writing a second, weaker copy of it. The rejection is
translated at this boundary: a bad save is a :class:`CorruptSave`, never a network error
code, because nothing here is going to answer a peer.

Strictness, and what it costs
-----------------------------
Unknown keys are rejected rather than ignored, at both levels. A document that carries a
field this build does not know is either not this document or not this version, and both
of those have an answer already -- ``kind`` and ``schema_version``. Ignoring the field
instead would let a newer build's half-understood file be read as if it were fully
understood, which is exactly the silent partial apply the persistence specification asks
the reader to avoid. The cost is that a *forward*-compatible addition needs a version
bump; that is the intended trade, and :mod:`~battle_city_client.persistence.migrations`
is how a bump is carried.

Sizes are bounded before anything is parsed. A local profile is a few hundred bytes; the
limit is generous enough to never be reached by this build's own writes and small enough
that a file somebody pointed at a disk image is refused without being read into memory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from battle_city_protocol import JsonValue, MessageError, decode_json_object, encode_json_object

FORMAT_TAG: Final[str] = "battle-city-reimagined/local-save"
"""The ``format`` field every document this build writes carries."""

MAX_DOCUMENT_BYTES: Final[int] = 8 * 1024
"""Refusal threshold for a saved file, checked before it is parsed."""

FORMAT_FIELD: Final[str] = "format"
KIND_FIELD: Final[str] = "kind"
VERSION_FIELD: Final[str] = "schema_version"
DATA_FIELD: Final[str] = "data"

ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {FORMAT_FIELD, KIND_FIELD, VERSION_FIELD, DATA_FIELD}
)


class SaveError(Exception):
    """Something about a saved document means it cannot be used as it stands."""


class CorruptSave(SaveError):
    """The bytes are not a document this build can read at all."""


class FutureSchema(SaveError):
    """The document is a later schema version than this build knows.

    Carries both versions because the recovery notice names them: a player who is told
    only that their save "did not load" cannot tell a newer build from a broken file.
    """

    def __init__(self, kind: str, found: int, current: int) -> None:
        self.kind = kind
        self.found = found
        self.current = current
        super().__init__(f"{kind} save is schema version {found}; this build reads up to {current}")


@dataclass(frozen=True, slots=True)
class Document:
    """One decoded envelope: which document, which layout, and the payload itself."""

    kind: str
    schema_version: int
    data: Mapping[str, JsonValue]


def encode_document(document: Document) -> bytes:
    """Encode ``document`` as the bytes a save file holds.

    Deterministic: sorted keys, compact separators, UTF-8. Two runs that mean the same
    thing write the same file, which is what makes a round trip assertable and what keeps
    an unchanged save from rewriting itself into a different-looking file.
    """
    payload = encode_json_object(
        {
            DATA_FIELD: dict(document.data),
            FORMAT_FIELD: FORMAT_TAG,
            KIND_FIELD: document.kind,
            VERSION_FIELD: document.schema_version,
        }
    )
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise SaveError(
            f"{document.kind} save would be {len(payload)} bytes, "
            f"over the local limit of {MAX_DOCUMENT_BYTES}"
        )
    return payload


def decode_document(payload: bytes, *, kind: str, current_version: int) -> Document:
    """Decode ``payload`` as a ``kind`` document this build can read.

    Raises :class:`CorruptSave` when the bytes are not such a document, and
    :class:`FutureSchema` when they are one from a later build. The two are separate
    because the recoveries differ: a corrupt file is replaced once the player saves
    again, and a newer file must be left exactly as it is.
    """
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise CorruptSave(
            f"{kind} save is {len(payload)} bytes, over the limit of {MAX_DOCUMENT_BYTES}"
        )
    try:
        document = decode_json_object(payload)
    except MessageError as error:
        raise CorruptSave(f"{kind} save is not a readable JSON object: {error}") from error

    require_exact_keys(document, ENVELOPE_FIELDS, f"{kind} save")
    if document[FORMAT_FIELD] != FORMAT_TAG:
        raise CorruptSave(f"{kind} save is not a {FORMAT_TAG} document")
    if document[KIND_FIELD] != kind:
        raise CorruptSave(f"save declares kind {document[KIND_FIELD]!r}, expected {kind!r}")

    version = document[VERSION_FIELD]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CorruptSave(f"{kind} save has no positive integer schema version")
    if version > current_version:
        raise FutureSchema(kind=kind, found=version, current=current_version)

    data = document[DATA_FIELD]
    if not isinstance(data, dict):
        raise CorruptSave(f"{kind} save data must be a JSON object")
    return Document(kind=kind, schema_version=version, data=data)


# -- strict field readers ------------------------------------------------------
#
# Every saved field is read through one of these. They raise :class:`CorruptSave` rather
# than returning a default, because a field that is present and wrong is a different
# thing from a field that is absent: the first means the file is not what it claims to
# be, and quietly substituting a default for it would hide that.


def require_exact_keys(data: Mapping[str, JsonValue], keys: frozenset[str], what: str) -> None:
    """Refuse a mapping whose key set is not exactly ``keys``."""
    present = frozenset(data)
    missing = sorted(keys - present)
    unknown = sorted(present - keys)
    if missing:
        raise CorruptSave(f"{what} is missing {', '.join(missing)}")
    if unknown:
        raise CorruptSave(f"{what} carries unknown field(s) {', '.join(unknown)}")


def read_int(data: Mapping[str, JsonValue], key: str, *, minimum: int, maximum: int) -> int:
    """Read a bounded integer. ``True`` is not an integer here, whatever Python says."""
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CorruptSave(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise CorruptSave(f"{key} must be between {minimum} and {maximum}, found {value}")
    return value


def read_text(data: Mapping[str, JsonValue], key: str, *, max_length: int) -> str:
    """Read a bounded string with no control characters in it."""
    value = data.get(key)
    if not isinstance(value, str):
        raise CorruptSave(f"{key} must be a string")
    if len(value) > max_length:
        raise CorruptSave(f"{key} must be at most {max_length} characters, found {len(value)}")
    if any(character < " " or character == "\x7f" for character in value):
        raise CorruptSave(f"{key} must not contain control characters")
    return value


def read_optional_object(data: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue] | None:
    """Read a nested object, where ``null`` means "there is none" rather than an error."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CorruptSave(f"{key} must be a JSON object or null")
    return value


def read_choice(data: Mapping[str, JsonValue], key: str, allowed: Sequence[str]) -> str:
    """Read a string that must be one of ``allowed``."""
    value = read_text(data, key, max_length=64)
    if value not in allowed:
        raise CorruptSave(f"{key} must be one of {', '.join(allowed)}, found {value!r}")
    return value
