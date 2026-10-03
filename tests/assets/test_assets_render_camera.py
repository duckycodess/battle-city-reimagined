"""Where the camera actually is when each frame is taken, and what the record says.

``render.json`` is the provenance half of this pack: the sidecar copies it verbatim, and
a reader is entitled to treat it as a description of the render rather than a transcript
of the script's intentions. The camera is the part of it that is easiest to get wrong,
because the camera is the one thing in the scene that moves between frames. A record that
named a single ``camera.location_x`` would be false for forty-three of the forty-four
frames, and false in a way that looks entirely reasonable.

So these tests run the real ``render_frames.py`` against a Blender-shaped fake (see
``blender_stub``) and ask what it did, not what it says. The fake's cells sit where
``build_scene.py`` puts them, read out of that script, so the positions exercised here
are the positions the real render uses.

Three claims are under test:

* the camera is moved, to each cell in turn, and the positions are the cells' own;
* the offset it is recorded with is *measured* from those placements -- feed the fake a
  camera that sits somewhere else and the number follows it;
* a run where that offset is not the same for every frame writes no record at all, rather
  than a number that happens to be true of the last frame.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from assets_helpers import BLENDER_DIR, script_literal, shipped_metadata
from battle_city_tools.assets import catalog
from battle_city_tools.assets.build import BLENDER_KEYS, RENDER_MANIFEST
from blender_stub import CAMERA_HEIGHT, FakeBlender, build_stub, load_render_script

RENDER_SCRIPT = BLENDER_DIR / "render_frames.py"
BUILD_SCRIPT = BLENDER_DIR / "build_scene.py"


def cell_origins() -> dict[str, tuple[float, float]]:
    """Every cell the scene builds, at the grid position ``build_scene.Cell`` gives it."""
    cells = script_literal(BUILD_SCRIPT, "CELLS")
    spacing = script_literal(BUILD_SCRIPT, "CELL_SPACING")
    columns = script_literal(BUILD_SCRIPT, "CELL_COLUMNS")
    return {
        cell_id: ((index % columns) * spacing, -(index // columns) * spacing)
        for index, cell_id in enumerate(cells)
    }


def run_render(output: Path, stub: FakeBlender) -> None:
    """Run the shipped script's ``main`` as Blender would, writing into ``output``."""
    module = load_render_script(RENDER_SCRIPT, stub)
    argv = [
        "blender",
        "--background",
        "--python",
        str(RENDER_SCRIPT),
        "--",
        "--output",
        str(output),
    ]
    saved, sys.argv = sys.argv, argv
    try:
        module.main()
    finally:
        sys.argv = saved


@pytest.fixture
def stub() -> FakeBlender:
    return build_stub(cell_origins())


# --------------------------------------------------------------------------------------
# The camera moves, and the record describes the movement truthfully
# --------------------------------------------------------------------------------------


def test_the_camera_is_moved_onto_every_cell_it_photographs(
    tmp_path: Path, stub: FakeBlender
) -> None:
    """The behaviour a single recorded location would misdescribe."""
    run_render(tmp_path, stub)

    rendered = stub.rendered()
    assert sorted(rendered) == sorted(catalog.FRAME_NAMES)
    plan = {row[0]: row[1] for row in script_literal(RENDER_SCRIPT, "RENDER_PLAN")}
    origins = cell_origins()
    for frame_name, call in rendered.items():
        x, y = origins[plan[frame_name]]
        assert call.camera == (x, y, CAMERA_HEIGHT), frame_name

    positions = {call.camera[:2] for call in stub.calls}
    assert len(positions) == len(set(plan.values())), (
        "the camera stood in one place; this test cannot say anything about a record "
        "that describes where it stood"
    )
    assert positions != {(0.0, 0.0)}


def test_the_record_holds_the_cell_offset_rather_than_a_camera_location(
    tmp_path: Path, stub: FakeBlender
) -> None:
    run_render(tmp_path, stub)
    render = json.loads((tmp_path / RENDER_MANIFEST).read_text(encoding="utf-8"))["render"]

    assert "camera.location_x" not in render
    assert "camera.location_y" not in render
    assert render["camera.cell_offset_x"] == 0.0
    assert render["camera.cell_offset_y"] == 0.0
    assert render["camera.location_z"] == CAMERA_HEIGHT


def test_the_recorded_offset_is_measured_and_not_a_constant(tmp_path: Path) -> None:
    """Move the camera away from the cell and the record has to follow it.

    This is the test that distinguishes a measurement from a hard-coded zero. The script
    places the camera on the cell origin, so the honest offset is ``(0, 0)`` -- which is
    indistinguishable from a literal until something makes it different. Here every cell
    sits at the origin and the camera is pinned a quarter of a tile to one side of it, so
    the offset is constant, the run is legitimate, and the recorded number is not zero.
    """
    moved = build_stub(dict.fromkeys(cell_origins(), (0.0, 0.0)))
    moved.camera.location = (0.25, -0.5, CAMERA_HEIGHT)
    moved.camera.moves_left = 0  # a constraint the render loop cannot override

    run_render(tmp_path, moved)
    render = json.loads((tmp_path / RENDER_MANIFEST).read_text(encoding="utf-8"))["render"]
    assert render["camera.cell_offset_x"] == 0.25
    assert render["camera.cell_offset_y"] == -0.5


def test_the_render_plan_and_the_record_agree_with_the_packer(
    tmp_path: Path, stub: FakeBlender
) -> None:
    """The manifest the script writes is the manifest ``build_pack`` demands.

    Two files have to hold the same key set -- the render script and
    ``metadata.RENDER_KEYS`` -- and nothing but a run of both would notice them drifting
    apart. The packer refuses a manifest with a missing or unknown key, so this is also
    what makes a change to the camera record a change that cannot half-land.
    """
    run_render(tmp_path, stub)
    manifest = json.loads((tmp_path / RENDER_MANIFEST).read_text(encoding="utf-8"))
    assert set(manifest["render"]) == set(BLENDER_KEYS)
    assert tuple(manifest["frames"]) == catalog.FRAME_NAMES


# --------------------------------------------------------------------------------------
# A camera that did not hold still records nothing at all
# --------------------------------------------------------------------------------------


def test_a_camera_that_stops_moving_halfway_through_refuses_to_be_recorded(
    tmp_path: Path, stub: FakeBlender
) -> None:
    """The failure the measurement exists for: a constraint or driver pinning the camera.

    After the first frame the fake camera ignores every placement, so the remaining
    forty-three frames are shot from the first cell's position. Each one therefore sits
    at a different offset from the cell it is supposed to be photographing, and no single
    camera record is true of the run.
    """
    stub.camera.moves_left = 1

    with pytest.raises(SystemExit) as caught:
        run_render(tmp_path, stub)

    message = str(caught.value)
    assert "camera.cell_offset_x" in message or "camera.cell_offset_y" in message
    assert "One camera record cannot describe this render" in message
    assert not (tmp_path / RENDER_MANIFEST).exists(), "a rejected run still wrote a record"


def test_inconsistent_observations_name_the_frame_and_the_key(
    tmp_path: Path, stub: FakeBlender
) -> None:
    """``constant_camera`` on its own, with the readings handed to it directly."""
    module = load_render_script(RENDER_SCRIPT, stub)
    first = {key: 0.0 for key in module.CAMERA_KEYS}
    second = dict(first, **{"camera.location_z": 7.0})

    with pytest.raises(SystemExit) as caught:
        module.constant_camera([("terrain-brick", first), ("terrain-stone", second)])
    assert "terrain-stone" in str(caught.value)
    assert "camera.location_z" in str(caught.value)

    assert module.constant_camera([("terrain-brick", first)]) == first
    with pytest.raises(SystemExit, match="no frame was rendered"):
        module.constant_camera([])


# --------------------------------------------------------------------------------------
# The shipped sidecar says the same thing
# --------------------------------------------------------------------------------------


def test_the_shipped_sidecar_records_a_cell_offset() -> None:
    """The tracked ``atlas.json``, which is what a consumer of this pack actually reads."""
    render = shipped_metadata().render
    assert render["camera.cell_offset_x"] == 0.0
    assert render["camera.cell_offset_y"] == 0.0
    assert render["camera.location_z"] == CAMERA_HEIGHT
    assert "camera.location_x" not in render
    assert "camera.location_y" not in render


def test_the_readme_explains_what_the_camera_record_means() -> None:
    text = (BLENDER_DIR / "README.md").read_text(encoding="utf-8")
    assert "camera.cell_offset_x" in text
