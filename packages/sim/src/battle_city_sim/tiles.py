"""Tile vocabulary and the explicit terrain interaction tables.

Tile codes match the content specification's encoding. A classic (schema version 1) row
is a 16-character string of ``"0"``-``"8"``; the opt-in gimmick format (schema version 2)
adds ``"9"``, ``"A"``, ``"B"``, ``"C"`` for the four conveyor directions and ``"D"`` for
a teleport pad. The simulation reads both: whether a *level* may carry the new codes is a
content-schema decision, made by the content loader and the agreed
``ContentRef.content_schema_version``, not by this table.

Row characters are not derived from the member values. Members ``9``-``13`` would spell
themselves as two characters, so :data:`TILE_CODE_CHARS` states the one-character code
for every tile explicitly and :data:`TILE_BY_CHAR` is its exact inverse. Every encoder in
the project goes through those two tables, which is what keeps a row the same string on
both sides of the wire.

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
    CONVEYOR_N = 9
    CONVEYOR_E = 10
    CONVEYOR_S = 11
    CONVEYOR_W = 12
    TELEPORT_PAD = 13


TILE_BY_CODE: Final[dict[int, Tile]] = {tile.value: tile for tile in Tile}
"""Integer-keyed tile lookup, for callers that already hold a numeric tile code.

Row parsing uses :data:`TILE_BY_CHAR` instead, because a character is not an integer and
converting one to reach this table is what let non-ASCII digits through.
"""

TILE_CODE_CHARS: Final[dict[Tile, str]] = {
    Tile.EMPTY: "0",
    Tile.STONE: "1",
    Tile.BRICK: "2",
    Tile.MIRROR_NE: "3",
    Tile.MIRROR_SE: "4",
    Tile.WATER: "5",
    Tile.CRACKED_BRICK: "6",
    Tile.FOREST: "7",
    Tile.HOME: "8",
    Tile.CONVEYOR_N: "9",
    Tile.CONVEYOR_E: "A",
    Tile.CONVEYOR_S: "B",
    Tile.CONVEYOR_W: "C",
    Tile.TELEPORT_PAD: "D",
}
"""The one character each tile is written as in a level row, stated rather than derived.

``str(tile.value)`` happened to be the code while every member was a single decimal
digit. It stops being one at ``CONVEYOR_E``, whose value is ``10``: deriving the
character would silently widen a 16-column row to 17 characters and would make the
sixteen-character contract depend on how many members the enum has. Stating the mapping
keeps the classic codes byte-for-byte what they were and makes the new ones a visible
table entry.
"""

TILE_BY_CHAR: Final[dict[str, Tile]] = {code: tile for tile, code in TILE_CODE_CHARS.items()}
"""The ASCII characters a level row may contain, mapped to their tile.

Membership in this table is the whole acceptance rule for a tile code. Testing
``str.isdigit()`` instead would admit non-ASCII digits such as U+0663 and would let a
character like U+00B2 escape as a bare ``ValueError`` from ``int()``, which is neither a
:class:`StageValidationError` nor a message naming the offending coordinate.

It is the exact inverse of :data:`TILE_CODE_CHARS`; a duplicate character there would
collapse an entry here, which :mod:`tests.sim.test_tiles` asserts against.
"""

CLASSIC_TILES: Final[tuple[Tile, ...]] = (
    Tile.EMPTY,
    Tile.STONE,
    Tile.BRICK,
    Tile.MIRROR_NE,
    Tile.MIRROR_SE,
    Tile.WATER,
    Tile.CRACKED_BRICK,
    Tile.FOREST,
    Tile.HOME,
)
"""The nine tiles a content schema version 1 level may use, in code order."""

GIMMICK_TILES: Final[tuple[Tile, ...]] = (
    Tile.CONVEYOR_N,
    Tile.CONVEYOR_E,
    Tile.CONVEYOR_S,
    Tile.CONVEYOR_W,
    Tile.TELEPORT_PAD,
)
"""The five tiles the opt-in content schema version 2 adds, in code order."""

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

Empty ground, the forest overlay, the four conveyors and the teleport pad are the
traversable tiles. Water blocks tanks but not projectiles; the home base is not
traversable. A conveyor or a pad that blocked movement could never displace the tank it
is about to displace, so the gimmick tiles are deliberately absent from this set.
"""

TILES_PASSING_PROJECTILES: Final[frozenset[Tile]] = frozenset(
    {
        Tile.EMPTY,
        Tile.WATER,
        Tile.FOREST,
        Tile.CONVEYOR_N,
        Tile.CONVEYOR_E,
        Tile.CONVEYOR_S,
        Tile.CONVEYOR_W,
        Tile.TELEPORT_PAD,
    }
)
"""Tiles a projectile flies through without any interaction.

Conveyors and teleport pads are ground: a shot crossing one is neither steered,
transported, slowed nor stopped. The accepted ``gimmicks-v1`` interaction matrix states
that in as many words, and putting them in this set rather than in a branch is what keeps
the projectile phases unchanged.
"""

CONVEYOR_DIRECTIONS: Final[dict[Tile, Direction]] = {
    Tile.CONVEYOR_N: Direction.UP,
    Tile.CONVEYOR_E: Direction.RIGHT,
    Tile.CONVEYOR_S: Direction.DOWN,
    Tile.CONVEYOR_W: Direction.LEFT,
}
"""Which way each conveyor pushes a tank whose centre started the tick on it.

The push is one ``rules.tank_speed`` step in this direction, checked against the same
bounds, terrain and tank bodies an ordinary move is. It never turns the tank: a conveyor
moves a body, it does not aim a barrel.
"""

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


def conveyor_direction(tile: Tile) -> Direction | None:
    """Return the direction ``tile`` pushes a tank, or ``None`` when it is not a conveyor."""
    return CONVEYOR_DIRECTIONS.get(tile)


def is_teleport_pad(tile: Tile) -> bool:
    """Return whether ``tile`` is one half of a teleport pair."""
    return tile is Tile.TELEPORT_PAD


def tile_code_char(tile: Tile) -> str:
    """Return the single row character ``tile`` is written as. See :data:`TILE_CODE_CHARS`."""
    return TILE_CODE_CHARS[tile]


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

        The tile codes are exactly the keys of :data:`TILE_BY_CHAR`. Raises
        :class:`StageValidationError` for a ragged grid or any other character.

        This accepts every code the simulation can represent, including the gimmick
        codes. Deciding whether a given *level* is allowed to carry them belongs to the
        content schema version and to the agreed network content reference, which are
        checked before rows reach here; a grid that is already in a simulation state is
        past that gate.
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
        """Render the grid back into tile-code strings.

        Goes through :data:`TILE_CODE_CHARS` rather than ``str(cell.value)``, so a tile
        whose member value is not a single digit still writes one column.
        """
        return tuple("".join(TILE_CODE_CHARS[cell] for cell in row) for row in self.rows)
