"""Shared paths and fixtures for the sprite pipeline tests.

Deliberately not a ``conftest.py``. ``tests/`` holds no ``__init__.py`` files and
``mypy packages tests`` runs over the whole tree, so a second module called ``conftest``
in a non-package directory is a hard duplicate-module error. The repository already names
its helpers uniquely for that reason -- ``client_helpers``, ``tools_helpers`` -- and this
module follows.

It also keeps the tests away from the client: nothing here imports pygame, and
``tests/sim/test_purity.py`` fails if anything leaves a display library in ``sys.modules``.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Final

from battle_city_tools.assets import catalog
from battle_city_tools.assets.metadata import (
    ATLAS_FILENAME,
    METADATA_FILENAME,
    RENDER_KEYS,
    AnimationRecord,
    AtlasMetadata,
    AtlasRecord,
    FrameRecord,
    LayoutRecord,
    LicenseRecord,
    ReadabilityRecord,
    RenderSetting,
    SourceRecord,
    load,
)
from battle_city_tools.assets.palette import PALETTE, PALETTE_COLORS
from battle_city_tools.assets.png import decode_png, encode_png
from battle_city_tools.assets.raster import Canvas, Image
from battle_city_tools.assets.validation import ValidationReport

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PACK_DIR: Final[Path] = REPO_ROOT / "assets" / "sprites" / "starter"
BLENDER_DIR: Final[Path] = REPO_ROOT / "assets" / "blender"
ATLAS_PATH: Final[Path] = PACK_DIR / ATLAS_FILENAME
SIDECAR_PATH: Final[Path] = PACK_DIR / METADATA_FILENAME
PREVIEW_PATH: Final[Path] = PACK_DIR / "preview.png"
COMPOSITE_PATH: Final[Path] = PACK_DIR / "stage-composite.png"

SYNTHETIC_WIDTH: Final[int] = 32
SYNTHETIC_FRAMES: Final[tuple[str, ...]] = ("alpha", "beta", "gamma", "delta")


def script_literal(path: Path, name: str) -> Any:
    """Read one module-level literal assignment out of a script without importing it.

    The Blender scripts cannot be imported in this process -- they begin with ``import
    bpy`` -- but their tables are plain literals, and a test that wants to know which
    cells the scene builds should read them from the file rather than keep a second copy.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{path} has no module-level assignment to {name}")


def shipped_metadata() -> AtlasMetadata:
    """The checked-in sidecar, parsed."""
    return load(SIDECAR_PATH.read_bytes(), artifact=str(SIDECAR_PATH))


def shipped_atlas() -> Image:
    """The checked-in atlas, decoded."""
    return decode_png(ATLAS_PATH.read_bytes(), origin=str(ATLAS_PATH))


def placeholder_render() -> dict[str, RenderSetting]:
    """A render record with the right keys and obviously synthetic values."""
    return {key: "synthetic" for key in RENDER_KEYS}


def synthetic_frames() -> dict[str, Image]:
    """Four distinct 16x16 frames built from palette colours, for structural tests."""
    width, height = catalog.FRAME_SIZE
    images: dict[str, Image] = {}
    for index, name in enumerate(SYNTHETIC_FRAMES):
        canvas = Canvas(width, height)
        canvas.fill_rect(0, 0, width, height, (*PALETTE_COLORS[index + 1], 255))
        canvas.fill_rect(index, index, 4, 4, (*PALETTE_COLORS[index + 10], 255))
        images[name] = canvas.freeze()
    return images


def synthetic_pack() -> tuple[AtlasMetadata, Image, dict[str, Image]]:
    """A tiny valid pack: four frames, one source, one animation, no readability rules.

    Built rather than copied so a corruption test can change exactly one thing and see
    exactly one failure, without having to keep real art satisfying real thresholds.
    """
    from battle_city_tools.assets.layout import ALGORITHM, SORT_KEY, pack

    images = synthetic_frames()
    layout = pack({name: image.size for name, image in images.items()}, atlas_width=SYNTHETIC_WIDTH)
    canvas = Canvas(layout.width, layout.height)
    for placement in layout.placements:
        canvas.blit(images[placement.name], placement.x, placement.y)
    atlas = canvas.freeze()

    metadata = AtlasMetadata(
        schema_version=1,
        pack_id="synthetic",
        generator="tests",
        atlas=AtlasRecord(
            path=ATLAS_FILENAME,
            width=atlas.width,
            height=atlas.height,
            sha256_pixels=atlas.digest(),
        ),
        frame_size=catalog.FRAME_SIZE,
        layout=LayoutRecord(
            algorithm=ALGORITHM, sort_key=SORT_KEY, atlas_width=SYNTHETIC_WIDTH, padding=0
        ),
        render=placeholder_render(),
        authors=("tests",),
        license=LicenseRecord(spdx_id="NOASSERTION", notice="synthetic fixture"),
        palette=PALETTE,
        hitboxes=dict(catalog.HITBOX_SIZES),
        sources=(
            SourceRecord(
                source_id="synthetic/source",
                scene="none.blend",
                build_script="none.py",
                render_script="none.py",
                collection="synthetic",
                authors=("tests",),
                spdx_id="NOASSERTION",
                notice="synthetic fixture",
            ),
        ),
        frames=tuple(
            FrameRecord(
                name=placement.name,
                group="synthetic",
                source_id="synthetic/source",
                hitbox="tile",
                rect=placement.rect,
                pivot=(0, 0),
                sha256_pixels=images[placement.name].digest(),
                description=f"synthetic frame {placement.name}",
            )
            for placement in layout.placements
        ),
        animations=(AnimationRecord(name="cycle", frames=("alpha", "beta"), loop=True),),
        states={"synthetic.first": "alpha"},
        readability=ReadabilityRecord(
            luma_contrast=(),
            silhouette_distinct=(),
            luma_distinct=(),
            luma_distinct_step=24,
            quadrant_sign=(),
        ),
    )
    return metadata, atlas, images


def problems_mentioning(report: ValidationReport, needle: str) -> list[str]:
    """Every problem in ``report`` whose text contains ``needle``."""
    return [str(problem) for problem in report.problems if needle in str(problem)]


SYNTHETIC_SUPERSAMPLE: Final[int] = 4
"""The supersample factor the synthetic render set declares, matching the real pipeline."""


def write_synthetic_render_set(
    frames_dir: Path, *, supersample: int = SYNTHETIC_SUPERSAMPLE
) -> Path:
    """Write a complete, catalogue-shaped render directory of made-up pixels.

    ``build_pack`` refuses any frame set that is not exactly the catalogue's, so a test
    that exercises it needs all of them. Rendering them would need Blender, which is the
    one thing the packer is built not to require, so the pixels here are invented.

    They are invented *deterministically*: each frame's content is derived from a digest
    of its own name, so the set is a pure function of the catalogue. That is what makes
    it usable as the input to a determinism test -- a second call writes the same bytes,
    so a difference between two builds can only have come from the packer.

    The pixels are deliberately not art and the frames carry no readability properties.
    This fixture is for checking that the *packing* is reproducible and that the frames
    directory is read strictly. The real art is checked against the real thresholds in
    ``test_assets_checked_in``.
    """
    frame_width, frame_height = catalog.FRAME_SIZE
    width, height = frame_width * supersample, frame_height * supersample
    frames_dir.mkdir(parents=True, exist_ok=True)
    for name in catalog.FRAME_NAMES:
        digest = hashlib.sha256(name.encode("utf-8")).digest()
        canvas = Canvas(width, height)
        for y in range(height):
            for x in range(width):
                index = digest[(x // supersample + y // supersample * 3) % len(digest)]
                colour = PALETTE_COLORS[index % len(PALETTE_COLORS)]
                opaque = (x // supersample + y // supersample + digest[0]) % 5 != 0
                canvas.set_pixel(x, y, (*colour, 255) if opaque else (0, 0, 0, 0))
        (frames_dir / f"{name}.png").write_bytes(encode_png(canvas.freeze()))

    manifest = {
        "frames": list(catalog.FRAME_NAMES),
        "render": synthetic_manifest_render(width, height),
    }
    path = frames_dir / "render.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def synthetic_manifest_render(width: int, height: int) -> dict[str, RenderSetting]:
    """The ``render`` block of a synthetic manifest: every Blender key, obviously fake.

    The resolution keys have to be real integers because the packer derives the
    supersample factor from them. Everything else is a placeholder, because the packer
    copies the block into the sidecar without interpreting it.
    """
    from battle_city_tools.assets.build import BLENDER_KEYS

    block: dict[str, RenderSetting] = {key: "synthetic" for key in BLENDER_KEYS}
    block["render.resolution_x"] = width
    block["render.resolution_y"] = height
    return block
