"""Canonical, versioned state encoding and hashing.

The encoding exists so two processes can prove they agree. It is deterministic by
construction: fixed-width big-endian integers, entities emitted in ascending identifier
order, and no dependence on dictionary or set iteration.

The leading magic and version make the format self-describing. Changing the layout,
field order, or any enum member value changes the bytes, so such a change needs a
proposal and a replay-compatibility policy, per the architecture specification.

``hashlib`` is used for SHA-256. It is a pure computation over bytes already in memory:
no clock, no file, no socket, no unseeded randomness.
"""

from __future__ import annotations

import hashlib
from typing import Final

from .entities import PlayerState, PowerupPickup, Projectile, Tank
from .geometry import GridPos, Vec2
from .rng import RNG_ALGORITHM_CODE, RNG_VERSION
from .state import SimulationState

CANONICAL_MAGIC: Final[bytes] = b"BCSIM"
CANONICAL_STATE_VERSION: Final[int] = 1
"""Bump only through an accepted proposal that states the replay-compatibility policy."""

_ABSENT: Final[int] = 0
_PRESENT: Final[int] = 1


class _Writer:
    """A tiny big-endian byte writer. Explicit widths keep the layout auditable."""

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def raw(self, value: bytes) -> None:
        self._parts.append(value)

    def u8(self, value: int) -> None:
        self._parts.append(value.to_bytes(1, "big", signed=False))

    def u16(self, value: int) -> None:
        self._parts.append(value.to_bytes(2, "big", signed=False))

    def u32(self, value: int) -> None:
        self._parts.append(value.to_bytes(4, "big", signed=False))

    def u64(self, value: int) -> None:
        self._parts.append(value.to_bytes(8, "big", signed=False))

    def i32(self, value: int) -> None:
        self._parts.append(value.to_bytes(4, "big", signed=True))

    def text(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self.u16(len(encoded))
        self.raw(encoded)

    def optional_u64(self, value: int | None) -> None:
        if value is None:
            self.u8(_ABSENT)
        else:
            self.u8(_PRESENT)
            self.u64(value)

    def optional_u16(self, value: int | None) -> None:
        if value is None:
            self.u8(_ABSENT)
        else:
            self.u8(_PRESENT)
            self.u16(value)

    def cell(self, value: GridPos) -> None:
        self.i32(value.x)
        self.i32(value.y)

    def optional_cell(self, value: GridPos | None) -> None:
        if value is None:
            self.u8(_ABSENT)
        else:
            self.u8(_PRESENT)
            self.cell(value)

    def point(self, value: Vec2) -> None:
        self.i32(value.x)
        self.i32(value.y)

    def build(self) -> bytes:
        return b"".join(self._parts)


def encode_state(state: SimulationState) -> bytes:
    """Return the canonical byte encoding of ``state``."""
    writer = _Writer()
    writer.raw(CANONICAL_MAGIC)
    writer.u16(CANONICAL_STATE_VERSION)

    writer.u64(state.tick)
    writer.text(state.stage_id)

    writer.u16(state.grid.width)
    writer.u16(state.grid.height)
    for row in state.grid.rows:
        for tile in row:
            writer.u8(tile.value)

    writer.u8(RNG_ALGORITHM_CODE)
    writer.u16(RNG_VERSION)
    writer.u64(state.rng.state)

    writer.u64(state.next_entity_id)
    writer.u8(_ABSENT if state.outcome is None else _PRESENT)
    if state.outcome is not None:
        writer.u8(state.outcome.value)

    writer.cell(state.base.cell)
    writer.u8(_PRESENT if state.base.destroyed else _ABSENT)

    players = sorted(state.players, key=lambda player: player.slot)
    writer.u16(len(players))
    for player in players:
        _write_player(writer, player)

    tanks = sorted(state.tanks, key=lambda tank: tank.entity_id)
    writer.u16(len(tanks))
    for tank in tanks:
        _write_tank(writer, tank)

    projectiles = sorted(state.projectiles, key=lambda shot: shot.entity_id)
    writer.u16(len(projectiles))
    for projectile in projectiles:
        _write_projectile(writer, projectile)

    powerups = sorted(state.powerups, key=lambda pickup: pickup.entity_id)
    writer.u16(len(powerups))
    for powerup in powerups:
        _write_powerup(writer, powerup)

    return writer.build()


def _write_player(writer: _Writer, player: PlayerState) -> None:
    writer.u16(player.slot)
    writer.i32(player.lives)
    writer.cell(player.spawn)
    writer.optional_u64(player.tank_id)


def _write_tank(writer: _Writer, tank: Tank) -> None:
    writer.u64(tank.entity_id)
    writer.u8(tank.variant.value)
    writer.point(tank.position)
    writer.u8(tank.facing.value)
    writer.optional_u16(tank.player_slot)
    writer.u32(tank.gatling_ticks)
    writer.u32(tank.invincible_ticks)


def _write_projectile(writer: _Writer, projectile: Projectile) -> None:
    writer.u64(projectile.entity_id)
    writer.u64(projectile.owner_id)
    writer.u8(projectile.faction.value)
    writer.point(projectile.position)
    writer.u8(projectile.direction.value)
    writer.optional_cell(projectile.reflected_cell)


def _write_powerup(writer: _Writer, powerup: PowerupPickup) -> None:
    writer.u64(powerup.entity_id)
    writer.u8(powerup.kind.value)
    writer.cell(powerup.cell)


def state_hash(state: SimulationState) -> str:
    """Return the lowercase SHA-256 hex digest of the canonical state bytes."""
    return hashlib.sha256(encode_state(state)).hexdigest()
