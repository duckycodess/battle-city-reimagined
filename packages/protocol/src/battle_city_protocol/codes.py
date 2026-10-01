"""Stable rejection codes.

A rejection code is a wire constant: a peer matches on it, a log greps for it, and a
test asserts it. Member values therefore never change and never get reused for a
different meaning. A new failure mode adds a member; it does not repurpose one.

Codes describe *why* a message was refused, never *what* was in it. Nothing here, and
nothing a server pairs with one of these, may carry a membership token.
"""

from __future__ import annotations

from enum import StrEnum


class RejectionCode(StrEnum):
    """Why a message or a request was refused."""

    PROTOCOL_VERSION_UNSUPPORTED = "protocol_version_unsupported"
    """The peer speaks a wire version this build cannot parse."""

    FRAME_TOO_LARGE = "frame_too_large"
    """A frame declared or carried more than the frame limit."""

    MALFORMED_FRAME = "malformed_frame"
    """A frame was truncated, was not UTF-8, or was not a strict JSON object."""

    UNKNOWN_MESSAGE_TYPE = "unknown_message_type"
    """The ``type`` field named no message this build knows."""

    UNEXPECTED_MESSAGE = "unexpected_message"
    """A known message arrived in the wrong direction or at the wrong point."""

    UNKNOWN_FIELD = "unknown_field"
    """A message carried a field this build does not define. Nothing is ignored."""

    INVALID_FIELD = "invalid_field"
    """A field was missing, had the wrong type, or fell outside its published bound."""

    UNKNOWN_SESSION = "unknown_session"
    """The message named a session this server is not running."""

    INVALID_TOKEN = "invalid_token"
    """The membership token did not match. The token itself is never reported."""

    UNKNOWN_SLOT = "unknown_slot"
    """The session has no such player slot."""

    SLOT_OCCUPIED = "slot_occupied"
    """The slot already has a live connection."""

    MEMBERSHIP_REVOKED = "membership_revoked"
    """The slot disconnected. First release has no reconnect; the credential is spent."""

    ALREADY_JOINED = "already_joined"
    """The connection already joined. One connection owns at most one slot."""

    NOT_JOINED = "not_joined"
    """The connection sent gameplay input before joining."""

    WRONG_PLAYER = "wrong_player"
    """The message claimed a slot the connection does not own."""

    SEQUENCE_NOT_MONOTONIC = "sequence_not_monotonic"
    """An input sequence repeated or went backwards. Gaps are allowed; rewinds are not."""

    TICK_IN_PAST = "tick_in_past"
    """The requested tick has already been simulated or is already closed."""

    TICK_OUT_OF_RANGE = "tick_out_of_range"
    """The requested tick is further ahead than the session buffers."""

    RATE_LIMITED = "rate_limited"
    """The client exceeded its accepted input rate."""

    QUEUE_OVERFLOW = "queue_overflow"
    """An inbound or outbound queue is full. Outbound overflow closes the connection."""

    CONTENT_MISMATCH = "content_mismatch"
    """The client's content pack, level or schema version is not the session's."""

    DUPLICATE_ACTION = "duplicate_action"
    """A batch carried two actions of one kind. One of each kind per tick is the rule."""

    ILLEGAL_COMMAND = "illegal_command"
    """The simulation refused the batch for the state it was scheduled against."""

    TOO_MANY_CONNECTIONS = "too_many_connections"
    """The server is already holding as many connections as it will hold."""

    JOIN_TIMEOUT = "join_timeout"
    """A connection was opened but never proved membership in time."""

    TOO_MANY_ATTEMPTS = "too_many_attempts"
    """A connection spent its budget of failed join attempts."""

    FRAME_TIMEOUT = "frame_timeout"
    """A frame began arriving and then stopped. An idle connection is not this."""

    INTERNAL_ERROR = "internal_error"
    """The server failed to produce a tick. The session ends rather than hanging."""

    SESSION_CLOSED = "session_closed"
    """The session ended. The first release does not migrate or resume a session."""

    SERVER_SHUTDOWN = "server_shutdown"
    """The server is stopping."""
