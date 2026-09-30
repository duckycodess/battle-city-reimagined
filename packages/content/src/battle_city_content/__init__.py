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
"""

from .errors import ContentError, ContentSchemaError, ContentValidationError
from .loader import (
    BUNDLED_PACK_PATH,
    LEVEL_SCHEMA_FILENAME,
    PACK_SCHEMA_FILENAME,
    bundled_content_root,
    load_bundled_pack,
    load_level,
    load_pack,
)
from .models import (
    CLASSIC_GRID_SIZE,
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
from .tiles import SPAWNABLE_TILES, TILE_BY_CHAR, TileCode

__all__ = [
    "BUNDLED_PACK_PATH",
    "CLASSIC_GRID_SIZE",
    "LEVEL_SCHEMA_FILENAME",
    "LEVEL_SCHEMA_VERSION",
    "PACK_SCHEMA_FILENAME",
    "PACK_SCHEMA_VERSION",
    "SPAWNABLE_TILES",
    "SUPPORTED_LEVEL_SCHEMA_VERSIONS",
    "TILE_BY_CHAR",
    "ContentError",
    "ContentSchemaError",
    "ContentValidationError",
    "GridCell",
    "Level",
    "LevelGrid",
    "LevelSource",
    "Pack",
    "PackLicense",
    "PlayerSpawn",
    "TileCode",
    "Wave",
    "bundled_content_root",
    "load_bundled_pack",
    "load_level",
    "load_pack",
]
