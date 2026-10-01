"""Export a pack as a self-contained directory, staged and validated before it lands.

An export is all-or-nothing in the same sense a load is. The whole pack -- a manifest at
the root and every level beside it -- is written into a sibling staging directory, and
then :func:`battle_city_content.load_pack` reads that staging directory **before**
anything is renamed into place. A pack that fails is removed with its staging directory
and the destination is untouched; a pack that passes is moved in with a single rename, so
nobody ever sees a half-written pack.

Staging *beside* the destination rather than in the system temporary directory is what
makes the final rename an atomic move: ``os.replace`` cannot rename across filesystems,
and a temporary directory very often is one.

The export refuses, rather than tries, in three cases: a destination inside the bundled
content root, a destination that already exists without ``overwrite``, and a destination
that contains any file the pack is being read *from*. The last one is the quiet one --
exporting a pack over its own source directory would delete the levels midway through
reading them -- and it is checked against resolved paths, so a symlink into the source
does not slip past it.
"""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from battle_city_content import ContentError, ContentValidationError, Level, Pack, load_pack

from .draft import LevelDraft
from .errors import Diagnostic, DocumentInvalid, ToolRefusal
from .serialization import JsonValue, serialize_document
from .storage import require_outside_bundled_content, write_atomic
from .validation import pack_diagnostic

MANIFEST_NAME: Final[str] = "pack.json"
"""The manifest sits at the root of an exported pack, so the pack root is the directory."""

LEVELS_DIRECTORY: Final[str] = "levels"
"""Levels live one directory down, matching the layout of the bundled pack."""


@dataclass(frozen=True, slots=True)
class ExportReport:
    """What an export wrote."""

    destination: Path
    manifest: Path
    levels: tuple[Path, ...]
    bytes_written: int


def export_pack(
    pack: Pack,
    destination: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> ExportReport:
    """Write ``pack`` to ``destination`` as a stand-alone pack directory."""
    target = Path(os.fspath(destination))
    require_outside_bundled_content(target)
    _require_writable_directory(target, overwrite=overwrite)
    _require_sources_outside(pack, target)

    manifest_payload, level_payloads = _encode(pack)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = _staging_directory(parent, target.name, "staging")
    try:
        _stage(staging, manifest_payload, level_payloads)
        _validate_staged(staging, target)
        _swap_into_place(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    written = len(manifest_payload) + sum(len(payload) for _, payload in level_payloads)
    return ExportReport(
        destination=target,
        manifest=target / MANIFEST_NAME,
        levels=tuple(target / LEVELS_DIRECTORY / name for name, _ in level_payloads),
        bytes_written=written,
    )


# -- encoding ----------------------------------------------------------------


def level_filename(level: Level) -> str:
    """The file a level is exported to.

    A level identifier is lower-case alphanumeric with hyphens, which is a subset of what
    the manifest's path pattern accepts, so the name needs no escaping and the manifest
    entry is a plain relative path.
    """
    return f"{level.level_id}.json"


def pack_document(pack: Pack, entries: Sequence[tuple[str, str]]) -> dict[str, JsonValue]:
    """The manifest document for ``pack``, naming ``entries`` as ``(id, path)`` pairs."""
    authors: list[JsonValue] = list(pack.authors)
    levels: list[JsonValue] = [{"id": level_id, "path": path} for level_id, path in entries]
    licence: dict[str, JsonValue] = {
        "spdx_id": pack.license.spdx_id,
        "notice": pack.license.notice,
    }
    return {
        "schema_version": pack.schema_version,
        "id": pack.pack_id,
        "version": pack.version,
        "name": pack.name,
        "content_schema_version": pack.content_schema_version,
        "authors": authors,
        "license": licence,
        "levels": levels,
    }


def _encode(pack: Pack) -> tuple[bytes, tuple[tuple[str, bytes], ...]]:
    """Serialise the manifest and every level exactly once."""
    payloads = tuple(
        (level_filename(level), serialize_document(LevelDraft.from_level(level).to_document()))
        for level in pack.levels
    )
    entries = [
        (level.level_id, f"{LEVELS_DIRECTORY}/{name}")
        for level, (name, _) in zip(pack.levels, payloads, strict=True)
    ]
    return serialize_document(pack_document(pack, entries)), payloads


# -- staging -----------------------------------------------------------------


def _stage(staging: Path, manifest: bytes, levels: Sequence[tuple[str, bytes]]) -> None:
    (staging / LEVELS_DIRECTORY).mkdir(parents=True)
    write_atomic(staging / MANIFEST_NAME, manifest)
    for name, payload in levels:
        write_atomic(staging / LEVELS_DIRECTORY / name, payload)


def _validate_staged(staging: Path, target: Path) -> None:
    """Load the staged pack as the game would, naming the destination in any complaint."""
    manifest = staging / MANIFEST_NAME
    labels = {manifest: str(target / MANIFEST_NAME)}
    for path in sorted((staging / LEVELS_DIRECTORY).glob("*.json")):
        labels[path] = str(target / LEVELS_DIRECTORY / path.name)
    try:
        load_pack(manifest, root=staging)
    except ContentValidationError as error:
        raise DocumentInvalid(pack_diagnostic(error, labels)) from error
    except ContentError as error:
        raise DocumentInvalid(Diagnostic(str(target / MANIFEST_NAME), "", str(error))) from error


def _swap_into_place(staging: Path, target: Path) -> None:
    """Move the validated pack in, keeping the old one until the move succeeded."""
    displaced: Path | None = None
    if target.exists():
        displaced = _staging_directory(target.parent, target.name, "replaced")
        os.replace(target, displaced)
    try:
        os.replace(staging, target)
    except BaseException:
        if displaced is not None:
            os.replace(displaced, target)
        raise
    if displaced is not None:
        shutil.rmtree(displaced, ignore_errors=True)


def _staging_directory(parent: Path, name: str, role: str) -> Path:
    """A hidden sibling of the destination, named so a leftover is recognisable."""
    return parent / f".{name}.{role}-{secrets.token_hex(6)}"


# -- refusals ----------------------------------------------------------------


def _require_writable_directory(target: Path, *, overwrite: bool) -> None:
    if not target.exists():
        return
    if not target.is_dir():
        raise ToolRefusal(f"{target} exists and is not a directory")
    if not overwrite:
        raise ToolRefusal(
            f"{target} already exists; pass --overwrite to replace it, or name another directory"
        )


def _require_sources_outside(pack: Pack, target: Path) -> None:
    """Refuse an export that would write over the pack it is reading."""
    resolved = target.resolve()
    sources = [pack.origin, *(level.origin for level in pack.levels)]
    for source in sources:
        source_resolved = source.resolve()
        if source_resolved == resolved or source_resolved.is_relative_to(resolved):
            raise ToolRefusal(
                f"{target} contains {source}, which this pack is being read from; "
                "export to a directory outside the source pack"
            )
