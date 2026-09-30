"""Integer geometry primitives.

Coordinates are pixels unless a name says ``cell`` or ``grid``. Nothing here uses
floating point: every quantity the simulation stores or compares is an ``int`` so that
two machines stepping the same inputs produce bit-identical results.

Rectangles are **half-open**: a rectangle covers ``x <= px < x + width`` and
``y <= py < y + height``. Two tile-aligned 16x16 bodies that merely touch therefore do
not overlap, which removes the one-pixel "sticking" the historical runtime showed when
it compared bodies with a mix of ``<`` and ``<=`` against an 18-pixel probe box.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Direction(Enum):
    """A cardinal facing.

    Member values are canonical encoding codes. Changing them changes the canonical
    state bytes and therefore requires a replay-compatibility proposal.
    """

    UP = 0
    DOWN = 1
    LEFT = 2
    RIGHT = 3

    @property
    def delta(self) -> tuple[int, int]:
        """Unit step ``(dx, dy)`` for this facing, with ``+y`` pointing down."""
        return _DIRECTION_DELTAS[self]

    def scaled(self, speed: int) -> tuple[int, int]:
        """Step ``(dx, dy)`` for this facing at ``speed`` pixels."""
        dx, dy = self.delta
        return dx * speed, dy * speed


_DIRECTION_DELTAS: Final[dict[Direction, tuple[int, int]]] = {
    Direction.UP: (0, -1),
    Direction.DOWN: (0, 1),
    Direction.LEFT: (-1, 0),
    Direction.RIGHT: (1, 0),
}

DIRECTION_ORDER: Final[tuple[Direction, ...]] = (
    Direction.UP,
    Direction.DOWN,
    Direction.LEFT,
    Direction.RIGHT,
)
"""Canonical iteration order for facings. Never iterate a ``set`` of directions."""


@dataclass(frozen=True, slots=True, order=True)
class GridPos:
    """A tile coordinate: ``x`` is the column, ``y`` is the row."""

    x: int
    y: int


@dataclass(frozen=True, slots=True, order=True)
class Vec2:
    """A pixel coordinate."""

    x: int
    y: int

    def translated(self, dx: int, dy: int) -> Vec2:
        return Vec2(self.x + dx, self.y + dy)


@dataclass(frozen=True, slots=True)
class Rect:
    """A half-open axis-aligned box anchored at its top-left corner."""

    x: int
    y: int
    width: int
    height: int

    def overlaps(self, other: Rect) -> bool:
        """Return whether two half-open boxes share at least one pixel."""
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )

    def contains_point(self, point: Vec2) -> bool:
        return self.x <= point.x < self.x + self.width and self.y <= point.y < self.y + self.height


def clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into the inclusive range ``[low, high]``."""
    if low > high:
        raise ValueError(f"empty clamp range: [{low}, {high}]")
    return max(low, min(high, value))


def cell_of(point: Vec2, tile_size: int) -> GridPos:
    """Return the tile that contains ``point``.

    Uses floor division so negative pixel coordinates map to negative cells instead of
    folding back onto the playfield.
    """
    return GridPos(point.x // tile_size, point.y // tile_size)


def cell_rect(cell: GridPos, tile_size: int) -> Rect:
    """Return the half-open pixel box covered by ``cell``."""
    return Rect(cell.x * tile_size, cell.y * tile_size, tile_size, tile_size)


def cells_overlapping(rect: Rect, tile_size: int) -> tuple[GridPos, ...]:
    """Return every tile the half-open ``rect`` touches, in row-major order.

    An empty rectangle touches no cells. The result is a tuple, never a set, so callers
    cannot accidentally depend on hash ordering.
    """
    if rect.width <= 0 or rect.height <= 0:
        return ()
    first_x = rect.x // tile_size
    first_y = rect.y // tile_size
    last_x = (rect.x + rect.width - 1) // tile_size
    last_y = (rect.y + rect.height - 1) // tile_size
    return tuple(
        GridPos(x, y) for y in range(first_y, last_y + 1) for x in range(first_x, last_x + 1)
    )
