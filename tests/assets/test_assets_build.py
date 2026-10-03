"""``build_pack``: the same renders must always produce the same four files.

This is the claim the whole pipeline rests on. The art is checked into git, continuous
integration re-derives every sidecar claim from those pixels, and the README tells an
author that re-running the build over unchanged renders rewrites identical bytes. If that
last part is not true then the checked-in files are not reproducible and the validator is
checking a snapshot rather than an output.

The renders here are synthetic. Rendering real ones needs Blender, which is the one thing
the packer is deliberately built not to require, and a test that needed a GPU-less
Blender install would not run in continuous integration at all. What is being tested is
the packer, not Cycles: given identical inputs, does step three of the pipeline produce
identical outputs. The complementary question -- does Blender produce identical renders --
cannot be answered in this process and is answered by the documented two-run procedure in
``assets/blender/README.md``.

``test_assets_checked_in`` covers the other half: that the real art satisfies the real
readability thresholds and that its digests match.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assets_helpers import write_synthetic_render_set
from battle_city_tools.assets import catalog
from battle_city_tools.assets.build import (
    COMPOSITE_FILENAME,
    PREVIEW_FILENAME,
    RENDER_MANIFEST,
    build_pack,
)
from battle_city_tools.assets.errors import AssetInvalid, AssetRefusal
from battle_city_tools.assets.metadata import ATLAS_FILENAME, METADATA_FILENAME
from battle_city_tools.assets.validation import validate_directory

OUTPUTS = (ATLAS_FILENAME, METADATA_FILENAME, PREVIEW_FILENAME, COMPOSITE_FILENAME)


def test_building_the_same_renders_twice_writes_byte_identical_files(tmp_path: Path) -> None:
    """Every tracked artifact, not just the atlas.

    The sidecar is the one most likely to drift, because it is the only output built from
    a dictionary rather than from pixels, and a dictionary is where an unordered iteration
    or a timestamp would show up. The previews are the ones most likely to be forgotten.
    """
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)

    first = build_pack(frames, tmp_path / "first")
    second = build_pack(frames, tmp_path / "second")

    for name in OUTPUTS:
        assert (tmp_path / "first" / name).read_bytes() == (
            tmp_path / "second" / name
        ).read_bytes(), f"{name} differs between two builds of the same renders"
    assert first.atlas_sha256_pixels == second.atlas_sha256_pixels
    assert first.frame_count == second.frame_count == len(catalog.FRAME_NAMES)


def test_two_independently_written_render_sets_build_identical_files(tmp_path: Path) -> None:
    """The fixture itself must be a pure function of the catalogue.

    Without this, the test above could pass by reading one directory twice while the
    fixture quietly depended on something ambient. Writing the renders twice into two
    directories and getting the same four outputs rules that out.
    """
    for run in ("a", "b"):
        write_synthetic_render_set(tmp_path / run / "frames")
        build_pack(tmp_path / run / "frames", tmp_path / run / "pack")

    for name in OUTPUTS:
        assert (tmp_path / "a" / "pack" / name).read_bytes() == (
            tmp_path / "b" / "pack" / name
        ).read_bytes()


def test_a_rebuild_over_an_existing_pack_leaves_the_same_bytes(tmp_path: Path) -> None:
    """Building into a directory that already holds a pack overwrites it with itself.

    This is what an author actually does: re-render, re-pack into ``assets/sprites/starter``
    and expect ``git status`` to be clean when nothing changed.
    """
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    output = tmp_path / "pack"
    build_pack(frames, output)
    before = {name: (output / name).read_bytes() for name in OUTPUTS}
    build_pack(frames, output)
    assert {name: (output / name).read_bytes() for name in OUTPUTS} == before


def test_a_synthetic_pack_passes_its_own_structural_validation(tmp_path: Path) -> None:
    """The output is a well-formed pack, readability thresholds aside.

    ``require_catalog`` stays on: the structure, the layout, the digests and the source
    references all have to hold. Only the readability rules cannot, because the pixels are
    noise rather than art, so this asserts that the problems found are *only* those.
    """
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    build_pack(frames, tmp_path / "pack")

    report = validate_directory(tmp_path / "pack")
    structural = [
        problem for problem in report.problems if not problem.field.startswith("readability")
    ]
    assert structural == [], structural


def test_a_stray_file_in_the_render_directory_is_refused(tmp_path: Path) -> None:
    """The failure this catches is a renamed sprite leaving its old render behind.

    The manifest lists the new names and matches, and the loader only ever opens the names
    it expects, so nothing else in the pipeline can notice that the directory holds two
    catalogue revisions at once.
    """
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    (frames / "terrain-removed-in-a-later-revision.png").write_bytes(
        (frames / "terrain-brick.png").read_bytes()
    )

    with pytest.raises(AssetInvalid, match="did not render"):
        build_pack(frames, tmp_path / "pack")


def test_the_stray_check_names_what_it_found(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    (frames / "notes.txt").write_text("scratch", encoding="utf-8")

    with pytest.raises(AssetInvalid) as caught:
        build_pack(frames, tmp_path / "pack")
    assert "notes.txt" in str(caught.value.diagnostic)


def test_a_render_directory_missing_a_frame_is_refused(tmp_path: Path) -> None:
    """Deleting a render makes the manifest disagree with the directory."""
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    (frames / f"{catalog.FRAME_NAMES[0]}.png").unlink()

    with pytest.raises(AssetInvalid, match="did not render|missing"):
        build_pack(frames, tmp_path / "pack")


def test_a_manifest_naming_frames_the_catalogue_does_not_have_is_refused(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    manifest_path = write_synthetic_render_set(frames)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["frames"].append("terrain-invented")
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AssetInvalid, match="catalogue's frame list"):
        build_pack(frames, tmp_path / "pack")


def test_a_render_at_the_wrong_resolution_is_refused(tmp_path: Path) -> None:
    """The manifest's resolution is what the supersample factor is derived from."""
    frames = tmp_path / "frames"
    manifest_path = write_synthetic_render_set(frames)
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["render"]["render.resolution_x"] = 96
    document["render"]["render.resolution_y"] = 96
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AssetInvalid, match="expected 96x96"):
        build_pack(frames, tmp_path / "pack")


def test_a_missing_manifest_says_to_render_first(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)
    (frames / RENDER_MANIFEST).unlink()

    with pytest.raises(AssetInvalid, match="render_frames.py"):
        build_pack(frames, tmp_path / "pack")


def test_an_unknown_composite_level_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    write_synthetic_render_set(frames)

    with pytest.raises(AssetRefusal, match="no level"):
        build_pack(frames, tmp_path / "pack", level_id="not-a-level")
