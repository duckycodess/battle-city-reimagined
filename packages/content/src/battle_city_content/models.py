"""Immutable records produced by the content loader.

These are decoded data, not simulation objects. The content package may not depend on
any other project package, so it does not build a ``battle_city_sim.Stage``: the client
and the server map a :class:`Level` onto one. The mapping is mechanical because this
package enforces the same stage contract the simulation does — 16x16 grid, one home
base, spawns on empty ground, unique slots — and reports a violation as a content error
with a file and a field rather than as a simulation error at start-up.

Every record is frozen and holds only tuples, so a loaded level cannot be mutated into
something that was never validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .tiles import TILE_BY_CHAR, TileCode

CLASSIC_GRID_SIZE: Final[int] = 16
"""The classic stage is 16 columns by 16 rows. See the content specification."""

LEVEL_SCHEMA_VERSION: Final[int] = 1
"""The classic level ``schema_version``: the format the three bundled stages are in.

Still the default a new document is written as. A document only becomes version 2 by
using a tile code version 1 does not have, which is what keeps an edit that touches no
gimmick terrain loadable by a version 1 consumer.
"""

GIMMICK_LEVEL_SCHEMA_VERSION: Final[int] = 2
"""The opt-in level ``schema_version`` that admits conveyor and teleport-pad codes.

Accepted as the ``gimmicks-v1`` change. It is a separate schema file beside the classic
one rather than an edit to it, so a level pinned to version 1 keeps loading byte for
byte and a version 1 consumer refuses a version 2 document by name instead of
reinterpreting a row it cannot read.
"""

PACK_SCHEMA_VERSION: Final[int] = 1
"""The pack manifest ``schema_version`` this package writes and understands.

Unchanged by the gimmick format: a manifest already states which *level* schema version
its levels are written against, so a version 2 pack is an ordinary version 1 manifest
whose ``content_schema_version`` is 2.
"""

SUPPORTED_LEVEL_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset(
    {LEVEL_SCHEMA_VERSION, GIMMICK_LEVEL_SCHEMA_VERSION}
)
"""Level schema versions a pack may declare as its compatible content schema.

A future level format adds its version here alongside a migration, so an old pack keeps
loading and a pack built for a newer format is rejected by name instead of by accident.
"""


@dataclass(frozen=True, slots=True, order=True)
class GridCell:
    """A grid coordinate: ``x`` is the column, ``y`` is the row, ``(0, 0)`` is top-left."""

    x: int
    y: int


@dataclass(frozen=True, slots=True, order=True)
class PlayerSpawn:
    """Where a player slot enters the level. Slots are one-based and unique."""

    slot: int
    cell: GridCell


@dataclass(frozen=True, slots=True)
class LevelSource:
    """Provenance for a converted level.

    Optional: an original level authored for this project has no upstream source. The
    bundled classic levels carry one, and their tests assert it.
    """

    repository: str
    revision: str
    path: str


@dataclass(frozen=True, slots=True)
class Wave:
    """Forward-declared wave metadata: how many enemy tanks a wave releases.

    Deliberately minimal. Enemy variant mix, spawn cadence, and win timing are campaign
    behaviour that the gameplay phase owns; nothing in this package interprets a wave,
    and the bundled classic levels declare none because the historical stage data has no
    wave information to convert.
    """

    enemies: int


@dataclass(frozen=True, slots=True)
class LevelGrid:
    """A validated rectangular terrain grid, stored as the rows the file declared."""

    width: int
    height: int
    rows: tuple[str, ...]

    def tile_at(self, cell: GridCell) -> TileCode:
        """Return the tile at ``cell``; raises :class:`IndexError` when out of bounds."""
        if not self.contains(cell):
            raise IndexError(f"cell out of bounds: ({cell.x}, {cell.y})")
        return TILE_BY_CHAR[self.rows[cell.y][cell.x]]

    def contains(self, cell: GridCell) -> bool:
        return 0 <= cell.x < self.width and 0 <= cell.y < self.height

    def cells_of(self, tile: TileCode) -> tuple[GridCell, ...]:
        """Return every cell holding ``tile``, in row-major order."""
        return tuple(
            GridCell(x, y)
            for y, row in enumerate(self.rows)
            for x, code in enumerate(row)
            if code == tile.value
        )


@dataclass(frozen=True, slots=True)
class Level:
    """A validated level document."""

    schema_version: int
    level_id: str
    name: str
    grid: LevelGrid
    player_spawns: tuple[PlayerSpawn, ...]
    enemy_spawns: tuple[GridCell, ...]
    base_cell: GridCell
    source: LevelSource | None
    waves: tuple[Wave, ...]
    origin: Path

    def player_spawn_for(self, slot: int) -> PlayerSpawn:
        """Return the spawn for ``slot``; raises :class:`KeyError` when unknown."""
        for spawn in self.player_spawns:
            if spawn.slot == slot:
                return spawn
        raise KeyError(f"unknown player slot: {slot}")


@dataclass(frozen=True, slots=True)
class PackLicense:
    """What a pack claims about the rights in its data.

    ``spdx_id`` is an SPDX licence identifier or the SPDX value ``NOASSERTION`` when the
    pack makes no claim. ``notice`` says in prose where the data came from.
    """

    spdx_id: str
    notice: str


@dataclass(frozen=True, slots=True)
class Pack:
    """A validated pack manifest together with every level it declares.

    A pack is only ever constructed once every one of its levels has loaded, so a caller
    never receives a partially loaded pack.
    """

    schema_version: int
    pack_id: str
    version: str
    name: str
    content_schema_version: int
    authors: tuple[str, ...]
    license: PackLicense
    levels: tuple[Level, ...]
    origin: Path

    @property
    def level_ids(self) -> tuple[str, ...]:
        """Level identifiers in manifest order, which is campaign order."""
        return tuple(level.level_id for level in self.levels)

    def level(self, level_id: str) -> Level:
        """Return the level with ``level_id``; raises :class:`KeyError` when unknown."""
        for level in self.levels:
            if level.level_id == level_id:
                return level
        raise KeyError(f"unknown level id: {level_id}")
