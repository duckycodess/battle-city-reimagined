"""Where everything sits in the editor's logical frame, as arithmetic.

The editor draws its own fixed logical frame -- wider than the game's, because it carries
a tool panel and a status bar the game has no use for -- and the window shows a whole
multiple of it, centred on a letterbox, exactly as the game window does. Nothing here
imports pygame: a rectangle is four integers, so every hit test in this module is a pure
function and is tested as one.

Mouse mapping is the reason this file exists. A click arrives in window pixels, and the
cell it means depends on the scale the window happens to be showing and on the letterbox
offset that centres the frame. :func:`logical_point` takes both as arguments rather than
measuring them, so the caller that owns the window reports them once and the arithmetic
stays testable without one.
"""

from __future__ import annotations

from typing import Final

from battle_city_content import CLASSIC_GRID_SIZE, GridCell, TileCode

type Rect = tuple[int, int, int, int]
"""A rectangle as ``(x, y, width, height)``, in logical pixels."""

TILE_SIZE: Final[int] = 16
"""Pixels per cell, matching the game's playfield so the two read as one program."""

GRID_CELLS: Final[int] = CLASSIC_GRID_SIZE
GRID_SPAN: Final[int] = TILE_SIZE * GRID_CELLS

MARGIN: Final[int] = 8
HEADER_HEIGHT: Final[int] = 26

GRID_ORIGIN: Final[tuple[int, int]] = (MARGIN, HEADER_HEIGHT)
PANEL_ORIGIN: Final[tuple[int, int]] = (MARGIN + GRID_SPAN + MARGIN, HEADER_HEIGHT)
PANEL_SIZE: Final[tuple[int, int]] = (128, GRID_SPAN)
STATUS_ORIGIN: Final[tuple[int, int]] = (MARGIN, HEADER_HEIGHT + GRID_SPAN + 6)
STATUS_SIZE: Final[tuple[int, int]] = (GRID_SPAN + MARGIN + PANEL_SIZE[0], 38)

LOGICAL_SIZE: Final[tuple[int, int]] = (
    MARGIN + GRID_SPAN + MARGIN + PANEL_SIZE[0] + MARGIN,
    STATUS_ORIGIN[1] + STATUS_SIZE[1] + MARGIN,
)
"""The editor's own frame. The game's frame is a different size and stays that way."""

PALETTE_TILES: Final[tuple[TileCode, ...]] = tuple(TileCode)
"""Every tile code the content specification defines, ``0`` through ``8``, in that order.

The palette is the vocabulary itself rather than a chosen subset: a tile the loader
accepts but the editor cannot paint would be a level nobody could author here.
"""

SWATCH_SIZE: Final[int] = 22
SWATCH_GAP: Final[int] = 4
SWATCH_COLUMNS: Final[int] = 3
PALETTE_ORIGIN: Final[tuple[int, int]] = (PANEL_ORIGIN[0] + 6, PANEL_ORIGIN[1] + 18)

PALETTE_ROWS: Final[int] = (len(PALETTE_TILES) + SWATCH_COLUMNS - 1) // SWATCH_COLUMNS
PALETTE_SPAN: Final[tuple[int, int]] = (
    SWATCH_COLUMNS * SWATCH_SIZE + (SWATCH_COLUMNS - 1) * SWATCH_GAP,
    PALETTE_ROWS * SWATCH_SIZE + (PALETTE_ROWS - 1) * SWATCH_GAP,
)


def grid_rect() -> Rect:
    """The whole editable field."""
    return (GRID_ORIGIN[0], GRID_ORIGIN[1], GRID_SPAN, GRID_SPAN)


def cell_rect(cell: GridCell) -> Rect:
    """Where ``cell`` is drawn."""
    return (
        GRID_ORIGIN[0] + cell.x * TILE_SIZE,
        GRID_ORIGIN[1] + cell.y * TILE_SIZE,
        TILE_SIZE,
        TILE_SIZE,
    )


def cell_at(point: tuple[int, int]) -> GridCell | None:
    """The cell a logical point lands in, or ``None`` when it is off the field."""
    x = point[0] - GRID_ORIGIN[0]
    y = point[1] - GRID_ORIGIN[1]
    if not (0 <= x < GRID_SPAN and 0 <= y < GRID_SPAN):
        return None
    return GridCell(x=x // TILE_SIZE, y=y // TILE_SIZE)


def swatch_rect(index: int) -> Rect:
    """Where palette entry ``index`` is drawn."""
    column = index % SWATCH_COLUMNS
    row = index // SWATCH_COLUMNS
    return (
        PALETTE_ORIGIN[0] + column * (SWATCH_SIZE + SWATCH_GAP),
        PALETTE_ORIGIN[1] + row * (SWATCH_SIZE + SWATCH_GAP),
        SWATCH_SIZE,
        SWATCH_SIZE,
    )


def swatch_at(point: tuple[int, int]) -> int | None:
    """The palette entry a logical point lands on, or ``None``.

    Tested against the drawn rectangles rather than derived from the grid arithmetic, so
    the gap between two swatches is a miss instead of rounding onto a neighbour.
    """
    for index in range(len(PALETTE_TILES)):
        if contains(swatch_rect(index), point):
            return index
    return None


def panel_rect() -> Rect:
    return (PANEL_ORIGIN[0], PANEL_ORIGIN[1], PANEL_SIZE[0], PANEL_SIZE[1])


def status_rect() -> Rect:
    return (STATUS_ORIGIN[0], STATUS_ORIGIN[1], STATUS_SIZE[0], STATUS_SIZE[1])


def header_rect() -> Rect:
    return (MARGIN, 4, LOGICAL_SIZE[0] - 2 * MARGIN, HEADER_HEIGHT - 8)


def contains(rect: Rect, point: tuple[int, int]) -> bool:
    """Whether ``point`` is inside ``rect``, right and bottom edges exclusive."""
    x, y, width, height = rect
    return x <= point[0] < x + width and y <= point[1] < y + height


def logical_point(
    window_point: tuple[int, int],
    *,
    origin: tuple[int, int],
    scale: int,
    logical_size: tuple[int, int] = LOGICAL_SIZE,
) -> tuple[int, int] | None:
    """Map a window pixel back to a logical pixel, or ``None`` for the letterbox.

    ``origin`` is where the scaled frame starts in the window and ``scale`` is the whole
    multiple it is drawn at -- both of them facts the window owner already computed to
    present the frame, passed in rather than re-derived so the two cannot disagree.
    """
    if scale <= 0:
        raise ValueError(f"scale must be positive, found {scale}")
    x = (window_point[0] - origin[0]) // scale
    y = (window_point[1] - origin[1]) // scale
    if 0 <= x < logical_size[0] and 0 <= y < logical_size[1]:
        return (x, y)
    return None
