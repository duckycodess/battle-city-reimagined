"""Entity records and their collision bodies.

Every entity carries a stable integer ``entity_id`` allocated from a monotonic counter
in the state. Identifiers are never reused within a run, and every ordered pass over
entities sorts by identifier, so collision resolution does not depend on how a
container happened to be built.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from .geometry import Direction, GridPos, Rect, Vec2
from .rules import Rules


class Faction(Enum):
    """Who an entity fights for. Values are canonical encoding codes."""

    PLAYER = 0
    ENEMY = 1


class TankVariant(Enum):
    """Tank kind. Values are canonical encoding codes.

    The enemy variants reproduce the historical ``enemy_type`` values: ``0`` normal,
    ``1`` shielded, ``2`` unshielded. A shielded enemy becomes unshielded on its first
    hit rather than being destroyed.
    """

    PLAYER = 0
    ENEMY_NORMAL = 1
    ENEMY_SHIELDED = 2
    ENEMY_UNSHIELDED = 3


ENEMY_VARIANTS: Final[tuple[TankVariant, ...]] = (
    TankVariant.ENEMY_NORMAL,
    TankVariant.ENEMY_SHIELDED,
    TankVariant.ENEMY_UNSHIELDED,
)

VARIANT_FACTIONS: Final[dict[TankVariant, Faction]] = {
    TankVariant.PLAYER: Faction.PLAYER,
    TankVariant.ENEMY_NORMAL: Faction.ENEMY,
    TankVariant.ENEMY_SHIELDED: Faction.ENEMY,
    TankVariant.ENEMY_UNSHIELDED: Faction.ENEMY,
}


class PowerupKind(Enum):
    """A collectable effect. Values are the historical powerup codes."""

    GATLING = 1
    INVINCIBILITY = 2
    EXTRA_LIFE = 3


class RunOutcome(Enum):
    """Why a run ended. Stage-win timing is deferred to the campaign rules proposal."""

    BASE_DESTROYED = 0
    PLAYERS_ELIMINATED = 1


@dataclass(frozen=True, slots=True)
class Tank:
    """A tank body.

    ``position`` is the top-left pixel of a ``rules.tank_size`` square.
    """

    entity_id: int
    variant: TankVariant
    position: Vec2
    facing: Direction
    player_slot: int | None = None
    gatling_ticks: int = 0
    invincible_ticks: int = 0

    @property
    def faction(self) -> Faction:
        return VARIANT_FACTIONS[self.variant]

    def body(self, rules: Rules) -> Rect:
        return Rect(self.position.x, self.position.y, rules.tank_size, rules.tank_size)


@dataclass(frozen=True, slots=True)
class Projectile:
    """A projectile.

    ``position`` is a single pixel used for terrain lookup. Entity collisions inflate it
    to a ``2 * rules.projectile_radius + 1`` square centred on that pixel.

    ``reflected_cell`` records the mirror cell that last deflected this projectile so a
    mirror deflects it once per entry rather than on every tick it spends inside the
    cell. The historical runtime re-applied the swap every frame, which made a shot
    oscillate inside a mirror instead of leaving it.
    """

    entity_id: int
    owner_id: int
    faction: Faction
    position: Vec2
    direction: Direction
    reflected_cell: GridPos | None = None

    def body(self, rules: Rules) -> Rect:
        span = 2 * rules.projectile_radius + 1
        return Rect(
            self.position.x - rules.projectile_radius,
            self.position.y - rules.projectile_radius,
            span,
            span,
        )


@dataclass(frozen=True, slots=True)
class PowerupPickup:
    """An uncollected powerup occupying one whole cell."""

    entity_id: int
    kind: PowerupKind
    cell: GridPos

    def body(self, rules: Rules) -> Rect:
        return Rect(
            self.cell.x * rules.tile_size,
            self.cell.y * rules.tile_size,
            rules.tile_size,
            rules.tile_size,
        )


@dataclass(frozen=True, slots=True)
class PlayerState:
    """Per-slot campaign state.

    ``tank_id`` is ``None`` while the slot is waiting for an explicit respawn input,
    which mirrors the historical "Press Space to Respawn" gate without reading a device.
    """

    slot: int
    lives: int
    spawn: GridPos
    tank_id: int | None

    @property
    def awaiting_respawn(self) -> bool:
        return self.tank_id is None and self.lives > 0

    @property
    def eliminated(self) -> bool:
        return self.tank_id is None and self.lives <= 0


@dataclass(frozen=True, slots=True)
class BaseState:
    """The home base. Destroying it ends the run."""

    cell: GridPos
    destroyed: bool = False
