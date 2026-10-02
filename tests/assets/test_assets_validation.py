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
