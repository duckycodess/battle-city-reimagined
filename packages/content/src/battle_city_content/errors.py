"""Errors raised while loading declarative content.

Every rejection is a value error carrying the file it came from and the field inside
that file. Loading is all-or-nothing: an error propagates before any record reaches the
caller, so a malformed document can never half-start a stage.
"""

from __future__ import annotations

from pathlib import Path


class ContentError(Exception):
    """Base class for every content package rejection."""


class ContentValidationError(ContentError):
    """A content document violates the checked-in contract.

    ``path`` is the offending file and ``field`` is the dotted path to the offending
    value inside it, for example ``grid.rows[3]`` or ``spawns.players[0].slot``. The
    field is empty when the whole document is at fault. Callers get the parts as
    attributes so tooling can format diagnostics without parsing the message back apart.
    """

    def __init__(self, *, path: Path, field: str, message: str) -> None:
        self.path = path
        self.field = field
        self.message = message
        super().__init__(f"{path}: {field}: {message}" if field else f"{path}: {message}")


class ContentSchemaError(ContentError):
    """A checked-in JSON Schema is malformed or uses an unsupported keyword.

    This reports a defect in this package rather than in the document being loaded, so
    it is deliberately a different type from :class:`ContentValidationError`.
    """
