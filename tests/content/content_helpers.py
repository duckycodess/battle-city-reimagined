"""Shared builders for the content validation tests.

Tests state their own synthetic level instead of mutating a bundled classic file, so a
failure names the rule under test rather than a fixture edit somewhere else. The bundled
layouts are pinned separately, against literal rows, in ``test_content_classic_stages``.

The module name is deliberately unique across the test tree: pytest imports test support
modules by basename, so a second ``helpers`` would collide with the simulation suite.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from battle_city_content import ContentValidationError

SYNTHETIC_ROWS: tuple[str, ...] = (
    "0000000000000000",
    "1111111111111111",
    "0000000000000000",
    "2222222222222222",
    "0000000000000000",
    "5555555555555555",
    "0000000000000000",
    "3434343434343434",
    "0000000000000000",
    "6666666666666666",
    "0000000000000000",
    "7777777777777777",
    "0000000000000000",
    "1212121212121212",
    "0000000000000000",
    "0000000800000000",
)
"""A synthetic 16x16 layout: one home base, every tile code, wide free rows.

Row 15 holds the only home tile at column 7. Even-numbered rows are entirely empty
ground, which gives every spawn test a free cell to move to.
"""

FREE_CELL = (8, 14)
"""An empty-ground cell in the synthetic layout, used as the default player spawn."""


def synthetic_level(**overrides: Any) -> dict[str, Any]:
    """Return a valid synthetic level document, with ``overrides`` applied at the root."""
    document: dict[str, Any] = {
        "schema_version": 1,
        "id": "synthetic-01",
        "name": "Synthetic Stage 1",
        "grid": {"width": 16, "height": 16, "rows": list(SYNTHETIC_ROWS)},
        "spawns": {
            "players": [{"slot": 1, "x": FREE_CELL[0], "y": FREE_CELL[1]}],
            "enemies": [{"x": 0, "y": 0}, {"x": 15, "y": 0}],
        },
    }
    document.update(overrides)
    return document


def synthetic_manifest(**overrides: Any) -> dict[str, Any]:
    """Return a valid synthetic pack manifest naming one level, before ``overrides``."""
    document: dict[str, Any] = {
        "schema_version": 1,
        "id": "synthetic",
        "version": "1.0.0",
        "name": "Synthetic pack",
        "content_schema_version": 1,
        "authors": ["Content tests"],
        "license": {"spdx_id": "MIT", "notice": "Synthetic test data, written for this suite."},
        "levels": [{"id": "synthetic-01", "path": "levels/synthetic-01.json"}],
    }
    document.update(overrides)
    return document


def mutated_level(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return the synthetic level after ``mutate`` has edited it in place."""
    document = synthetic_level()
    mutate(document)
    return document


def mutated_manifest(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return the synthetic manifest after ``mutate`` has edited it in place."""
    document = synthetic_manifest()
    mutate(document)
    return document


def write_json(path: Path, document: Mapping[str, Any] | list[Any]) -> Path:
    """Write ``document`` as JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    """Write raw text, for documents that are not valid JSON at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_bytes(path: Path, raw: bytes) -> Path:
    """Write raw bytes, for encoding-level rejections."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def build_pack(
    root: Path,
    *,
    levels: Mapping[str, Mapping[str, Any]] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> Path:
    """Lay out a pack under ``root`` and return the manifest path.

    ``levels`` maps a path relative to ``root`` to the level document written there. The
    default is the single synthetic level the default manifest names.
    """
    written = (
        dict(levels) if levels is not None else {"levels/synthetic-01.json": synthetic_level()}
    )
    for relative, document in written.items():
        write_json(root / relative, document)
    return write_json(
        root / "packs" / "pack.json",
        manifest if manifest is not None else synthetic_manifest(),
    )


def rejection(action: Callable[[], object]) -> ContentValidationError:
    """Run ``action``, assert it was rejected, and return the error for inspection."""
    with pytest.raises(ContentValidationError) as caught:
        action()
    return caught.value
