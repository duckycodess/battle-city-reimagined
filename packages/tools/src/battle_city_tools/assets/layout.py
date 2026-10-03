"""Where each frame sits in the atlas, decided the same way every time.

The packing is a shelf: sort the frames by name, lay them left to right until the next one
would not fit, start a new row whose height is the tallest frame already on it. It wastes
a few transparent pixels against a best-fit packer and it is worth it, because the layout
is a pure function of the frame names and sizes. The validator re-runs this function over
the metadata's own declared sizes and requires the result to equal the rectangles the
metadata claims, which is only a meaningful check while the algorithm has no state, no
randomness and no dependence on the order the caller happened to pass the frames in.

Changing :data:`ATLAS_WIDTH`, :data:`PADDING` or the sort key moves every sprite. They are
recorded in the metadata so a reader can tell a re-pack from a re-render.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

ALGORITHM: Final[str] = "shelf-v1"
SORT_KEY: Final[str] = "name"
ATLAS_WIDTH: Final[int] = 128
PADDING: Final[int] = 0
"""No gutter. Nothing in this pipeline scales or filters a frame, so bleed cannot occur."""


@dataclass(frozen=True, slots=True)
class Placement:
    """One frame's rectangle inside the atlas."""

    name: str
    x: int
    y: int
    width: int
    height: int

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """``(x, y, width, height)``, the shape the metadata records."""
        return self.x, self.y, self.width, self.height


@dataclass(frozen=True, slots=True)
class Layout:
    """A packed atlas: its size, and every frame's rectangle in packing order."""

    width: int
    height: int
    placements: tuple[Placement, ...]


def pack(
    sizes: Mapping[str, tuple[int, int]],
    *,
    atlas_width: int = ATLAS_WIDTH,
    padding: int = PADDING,
) -> Layout:
    """Lay ``sizes`` out by ascending name. Raises :class:`ValueError` on an unpackable frame."""
    if atlas_width <= 0:
        raise ValueError(f"atlas width must be positive, got {atlas_width}")
    if padding < 0:
        raise ValueError(f"padding must not be negative, got {padding}")
    if not sizes:
        raise ValueError("an atlas needs at least one frame")

    placements: list[Placement] = []
    x = y = shelf_height = 0
    for name in sorted(sizes):
        width, height = sizes[name]
        if width <= 0 or height <= 0:
            raise ValueError(f"frame {name!r} has a non-positive size of {width}x{height}")
        if width > atlas_width:
            raise ValueError(
                f"frame {name!r} is {width} pixels wide, wider than the {atlas_width}-pixel atlas"
            )
        if x and x + width > atlas_width:
            y += shelf_height + padding
            x = shelf_height = 0
        placements.append(Placement(name=name, x=x, y=y, width=width, height=height))
        x += width + padding
        shelf_height = max(shelf_height, height)
    return Layout(width=atlas_width, height=y + shelf_height, placements=tuple(placements))
