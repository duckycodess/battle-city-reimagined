"""Reading an authoritative snapshot as something the renderer can draw.

An online client does not have a :class:`~battle_city_sim.SimulationState` and must not
make one. The server owns the run; this client is a window onto it. What arrives is a
:class:`~battle_city_protocol.StateSnapshot` full of canonical integer codes, and what
the renderer wants is tiles, variants, facings and factions, so this module is the one
place the two meet — the client's half of the mapping the server does in
:mod:`battle_city_server.translation`.

Nothing here steps anything. There is no rules engine in this file, no tick, no
collision, no respawn policy and no outcome decision. A field this module cannot read is
a disagreement about the canonical encoding, which is reported as
:class:`RemoteStateError` rather than guessed at: a client that quietly rendered an
unknown code would be showing a board that does not exist.

Terrain between keyframes
-------------------------
A snapshot carries the grid on a keyframe only, and terrain changes in between arrive as
:data:`~battle_city_protocol.EventKind.TILE_DAMAGED` events. :meth:`RemoteBoard.damaged`
applies one to the terrain this client is holding, so a brick someone shot looks shot
before the next keyframe says so. That is presentation keeping up with authoritative
events, not the client deciding anything: the next keyframe replaces the whole grid, and
a missed event therefore corrects itself within one keyframe interval.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from battle_city_protocol import (
    EventKind,
    GameEvent,
    PlayerSnapshot,
    StateSnapshot,
)
from battle_city_sim import (
    Direction,
    Faction,
    GridPos,
    PowerupKind,
    Rules,
    StageValidationError,
    TankVariant,
    Tile,
    TileGrid,
)


class RemoteStateError(ValueError):
    """An authoritative snapshot carried a value this build cannot read.

    Raised for an unknown tile code, variant, facing, faction or powerup kind. It means
    the peers disagree about the canonical encoding, which the snapshot's state version
    is there to catch; reporting it is how a client says "this server is newer than me"
    instead of drawing a board with holes in it.
    """


@dataclass(frozen=True, slots=True)
class RemoteTank:
    """A tank as the server last reported it."""

    entity_id: int
    variant: TankVariant
    facing: Direction
    x: int
    y: int
    slot: int | None
    gatling_ticks: int
    invincible_ticks: int

    @property
    def invincible(self) -> bool:
        return self.invincible_ticks > 0


@dataclass(frozen=True, slots=True)
class RemoteShot:
    """A projectile as the server last reported it."""

    entity_id: int
    faction: Faction
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class RemotePowerup:
    """An uncollected powerup as the server last reported it."""

    entity_id: int
    kind: PowerupKind
    cell: GridPos


@dataclass(frozen=True, slots=True)
class RemoteBoard:
    """Everything the renderer needs for one authoritative tick.

    Built from the last snapshot that arrived, plus the terrain the client is holding.
    It is frozen, so a frame draws one consistent board even if a message lands halfway
    through drawing it.
    """

    tick: int
    grid: TileGrid
    tanks: tuple[RemoteTank, ...]
    shots: tuple[RemoteShot, ...]
    powerups: tuple[RemotePowerup, ...]
    players: tuple[PlayerSnapshot, ...]
    base_cell: GridPos
    base_destroyed: bool
    outcome: int | None
    state_hash: str

    def player(self, slot: int) -> PlayerSnapshot | None:
        for player in self.players:
            if player.slot == slot:
                return player
        return None

    def tank_of(self, slot: int) -> RemoteTank | None:
        for tank in self.tanks:
            if tank.slot == slot:
                return tank
        return None

    def damaged(self, event: GameEvent) -> RemoteBoard:
        """Return this board with one tile-damage event applied to the terrain.

        A code the build does not know is ignored rather than raised on: a damage event
        is presentation detail, the next keyframe carries the authoritative grid, and
        dropping a frame of brick decay is a far better failure than refusing to draw.
        """
        if event.kind is not EventKind.TILE_DAMAGED:
            return self
        cell = GridPos(event.field("cell_x"), event.field("cell_y"))
        if not self.grid.contains(cell):
            return self
        try:
            tile = Tile(event.field("current"))
        except ValueError:
            return self
        return replace(self, grid=self.grid.with_tile(cell, tile))


def terrain_from_rows(rows: tuple[str, ...]) -> TileGrid:
    """Build the terrain a keyframe carried.

    The rows are the content package's tile codes, which is the same vocabulary the
    simulation reads, so this is a parse rather than a translation.
    """
    try:
        return TileGrid.from_rows(rows)
    except StageValidationError as error:
        raise RemoteStateError(f"keyframe grid: {error}") from error


def board_from_snapshot(snapshot: StateSnapshot, terrain: TileGrid) -> RemoteBoard:
    """Read ``snapshot`` against the terrain this client is holding."""
    return RemoteBoard(
        tick=snapshot.tick,
        grid=terrain,
        tanks=tuple(
            RemoteTank(
                entity_id=tank.entity_id,
                variant=_variant(tank.variant),
                facing=_facing(tank.facing),
                x=tank.x,
                y=tank.y,
                slot=tank.slot,
                gatling_ticks=tank.gatling_ticks,
                invincible_ticks=tank.invincible_ticks,
            )
            for tank in snapshot.tanks
        ),
        shots=tuple(
            RemoteShot(
                entity_id=shot.entity_id,
                faction=_faction(shot.faction),
                x=shot.x,
                y=shot.y,
            )
            for shot in snapshot.projectiles
        ),
        powerups=tuple(
            RemotePowerup(
                entity_id=powerup.entity_id,
                kind=_powerup(powerup.kind),
                cell=GridPos(powerup.cell_x, powerup.cell_y),
            )
            for powerup in snapshot.powerups
        ),
        players=snapshot.players,
        base_cell=GridPos(snapshot.base.cell_x, snapshot.base.cell_y),
        base_destroyed=snapshot.base.destroyed,
        outcome=snapshot.outcome,
        state_hash=snapshot.state_hash,
    )


def shot_origin(shot: RemoteShot, rules: Rules) -> tuple[int, int]:
    """The top-left pixel of a projectile's body, matching the simulation's geometry.

    A snapshot carries the single pixel the simulation uses for terrain lookup; the body
    is the square centred on it. Computing the corner here keeps the online and offline
    draw calls identical rather than nearly identical.
    """
    return (shot.x - rules.projectile_radius, shot.y - rules.projectile_radius)


def _variant(code: int) -> TankVariant:
    try:
        return TankVariant(code)
    except ValueError as error:
        raise RemoteStateError(f"unknown tank variant code {code}") from error


def _facing(code: int) -> Direction:
    try:
        return Direction(code)
    except ValueError as error:
        raise RemoteStateError(f"unknown facing code {code}") from error


def _faction(code: int) -> Faction:
    try:
        return Faction(code)
    except ValueError as error:
        raise RemoteStateError(f"unknown faction code {code}") from error


def _powerup(code: int) -> PowerupKind:
    try:
        return PowerupKind(code)
    except ValueError as error:
        raise RemoteStateError(f"unknown powerup code {code}") from error
