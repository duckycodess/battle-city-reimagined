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
import subprocess
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
    REPO_ROOT,
    SIDECAR_PATH,
    shipped_atlas,
    shipped_metadata,
)

try:
    from battle_city_sim import DEFAULT_RULES
except ModuleNotFoundError as error:  # pragma: no cover - an environment, not a behaviour
    raise ModuleNotFoundError(
        "battle_city_sim is not importable, so the checks that art cannot move collision "
        "geometry cannot run.\n\n"
        "This is a workspace setup problem and is not specific to the asset tests: "
        "tests/ai, tests/client, tests/server, tests/sim and tests/tools import workspace "
        "packages the same way and fail to collect in the same environment. Continuous "
        "integration installs them before running pytest with\n\n"
        "    uv sync --locked --all-packages --all-groups\n\n"
        "and a local environment needs that command once. Skipping these checks instead "
        "is not an option: they are the only thing asserting that the hitboxes this pack "
        "ships are still battle_city_sim.DEFAULT_RULES, which is the contract the art "
        "pipeline specification is most insistent about."
    ) from error

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
    for tile in (
        "empty",
        "stone",
        "brick",
        "brick-cracked",
        "mirror-ne",
        "mirror-se",
        "water-0",
        "forest",
        "home-intact",
        "home-destroyed",
    ):
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
    tile = DEFAULT_RULES.tile_size
    assert (tile, tile) == catalog.FRAME_SIZE


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


# --------------------------------------------------------------------------------------
# Source references resolve to files that are actually here
# --------------------------------------------------------------------------------------


def test_every_source_names_a_scene_and_scripts_that_exist_in_the_repository() -> None:
    """A provenance record that names a file which is not here is a dangling claim.

    The validator already checks that every frame's source identifier resolves inside the
    sidecar and that each source carries authorship and a licence value. It deliberately
    stops there: it is a pure function of metadata and pixels and has no repository root
    to resolve a path against. That leaves the other half -- that the scene and the two
    scripts a source names can actually be opened -- checkable only from here, where the
    root is known. Without it the sidecar could promise reproducibility from a scene that
    was deleted three commits ago.
    """
    for source in shipped_metadata().sources:
        for field, reference in (
            ("scene", source.scene),
            ("build_script", source.build_script),
            ("render_script", source.render_script),
        ):
            path = REPO_ROOT / reference
            assert path.is_file(), (
                f"source {source.source_id!r} names {field} {reference!r}, "
                f"which is not a file in this repository"
            )


def test_source_references_are_repository_relative_and_inside_the_pipeline() -> None:
    """Relative, so the record means the same thing in any checkout.

    An absolute path would record one machine's directory layout, and a path climbing out
    of ``assets/blender`` would point the provenance of this pack at something the pack
    does not own.
    """
    for source in shipped_metadata().sources:
        for reference in (source.scene, source.build_script, source.render_script):
            assert not Path(reference).is_absolute(), reference
            assert ".." not in Path(reference).parts, reference
            assert reference.startswith("assets/blender/"), reference


def test_the_sidecar_names_the_same_scene_and_scripts_the_catalogue_does() -> None:
    """The files this test suite reads directly and the ones the sidecar advertises."""
    for source in shipped_metadata().sources:
        assert REPO_ROOT / source.scene == SCENE
        assert REPO_ROOT / source.build_script == BUILD_SCRIPT
        assert REPO_ROOT / source.render_script == RENDER_SCRIPT


def test_every_declared_source_is_used_by_a_frame_and_every_frame_resolves() -> None:
    """The shipped pack, not a fixture: no orphan provenance record, no dangling frame."""
    metadata = shipped_metadata()
    declared = {source.source_id for source in metadata.sources}
    used = {frame.source_id for frame in metadata.frames}
    assert used <= declared, sorted(used - declared)
    assert declared == used, sorted(declared - used)


def test_the_blender_readme_does_not_claim_the_scene_file_is_reproducible() -> None:
    text = (BLENDER_DIR / "README.md").read_text(encoding="utf-8")
    assert "Not reproducible: `starter_set.blend`" in text


# --------------------------------------------------------------------------------------
# Headlessness
# --------------------------------------------------------------------------------------


MARKER = "probe-result:"
"""A prefix, because pygame prints a banner to stdout the moment it is imported.

The negative control below caught this: a probe that read the whole of stdout saw
``pygame-ce 2.5.8 (SDL ...)`` ahead of its own answer and compared unequal. Tagging the
line the probe actually cares about makes the reading independent of whatever an imported
module decides to announce.
"""


def _probe(source: str) -> str:
    """Run ``source`` in a fresh interpreter and return its tagged line."""
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True
    )
    for line in result.stdout.splitlines():
        if line.startswith(MARKER):
            return line[len(MARKER) :].strip()
    raise AssertionError(f"the probe printed no {MARKER!r} line; stdout was {result.stdout!r}")


def test_the_asset_tools_pull_in_no_display_library() -> None:
    """Import the package in a fresh interpreter and look at *its* ``sys.modules``.

    The obvious version of this test reads this process's ``sys.modules`` directly. That
    does not work, and the way it fails is silent. pytest imports every selected test
    module during collection, before the first test runs, so a full-suite run has already
    imported ``tests/client`` -- and pygame with it -- by the time anything here executes.
    ``tests/assets`` sorts before ``tests/client``, so the teardown in that package's
    conftest which drops pygame again has not run yet either. Run on its own the
    in-process check passed and looked meaningful; run under ``make ci`` it failed, and it
    could only ever have been reporting on its neighbours rather than on this package.

    ``tests/client/conftest.py`` already works through exactly this problem for
    ``tests/sim/test_purity.py`` and reaches the same conclusion: the claim is only really
    testable in a subprocess that imports the package alone. This package can do that from
    inside its own allowed files, so it does.

    What is being asserted is a boundary that matters: the validator has to run in
    continuous integration on a machine with no GPU, no display and no Blender, so
    importing it must not reach for either.
    """
    probe = (
        "import sys\n"
        "import battle_city_tools.assets\n"
        "import battle_city_tools.assets.cli\n"
        "roots = {name.split('.')[0] for name in sys.modules}\n"
        f"print('{MARKER}' + ','.join(sorted(roots & {{'pygame', 'bpy'}})))\n"
    )
    leaked = _probe(probe)
    assert leaked == "", f"importing the asset tools pulled in {leaked}"


def test_the_probe_would_notice_a_display_library() -> None:
    """A negative control, because the test above asserts that nothing happened.

    An assertion that a set is empty passes just as well when the measurement is broken,
    so prove the measurement can fail: a probe that does import pygame must report it.
    Without this, a typo in the module name would read as a clean boundary forever.
    """
    probe = (
        "import sys\n"
        "import pygame\n"
        "roots = {name.split('.')[0] for name in sys.modules}\n"
        f"print('{MARKER}' + ','.join(sorted(roots & {{'pygame', 'bpy'}})))\n"
    )
    assert _probe(probe) == "pygame"
