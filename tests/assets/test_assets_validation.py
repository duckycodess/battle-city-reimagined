"""Every way a pack can be wrong, and the one way it can be right.

The validator is the gate the art passes in continuous integration, so each check gets a
test that breaks exactly one thing and expects exactly that failure. A validator nobody
has seen fail is a validator nobody knows works.

The structural cases run against a small synthetic pack so a single corruption produces a
single, readable diagnostic. The readability cases run against the real shipped art with
an impossible threshold, because those rules are claims about *this* art.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assets_helpers import (
    PACK_DIR,
    problems_mentioning,
    shipped_atlas,
    shipped_metadata,
    synthetic_pack,
)
from battle_city_tools.assets.metadata import AtlasMetadata, serialize
from battle_city_tools.assets.png import encode_png
from battle_city_tools.assets.raster import Canvas, Image
from battle_city_tools.assets.validation import (
    ValidationReport,
    validate,
    validate_directory,
)


def _check(metadata: AtlasMetadata, atlas: Image) -> ValidationReport:
    return validate(metadata, atlas, artifact="fixture.json", require_catalog=False)


def _rehashed(metadata: AtlasMetadata, atlas: Image) -> AtlasMetadata:
    """Re-record every digest, so a pixel edit is not also reported as a digest mismatch."""
    return replace(
        metadata,
        atlas=replace(metadata.atlas, sha256_pixels=atlas.digest()),
        frames=tuple(
            replace(frame, sha256_pixels=atlas.crop(*frame.rect).digest())
            for frame in metadata.frames
        ),
    )


def test_a_well_formed_pack_passes_every_check() -> None:
    metadata, atlas, _ = synthetic_pack()
    report = _check(metadata, atlas)
    assert report.ok, report.summary()
    assert report.checks


def test_the_shipped_pack_passes_including_the_catalogue_check() -> None:
    report = validate_directory(PACK_DIR)
    assert report.ok, report.summary()


def test_a_wrong_atlas_digest_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata, atlas=replace(metadata.atlas, sha256_pixels="0" * 64))
    assert problems_mentioning(_check(broken, atlas), "atlas.sha256_pixels")


def test_declared_atlas_dimensions_that_do_not_match_the_image_are_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata, atlas=replace(metadata.atlas, width=metadata.atlas.width + 16))
    assert problems_mentioning(_check(broken, atlas), "the image is")


def test_a_wrong_frame_digest_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    frames = (replace(metadata.frames[0], sha256_pixels="1" * 64), *metadata.frames[1:])
    assert problems_mentioning(_check(replace(metadata, frames=frames), atlas), "sha256_pixels")


def test_a_rectangle_the_packer_would_not_choose_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    moved = replace(metadata.frames[0], rect=(16, 16, 16, 16))
    report = _check(replace(metadata, frames=(moved, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "packing puts this frame at")


def test_an_overlapping_rectangle_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    second = metadata.frames[1]
    overlapped = replace(second, rect=metadata.frames[0].rect)
    report = _check(
        replace(metadata, frames=(metadata.frames[0], overlapped, *metadata.frames[2:])), atlas
    )
    assert problems_mentioning(report, "overlaps another frame")


def test_an_opaque_pixel_outside_every_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    report = _check(replace(metadata, frames=metadata.frames[:-1]), atlas)
    assert problems_mentioning(report, "outside every declared frame")


def test_a_frame_of_the_wrong_size_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    squashed = replace(metadata.frames[0], rect=(0, 0, 8, 16))
    report = _check(replace(metadata, frames=(squashed, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "where the pack declares")


def test_a_rectangle_outside_the_atlas_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    adrift = replace(metadata.frames[0], rect=(900, 900, 16, 16))
    report = _check(replace(metadata, frames=(adrift, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "falls outside the atlas")


def test_two_frames_with_identical_pixels_are_reported() -> None:
    metadata, atlas, images = synthetic_pack()
    canvas = Canvas(atlas.width, atlas.height)
    for frame in metadata.frames:
        canvas.blit(images["alpha"], frame.rect[0], frame.rect[1])
    duplicated = canvas.freeze()
    report = _check(_rehashed(metadata, duplicated), duplicated)
    assert problems_mentioning(report, "pixel-for-pixel identical")


def test_a_blank_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    canvas = Canvas(atlas.width, atlas.height)
    canvas.blit(atlas, 0, 0)
    first = metadata.frames[0]
    canvas.fill_rect(first.rect[0], first.rect[1], first.rect[2], first.rect[3], (0, 0, 0, 0))
    blanked = canvas.freeze()
    report = _check(_rehashed(metadata, blanked), blanked)
    assert problems_mentioning(report, "entirely transparent")


def test_a_pivot_outside_its_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    adrift = replace(metadata.frames[0], pivot=(99, 0))
    report = _check(replace(metadata, frames=(adrift, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "lies outside a 16x16 frame")


def test_a_hitbox_the_sidecar_does_not_declare_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    unknown = replace(metadata.frames[0], hitbox="spaceship")
    report = _check(replace(metadata, frames=(unknown, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "no hitbox named")


def test_a_pixel_outside_the_declared_palette_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    canvas = Canvas(atlas.width, atlas.height)
    canvas.blit(atlas, 0, 0)
    canvas.set_pixel(1, 1, (1, 2, 3, 255))
    painted = canvas.freeze()
    report = _check(_rehashed(metadata, painted), painted)
    assert problems_mentioning(report, "outside the declared palette")


def test_a_frame_naming_an_unknown_source_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    orphan = replace(metadata.frames[0], source_id="nowhere")
    report = _check(replace(metadata, frames=(orphan, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "no source named")


def test_a_source_no_frame_uses_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    spare = replace(metadata.sources[0], source_id="unused")
    report = _check(replace(metadata, sources=(*metadata.sources, spare)), atlas)
    assert problems_mentioning(report, "but no frame uses it")


def test_a_source_without_an_author_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    anonymous = replace(metadata.sources[0], authors=())
    report = _check(replace(metadata, sources=(anonymous,)), atlas)
    assert problems_mentioning(report, "no author is named")


def test_a_pack_without_a_licence_notice_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    stripped = replace(metadata, license=replace(metadata.license, notice=""))
    assert problems_mentioning(_check(stripped, atlas), "SPDX value and a notice")


def test_an_animation_naming_a_missing_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata.animations[0], frames=("alpha", "nowhere"))
    report = _check(replace(metadata, animations=(broken,)), atlas)
    assert problems_mentioning(report, "not a frame in this atlas")


def test_an_animation_that_repeats_a_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata.animations[0], frames=("alpha", "alpha"))
    report = _check(replace(metadata, animations=(broken,)), atlas)
    assert problems_mentioning(report, "repeats a frame")


def test_a_state_mapping_to_a_missing_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    report = _check(replace(metadata, states={"home.intact": "nowhere"}), atlas)
    assert problems_mentioning(report, "not a frame in this atlas")


def test_a_layout_algorithm_this_validator_cannot_reproduce_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    report = _check(replace(metadata, layout=replace(metadata.layout, algorithm="magic")), atlas)
    assert problems_mentioning(report, "cannot reproduce")


def test_a_sort_key_this_validator_cannot_reproduce_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    report = _check(replace(metadata, layout=replace(metadata.layout, sort_key="vibes")), atlas)
    assert problems_mentioning(report, "cannot reproduce the sort key")


def test_faction_contrast_is_actually_measured() -> None:
    metadata, atlas = shipped_metadata(), shipped_atlas()
    impossible = replace(
        metadata.readability,
        luma_contrast=(("tank-player-up", "tank-enemy-normal-up", 250),),
    )
    report = validate(replace(metadata, readability=impossible), atlas, require_catalog=False)
    assert problems_mentioning(report, "mean luma differs by")


def test_silhouette_difference_is_actually_measured() -> None:
    metadata, atlas = shipped_metadata(), shipped_atlas()
    impossible = replace(
        metadata.readability,
        silhouette_distinct=(("tank-enemy-shielded-up", "tank-enemy-unshielded-up", 1000),),
    )
    report = validate(replace(metadata, readability=impossible), atlas, require_catalog=False)
    assert problems_mentioning(report, "opaque silhouettes differ on")


def test_grey_scale_difference_is_actually_measured() -> None:
    metadata, atlas = shipped_metadata(), shipped_atlas()
    impossible = replace(
        metadata.readability, luma_distinct=(("terrain-water-0", "terrain-water-1", 1000),)
    )
    report = validate(replace(metadata, readability=impossible), atlas, require_catalog=False)
    assert problems_mentioning(report, "grey scale differs on")


def test_mirror_lean_is_actually_measured() -> None:
    """Flipping the expected sign must fail: the check is about this art, not a constant."""
    metadata, atlas = shipped_metadata(), shipped_atlas()
    flipped = replace(metadata.readability, quadrant_sign=(("terrain-mirror-ne", 1, 10),))
    report = validate(replace(metadata, readability=flipped), atlas, require_catalog=False)
    assert problems_mentioning(report, "does not lean")


def test_a_readability_rule_naming_a_missing_frame_is_reported() -> None:
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata.readability, luma_contrast=(("alpha", "nowhere", 1),))
    report = _check(replace(metadata, readability=broken), atlas)
    assert problems_mentioning(report, "not a frame in this atlas")


def test_a_sidecar_that_disagrees_with_the_catalogue_is_reported() -> None:
    metadata, atlas = shipped_metadata(), shipped_atlas()
    moved = replace(metadata.frames[0], pivot=(1, 1))
    report = validate(replace(metadata, frames=(moved, *metadata.frames[1:])), atlas)
    assert problems_mentioning(report, "the catalogue declares")


def test_a_missing_sidecar_is_reported_rather_than_raised(tmp_path: Path) -> None:
    report = validate_directory(tmp_path)
    assert problems_mentioning(report, "the sidecar is missing")


def test_a_missing_atlas_image_is_reported_rather_than_raised(tmp_path: Path) -> None:
    metadata, _, _ = synthetic_pack()
    (tmp_path / "atlas.json").write_bytes(serialize(metadata))
    report = validate_directory(tmp_path)
    assert problems_mentioning(report, "the atlas image is missing")


def test_an_unreadable_sidecar_is_reported_rather_than_raised(tmp_path: Path) -> None:
    (tmp_path / "atlas.json").write_bytes(b"{")
    report = validate_directory(tmp_path)
    assert problems_mentioning(report, "not valid JSON")


def test_a_directory_round_trip_passes(tmp_path: Path) -> None:
    metadata, atlas, _ = synthetic_pack()
    (tmp_path / "atlas.json").write_bytes(serialize(metadata))
    (tmp_path / "atlas.png").write_bytes(encode_png(atlas))
    report = validate_directory(tmp_path, require_catalog=False)
    assert report.ok, report.summary()


# --------------------------------------------------------------------------------------
# The sidecar's readability rules are the catalogue's, not whatever it says they are
# --------------------------------------------------------------------------------------
#
# ``_check_readability`` measures the pixels against the thresholds *the sidecar declares*.
# That is the right thing for it to do and it is also why it can be defeated by editing
# the document: a rule that is not there cannot fail, and a threshold the art already
# clears is not a guard. Only the comparison against the catalogue closes that, so each
# way of weakening the block gets a test here.


def _shipped_with(**readability: Any) -> tuple[AtlasMetadata, Image]:
    metadata, atlas = shipped_metadata(), shipped_atlas()
    return replace(metadata, readability=replace(metadata.readability, **readability)), atlas


def test_a_sidecar_that_dropped_a_whole_readability_list_is_reported() -> None:
    """The cheapest way to disable a check: delete the rules and pass by vacuum."""
    metadata, atlas = _shipped_with(luma_contrast=())
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "the sidecar declares no rules here")
    assert problems_mentioning(report, "readability.luma_contrast")


def test_a_sidecar_that_slackened_a_threshold_is_reported() -> None:
    """A number the art clears by miles is not a regression guard."""
    original = shipped_metadata().readability.luma_contrast
    weakened = ((original[0][0], original[0][1], 1), *original[1:])
    metadata, atlas = _shipped_with(luma_contrast=weakened)
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "the catalogue declares [18], the sidecar declares [1]")


def test_a_sidecar_that_dropped_one_readability_rule_is_reported() -> None:
    original = shipped_metadata().readability.luma_distinct
    metadata, atlas = _shipped_with(luma_distinct=original[1:])
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "the sidecar omits it")
    assert problems_mentioning(report, "terrain-mirror-ne:terrain-mirror-se")


def test_a_sidecar_that_invented_a_readability_rule_is_reported() -> None:
    original = shipped_metadata().readability.silhouette_distinct
    metadata, atlas = _shipped_with(
        silhouette_distinct=(*original, ("terrain-empty", "terrain-stone", 1))
    )
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "the catalogue does not")


def test_a_sidecar_that_moved_the_luma_step_is_reported() -> None:
    """The step decides which pixels count, so it moves every grey-scale rule at once."""
    metadata, atlas = _shipped_with(luma_distinct_step=1)
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "readability.luma_distinct_step")


def test_a_sidecar_that_flipped_a_mirror_lean_is_reported_against_the_catalogue() -> None:
    """Not only measured against the pixels: the sign is the deflection table's."""
    original = shipped_metadata().readability.quadrant_sign
    flipped = tuple((name, -sign, magnitude) for name, sign, magnitude in original)
    metadata, atlas = _shipped_with(quadrant_sign=flipped)
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "the catalogue declares [-1, 10]")


def test_a_sidecar_that_reordered_the_readability_rules_is_reported() -> None:
    original = shipped_metadata().readability.luma_contrast
    metadata, atlas = _shipped_with(luma_contrast=tuple(reversed(original)))
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "in a different order")


def test_a_sidecar_that_repeats_a_readability_rule_is_reported() -> None:
    original = shipped_metadata().readability.luma_contrast
    metadata, atlas = _shipped_with(luma_contrast=(*original, original[0]))
    report = validate(metadata, atlas)
    assert problems_mentioning(report, "more than once")


def test_the_readability_comparison_is_skipped_with_the_catalogue_check() -> None:
    """``--ignore-catalog`` is one switch, and it turns off one thing: the comparison.

    The measurement against the pixels still runs, so a pack validated this way is still
    held to its own declared thresholds. What it is not held to is the catalogue's.
    """
    metadata, atlas = _shipped_with(luma_contrast=())
    assert validate(metadata, atlas, require_catalog=False).ok


def test_a_weakened_readability_block_is_reported_even_when_the_frame_list_is_wrong() -> None:
    """The early-return case, which is the one that mattered.

    A hand edit that drops a frame and a hand edit that slackens a threshold arrive
    together, because they are the same edit: someone made the document agree with
    whatever is in front of them. A catalogue check that stopped at the frame list would
    report the first and silently accept the second -- and the second is the one nobody
    would look for again.
    """
    metadata, atlas = shipped_metadata(), shipped_atlas()
    weakened = replace(metadata.readability, luma_contrast=(), luma_distinct_step=1)
    broken = replace(
        metadata,
        frames=(replace(metadata.frames[0], pivot=(1, 1)), *metadata.frames[2:]),
        readability=weakened,
    )
    report = validate(broken, atlas)

    assert problems_mentioning(report, "the catalogue declares frames the atlas lacks")
    assert problems_mentioning(report, "the sidecar declares no rules here")
    assert problems_mentioning(report, "readability.luma_distinct_step")
    assert problems_mentioning(report, "pivot")
    assert problems_mentioning(report, "hitboxes") == []


def test_a_frame_the_catalogue_does_not_know_does_not_stop_the_other_comparisons() -> None:
    """An extra frame is reported once, not raised as a lookup failure."""
    metadata, atlas = shipped_metadata(), shipped_atlas()
    invented = replace(metadata.frames[0], name="terrain-invented")
    broken = replace(metadata, frames=(*metadata.frames, invented), hitboxes={"tile": (1, 1)})
    report = validate(broken, atlas)
    assert problems_mentioning(report, "the catalogue does not: ['terrain-invented']")
    assert problems_mentioning(report, "hitboxes.tile")


# --------------------------------------------------------------------------------------
# A pack owns its own directory and nothing else
# --------------------------------------------------------------------------------------
#
# ``atlas.path`` comes out of a text file and decides which bytes get opened. Every case
# below writes a *valid* atlas somewhere the pack does not own, so a validator that read
# the file first would report a clean pack; the only correct answer is a refusal that
# names the path and opens nothing.


def _pack_pointing_at(directory: Path, declared: str) -> Image:
    """Write a sidecar in ``directory`` naming ``declared``, and the real atlas elsewhere."""
    metadata, atlas, _ = synthetic_pack()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "atlas.json").write_bytes(
        serialize(replace(metadata, atlas=replace(metadata.atlas, path=declared)))
    )
    return atlas


@pytest.mark.parametrize("require_catalog", [True, False])
@pytest.mark.parametrize(
    ("declared", "because"),
    [
        ("/etc/hostname", "an absolute path"),
        ("../outside/atlas.png", "a '..' component"),
        ("..\\outside\\atlas.png", "a backslash"),
        ("sub\\atlas.png", "a backslash"),
        ("C:\\windows\\atlas.png", "a backslash"),
        ("C:atlas.png", "an absolute path"),
        ("//server/share/atlas.png", "an absolute path"),
    ],
)
def test_an_atlas_path_outside_the_pack_is_refused(
    tmp_path: Path, declared: str, because: str, require_catalog: bool
) -> None:
    pack = tmp_path / "pack"
    atlas = _pack_pointing_at(pack, declared)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "atlas.png").write_bytes(encode_png(atlas))

    report = validate_directory(pack, require_catalog=require_catalog)
    assert not report.ok
    assert problems_mentioning(report, "atlas.path")
    assert problems_mentioning(report, because)
    assert report.checks == (), "the refusal must come before anything is read"


def test_a_symlink_leading_out_of_the_pack_is_refused(tmp_path: Path) -> None:
    """The case the textual check cannot see: an ordinary relative name that is a door.

    ``atlas.png`` here is a name a reviewer would not look at twice, and the file it
    opens is in another directory. Resolving before reading is the only thing that
    notices, which is why the check is not purely textual.
    """
    pack = tmp_path / "pack"
    atlas = _pack_pointing_at(pack, "atlas.png")
    outside = tmp_path / "outside"
    outside.mkdir()
    real = outside / "atlas.png"
    real.write_bytes(encode_png(atlas))
    try:
        (pack / "atlas.png").symlink_to(real)
    except OSError:  # pragma: no cover - a filesystem without symlinks
        pytest.skip("this filesystem does not support symbolic links")

    report = validate_directory(pack, require_catalog=False)
    assert problems_mentioning(report, "the pack does not own")
    assert report.checks == ()


def test_a_relative_path_inside_the_pack_is_allowed(tmp_path: Path) -> None:
    """Ownership, not a hard-coded filename: a subdirectory of the pack is still the pack."""
    pack = tmp_path / "pack"
    atlas = _pack_pointing_at(pack, "sheets/atlas.png")
    (pack / "sheets").mkdir()
    (pack / "sheets" / "atlas.png").write_bytes(encode_png(atlas))

    report = validate_directory(pack, require_catalog=False)
    assert report.ok, report.summary()


def test_a_symlink_inside_the_pack_is_still_read(tmp_path: Path) -> None:
    """The negative control: the rule is about leaving the pack, not about symlinks."""
    pack = tmp_path / "pack"
    atlas = _pack_pointing_at(pack, "atlas.png")
    (pack / "real.png").write_bytes(encode_png(atlas))
    try:
        (pack / "atlas.png").symlink_to(pack / "real.png")
    except OSError:  # pragma: no cover - a filesystem without symlinks
        pytest.skip("this filesystem does not support symbolic links")

    assert validate_directory(pack, require_catalog=False).ok


def test_an_unowned_atlas_path_is_reported_by_the_in_memory_validator_too(tmp_path: Path) -> None:
    """``validate`` never opens a file, so it reports the claim rather than refusing a read."""
    metadata, atlas, _ = synthetic_pack()
    broken = replace(metadata, atlas=replace(metadata.atlas, path="/etc/hostname"))
    report = validate(broken, atlas, artifact="fixture.json", require_catalog=False)
    assert problems_mentioning(report, "is not a name this pack owns")
