"""The artifacts tracked in git, and the Blender scripts that produced them.

Two jobs. The first is to check the checked-in pack on every run, with no GPU, no Blender
and no image library -- that is the whole point of a standard-library validator. The
second is to check the Blender scripts *without* Blender, by parsing them: that the frames
they say they render are the frames the catalogue declares, that the colours they use are
palette colours, and that they stay thin, because ``assets/`` is outside this repository's
lint and type checking and only a test can hold a line there.

The one claim this file is careful not to make is that any of it is a screenshot. The
stage composite is assembled from frames; the test checks that it still carries the banner
saying so.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest
from assets_helpers import (
    ATLAS_PATH,
    BLENDER_DIR,
    COMPOSITE_PATH,
    PACK_DIR,
    PREVIEW_PATH,
    SIDECAR_PATH,
    shipped_atlas,
    shipped_metadata,
)
from battle_city_sim import DEFAULT_RULES
from battle_city_tools.assets import catalog
from battle_city_tools.assets.composite import BANNER, BANNER_LINE
from battle_city_tools.assets.palette import PALETTE, PALETTE_NAMES
from battle_city_tools.assets.png import decode_png
from battle_city_tools.assets.validation import validate_directory

MAX_ATLAS_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 256 * 1024
MAX_SIDECAR_BYTES = 128 * 1024
MAX_SCENE_BYTES = 2 * 1024 * 1024

BUILD_SCRIPT = BLENDER_DIR / "build_scene.py"
RENDER_SCRIPT = BLENDER_DIR / "render_frames.py"
SCENE = BLENDER_DIR / "starter_set.blend"

BUILD_IMPORTS = {"__future__", "argparse", "bpy", "math", "mathutils", "sys"}
RENDER_IMPORTS = {"__future__", "argparse", "bpy", "json", "math", "os", "sys"}


def _literal(path: Path, name: str) -> Any:
    """Read one module-level literal assignment without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{path} has no module-level assignment to {name}")


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


# --------------------------------------------------------------------------------------
# The tracked pack
# --------------------------------------------------------------------------------------


def test_the_checked_in_pack_validates() -> None:
    report = validate_directory(PACK_DIR)
    assert report.ok, report.summary()


def test_the_pack_holds_exactly_the_files_it_documents() -> None:
    assert sorted(path.name for path in PACK_DIR.iterdir()) == [
        "README.md",
        "atlas.json",
        "atlas.png",
        "preview.png",
        "stage-composite.png",
    ]


def test_the_atlas_holds_every_catalogued_frame() -> None:
    assert shipped_metadata().frame_names == catalog.FRAME_NAMES


def test_the_pack_covers_every_tile_kind_faction_direction_and_powerup() -> None:
    names = set(catalog.FRAME_NAMES)
    for tile in ("empty", "stone", "brick", "brick-cracked", "mirror-ne", "mirror-se",
                 "water-0", "forest", "home-intact", "home-destroyed"):
        assert f"terrain-{tile}" in names
    for variant in ("player", "enemy-normal", "enemy-shielded", "enemy-unshielded"):
        for direction in ("up", "down", "left", "right"):
            assert f"tank-{variant}-{direction}" in names
    for kind in ("gatling", "invincibility", "extra-life"):
        assert f"powerup-{kind}" in names
    assert {"shot-up", "shot-down", "shot-left", "shot-right"} <= names


# --------------------------------------------------------------------------------------
# Art may not move collision geometry
# --------------------------------------------------------------------------------------


def test_the_declared_hitboxes_are_the_simulation_rules() -> None:
    rules = DEFAULT_RULES
    assert catalog.HITBOX_SIZES["tile"] == (rules.tile_size, rules.tile_size)
    assert catalog.HITBOX_SIZES["tank"] == (rules.tank_size, rules.tank_size)
    span = 2 * rules.projectile_radius + 1
    assert catalog.HITBOX_SIZES["projectile"] == (span, span)
    assert catalog.HITBOX_SIZES["none"] is None


def test_tile_and_tank_frames_are_exactly_the_rules_sizes() -> None:
    metadata = shipped_metadata()
    for frame in metadata.frames:
        if frame.hitbox not in ("tile", "tank"):
            continue
        box = metadata.hitboxes[frame.hitbox]
        assert box is not None
        assert (frame.rect[2], frame.rect[3]) == box


def test_projectile_art_overhangs_its_hitbox_and_says_so_with_a_pivot() -> None:
    """The guarded case: a readable shot is far bigger than the square it collides with."""
    metadata, atlas = shipped_metadata(), shipped_atlas()
    hitbox = metadata.hitboxes["projectile"]
    assert hitbox == (3, 3)
    for name in ("shot-up", "shot-down", "shot-left", "shot-right"):
        frame = metadata.frame(name)
        bounds = atlas.crop(*frame.rect).alpha_bounds()
        assert bounds is not None
        assert bounds[2] > hitbox[0] or bounds[3] > hitbox[1]
        assert frame.pivot == (frame.rect[2] // 2, frame.rect[3] // 2)


def test_every_frame_in_the_atlas_is_one_tile_square() -> None:
    assert catalog.FRAME_SIZE == (DEFAULT_RULES.tile_size, DEFAULT_RULES.tile_size)


# --------------------------------------------------------------------------------------
# Size caps, so the repository does not quietly grow an art budget
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "cap"),
    [
        (ATLAS_PATH, MAX_ATLAS_BYTES),
        (SIDECAR_PATH, MAX_SIDECAR_BYTES),
        (PREVIEW_PATH, MAX_IMAGE_BYTES),
        (COMPOSITE_PATH, MAX_IMAGE_BYTES),
        (SCENE, MAX_SCENE_BYTES),
    ],
)
def test_a_tracked_artifact_stays_under_its_size_cap(path: Path, cap: int) -> None:
    assert path.stat().st_size <= cap, f"{path.name} is {path.stat().st_size} bytes"


# --------------------------------------------------------------------------------------
# The generated pictures
# --------------------------------------------------------------------------------------


def test_the_preview_shows_every_frame() -> None:
    preview = decode_png(PREVIEW_PATH.read_bytes(), origin=str(PREVIEW_PATH))
    metadata = shipped_metadata()
    assert preview.width > metadata.atlas.width
    assert preview.height > len(metadata.frames) * 8


def test_the_stage_composite_still_carries_its_not_a_capture_banner() -> None:
    composite = decode_png(COMPOSITE_PATH.read_bytes(), origin=str(COMPOSITE_PATH))
    banner = {composite.pixel(x, 12)[:3] for x in range(composite.width)}
    assert BANNER[:3] in banner, "the composite lost the banner strip"
    assert "NOT AN IN-GAME CAPTURE" in BANNER_LINE


def test_the_pack_readme_says_the_composite_is_not_a_capture() -> None:
    text = (PACK_DIR / "README.md").read_text(encoding="utf-8")
    assert "not a screenshot" in text.lower()
    assert "do not edit by hand" in text.lower()


# --------------------------------------------------------------------------------------
# The Blender scripts, checked without Blender
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [BUILD_SCRIPT, RENDER_SCRIPT])
def test_a_blender_script_parses(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_render_plan_is_exactly_the_catalogue() -> None:
    plan = _literal(RENDER_SCRIPT, "RENDER_PLAN")
    assert tuple(row[0] for row in plan) == catalog.FRAME_NAMES


def test_every_rendered_cell_exists_in_the_scene_script() -> None:
    plan = _literal(RENDER_SCRIPT, "RENDER_PLAN")
    cells = set(_literal(BUILD_SCRIPT, "CELLS"))
    assert {row[1] for row in plan} <= cells


def test_every_cell_the_scene_builds_is_rendered() -> None:
    plan = _literal(RENDER_SCRIPT, "RENDER_PLAN")
    assert set(_literal(BUILD_SCRIPT, "CELLS")) == {row[1] for row in plan}


def test_facings_are_the_four_quarter_turns() -> None:
    plan = _literal(RENDER_SCRIPT, "RENDER_PLAN")
    assert {row[2] for row in plan} <= {0, 90, 180, -90}


def test_the_scene_script_only_paints_with_palette_colours() -> None:
    scene_palette = _literal(BUILD_SCRIPT, "PALETTE")
    for name, color in scene_palette.items():
        assert PALETTE_NAMES.get(tuple(color)) == name, f"{name} is not a shipped palette colour"


def test_the_shipped_palette_is_a_superset_of_the_scene_palette() -> None:
    scene_palette = _literal(BUILD_SCRIPT, "PALETTE")
    assert set(scene_palette) <= {name for name, _ in PALETTE}


@pytest.mark.parametrize(
    ("path", "allowed"), [(BUILD_SCRIPT, BUILD_IMPORTS), (RENDER_SCRIPT, RENDER_IMPORTS)]
)
def test_a_blender_script_stays_thin(path: Path, allowed: set[str]) -> None:
    """Logic belongs in the tool package, where ruff and mypy can see it."""
    assert _imports(path) <= allowed


def test_the_blender_readme_records_the_version_the_pack_was_rendered_with() -> None:
    text = (BLENDER_DIR / "README.md").read_text(encoding="utf-8")
    render = shipped_metadata().render
    assert str(render["blender.version"]) in text
    assert str(render["blender.build_hash"]) in text


def test_the_blender_readme_does_not_claim_the_scene_file_is_reproducible() -> None:
    text = (BLENDER_DIR / "README.md").read_text(encoding="utf-8")
    assert "Not reproducible: `starter_set.blend`" in text


# --------------------------------------------------------------------------------------
# Headlessness
# --------------------------------------------------------------------------------------


def test_the_asset_tools_pull_in_no_display_library() -> None:
    assert not [name for name in sys.modules if name.split(".")[0] in {"pygame", "bpy"}]
