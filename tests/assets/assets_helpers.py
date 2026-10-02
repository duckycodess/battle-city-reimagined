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

from pathlib import Path
from typing import Final

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
from battle_city_tools.assets.png import decode_png
from battle_city_tools.assets.validation import ValidationReport
from battle_city_tools.assets.raster import Canvas, Image

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PACK_DIR: Final[Path] = REPO_ROOT / "assets" / "sprites" / "starter"
BLENDER_DIR: Final[Path] = REPO_ROOT / "assets" / "blender"
ATLAS_PATH: Final[Path] = PACK_DIR / ATLAS_FILENAME
SIDECAR_PATH: Final[Path] = PACK_DIR / METADATA_FILENAME
PREVIEW_PATH: Final[Path] = PACK_DIR / "preview.png"
COMPOSITE_PATH: Final[Path] = PACK_DIR / "stage-composite.png"

SYNTHETIC_WIDTH: Final[int] = 32
SYNTHETIC_FRAMES: Final[tuple[str, ...]] = ("alpha", "beta", "gamma", "delta")


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
        animations=(
            AnimationRecord(name="cycle", frames=("alpha", "beta"), loop=True),
        ),
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
