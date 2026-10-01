"""Exporting a pack: staged, validated in full, then moved in -- or nothing at all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from battle_city_content import TileCode, load_bundled_pack, load_pack
from battle_city_tools import BUNDLED_ROOT, DocumentInvalid, ToolRefusal, export_pack
from battle_city_tools.export import LEVELS_DIRECTORY, MANIFEST_NAME
from tools_helpers import make_level, make_pack, rows_with, semantics


def test_an_exported_pack_is_self_contained_and_loads(tmp_path: Path) -> None:
    source = load_bundled_pack()
    report = export_pack(source, tmp_path / "out")

    assert report.manifest == tmp_path / "out" / MANIFEST_NAME
    assert report.manifest.is_file()
    assert sorted(path.name for path in report.levels) == [
        "classic-01.json",
        "classic-02.json",
        "classic-03.json",
    ]
    reloaded = load_pack(report.manifest)
    assert reloaded.level_ids == source.level_ids


def test_the_export_preserves_every_level_semantically(tmp_path: Path) -> None:
    """Rows, spawns, provenance and waves survive; only the file they live in changes."""
    source = load_bundled_pack()
    report = export_pack(source, tmp_path / "out")
    reloaded = load_pack(report.manifest)
    for original, copy in zip(source.levels, reloaded.levels, strict=True):
        assert semantics(copy) == semantics(original)


def test_the_manifest_sits_at_the_root_with_relative_paths(tmp_path: Path) -> None:
    report = export_pack(load_bundled_pack(), tmp_path / "out")
    manifest = json.loads(report.manifest.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["levels"]]
    assert paths == [f"{LEVELS_DIRECTORY}/classic-0{index}.json" for index in (1, 2, 3)]
    for path in paths:
        assert not path.startswith("/")
        assert ".." not in path.split("/")
        assert (report.destination / path).is_file()


def test_pack_metadata_is_carried_across(tmp_path: Path) -> None:
    source = load_bundled_pack()
    reloaded = load_pack(export_pack(source, tmp_path / "out").manifest)
    assert (reloaded.pack_id, reloaded.version, reloaded.name) == (
        source.pack_id,
        source.version,
        source.name,
    )
    assert reloaded.authors == source.authors
    assert reloaded.license == source.license
    assert reloaded.content_schema_version == source.content_schema_version


def test_an_existing_destination_is_refused_until_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "out"
    export_pack(load_bundled_pack(), destination)
    marker = destination / "levels" / "classic-01.json"
    marker.write_text("{}", encoding="utf-8")

    with pytest.raises(ToolRefusal, match="already exists"):
        export_pack(load_bundled_pack(), destination)
    assert marker.read_text(encoding="utf-8") == "{}"

    export_pack(load_bundled_pack(), destination, overwrite=True)
    assert load_pack(destination / MANIFEST_NAME).level_ids == (
        "classic-01",
        "classic-02",
        "classic-03",
    )


def test_an_invalid_pack_writes_nothing_and_leaves_no_staging_behind(tmp_path: Path) -> None:
    """All-or-nothing: the staged pack is loaded before anything is renamed into place."""
    broken = make_pack([make_level(rows=rows_with({(0, 0): TileCode.HOME}))])
    destination = tmp_path / "out"
    with pytest.raises(DocumentInvalid) as raised:
        export_pack(broken, destination)
    assert raised.value.diagnostic.field == "grid.rows"
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_overwrite_leaves_the_previous_pack_in_place(tmp_path: Path) -> None:
    destination = tmp_path / "out"
    export_pack(load_bundled_pack(), destination)
    before = (destination / MANIFEST_NAME).read_bytes()

    broken = make_pack([make_level(rows=rows_with(base=None))])
    with pytest.raises(DocumentInvalid):
        export_pack(broken, destination, overwrite=True)
    assert (destination / MANIFEST_NAME).read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["out"]


def test_a_failed_export_names_the_destination_not_the_staging_directory(tmp_path: Path) -> None:
    broken = make_pack([make_level(level_id="broken", rows=rows_with(base=None))])
    destination = tmp_path / "out"
    with pytest.raises(DocumentInvalid) as raised:
        export_pack(broken, destination)
    assert raised.value.diagnostic.document == str(destination / LEVELS_DIRECTORY / "broken.json")


def test_exporting_over_the_pack_being_read_is_refused(tmp_path: Path) -> None:
    """The levels would be deleted part-way through reading them."""
    destination = tmp_path / "out"
    export_pack(load_bundled_pack(), destination)
    loaded = load_pack(destination / MANIFEST_NAME)
    with pytest.raises(ToolRefusal, match="being read from"):
        export_pack(loaded, destination, overwrite=True)


def test_exporting_into_a_parent_of_the_pack_being_read_is_refused(tmp_path: Path) -> None:
    export_pack(load_bundled_pack(), tmp_path / "nested" / "out")
    loaded = load_pack(tmp_path / "nested" / "out" / MANIFEST_NAME)
    with pytest.raises(ToolRefusal, match="being read from"):
        export_pack(loaded, tmp_path / "nested", overwrite=True)


def test_exporting_into_the_bundled_pack_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ToolRefusal, match="bundled content root"):
        export_pack(load_bundled_pack(), BUNDLED_ROOT / "packs", overwrite=True)


def test_a_symlinked_destination_cannot_smuggle_the_export_into_the_bundled_pack(
    tmp_path: Path,
) -> None:
    link = tmp_path / "link"
    link.symlink_to(BUNDLED_ROOT, target_is_directory=True)
    with pytest.raises(ToolRefusal, match="bundled content root"):
        export_pack(load_bundled_pack(), link / "packs", overwrite=True)


def test_a_file_where_the_destination_should_be_is_refused(tmp_path: Path) -> None:
    occupied = tmp_path / "out"
    occupied.write_text("", encoding="utf-8")
    with pytest.raises(ToolRefusal, match="not a directory"):
        export_pack(load_bundled_pack(), occupied, overwrite=True)


def test_the_export_is_byte_identical_when_repeated(tmp_path: Path) -> None:
    """One document encodes to one payload, so an export can be diffed between runs."""
    first = export_pack(load_bundled_pack(), tmp_path / "a")
    second = export_pack(load_bundled_pack(), tmp_path / "b")
    assert first.bytes_written == second.bytes_written
    assert first.manifest.read_bytes() == second.manifest.read_bytes()
    for left, right in zip(first.levels, second.levels, strict=True):
        assert left.read_bytes() == right.read_bytes()
