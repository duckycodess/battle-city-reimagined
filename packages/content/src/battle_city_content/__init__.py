"""Declarative level data, versioned schemas, and content validation.

This package owns the level format: the tile vocabulary, the checked-in JSON Schemas,
the loader that rejects a malformed document with a file and a field, and the bundled
classic stages that act as regression fixtures.

It depends on no other project package. In particular it does not build a simulation
stage: the client and the server map a :class:`Level` onto ``battle_city_sim.Stage``.
The mapping is mechanical because the loader already enforces the same stage contract —
a 16x16 grid, exactly one home base, spawns on empty ground that no two tanks share, and
unique player slots — and reports a violation as a content error naming the file.

Loading is all-or-nothing and never executes content::

    from battle_city_content import load_bundled_pack

    pack = load_bundled_pack()
    level = pack.level("classic-01")

A pack from outside this package is loaded with :func:`load_pack`, which takes the
manifest path and an optional pack root that every level path must stay inside.

Every document declares its ``schema_version``, and each version has its own schema
file: ``classic-level.schema.json`` and ``pack.schema.json`` are version 1, and a later
format ships as a new file beside them rather than editing those. A pack declares which
level schema version its levels are written against, so a pack pinned to version 1 keeps
loading unchanged.

Level schema version 2 is the opt-in gimmick format accepted as the ``gimmicks-v1``
change: ``classic-level.v2.schema.json``, which is version 1 plus five tile codes for
four directional conveyors and a teleport pad. The version a document declares decides
which schema checks it and which tile codes its rows may contain, so a classic level
never silently gains the new vocabulary and a version 1 consumer refuses a version 2
document by name. A level declaring teleport pads must declare zero or exactly two of
them; the loader enforces that after the schema, because a row pattern cannot count.

The gimmick sample lives in its own pack, :func:`load_gimmick_demo_pack`. The classic
manifest and its three levels are regression fixtures and are not extended.
"""

from .errors import ContentError, ContentSchemaError, ContentValidationError
from .loader import (
    BUNDLED_PACK_PATH,
    GIMMICK_DEMO_PACK_PATH,
    LEVEL_SCHEMA_FILENAME,
    LEVEL_SCHEMA_FILENAMES,
    LEVEL_V2_SCHEMA_FILENAME,
    PACK_SCHEMA_FILENAME,
    bundled_content_root,
    load_bundled_pack,
    load_gimmick_demo_pack,
    load_level,
    load_pack,
)
from .models import (
    CLASSIC_GRID_SIZE,
    GIMMICK_LEVEL_SCHEMA_VERSION,
    LEVEL_SCHEMA_VERSION,
    PACK_SCHEMA_VERSION,
    SUPPORTED_LEVEL_SCHEMA_VERSIONS,
    GridCell,
    Level,
    LevelGrid,
    LevelSource,
    Pack,
    PackLicense,
    PlayerSpawn,
    Wave,
)
from .tiles import (
    CLASSIC_TILE_BY_CHAR,
    CLASSIC_TILES,
    GIMMICK_TILES,
    SPAWNABLE_TILES,
    TELEPORT_PAIR_SIZE,
    TILE_BY_CHAR,
    TILE_CODES_BY_SCHEMA_VERSION,
    TileCode,
    tile_codes_for,
    uses_gimmick_tiles,
)

__all__ = [
    "BUNDLED_PACK_PATH",
    "CLASSIC_GRID_SIZE",
    "CLASSIC_TILES",
    "CLASSIC_TILE_BY_CHAR",
    "ContentError",
    "ContentSchemaError",
    "ContentValidationError",
    "GIMMICK_DEMO_PACK_PATH",
    "GIMMICK_LEVEL_SCHEMA_VERSION",
    "GIMMICK_TILES",
    "GridCell",
    "LEVEL_SCHEMA_FILENAME",
    "LEVEL_SCHEMA_FILENAMES",
    "LEVEL_SCHEMA_VERSION",
    "LEVEL_V2_SCHEMA_FILENAME",
    "Level",
    "LevelGrid",
    "LevelSource",
    "PACK_SCHEMA_FILENAME",
    "PACK_SCHEMA_VERSION",
    "Pack",
    "PackLicense",
    "PlayerSpawn",
    "SPAWNABLE_TILES",
    "SUPPORTED_LEVEL_SCHEMA_VERSIONS",
    "TELEPORT_PAIR_SIZE",
    "TILE_BY_CHAR",
    "TILE_CODES_BY_SCHEMA_VERSION",
    "TileCode",
    "Wave",
    "bundled_content_root",
    "load_bundled_pack",
    "load_gimmick_demo_pack",
    "load_level",
    "load_pack",
    "tile_codes_for",
    "uses_gimmick_tiles",
]
