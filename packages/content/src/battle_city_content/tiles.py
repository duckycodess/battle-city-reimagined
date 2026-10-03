"""The tile vocabulary, as the content specification defines it.

The content package owns this encoding: a level row is a string of tile-code characters
and every other package decodes it through the record this package returns. The
simulation keeps its own copy of the vocabulary because it may not depend on any project
package; the two are kept in step by cross-package tests, not by an import.

Two schema versions share one vocabulary
----------------------------------------
Level schema version 1 -- the classic format, and the one the three bundled stages are
written in -- accepts exactly ``"0"``-``"8"``. The opt-in version 2 adds ``"9"``,
``"A"``, ``"B"`` and ``"C"`` for the four conveyor directions and ``"D"`` for a teleport
pad. Which characters a given document may carry is therefore a question about *that
document's declared version*, not about this module, and is answered by
:func:`tile_codes_for`. The JSON Schema for each version states the same alphabet as a
row pattern, so a version 1 level carrying a conveyor is refused by shape before the
loader looks at it, and the classic files keep loading unchanged.

The same question is asked again on the wire, where the agreed
``ContentRef.content_schema_version`` decides whether a keyframe grid may carry the new
codes. That check reads this table too, which is what keeps one answer in one place.
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
    CONVEYOR_N = "9"
    CONVEYOR_E = "A"
    CONVEYOR_S = "B"
    CONVEYOR_W = "C"
    TELEPORT_PAD = "D"


TILE_BY_CHAR: Final[dict[str, TileCode]] = {tile.value: tile for tile in TileCode}
"""Every character any level row may contain, mapped to its tile.

Membership here plus the document's schema version is the whole acceptance rule for a
tile code. Testing ``str.isdigit()`` instead would admit non-ASCII digits such as U+0663
and would now also miss the four letter codes entirely.
"""

CLASSIC_TILES: Final[tuple[TileCode, ...]] = (
    TileCode.EMPTY,
    TileCode.STONE,
    TileCode.BRICK,
    TileCode.MIRROR_NE,
    TileCode.MIRROR_SE,
    TileCode.WATER,
    TileCode.CRACKED_BRICK,
    TileCode.FOREST,
    TileCode.HOME,
)
"""The nine tiles a level schema version 1 document may use, in code order."""

GIMMICK_TILES: Final[tuple[TileCode, ...]] = (
    TileCode.CONVEYOR_N,
    TileCode.CONVEYOR_E,
    TileCode.CONVEYOR_S,
    TileCode.CONVEYOR_W,
    TileCode.TELEPORT_PAD,
)
"""The five tiles level schema version 2 adds, in code order.

A document using any of them is a version 2 document. Nothing here interprets what they
*do*: conveyor direction and pad pairing are simulation rules, recorded in the accepted
``gimmicks-v1`` change and implemented in :mod:`battle_city_sim.gimmicks`.
"""

CLASSIC_TILE_BY_CHAR: Final[dict[str, TileCode]] = {tile.value: tile for tile in CLASSIC_TILES}
"""The version 1 alphabet. A version 1 row carrying anything else is rejected, never read."""

TILE_CODES_BY_SCHEMA_VERSION: Final[dict[int, dict[str, TileCode]]] = {
    1: CLASSIC_TILE_BY_CHAR,
    2: TILE_BY_CHAR,
}
"""Which characters each supported level schema version admits in a grid row."""

SPAWNABLE_TILES: Final[frozenset[TileCode]] = frozenset({TileCode.EMPTY})
"""Tiles a spawn point may stand on.

Anything else starts a tank inside blocking terrain or, for forest, starts it already
concealed. Conveyors and pads are traversable, but a spawn on one would displace or
transport a tank on the tick it entered play, which is a start nobody authored; a level
that wants either needs a content proposal saying what it means.
"""

TELEPORT_PAIR_SIZE: Final[int] = 2
"""How many teleport pads a valid level declares, when it declares any at all.

Zero or exactly two. One pad has no destination and three have no unambiguous pairing,
so the loader rejects any other count before a stage can start, with no fallback and no
silent substitution.
"""


def tile_codes_for(schema_version: int) -> dict[str, TileCode]:
    """Return the tile codes ``schema_version`` admits.

    Raises :class:`KeyError` for a version this build does not know, which is a caller
    bug: the supported set is checked against
    :data:`~battle_city_content.models.SUPPORTED_LEVEL_SCHEMA_VERSIONS` first.
    """
    return TILE_CODES_BY_SCHEMA_VERSION[schema_version]


def uses_gimmick_tiles(rows: tuple[str, ...]) -> bool:
    """Return whether any row carries a code only level schema version 2 admits."""
    gimmick = {tile.value for tile in GIMMICK_TILES}
    return any(code in gimmick for row in rows for code in row)
