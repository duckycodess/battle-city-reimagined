"""Pack manifests: containment, stable identifiers, and all-or-nothing loading.

A pack that fails validation must produce nothing at all. Several cases here put the
fault in the *second* or *third* level so that a loader which returned what it had
already parsed would visibly differ from one that refuses the whole pack.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from battle_city_content import (
    ContentValidationError,
    PackLicense,
    load_pack,
)
from content_helpers import (
    build_pack,
    mutated_manifest,
    rejection,
    synthetic_level,
    synthetic_manifest,
    write_json,
    write_text,
)


def three_level_pack(root: Path, *, broken_index: int | None = None) -> Path:
    """Lay out a three-level pack, optionally corrupting one level's file."""
    levels: dict[str, Any] = {}
    entries: list[dict[str, str]] = []
    for index in range(3):
        level_id = f"synthetic-0{index + 1}"
        relative = f"levels/{level_id}.json"
        document = synthetic_level(id=level_id, name=f"Synthetic Stage {index + 1}")
        if index == broken_index:
            document["spawns"]["players"] = [{"slot": 1, "x": 0, "y": 1}]
        levels[relative] = document
        entries.append({"id": level_id, "path": relative})
    return build_pack(root, levels=levels, manifest=synthetic_manifest(levels=entries))


def reject_pack(manifest: Path, **kwargs: Any) -> ContentValidationError:
    return rejection(lambda: load_pack(manifest, **kwargs))


def test_a_valid_synthetic_pack_loads(tmp_path: Path) -> None:
    manifest = build_pack(tmp_path)
    pack = load_pack(manifest, root=tmp_path)
    assert pack.pack_id == "synthetic"
    assert pack.schema_version == 1
    assert pack.version == "1.0.0"
    assert pack.name == "Synthetic pack"
    assert pack.content_schema_version == 1
    assert pack.authors == ("Content tests",)
    assert pack.license == PackLicense(
        spdx_id="MIT", notice="Synthetic test data, written for this suite."
    )
    assert pack.level_ids == ("synthetic-01",)
    assert pack.origin == manifest


def test_a_loaded_pack_is_immutable(tmp_path: Path) -> None:
    pack = load_pack(build_pack(tmp_path), root=tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pack.pack_id = "other"  # type: ignore[misc]
    assert isinstance(pack.levels, tuple)
    assert isinstance(pack.authors, tuple)


def test_a_pack_keeps_manifest_order(tmp_path: Path) -> None:
    manifest_path = three_level_pack(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["levels"].reverse()
    write_json(manifest_path, document)
    pack = load_pack(manifest_path, root=tmp_path)
    assert pack.level_ids == ("synthetic-03", "synthetic-02", "synthetic-01")
    assert pack.level("synthetic-02").name == "Synthetic Stage 2"
    with pytest.raises(KeyError):
        pack.level("synthetic-04")


def test_the_pack_root_defaults_to_the_manifest_directory(tmp_path: Path) -> None:
    write_json(tmp_path / "levels" / "synthetic-01.json", synthetic_level())
    manifest = write_json(
        tmp_path / "pack.json",
        synthetic_manifest(levels=[{"id": "synthetic-01", "path": "levels/synthetic-01.json"}]),
    )
    assert load_pack(manifest).level_ids == ("synthetic-01",)


def test_the_default_root_cannot_reach_outside_the_manifest_directory(tmp_path: Path) -> None:
    manifest = build_pack(tmp_path)
    # The manifest points at ../levels, which only resolves under an explicit wider root.
    error = reject_pack(manifest)
    assert error.field == ""
    assert "cannot be read" in error.message


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("../outside.json", "segment"),
        ("levels/../../outside.json", "segment"),
        ("./levels/synthetic-01.json", "segment"),
        ("/etc/passwd.json", "match"),
        ("levels\\synthetic-01.json", "match"),
        ("levels//synthetic-01.json", "match"),
        ("levels/synthetic-01.yaml", "match"),
        ("", "at least"),
    ],
)
def test_an_unsafe_level_path_is_rejected(tmp_path: Path, path: str, reason: str) -> None:
    manifest = build_pack(
        tmp_path, manifest=synthetic_manifest(levels=[{"id": "synthetic-01", "path": path}])
    )
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == "levels[0].path"
    assert reason in error.message


def test_a_symlinked_level_outside_the_pack_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write_json(outside / "synthetic-01.json", synthetic_level())
    root = tmp_path / "pack"
    (root / "levels").mkdir(parents=True)
    link = root / "levels" / "synthetic-01.json"
    try:
        link.symlink_to(outside / "synthetic-01.json")
    except OSError, NotImplementedError:  # pragma: no cover - platform dependent
        pytest.skip("symlinks are not available on this platform")
    manifest = write_json(root / "packs" / "pack.json", synthetic_manifest())
    error = reject_pack(manifest, root=root)
    assert error.field == "levels[0].path"
    assert "outside the pack root" in error.message


def test_a_manifest_outside_its_declared_root_is_rejected(tmp_path: Path) -> None:
    manifest = build_pack(tmp_path)
    inner = tmp_path / "levels"
    error = reject_pack(manifest, root=inner)
    assert error.field == ""
    assert "outside its pack root" in error.message


def test_a_duplicate_level_id_is_rejected(tmp_path: Path) -> None:
    manifest_path = three_level_pack(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["levels"][2]["id"] = "synthetic-01"
    write_json(manifest_path, document)
    error = reject_pack(manifest_path, root=tmp_path)
    assert error.field == "levels[2].id"
    assert error.message == "repeats the level id 'synthetic-01' already declared at levels[0]"


def test_a_duplicate_level_path_is_rejected(tmp_path: Path) -> None:
    manifest_path = three_level_pack(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["levels"][2]["path"] = "levels/synthetic-01.json"
    write_json(manifest_path, document)
    error = reject_pack(manifest_path, root=tmp_path)
    assert error.field == "levels[2].path"
    assert error.message == "repeats the level file already declared at levels[0]"


def test_a_manifest_id_that_disagrees_with_the_level_is_rejected(tmp_path: Path) -> None:
    manifest_path = three_level_pack(tmp_path)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["levels"][1]["id"] = "synthetic-99"
    write_json(manifest_path, document)
    error = reject_pack(manifest_path, root=tmp_path)
    assert error.field == "levels[1].id"
    assert "declares 'synthetic-99'" in error.message
    assert "'synthetic-02'" in error.message


@pytest.mark.parametrize("version", [2, 7, 1000])
def test_an_unsupported_content_schema_version_is_rejected(tmp_path: Path, version: int) -> None:
    manifest = build_pack(tmp_path, manifest=synthetic_manifest(content_schema_version=version))
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == "content_schema_version"
    assert error.message == (f"requires level schema version {version}; this build supports 1")


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda document: document.pop("schema_version"), "schema_version"),
        (lambda document: document.pop("id"), "id"),
        (lambda document: document.pop("version"), "version"),
        (lambda document: document.pop("name"), "name"),
        (lambda document: document.pop("content_schema_version"), "content_schema_version"),
        (lambda document: document.pop("authors"), "authors"),
        (lambda document: document.pop("license"), "license"),
        (lambda document: document.pop("levels"), "levels"),
        (lambda document: document["license"].pop("spdx_id"), "license.spdx_id"),
        (lambda document: document["license"].pop("notice"), "license.notice"),
        (lambda document: document["levels"][0].pop("path"), "levels[0].path"),
        (lambda document: document.update(extra=1), "extra"),
        (lambda document: document["license"].update(url="x"), "license.url"),
        (lambda document: document["levels"][0].update(order=1), "levels[0].order"),
        (lambda document: document.update(schema_version=2), "schema_version"),
        (lambda document: document.update(id="Synthetic Pack"), "id"),
        (lambda document: document.update(version="1.0"), "version"),
        (lambda document: document.update(version="1.0.0-rc1"), "version"),
        (lambda document: document.update(authors=[]), "authors"),
        (lambda document: document.update(authors=["", "x"]), "authors[0]"),
        (lambda document: document.update(levels=[]), "levels"),
        (
            lambda document: document["license"].update(spdx_id="not a licence id"),
            "license.spdx_id",
        ),
        (lambda document: document.update(content_schema_version="1"), "content_schema_version"),
        # A trailing newline used to satisfy a "$" anchor, so every end-anchored
        # manifest field is pinned against one here.
        (lambda document: document.update(id="synthetic\n"), "id"),
        (lambda document: document.update(id="\nsynthetic"), "id"),
        (lambda document: document.update(version="1.0.0\n"), "version"),
        (lambda document: document.update(version="\n1.0.0"), "version"),
        (lambda document: document["license"].update(spdx_id="MIT\n"), "license.spdx_id"),
        (lambda document: document["levels"][0].update(id="synthetic-01\n"), "levels[0].id"),
        (
            lambda document: document["levels"][0].update(path="levels/synthetic-01.json\n"),
            "levels[0].path",
        ),
        (
            lambda document: document["levels"][0].update(path="\nlevels/synthetic-01.json"),
            "levels[0].path",
        ),
    ],
)
def test_a_malformed_manifest_names_its_field(tmp_path: Path, mutate: Any, field: str) -> None:
    manifest = build_pack(tmp_path, manifest=mutated_manifest(mutate))
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == field
    assert error.path == manifest


def test_a_level_path_with_a_trailing_newline_never_reaches_the_filesystem(
    tmp_path: Path,
) -> None:
    """The schema rejects the path before the loader tries to open anything.

    ``"levels/synthetic-01.json\n"`` matched the path pattern while ``$`` could match
    before a final newline, and the loader would then have asked the filesystem for a
    name no pack declares.
    """
    manifest = build_pack(
        tmp_path,
        manifest=mutated_manifest(
            lambda document: document["levels"][0].update(path="levels/synthetic-01.json\n")
        ),
    )
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == "levels[0].path"
    assert error.path == manifest
    assert "must match" in error.message


def test_a_manifest_may_not_declare_more_levels_than_the_bound_allows(tmp_path: Path) -> None:
    """The manifest is bounded so one small file cannot ask for unbounded file opens."""
    entries = [
        {"id": f"synthetic-{index:04d}", "path": f"levels/{index}.json"} for index in range(257)
    ]
    manifest = build_pack(tmp_path, manifest=synthetic_manifest(levels=entries))
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == "levels"
    assert error.message == "must have at most 256 item(s), found 257"


def test_a_missing_manifest_is_reported(tmp_path: Path) -> None:
    error = reject_pack(tmp_path / "packs" / "absent.json")
    assert error.field == ""
    assert "cannot be read" in error.message


def test_a_manifest_that_is_not_json_is_reported(tmp_path: Path) -> None:
    manifest = write_text(tmp_path / "packs" / "pack.json", "id: synthetic\n")
    error = reject_pack(manifest, root=tmp_path)
    assert error.field == ""
    assert "is not valid JSON" in error.message


def test_a_pack_with_a_later_invalid_level_loads_nothing(tmp_path: Path) -> None:
    manifest = three_level_pack(tmp_path, broken_index=1)
    error = reject_pack(manifest, root=tmp_path)
    # The error names the offending level file, not the manifest: the first level parsed
    # cleanly and is discarded rather than returned.
    assert error.path == tmp_path / "levels" / "synthetic-02.json"
    assert error.field == "spawns.players[0]"
    assert error.message == "cell (0, 1) must be empty ground, found STONE"


def test_a_pack_with_a_missing_level_file_loads_nothing(tmp_path: Path) -> None:
    manifest = three_level_pack(tmp_path)
    (tmp_path / "levels" / "synthetic-03.json").unlink()
    error = reject_pack(manifest, root=tmp_path)
    assert error.path == tmp_path / "levels" / "synthetic-03.json"
    assert "cannot be read" in error.message


def test_repairing_the_broken_level_makes_the_whole_pack_load(tmp_path: Path) -> None:
    """Nothing is cached from the failed attempt: the repaired pack loads in full."""
    manifest = three_level_pack(tmp_path, broken_index=2)
    reject_pack(manifest, root=tmp_path)
    write_json(
        tmp_path / "levels" / "synthetic-03.json",
        synthetic_level(id="synthetic-03", name="Synthetic Stage 3"),
    )
    pack = load_pack(manifest, root=tmp_path)
    assert pack.level_ids == ("synthetic-01", "synthetic-02", "synthetic-03")


def test_a_pack_accepts_a_string_path_argument(tmp_path: Path) -> None:
    manifest = build_pack(tmp_path)
    assert load_pack(str(manifest), root=str(tmp_path)).pack_id == "synthetic"
