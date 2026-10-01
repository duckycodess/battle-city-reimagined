"""Structured session logging.

The observability section of the architecture specification asks for logs that carry
the session ID, the tick, the protocol version, the content hash and a reason code, and
that never carry a secret. Both halves of that are enforced here rather than left to
each call site.

Every record is emitted with the same field set, as ``key=value`` text and as record
attributes, so a text log stays greppable and a structured handler keeps the fields
typed. There is no formatting of free-form objects: a caller passes named fields, and
:func:`log_event` renders them.

Secrets have no way in. A membership token is never a field here, and the one field
that carries prose — ``reason``/``detail`` — comes from the protocol's validated detail
strings, which name a field and a bound and never quote a value. ``test_server_logging``
holds that line by asserting a real token never reaches a record.
"""

from __future__ import annotations

import logging
from typing import Final

from battle_city_protocol import PROTOCOL_VERSION, ContentRef, RejectionCode

LOGGER_NAME: Final[str] = "battle_city_server"

FIELD_ORDER: Final[tuple[str, ...]] = (
    "event",
    "session_id",
    "tick",
    "protocol_version",
    "content",
    "rules_digest",
    "slot",
    "peer",
    "connection",
    "sequence",
    "reason",
    "detail",
)
"""The fields a record may carry, in the order they are rendered.

Published as a tuple so the shape of a log line is a reviewable constant rather than an
accident of keyword order at each call site.
"""


def session_logger() -> logging.Logger:
    """The one logger this package emits on."""
    return logging.getLogger(LOGGER_NAME)


def content_label(content: ContentRef) -> str:
    """Render a content reference as one stable identifier for a log line."""
    return (
        f"{content.pack_id}@{content.pack_version}"
        f"/{content.level_id}#{content.content_schema_version}"
    )


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    session_id: str,
    tick: int,
    content: str | None = None,
    rules_digest: str | None = None,
    slot: int | None = None,
    peer: str | None = None,
    connection: int | None = None,
    sequence: int | None = None,
    reason: RejectionCode | None = None,
    detail: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit one session record.

    ``reason`` is a stable :class:`~battle_city_protocol.codes.RejectionCode`, so a log
    search and a wire capture agree on what to look for.
    """
    fields: dict[str, object] = {
        "event": event,
        "session_id": session_id,
        "tick": tick,
        "protocol_version": PROTOCOL_VERSION,
        "content": content,
        "rules_digest": rules_digest,
        "slot": slot,
        "peer": peer,
        "connection": connection,
        "sequence": sequence,
        "reason": None if reason is None else reason.value,
        "detail": detail,
    }
    rendered = " ".join(
        f"{name}={fields[name]}" for name in FIELD_ORDER if fields[name] is not None
    )
    logger.log(level, "%s", rendered, extra=fields)
