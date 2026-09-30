"""The classic tile vocabulary, as the content specification defines it.

The content package owns this encoding: a level row is a string of tile-code characters
and every other package decodes it through the record this package returns. The
simulation keeps its own copy of the vocabulary because it may not depend on any project
package; the two are kept in step by cross-package tests, not by an import.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class TileCode(Enum):
    """A terrain cell. Member values are the characters a level row may contain."""

    EMPTY = "0"
    STONE = "1"
    BRICK = "2"
    MIRROR_NE = "3"
    MIRROR_SE = "4"
    WATER = "5"
    CRACKED_BRICK = "6"
    FOREST = "7"
    HOME = "8"


TILE_BY_CHAR: Final[dict[str, TileCode]] = {tile.value: tile for tile in TileCode}
"""Every character a level row may contain, mapped to its tile.

Membership here is the whole acceptance rule for a tile code. Testing ``str.isdigit()``
instead would admit non-ASCII digits such as U+0663.
"""

SPAWNABLE_TILES: Final[frozenset[TileCode]] = frozenset({TileCode.EMPTY})
"""Tiles a spawn point may stand on.

Anything else starts a tank inside blocking terrain or, for forest, starts it already
concealed. A level that wants either needs a content proposal saying what it means.
"""
