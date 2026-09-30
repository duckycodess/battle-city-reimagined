"""Tile vocabulary and the explicit terrain interaction tables.

Tile codes match the content specification's classic encoding, so a grid row loaded
from a level pack is a 16-character string of ``"0"``-``"8"``.

Terrain behaviour is expressed as explicit lookup tables rather than a chain of
conditionals. Tests assert against the tables, which makes a rule change visible as a
table diff instead of hiding inside control flow.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .errors import StageValidationError
from .geometry import Direction, GridPos


class Tile(Enum):
    """A terrain cell. Member values are the content-spec tile codes."""

    EMPTY = 0
    STONE = 1
    BRICK = 2
    MIRROR_NE = 3
    MIRROR_SE = 4
    WATER = 5
    CRACKED_BRICK = 6
    FOREST = 7
    HOME = 8


TILE_BY_CODE: Final[dict[int, Tile]] = {tile.value: tile for tile in Tile}
"""Integer-keyed tile lookup, for callers that already hold a numeric tile code.

Row parsing uses :data:`TILE_BY_CHAR` instead, because a character is not an integer and
converting one to reach this table is what let non-ASCII digits through.
"""

TILE_BY_CHAR: Final[dict[str, Tile]] = {str(tile.value): tile for tile in Tile}
"""The ASCII characters a level row may contain, mapped to their tile.

Membership in this table is the whole acceptance rule for a tile code. Testing
``str.isdigit()`` instead would admit non-ASCII digits such as U+0663 and would let a
character like U+00B2 escape as a bare ``ValueError`` from ``int()``, which is neither a
:class:`StageValidationError` nor a message naming the offending coordinate.
"""

TILES_BLOCKING_TANKS: Final[frozenset[Tile]] = frozenset(
    {
        Tile.STONE,
        Tile.BRICK,
        Tile.MIRROR_NE,
        Tile.MIRROR_SE,
        Tile.WATER,
        Tile.CRACKED_BRICK,
        Tile.HOME,
    }
)
"""Tiles a tank body may not overlap.

Empty ground and the forest overlay are the only traversable tiles. Water blocks tanks
but not projectiles; the home base is not traversable.
"""

TILES_PASSING_PROJECTILES: Final[frozenset[Tile]] = frozenset({Tile.EMPTY, Tile.WATER, Tile.FOREST})
"""Tiles a projectile flies through without any interaction."""

PROJECTILE_TILE_DAMAGE: Final[dict[Tile, Tile]] = {
    Tile.BRICK: Tile.CRACKED_BRICK,
    Tile.CRACKED_BRICK: Tile.EMPTY,
}
"""One projectile hit converts the key tile into the value tile and stops the shot."""

MIRROR_REFLECTIONS: Final[dict[Tile, dict[Direction, Direction]]] = {
    # Historical `battle city.py` reflected with `vx, vy = vy, vx` for tile 3 and
    # `vx, vy = -vy, -vx` for tile 4. Those swaps are reproduced verbatim here as
    # explicit direction tables. Note the historical names read backwards against the
    # geometry they produce: tile 3 ("north-east leaning") deflects a rightward shot
    # downward, which is a "\" mirror, and tile 4 deflects it upward, which is a "/".
    # The rebuild keeps the historical mapping and the historical names so converted
    # stages stay playable; renaming the tiles needs a content proposal.
    Tile.MIRROR_NE: {
        Direction.UP: Direction.LEFT,
        Direction.DOWN: Direction.RIGHT,
        Direction.LEFT: Direction.UP,
        Direction.RIGHT: Direction.DOWN,
    },
    Tile.MIRROR_SE: {
        Direction.UP: Direction.RIGHT,
        Direction.DOWN: Direction.LEFT,
        Direction.LEFT: Direction.DOWN,
        Direction.RIGHT: Direction.UP,
    },
}

TILES_CONCEALING_TANKS: Final[frozenset[Tile]] = frozenset({Tile.FOREST})
"""Tiles that hide a tank from presentation layers. See :mod:`battle_city_sim.visibility`."""


def blocks_tank(tile: Tile) -> bool:
    """Return whether a tank body may not overlap ``tile``."""
    return tile in TILES_BLOCKING_TANKS


def passes_projectile(tile: Tile) -> bool:
    """Return whether a projectile crosses ``tile`` without interacting."""
    return tile in TILES_PASSING_PROJECTILES


@dataclass(frozen=True, slots=True)
class TileGrid:
    """An immutable rectangular terrain grid addressed in row-major order."""

    width: int
    height: int
    rows: tuple[tuple[Tile, ...], ...]

    @classmethod
    def from_rows(cls, rows: Sequence[str]) -> TileGrid:
        """Build a grid from tile-code strings, one string per row.

        Only the ASCII characters ``"0"``-``"8"`` are tile codes. Raises
        :class:`StageValidationError` for a ragged grid or any other character.
        """
        if not rows:
            raise StageValidationError("grid.rows: must declare at least one row")
        width = len(rows[0])
        if width == 0:
            raise StageValidationError("grid.rows[0]: must declare at least one column")
        parsed: list[tuple[Tile, ...]] = []
        for y, row in enumerate(rows):
            if len(row) != width:
                raise StageValidationError(
                    f"grid.rows[{y}]: expected {width} columns, found {len(row)}"
                )
            cells: list[Tile] = []
            for x, code in enumerate(row):
                tile = TILE_BY_CHAR.get(code)
                if tile is None:
                    raise StageValidationError(f"grid.rows[{y}][{x}]: unknown tile code {code!r}")
                cells.append(tile)
            parsed.append(tuple(cells))
        return cls(width=width, height=len(parsed), rows=tuple(parsed))

    def contains(self, cell: GridPos) -> bool:
        return 0 <= cell.x < self.width and 0 <= cell.y < self.height

    def at(self, cell: GridPos) -> Tile:
        """Return the tile at ``cell``; raises :class:`IndexError` when out of bounds."""
        if not self.contains(cell):
            raise IndexError(f"cell out of bounds: ({cell.x}, {cell.y})")
        return self.rows[cell.y][cell.x]

    def with_tile(self, cell: GridPos, tile: Tile) -> TileGrid:
        """Return a copy of this grid with ``cell`` replaced by ``tile``."""
        if not self.contains(cell):
            raise IndexError(f"cell out of bounds: ({cell.x}, {cell.y})")
        if self.rows[cell.y][cell.x] is tile:
            return self
        row = self.rows[cell.y]
        new_row = row[: cell.x] + (tile,) + row[cell.x + 1 :]
        rows = self.rows[: cell.y] + (new_row,) + self.rows[cell.y + 1 :]
        return TileGrid(width=self.width, height=self.height, rows=rows)

    def positions_of(self, tile: Tile) -> tuple[GridPos, ...]:
        """Return every cell holding ``tile``, in row-major order."""
        return tuple(
            GridPos(x, y)
            for y, row in enumerate(self.rows)
            for x, cell in enumerate(row)
            if cell is tile
        )

    def to_rows(self) -> tuple[str, ...]:
        """Render the grid back into tile-code strings."""
        return tuple("".join(str(cell.value) for cell in row) for row in self.rows)
