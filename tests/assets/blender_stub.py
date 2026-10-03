"""A fake ``bpy``, just real enough to run ``render_frames.py`` and watch what it does.

Why this exists
---------------
``render_frames.py`` is the one file in the pipeline that decides where the camera is
when each frame is taken, and ``render.json`` -- and through it the sidecar's provenance
record -- is only as true as that decision. Checking it by parsing the source tells you
what the script says; it cannot tell you what the script does. Checking it with Blender
would mean a test that needs a 300 MB download, which is exactly what the rest of this
suite is built to avoid: continuous integration validates the checked-in pixels with the
standard library and never renders.

So the script is run for real, against a Blender-shaped object that records what was
asked of it. The code under test is the shipped file, loaded from its path; nothing here
re-implements it.

What this is not
----------------
It is not a model of Blender. It knows nothing about geometry, light or sampling, and it
would be worthless for a question about pixels. It models exactly three things the render
loop touches -- objects that can be moved and rotated, collections that can be hidden, and
a render operator that can be asked to write a file -- plus a settings tree with the
values :func:`render_frames.record` reads back. A test written against it can honestly
claim that the script moves the camera where it claims to and measures what it records,
and nothing more than that.

The one deliberate piece of misbehaviour is :attr:`FakeObject.moves_left`. A real scene
can refuse to move an object: a constraint, a driver or a keyframe will quietly override
an assignment to ``location``. That is the failure the recorded camera offset exists to
catch, so the fake can be told to stop accepting moves and the test can watch the script
refuse to write a record rather than record a number true of one frame.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

CAMERA_HEIGHT: float = 6.0
"""The height ``build_scene.add_camera`` puts the camera at. Mirrored, and asserted."""


@dataclass
class Vector:
    """What Blender hands back for ``location`` and ``rotation_euler``."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class FakeObject:
    """A scene object whose placement can be read, written, and refused."""

    def __init__(self, name: str, location: tuple[float, float, float] = (0.0, 0.0, 0.0)):
        self.name = name
        self.data: Any = None
        self.moves_left: int | None = None
        """How many more assignments to ``location`` will be honoured. ``None`` is all."""
        self._location = Vector(*location)
        self._rotation = Vector()

    @property
    def location(self) -> Vector:
        return self._location

    @location.setter
    def location(self, value: tuple[float, float, float]) -> None:
        if self.moves_left is not None:
            if self.moves_left <= 0:
                return
            self.moves_left -= 1
        self._location = Vector(*value)

    @property
    def rotation_euler(self) -> Vector:
        return self._rotation

    @rotation_euler.setter
    def rotation_euler(self, value: tuple[float, float, float]) -> None:
        self._rotation = Vector(*value)


class FakeCollection:
    """A collection that can be hidden from the render, which is how cells are isolated."""

    def __init__(self, name: str):
        self.name = name
        self.hide_render = False


class Registry:
    """``bpy.data.collections`` and ``bpy.data.objects``: iterable, and addressable by name."""

    def __init__(self) -> None:
        self._entries: dict[str, Any] = {}

    def add(self, key: str, value: Any) -> Any:
        self._entries[key] = value
        return value

    def get(self, key: str) -> Any:
        return self._entries.get(key)

    def __iter__(self) -> Any:
        return iter(self._entries.values())


@dataclass
class RenderCall:
    """One call to ``bpy.ops.render.render``, and where the camera was for it."""

    filepath: str
    camera: tuple[float, float, float]
    rotation: tuple[float, float, float]


@dataclass
class FakeBlender:
    """The whole fake: the module object the script imports, plus what it observed."""

    module: ModuleType
    camera: FakeObject
    pivots: dict[str, FakeObject]
    collections: dict[str, FakeCollection]
    calls: list[RenderCall] = field(default_factory=list)

    def rendered(self) -> dict[str, RenderCall]:
        """Every render, keyed by the frame name its output path ends with."""
        return {Path(call.filepath).name: call for call in self.calls}


def _settings(scene_camera: FakeObject) -> Any:
    """The scene's settings tree, holding one readable value per recorded key.

    The values are plausible rather than meaningful: nothing here is asserted against the
    shipped pack except the camera, because everything else is Blender reporting on
    itself and a fake cannot stand in for that.
    """
    background = SimpleNamespace(
        inputs={
            "Color": SimpleNamespace(default_value=(0.16, 0.18, 0.24, 1.0)),
            "Strength": SimpleNamespace(default_value=0.35),
        }
    )
    return SimpleNamespace(
        camera=scene_camera,
        render=SimpleNamespace(
            filepath="",
            film_transparent=True,
            threads=8,
            threads_mode="AUTO",
            dither_intensity=0.0,
            engine="CYCLES",
            resolution_percentage=100,
            resolution_x=64,
            resolution_y=64,
            image_settings=SimpleNamespace(
                color_depth="8", color_mode="RGBA", compression=15, file_format="PNG"
            ),
        ),
        cycles=SimpleNamespace(
            use_adaptive_sampling=False,
            use_denoising=False,
            device="CPU",
            diffuse_bounces=1,
            max_bounces=1,
            samples=96,
            seed=0,
            filter_width=1.0,
            pixel_filter_type="BLACKMAN_HARRIS",
        ),
        display_settings=SimpleNamespace(display_device="sRGB"),
        view_settings=SimpleNamespace(
            exposure=0.0, gamma=1.0, look="None", view_transform="Standard"
        ),
        sequencer_colorspace_settings=SimpleNamespace(name="sRGB"),
        world=SimpleNamespace(node_tree=SimpleNamespace(nodes={"Background": background})),
    )


def build_stub(cells: dict[str, tuple[float, float]]) -> FakeBlender:
    """A fake Blender holding one cell per entry in ``cells``, at the given grid origin."""
    camera_data = SimpleNamespace(clip_end=100.0, clip_start=0.1, ortho_scale=1.0, type="ORTHO")
    camera = FakeObject("sprite-camera", (0.0, 0.0, CAMERA_HEIGHT))
    camera.data = camera_data

    collections = Registry()
    objects = Registry()
    pivots: dict[str, FakeObject] = {}
    named_collections: dict[str, FakeCollection] = {}
    for cell_id, (x, y) in cells.items():
        named_collections[cell_id] = collections.add("cell/" + cell_id, FakeCollection(cell_id))
        pivots[cell_id] = objects.add(
            "pivot/" + cell_id, FakeObject("pivot/" + cell_id, (x, y, 0.0))
        )

    scene = _settings(camera)
    stub = ModuleType("bpy")
    calls: list[RenderCall] = []

    def render(write_still: bool = False) -> None:
        calls.append(
            RenderCall(
                filepath=scene.render.filepath,
                camera=camera.location.as_tuple(),
                rotation=camera.rotation_euler.as_tuple(),
            )
        )

    stub.__dict__.update(
        app=SimpleNamespace(
            build_date=b"2026-09-15",
            build_hash=b"62c1db4208e8",
            build_platform=b"Linux",
            version_string="4.5.14 LTS",
        ),
        context=SimpleNamespace(scene=scene),
        data=SimpleNamespace(collections=collections, objects=objects),
        ops=SimpleNamespace(render=SimpleNamespace(render=render)),
    )
    return FakeBlender(
        module=stub, camera=camera, pivots=pivots, collections=named_collections, calls=calls
    )


def load_render_script(path: Path, stub: FakeBlender) -> ModuleType:
    """Import the shipped ``render_frames.py`` against ``stub`` instead of Blender.

    Loaded from its path under a private name and never left in ``sys.modules`` as
    ``bpy``: the suite asserts elsewhere, in a subprocess, that the asset tools import no
    display library, and a fake one lying around in this process would be its own kind of
    lie.
    """
    saved = sys.modules.get("bpy")
    sys.modules["bpy"] = stub.module
    try:
        spec = importlib.util.spec_from_file_location("render_frames_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is None:
            del sys.modules["bpy"]
        else:  # pragma: no cover - nothing in this suite imports a real bpy
            sys.modules["bpy"] = saved
