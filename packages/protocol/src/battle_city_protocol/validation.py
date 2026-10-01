"""Field validators shared by every message.

Validation lives with the message record, not with the decoder, for one reason: a
message that exists is a message that was checked. A server constructing a snapshot and
a client decoding one both pass through the same bounds, so a local bug cannot put on
the wire a value the decoder would have refused.

Every failure raises :class:`~battle_city_protocol.errors.MessageError` naming the field
and the bound. A failure never quotes the value, because one of these fields is a
membership token.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .limits import (
    MAX_DETAIL_LENGTH,
    MAX_DIGEST_LENGTH,
    MAX_IDENTIFIER_LENGTH,
    MAX_TOKEN_LENGTH,
    MIN_TOKEN_LENGTH,
)

_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
"""Identifier charset: alphanumerics plus ``. _ -`` inside, alphanumeric at both ends.

Matched with :func:`re.fullmatch`, so no anchor can be satisfied by a trailing newline.
"""

_TOKEN: Final[re.Pattern[str]] = re.compile(r"[!-~]+")
"""Tokens are printable ASCII with no spaces: safe to compare, never safe to log."""

_DIGEST: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]+")
_DETAIL: Final[re.Pattern[str]] = re.compile(r"[ -~]*")
"""Details stay printable ASCII so a rejection cannot inject control bytes into a log."""

MIN_DIGEST_LENGTH: Final[int] = 16


def require_int(field: str, value: object, *, minimum: int, maximum: int) -> int:
    """Return ``value`` as an integer inside ``[minimum, maximum]``.

    ``bool`` is refused explicitly: it is an ``int`` subclass, so ``True`` would
    otherwise pass as ``1`` and make a flag and a count interchangeable on the wire.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} must be between {minimum} and {maximum}",
        )
    return value


def require_optional_int(field: str, value: object, *, minimum: int, maximum: int) -> int | None:
    """Return ``value`` as an integer inside the bound, or ``None``."""
    if value is None:
        return None
    return require_int(field, value, minimum=minimum, maximum=maximum)


def require_bool(field: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a boolean")
    return value


def require_identifier(field: str, value: object) -> str:
    """Return ``value`` as a bounded, printable stable identifier."""
    if not isinstance(value, str):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a string")
    if not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} must be 1 to {MAX_IDENTIFIER_LENGTH} characters",
        )
    if _IDENTIFIER.fullmatch(value) is None:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} must use letters, digits, '.', '_' or '-' and start and end alphanumeric",
        )
    return value


def require_token(field: str, value: object) -> str:
    """Return ``value`` as a bounded membership token.

    The token is never echoed into the error, because errors reach logs and tokens
    must not.
    """
    if not isinstance(value, str):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a string")
    if not MIN_TOKEN_LENGTH <= len(value) <= MAX_TOKEN_LENGTH:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} must be {MIN_TOKEN_LENGTH} to {MAX_TOKEN_LENGTH} characters",
        )
    if _TOKEN.fullmatch(value) is None:
        raise MessageError(
            RejectionCode.INVALID_FIELD, f"{field} must be printable ASCII without spaces"
        )
    return value


def require_digest(field: str, value: object) -> str:
    """Return ``value`` as a lowercase hexadecimal digest string."""
    if not isinstance(value, str):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a string")
    if not MIN_DIGEST_LENGTH <= len(value) <= MAX_DIGEST_LENGTH:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} must be {MIN_DIGEST_LENGTH} to {MAX_DIGEST_LENGTH} characters",
        )
    if _DIGEST.fullmatch(value) is None:
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be lowercase hexadecimal")
    return value


def require_detail(field: str, value: object) -> str:
    """Return ``value`` as a bounded printable rejection detail."""
    if not isinstance(value, str):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a string")
    if len(value) > MAX_DETAIL_LENGTH:
        raise MessageError(
            RejectionCode.INVALID_FIELD, f"{field} must be at most {MAX_DETAIL_LENGTH} characters"
        )
    if _DETAIL.fullmatch(value) is None:
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be printable ASCII")
    return value


def require_member[T: StrEnum](field: str, value: object, enum: type[T]) -> T:
    """Return ``value`` as a member of ``enum``, refusing anything outside it.

    Enum membership is the whole acceptance rule for every vocabulary on the wire, so an
    unknown action, direction, event kind or rejection code is a rejection rather than a
    value a later branch has to guess about.
    """
    if not isinstance(value, str):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{field} must be a string")
    try:
        return enum(value)
    except ValueError as error:
        raise MessageError(
            RejectionCode.INVALID_FIELD, f"{field} is not a known {enum.__name__}"
        ) from error
