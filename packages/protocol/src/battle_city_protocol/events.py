"""The authoritative event vocabulary, published as a table.

An event says what the simulation did during a tick. A snapshot already carries the
resulting state, so events exist for the things a state cannot express: the moment a
shot was fired, the tile that broke, the points a kill was worth. A client uses them
for presentation; it never derives authoritative state from them.

Every event payload in the rules engine is made of integers — entity identifiers, slot
numbers, coordinates and enum codes — so one record shape covers all of them::

    GameEvent(kind=EventKind.TILE_DAMAGED, values=(3, 7, 2, 0, 41))

The field names for each kind live in :data:`EVENT_FIELDS`, which is the contract: a
decoder checks a record's arity against it, and :meth:`GameEvent.field` reads a value
by name. Publishing one table rather than nineteen bespoke records keeps the wire
format, the validation and the documentation in a single place that a diff can review.

This package does not import the simulation, so the enum codes carried in ``values``
are opaque here. The snapshot that accompanies a tick declares the canonical state
version those codes belong to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .limits import EVENT_VALUE_MAX, EVENT_VALUE_MIN, MAX_EVENT_VALUES


class EventKind(StrEnum):
    """What happened. Member values are wire constants and never change."""

    ENEMY_SPAWNED = "enemy_spawned"
    POWERUP_SPAWNED = "powerup_spawned"
    POWERUP_DESPAWNED = "powerup_despawned"
    TANK_MOVED = "tank_moved"
    TANK_MOVE_BLOCKED = "tank_move_blocked"
    PROJECTILE_FIRED = "projectile_fired"
    PROJECTILE_REFLECTED = "projectile_reflected"
    PROJECTILE_ENDED = "projectile_ended"
    TILE_DAMAGED = "tile_damaged"
    SHIELD_BROKEN = "shield_broken"
    TANK_DESTROYED = "tank_destroyed"
    SCORE_AWARDED = "score_awarded"
    PLAYER_LIFE_LOST = "player_life_lost"
    PLAYER_RESPAWNED = "player_respawned"
    POWERUP_COLLECTED = "powerup_collected"
    POWERUP_EXPIRED = "powerup_expired"
    EXTRA_LIFE_GRANTED = "extra_life_granted"
    BASE_DESTROYED = "base_destroyed"
    RUN_ENDED = "run_ended"


EVENT_FIELDS: Final[dict[EventKind, tuple[str, ...]]] = {
    EventKind.ENEMY_SPAWNED: ("tank_id", "variant", "cell_x", "cell_y"),
    EventKind.POWERUP_SPAWNED: ("powerup_id", "kind", "cell_x", "cell_y"),
    EventKind.POWERUP_DESPAWNED: ("powerup_id", "kind", "cell_x", "cell_y"),
    EventKind.TANK_MOVED: ("tank_id", "origin_x", "origin_y", "x", "y", "facing"),
    EventKind.TANK_MOVE_BLOCKED: ("tank_id", "x", "y", "facing"),
    EventKind.PROJECTILE_FIRED: (
        "projectile_id",
        "owner_id",
        "faction",
        "x",
        "y",
        "direction",
    ),
    EventKind.PROJECTILE_REFLECTED: (
        "projectile_id",
        "cell_x",
        "cell_y",
        "tile",
        "incoming",
        "outgoing",
    ),
    EventKind.PROJECTILE_ENDED: ("projectile_id", "reason", "x", "y"),
    EventKind.TILE_DAMAGED: ("cell_x", "cell_y", "previous", "current", "projectile_id"),
    EventKind.SHIELD_BROKEN: ("tank_id", "projectile_id"),
    EventKind.TANK_DESTROYED: ("tank_id", "variant", "projectile_id"),
    EventKind.SCORE_AWARDED: ("points", "reason", "subject_tank_id", "projectile_id"),
    EventKind.PLAYER_LIFE_LOST: ("slot", "lives_remaining", "tank_id"),
    EventKind.PLAYER_RESPAWNED: ("slot", "tank_id", "cell_x", "cell_y"),
    EventKind.POWERUP_COLLECTED: ("powerup_id", "kind", "tank_id", "slot"),
    EventKind.POWERUP_EXPIRED: ("tank_id", "kind"),
    EventKind.EXTRA_LIFE_GRANTED: ("slot", "lives"),
    EventKind.BASE_DESTROYED: ("cell_x", "cell_y", "projectile_id"),
    EventKind.RUN_ENDED: ("outcome",),
}
"""Field names, in wire order, for every event kind.

Adding a field to a kind changes the wire format for that kind and needs the same
compatibility decision a new message would.
"""


@dataclass(frozen=True, slots=True)
class GameEvent:
    """One thing the simulation did, as a kind plus its published integer fields."""

    kind: EventKind
    values: tuple[int, ...]

    def __post_init__(self) -> None:
        names = EVENT_FIELDS[self.kind]
        if len(self.values) != len(names):
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"event {self.kind.value} takes {len(names)} values, got {len(self.values)}",
            )
        if len(names) > MAX_EVENT_VALUES:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"event {self.kind.value} declares more than {MAX_EVENT_VALUES} values",
            )
        for name, value in zip(names, self.values, strict=True):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MessageError(
                    RejectionCode.INVALID_FIELD,
                    f"event {self.kind.value} field {name} must be an integer",
                )
            if not EVENT_VALUE_MIN <= value <= EVENT_VALUE_MAX:
                raise MessageError(
                    RejectionCode.INVALID_FIELD,
                    f"event {self.kind.value} field {name} is out of range",
                )

    @classmethod
    def of(cls, kind: EventKind, *values: int) -> GameEvent:
        """Convenience constructor: ``GameEvent.of(EventKind.RUN_ENDED, 0)``."""
        return cls(kind=kind, values=tuple(values))

    @property
    def fields(self) -> tuple[str, ...]:
        """The field names this event carries, in wire order."""
        return EVENT_FIELDS[self.kind]

    def field(self, name: str) -> int:
        """Return the value of ``name``; raises :class:`KeyError` when this kind has none."""
        names = EVENT_FIELDS[self.kind]
        if name not in names:
            raise KeyError(f"event {self.kind.value} has no field {name!r}")
        return self.values[names.index(name)]

    def as_mapping(self) -> dict[str, int]:
        """Return this event's fields by name, for logging and assertions."""
        return dict(zip(EVENT_FIELDS[self.kind], self.values, strict=True))
