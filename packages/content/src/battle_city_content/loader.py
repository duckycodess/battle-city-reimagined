"""The public content loader: read a level or a pack, or reject it with a reason.

Loading is all-or-nothing. ``load_pack`` validates the manifest, then loads every level
it names, and only then builds the :class:`~battle_city_content.models.Pack`. A failure
anywhere raises :class:`~battle_city_content.errors.ContentValidationError` naming the
file and the field, and the caller receives nothing partial.

Validation happens in three layers, each reporting the same error type:

1. :mod:`battle_city_content.jsonio` decodes the bytes under explicit limits.
2. :mod:`battle_city_content.schema` checks shape against a checked-in JSON Schema.
3. This module checks the rules a schema cannot express: exactly one home base, spawns
   on empty ground, unique slots, spawns that no two tanks share, level identifiers that
   match their manifest entry, and paths that stay inside the pack.

Nothing here executes content. A level file is data that is read, never imported.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Final

from .errors import ContentError, ContentSchemaError, ContentValidationError
from .jsonio import JsonValue, read_json_object
from .models import (
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
from .schema import CompiledSchema, compile_schema
from .tiles import SPAWNABLE_TILES, TILE_BY_CHAR, TileCode

LEVEL_SCHEMA_FILENAME: Final[str] = "classic-level.schema.json"
"""Schema for level ``schema_version`` 1. A later format ships as its own file."""

PACK_SCHEMA_FILENAME: Final[str] = "pack.schema.json"
"""Schema for manifest ``schema_version`` 1. A later format ships as its own file."""

BUNDLED_PACK_PATH: Final[str] = "packs/classic.json"
"""The bundled classic pack manifest, relative to the packaged content root."""

_UNSAFE_PATH_SEGMENTS: Final[frozenset[str]] = frozenset({"", ".", ".."})

_GRID_ROW_FIELD: Final[re.Pattern[str]] = re.compile(r"grid\.rows\[(?P<index>\d+)\]")


def load_level(path: str | os.PathLike[str]) -> Level:
    """Load and fully validate one level file.

    ``path`` is resolved before anything is read, so one file has one origin however it
    was reached. ``load_pack`` already resolves the paths it declares, to check that they
    stay inside the pack root; without resolving here, the same level loaded directly
    through a relative path or a symlink would carry a different ``Level.origin`` and
    report its errors against a different name.

    Raises :class:`ContentValidationError` naming the resolved file and the offending
    field.
    """
    level_path = Path(os.fspath(path)).resolve()
    document = read_json_object(level_path)
    try:
        _level_schema().validate(document, path=level_path)
    except ContentValidationError as error:
        # The schema rejects a malformed row as a whole-string pattern mismatch. Refine
        # that one case into the offending column before re-raising. The schema still
        # decides what is valid; this only says where the invalid character is.
        raise _refine_row_error(level_path, document, error) from None
    return _build_level(level_path, document)


def load_pack(path: str | os.PathLike[str], *, root: str | os.PathLike[str] | None = None) -> Pack:
    """Load a pack manifest and every level it declares, or raise without loading any.

    Level paths in the manifest are relative to ``root``, which defaults to the
    manifest's own directory: the safe choice for a pack from outside this repository. A
    caller whose manifests sit in a ``packs/`` directory beside a ``levels/`` directory
    passes their shared parent instead. Every declared level path must resolve inside
    ``root``, and so must the manifest itself.
    """
    manifest_path = Path(os.fspath(path))
    document = read_json_object(manifest_path)
    _pack_schema().validate(document, path=manifest_path)

    content_schema_version = _expect_int(manifest_path, document, "content_schema_version")
    if content_schema_version not in SUPPORTED_LEVEL_SCHEMA_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_LEVEL_SCHEMA_VERSIONS))
        raise ContentValidationError(
            path=manifest_path,
            field="content_schema_version",
            message=(
                f"requires level schema version {content_schema_version}; "
                f"this build supports {supported}"
            ),
        )

    pack_root = (Path(os.fspath(root)) if root is not None else manifest_path.parent).resolve()
    if not manifest_path.resolve().is_relative_to(pack_root):
        raise ContentValidationError(
            path=manifest_path, field="", message=f"is outside its pack root {pack_root}"
        )

    entries = _resolve_level_entries(manifest_path, document, pack_root)
    levels: list[Level] = []
    for index, (declared_id, level_path) in enumerate(entries):
        level = load_level(level_path)
        if level.level_id != declared_id:
            raise ContentValidationError(
                path=manifest_path,
                field=f"levels[{index}].id",
                message=(f"declares {declared_id!r} but {level_path} declares {level.level_id!r}"),
            )
        if level.schema_version != content_schema_version:
            raise ContentValidationError(
                path=level_path,
                field="schema_version",
                message=(
                    f"is {level.schema_version} but pack {manifest_path} requires "
                    f"{content_schema_version}"
                ),
            )
        levels.append(level)

    license_document = _expect_object(manifest_path, document, "license")
    return Pack(
        schema_version=_expect_int(manifest_path, document, "schema_version"),
        pack_id=_expect_str(manifest_path, document, "id"),
        version=_expect_str(manifest_path, document, "version"),
        name=_expect_str(manifest_path, document, "name"),
        content_schema_version=content_schema_version,
        authors=tuple(
            _expect_str_item(manifest_path, item)
            for item in _expect_array(manifest_path, document, "authors")
        ),
        license=PackLicense(
            spdx_id=_expect_str(manifest_path, license_document, "spdx_id"),
            notice=_expect_str(manifest_path, license_document, "notice"),
        ),
        levels=tuple(levels),
        origin=manifest_path,
    )


def bundled_content_root() -> Path:
    """Return the directory holding the packaged levels, packs and schemas."""
    resource = resources.files(__package__ or "battle_city_content")
    if not isinstance(resource, Path):
        raise ContentError(
            f"bundled content is only available from an unpacked installation, found {resource!r}"
        )
    return resource


def load_bundled_pack() -> Pack:
    """Load the classic pack that ships with this package."""
    root = bundled_content_root()
    return load_pack(root.joinpath(*BUNDLED_PACK_PATH.split("/")), root=root)


def _resolve_level_entries(
    manifest_path: Path, document: dict[str, JsonValue], pack_root: Path
) -> tuple[tuple[str, Path], ...]:
    """Check identifiers and paths before any level file is opened."""
    entries: list[tuple[str, Path]] = []
    seen_ids: dict[str, int] = {}
    seen_paths: dict[Path, int] = {}
    for index, item in enumerate(_expect_array(manifest_path, document, "levels")):
        entry = _expect_entry(manifest_path, item, f"levels[{index}]")
        level_id = _expect_str(manifest_path, entry, "id")
        relative = _expect_str(manifest_path, entry, "path")

        first = seen_ids.get(level_id)
        if first is not None:
            raise ContentValidationError(
                path=manifest_path,
                field=f"levels[{index}].id",
                message=f"repeats the level id {level_id!r} already declared at levels[{first}]",
            )
        seen_ids[level_id] = index

        resolved = _resolve_contained(
            manifest_path, field=f"levels[{index}].path", relative=relative, pack_root=pack_root
        )
        duplicate = seen_paths.get(resolved)
        if duplicate is not None:
            raise ContentValidationError(
                path=manifest_path,
                field=f"levels[{index}].path",
                message=f"repeats the level file already declared at levels[{duplicate}]",
            )
        seen_paths[resolved] = index
        entries.append((level_id, resolved))
    return tuple(entries)


def _resolve_contained(manifest_path: Path, *, field: str, relative: str, pack_root: Path) -> Path:
    """Resolve a level path against the pack root, refusing anything outside it.

    Paths are relative to the pack root, which defaults to the manifest's own directory.
    The schema already rejects backslashes, absolute paths and drive letters. This also
    rejects ``.`` and ``..`` segments, and resolves symlinks before the containment
    check, so a link inside the pack cannot point at a file outside it.
    """
    segments = relative.split("/")
    for segment in segments:
        if segment in _UNSAFE_PATH_SEGMENTS:
            raise ContentValidationError(
                path=manifest_path,
                field=field,
                message=f"must not contain the path segment {segment!r}",
            )
    resolved = pack_root.joinpath(*segments).resolve()
    if not resolved.is_relative_to(pack_root):
        raise ContentValidationError(
            path=manifest_path,
            field=field,
            message=f"resolves to {resolved}, which is outside the pack root {pack_root}",
        )
    return resolved


def _refine_row_error(
    path: Path, document: dict[str, JsonValue], error: ContentValidationError
) -> ContentValidationError:
    """Return a column-accurate error for a rejected grid row, or ``error`` unchanged.

    Never widens what the schema accepts: this is only called on a document the schema
    has already rejected, and it returns the original error whenever it cannot say
    something more precise.
    """
    match = _GRID_ROW_FIELD.fullmatch(error.field)
    if match is None:
        return error
    grid = document.get("grid")
    if not isinstance(grid, dict):
        return error
    rows = grid.get("rows")
    if not isinstance(rows, list):
        return error
    index = int(match.group("index"))
    if index >= len(rows):
        return error
    row = rows[index]
    if not isinstance(row, str):
        return error

    for column, code in enumerate(row):
        if code not in TILE_BY_CHAR:
            return ContentValidationError(
                path=path,
                field=f"grid.rows[{index}][{column}]",
                message=f"is not a known tile code: {code!r}",
            )
    width = grid.get("width")
    if isinstance(width, int) and not isinstance(width, bool) and len(row) != width:
        return ContentValidationError(
            path=path,
            field=f"grid.rows[{index}]",
            message=f"must have {width} tile codes, found {len(row)}",
        )
    return error


def _build_level(path: Path, document: dict[str, JsonValue]) -> Level:
    """Apply the rules the schema cannot express, then freeze the record."""
    grid = _build_grid(path, _expect_object(path, document, "grid"))
    base_cells = grid.cells_of(TileCode.HOME)
    if len(base_cells) != 1:
        raise ContentValidationError(
            path=path,
            field="grid.rows",
            message=f"must declare exactly one home base tile, found {len(base_cells)}",
        )

    spawns = _expect_object(path, document, "spawns")
    players = _build_player_spawns(path, grid, _expect_array(path, spawns, "players"))
    enemies = _build_enemy_spawns(path, grid, _expect_array(path, spawns, "enemies"))
    shared = sorted({spawn.cell for spawn in players} & set(enemies))
    if shared:
        # Two bodies exactly coincident can never step off one another: the run would
        # begin soft-locked. The simulation rejects the same condition.
        cell = shared[0]
        raise ContentValidationError(
            path=path,
            field="spawns",
            message=f"player and enemy spawns share cell ({cell.x}, {cell.y})",
        )

    source: LevelSource | None = None
    if "source" in document:
        raw_source = _expect_object(path, document, "source")
        source = LevelSource(
            repository=_expect_str(path, raw_source, "repository"),
            revision=_expect_str(path, raw_source, "revision"),
            path=_expect_str(path, raw_source, "path"),
        )

    waves: tuple[Wave, ...] = ()
    if "waves" in document:
        waves = tuple(
            Wave(enemies=_expect_int(path, _expect_entry(path, item, f"waves[{index}]"), "enemies"))
            for index, item in enumerate(_expect_array(path, document, "waves"))
        )

    return Level(
        schema_version=_expect_int(path, document, "schema_version"),
        level_id=_expect_str(path, document, "id"),
        name=_expect_str(path, document, "name"),
        grid=grid,
        player_spawns=players,
        enemy_spawns=enemies,
        base_cell=base_cells[0],
        source=source,
        waves=waves,
        origin=path,
    )


def _build_grid(path: Path, document: dict[str, JsonValue]) -> LevelGrid:
    width = _expect_int(path, document, "width")
    height = _expect_int(path, document, "height")
    rows = tuple(_expect_str_item(path, row) for row in _expect_array(path, document, "rows"))
    # The schema fixes both dimensions and the row pattern for this version; a version
    # that parameterises them still needs these to agree with the rows as written.
    if len(rows) != height:
        raise ContentValidationError(
            path=path,
            field="grid.rows",
            message=f"must have {height} rows, found {len(rows)}",
        )
    for index, row in enumerate(rows):
        if len(row) != width:
            raise ContentValidationError(
                path=path,
                field=f"grid.rows[{index}]",
                message=f"must have {width} tile codes, found {len(row)}",
            )
        for column, code in enumerate(row):
            if code not in TILE_BY_CHAR:
                raise ContentValidationError(
                    path=path,
                    field=f"grid.rows[{index}][{column}]",
                    message=f"is not a known tile code: {code!r}",
                )
    return LevelGrid(width=width, height=height, rows=rows)


def _build_player_spawns(
    path: Path, grid: LevelGrid, items: Sequence[JsonValue]
) -> tuple[PlayerSpawn, ...]:
    spawns: list[PlayerSpawn] = []
    slots: dict[int, int] = {}
    cells: dict[GridCell, int] = {}
    for index, item in enumerate(items):
        field = f"spawns.players[{index}]"
        entry = _expect_entry(path, item, field)
        slot = _expect_int(path, entry, "slot")
        cell = GridCell(_expect_int(path, entry, "x"), _expect_int(path, entry, "y"))

        first_slot = slots.get(slot)
        if first_slot is not None:
            raise ContentValidationError(
                path=path,
                field=f"{field}.slot",
                message=f"repeats slot {slot}, already declared at spawns.players[{first_slot}]",
            )
        slots[slot] = index

        first_cell = cells.get(cell)
        if first_cell is not None:
            raise ContentValidationError(
                path=path,
                field=field,
                message=(
                    f"repeats cell ({cell.x}, {cell.y}), already declared at "
                    f"spawns.players[{first_cell}]"
                ),
            )
        cells[cell] = index

        _require_free_spawn(path, grid, cell, field)
        spawns.append(PlayerSpawn(slot=slot, cell=cell))
    return tuple(sorted(spawns))


def _build_enemy_spawns(
    path: Path, grid: LevelGrid, items: Sequence[JsonValue]
) -> tuple[GridCell, ...]:
    spawns: list[GridCell] = []
    cells: dict[GridCell, int] = {}
    for index, item in enumerate(items):
        field = f"spawns.enemies[{index}]"
        entry = _expect_entry(path, item, field)
        cell = GridCell(_expect_int(path, entry, "x"), _expect_int(path, entry, "y"))
        first = cells.get(cell)
        if first is not None:
            raise ContentValidationError(
                path=path,
                field=field,
                message=(
                    f"repeats cell ({cell.x}, {cell.y}), already declared at "
                    f"spawns.enemies[{first}]"
                ),
            )
        cells[cell] = index
        _require_free_spawn(path, grid, cell, field)
        spawns.append(cell)
    return tuple(spawns)


def _require_free_spawn(path: Path, grid: LevelGrid, cell: GridCell, field: str) -> None:
    """Reject a spawn that is off-grid or standing on anything but empty ground."""
    if not grid.contains(cell):
        raise ContentValidationError(
            path=path,
            field=field,
            message=(f"cell ({cell.x}, {cell.y}) is outside the {grid.width}x{grid.height} grid"),
        )
    tile = grid.tile_at(cell)
    if tile not in SPAWNABLE_TILES:
        raise ContentValidationError(
            path=path,
            field=field,
            message=f"cell ({cell.x}, {cell.y}) must be empty ground, found {tile.name}",
        )


@cache
def _level_schema() -> CompiledSchema:
    return _load_schema(LEVEL_SCHEMA_FILENAME)


@cache
def _pack_schema() -> CompiledSchema:
    return _load_schema(PACK_SCHEMA_FILENAME)


def _load_schema(filename: str) -> CompiledSchema:
    schema_path = bundled_content_root() / "schemas" / filename
    return compile_schema(read_json_object(schema_path), name=filename)


def _schema_disagreement(path: Path, field: str, found: str) -> ContentSchemaError:
    """Report a document the schema should have rejected before the loader saw it."""
    return ContentSchemaError(f"{path}: {field}: schema and loader disagree, loader found {found}")


def _expect_object(path: Path, document: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise _schema_disagreement(path, key, _describe(value))
    return value


def _expect_entry(path: Path, value: JsonValue, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise _schema_disagreement(path, field, _describe(value))
    return value


def _expect_array(path: Path, document: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = document.get(key)
    if not isinstance(value, list):
        raise _schema_disagreement(path, key, _describe(value))
    return value


def _expect_str(path: Path, document: dict[str, JsonValue], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise _schema_disagreement(path, key, _describe(value))
    return value


def _expect_str_item(path: Path, value: JsonValue) -> str:
    if not isinstance(value, str):
        raise _schema_disagreement(path, "", _describe(value))
    return value


def _expect_int(path: Path, document: dict[str, JsonValue], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _schema_disagreement(path, key, _describe(value))
    return value


def _describe(value: JsonValue) -> str:
    return f"{type(value).__name__} {value!r}"
