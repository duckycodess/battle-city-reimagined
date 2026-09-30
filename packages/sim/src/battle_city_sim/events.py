"""Events emitted by one simulation tick.

Events are the simulation's only outward channel besides the next state. They carry no
presentation decisions: a renderer chooses a sprite, a campaign chooses whether a score
event counts, and a server chooses what to forward.

Score events deliberately report points without accumulating them. The product spec
defers exact scoring to a gameplay change proposal, so the rules engine reports the
historical values and keeps no score field in its state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .entities import Faction, PowerupKind, RunOutcome, TankVariant
from .geometry import Direction, GridPos, Vec2
from .tiles import Tile


class ProjectileEndReason(Enum):
    """Why a projectile left play. Values are canonical encoding codes."""

    OUT_OF_BOUNDS = 0
    TILE_BLOCKED = 1
    TILE_DESTROYED = 2
    HIT_TANK = 3
    HIT_PROJECTILE = 4
    HIT_BASE = 5


class ScoreReason(Enum):
    """What a reported score refers to."""

    SHIELD_BROKEN = 0
    NORMAL_KILL = 1
    UNSHIELDED_KILL = 2


@dataclass(frozen=True, slots=True)
class EnemySpawned:
    tank_id: int
    variant: TankVariant
    cell: GridPos


@dataclass(frozen=True, slots=True)
class PowerupSpawned:
    powerup_id: int
    kind: PowerupKind
    cell: GridPos


@dataclass(frozen=True, slots=True)
class PowerupDespawned:
    powerup_id: int
    kind: PowerupKind
    cell: GridPos


@dataclass(frozen=True, slots=True)
class TankMoved:
    tank_id: int
    origin: Vec2
    position: Vec2
    facing: Direction


@dataclass(frozen=True, slots=True)
class TankMoveBlocked:
    tank_id: int
    position: Vec2
    facing: Direction


@dataclass(frozen=True, slots=True)
class ProjectileFired:
    projectile_id: int
    owner_id: int
    faction: Faction
    position: Vec2
    direction: Direction


@dataclass(frozen=True, slots=True)
class ProjectileReflected:
    projectile_id: int
    cell: GridPos
    tile: Tile
    incoming: Direction
    outgoing: Direction


@dataclass(frozen=True, slots=True)
class ProjectileEnded:
    projectile_id: int
    reason: ProjectileEndReason
    position: Vec2


@dataclass(frozen=True, slots=True)
class TileDamaged:
    cell: GridPos
    previous: Tile
    current: Tile
    projectile_id: int


@dataclass(frozen=True, slots=True)
class ShieldBroken:
    tank_id: int
    projectile_id: int


@dataclass(frozen=True, slots=True)
class TankDestroyed:
    tank_id: int
    variant: TankVariant
    projectile_id: int


@dataclass(frozen=True, slots=True)
class ScoreAwarded:
    """Points the campaign layer may apply. The simulation keeps no running total."""

    points: int
    reason: ScoreReason
    subject_tank_id: int
    projectile_id: int


@dataclass(frozen=True, slots=True)
class PlayerLifeLost:
    slot: int
    lives_remaining: int
    tank_id: int


@dataclass(frozen=True, slots=True)
class PlayerRespawned:
    slot: int
    tank_id: int
    cell: GridPos


@dataclass(frozen=True, slots=True)
class PowerupCollected:
    powerup_id: int
    kind: PowerupKind
    tank_id: int
    slot: int


@dataclass(frozen=True, slots=True)
class PowerupExpired:
    tank_id: int
    kind: PowerupKind


@dataclass(frozen=True, slots=True)
class ExtraLifeGranted:
    slot: int
    lives: int


@dataclass(frozen=True, slots=True)
class BaseDestroyed:
    cell: GridPos
    projectile_id: int


@dataclass(frozen=True, slots=True)
class RunEnded:
    outcome: RunOutcome


type Event = (
    EnemySpawned
    | PowerupSpawned
    | PowerupDespawned
    | TankMoved
    | TankMoveBlocked
    | ProjectileFired
    | ProjectileReflected
    | ProjectileEnded
    | TileDamaged
    | ShieldBroken
    | TankDestroyed
    | ScoreAwarded
    | PlayerLifeLost
    | PlayerRespawned
    | PowerupCollected
    | PowerupExpired
    | ExtraLifeGranted
    | BaseDestroyed
    | RunEnded
)
