"""Every bound a message is validated against, in one auditable table.

The networking specification requires message size, shape, enum values, rates,
sequence, ownership, membership and content compatibility to be checked before any
state changes. This module owns the size and shape half of that list: a limit is a
named constant here, never a literal buried in a decoder, so raising one is a visible
diff and a deliberate compatibility decision.

Limits are chosen against the classic 16x16 stage. They are deliberately generous
enough for a future larger stage and deliberately small enough that a single frame
cannot exhaust a server's memory: a worst-case snapshot carrying the maximum number of
entities and a keyframe grid stays well under :data:`MAX_FRAME_BYTES`.
"""

from __future__ import annotations

from typing import Final

PROTOCOL_VERSION: Final[int] = 1
"""The wire version this package speaks.

Every message carries it and every decode rejects a mismatch outright. Incompatible
peers fail with :data:`~battle_city_protocol.codes.RejectionCode.PROTOCOL_VERSION_UNSUPPORTED`
rather than attempting a best-effort parse, per the compatibility specification.
"""

SNAPSHOT_VERSION: Final[int] = 1
"""The layout version of :class:`~battle_city_protocol.messages.StateSnapshot`.

Distinct from the simulation's canonical state version, which a snapshot also carries:
the two change for different reasons. Reordering a snapshot field is a protocol change;
changing the canonical entity encoding is a simulation change that needs its own
proposal. A peer that understands one but not the other must be able to say which.
"""

MAX_FRAME_BYTES: Final[int] = 1 << 16
"""Largest single message payload, excluding the length header.

A frame longer than this is refused without being read into a message, so an oversized
or hostile declaration costs the length header and nothing else.
"""

MAX_JSON_DEPTH: Final[int] = 16
"""Deepest array/object nesting accepted. The deepest message nests four levels."""

MAX_IDENTIFIER_LENGTH: Final[int] = 64
"""Longest session, pack, level or version identifier."""

MIN_TOKEN_LENGTH: Final[int] = 8
"""Shortest membership token accepted. Short secrets are not secrets."""

MAX_TOKEN_LENGTH: Final[int] = 128
"""Longest membership token accepted."""

MAX_DETAIL_LENGTH: Final[int] = 200
"""Longest human-readable detail string on a rejection.

Details name fields and limits. They never quote a token, so a rejection is safe to log.
"""

MAX_DIGEST_LENGTH: Final[int] = 64
"""Longest hexadecimal digest string, sized for SHA-256."""

MAX_SLOT: Final[int] = 8
"""Highest player slot number. Slots are one-based, matching the stage contract."""

MAX_TICK: Final[int] = (1 << 31) - 1
"""Highest tick number a message may reference."""

MAX_SEQUENCE: Final[int] = (1 << 31) - 1
"""Highest input sequence number a client may send."""

MAX_TICK_RATE: Final[int] = 240
"""Highest fixed tick rate a session may advertise."""

MAX_ACTIONS_PER_BATCH: Final[int] = 3
"""Actions one input batch may carry: at most one of each allowed kind."""

MAX_EVENTS_PER_MESSAGE: Final[int] = 256
"""Events one tick may report. A tick that produces more is truncated, never dropped."""

MAX_EVENT_VALUES: Final[int] = 8
"""Widest event record. The widest kind in the published table carries six values."""

MAX_TANKS_PER_SNAPSHOT: Final[int] = 64
MAX_PROJECTILES_PER_SNAPSHOT: Final[int] = 128
MAX_POWERUPS_PER_SNAPSHOT: Final[int] = 32
MAX_PLAYERS_PER_SNAPSHOT: Final[int] = 8

MAX_GRID_DIMENSION: Final[int] = 64
"""Largest keyframe grid side. The classic stage is 16x16."""

MAX_ENTITY_ID: Final[int] = (1 << 31) - 1
"""Highest entity identifier. Identifiers are never reused inside a run."""

MAX_COORDINATE: Final[int] = 1 << 20
"""Largest absolute pixel or cell coordinate a message may carry."""

MAX_ENUM_CODE: Final[int] = 255
"""Largest simulation enum code a snapshot or event may carry.

Enum codes are the canonical simulation values. A snapshot states the canonical state
version that gives them meaning, so this package bounds them without naming them and
stays independent of the simulation package.
"""

MAX_LIVES: Final[int] = 99
MAX_KEYFRAME_INTERVAL: Final[int] = 3600

EVENT_VALUE_MIN: Final[int] = -(1 << 31)
EVENT_VALUE_MAX: Final[int] = (1 << 31) - 1
