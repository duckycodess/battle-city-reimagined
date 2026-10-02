"""Turn a directory of Blender renders into the artifacts that are tracked in git.

This is the only step that writes into ``assets/sprites``. It is deliberately a pure
function of its inputs: the renders, the catalogue, the palette and the packer. Nothing
here reads a clock, consults the environment or asks the filesystem what day it is, so
re-running it over unchanged renders rewrites identical bytes.

The order is fixed and each step is small enough to state:

1. read ``render.json`` and refuse a frame set that is not exactly the catalogue's,
   including a directory carrying a render the current catalogue did not ask for
2. decode each render, check it is the supersampled frame size, box it down with
   premultiplied coverage, snap coverage to binary and quantise to the palette
3. pack by ascending name and composite the sheet
4. build the sidecar, including the pixel digests and the render record
5. draw the contact sheet and the labelled stage composite
6. write all four files atomically

Step 2 is where a render becomes pixel art. Supersampling and boxing down is what gives a
16-pixel sprite clean edges out of a path tracer, and quantising is what makes the
palette claim in the sidecar true rather than aspirational.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from battle_city_content import Level, load_bundled_pack

from ..storage import write_atomic
from . import catalog as catalog_module
from .composite import build_composite
from .errors import AssetRefusal, invalid
from .layout import ALGORITHM, ATLAS_WIDTH, PADDING, SORT_KEY, pack
from .metadata import (
    ATLAS_FILENAME,
    GENERATOR,
    METADATA_FILENAME,
    PACK_ID,
    RENDER_KEYS,
    SCHEMA_VERSION,
    AnimationRecord,
    AtlasMetadata,
    AtlasRecord,
    FrameRecord,
    LayoutRecord,
    LicenseRecord,
    ReadabilityRecord,
    RenderSetting,
    SourceRecord,
    serialize,
)
from .palette import PALETTE, PALETTE_COLORS
from .png import decode_png, encode_png
from .preview import build_preview
from .raster import ALPHA_THRESHOLD, Canvas, Image

RENDER_MANIFEST: Final[str] = "render.json"
PREVIEW_FILENAME: Final[str] = "preview.png"
COMPOSITE_FILENAME: Final[str] = "stage-composite.png"
COMPOSITE_LEVEL: Final[str] = "classic-03"
"""The bundled level the composite is drawn from: the only one using every tile kind."""

POST_KEYS: Final[tuple[str, ...]] = (
    "post.alpha_threshold",
    "post.downsample",
    "post.palette_size",
    "post.quantize",
    "post.supersample",
)
BLENDER_KEYS: Final[tuple[str, ...]] = tuple(
    key for key in RENDER_KEYS if key not in set(POST_KEYS)
)


@dataclass(frozen=True, slots=True)
class BuildReport:
    """What a build wrote, and the digest a second build has to reproduce."""

    output_dir: Path
    frame_count: int
    atlas_size: tuple[int, int]
    atlas_sha256_pixels: str
    written: tuple[Path, ...]

    def summary(self) -> str:
        """A few lines for a terminal."""
        width, height = self.atlas_size
        lines = [
            f"packed {self.frame_count} frames into a {width}x{height} atlas",
            f"atlas pixel digest {self.atlas_sha256_pixels}",
        ]
        lines.extend(f"wrote {path}" for path in self.written)
        return "\n".join(lines)


def build_pack(
    frames_dir: Path, output_dir: Path, *, level_id: str = COMPOSITE_LEVEL
) -> BuildReport:
    """Build the atlas, the sidecar, the contact sheet and the composite into ``output_dir``."""
    render = _read_manifest(frames_dir)
    _check_no_strays(frames_dir)
    supersample = _supersample(render, frames_dir)
    images = _load_frames(frames_dir, supersample)

    sizes = {name: image.size for name, image in images.items()}
    layout = pack(sizes, atlas_width=ATLAS_WIDTH, padding=PADDING)
    canvas = Canvas(layout.width, layout.height)
    for placement in layout.placements:
        canvas.blit(images[placement.name], placement.x, placement.y)
    atlas = canvas.freeze()

    settings: dict[str, RenderSetting] = dict(render)
    settings["post.alpha_threshold"] = ALPHA_THRESHOLD
    settings["post.downsample"] = "box-premultiplied"
    settings["post.palette_size"] = len(PALETTE)
    settings["post.quantize"] = "nearest-palette-srgb"
    settings["post.supersample"] = supersample

    metadata = AtlasMetadata(
        schema_version=SCHEMA_VERSION,
        pack_id=PACK_ID,
        generator=GENERATOR,
        atlas=AtlasRecord(
            path=ATLAS_FILENAME,
            width=atlas.width,
            height=atlas.height,
            sha256_pixels=atlas.digest(),
        ),
        frame_size=catalog_module.FRAME_SIZE,
        layout=LayoutRecord(
            algorithm=ALGORITHM, sort_key=SORT_KEY, atlas_width=ATLAS_WIDTH, padding=PADDING
        ),
        render=settings,
        authors=catalog_module.AUTHORS,
        license=LicenseRecord(
            spdx_id=catalog_module.LICENSE_SPDX, notice=catalog_module.LICENSE_NOTICE
        ),
        palette=PALETTE,
        hitboxes=dict(catalog_module.HITBOX_SIZES),
        sources=tuple(
            SourceRecord(
                source_id=source.source_id,
                scene=source.scene,
                build_script=source.build_script,
                render_script=source.render_script,
                collection=source.collection,
                authors=source.authors,
                spdx_id=source.spdx_id,
                notice=source.notice,
            )
            for source in catalog_module.SOURCES
        ),
        frames=tuple(
            FrameRecord(
                name=placement.name,
                group=catalog_module.FRAMES_BY_NAME[placement.name].group,
                source_id=catalog_module.FRAMES_BY_NAME[placement.name].source_id,
                hitbox=catalog_module.FRAMES_BY_NAME[placement.name].hitbox,
                rect=placement.rect,
                pivot=catalog_module.FRAMES_BY_NAME[placement.name].pivot,
                sha256_pixels=images[placement.name].digest(),
                description=catalog_module.FRAMES_BY_NAME[placement.name].description,
            )
            for placement in layout.placements
        ),
        animations=tuple(
            AnimationRecord(name=animation.name, frames=animation.frames, loop=animation.loop)
            for animation in catalog_module.ANIMATIONS
        ),
        states=dict(catalog_module.STATE_FRAMES),
        readability=ReadabilityRecord(
            luma_contrast=catalog_module.LUMA_CONTRAST,
            silhouette_distinct=catalog_module.SILHOUETTE_DISTINCT,
            luma_distinct=catalog_module.LUMA_DISTINCT,
            luma_distinct_step=catalog_module.LUMA_DISTINCT_STEP,
            quadrant_sign=catalog_module.QUADRANT_SIGN,
        ),
    )

    preview = build_preview(metadata, atlas)
    composite = build_composite(metadata, atlas, _bundled_level(level_id))

    output_dir.mkdir(parents=True, exist_ok=True)
    written = (
        _write(output_dir / ATLAS_FILENAME, encode_png(atlas)),
        _write(output_dir / METADATA_FILENAME, serialize(metadata)),
        _write(output_dir / PREVIEW_FILENAME, encode_png(preview)),
        _write(output_dir / COMPOSITE_FILENAME, encode_png(composite)),
    )
    return BuildReport(
        output_dir=output_dir,
        frame_count=len(metadata.frames),
        atlas_size=(atlas.width, atlas.height),
        atlas_sha256_pixels=metadata.atlas.sha256_pixels,
        written=written,
    )


def _write(path: Path, payload: bytes) -> Path:
    write_atomic(path, payload)
    return path


def _bundled_level(level_id: str) -> Level:
    pack_document = load_bundled_pack()
    try:
        return pack_document.level(level_id)
    except KeyError as error:
        raise AssetRefusal(
            f"the bundled pack has no level {level_id!r} to draw a composite from"
        ) from error


def _read_manifest(frames_dir: Path) -> dict[str, RenderSetting]:
    path = frames_dir / RENDER_MANIFEST
    artifact = str(path)
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError as error:
        raise invalid(
            artifact, "", "the render manifest is missing; run render_frames.py first"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise invalid(artifact, "", f"the render manifest is not valid JSON: {error}") from error
    if not isinstance(document, dict) or set(document) != {"frames", "render"}:
        raise invalid(artifact, "", "the manifest must hold exactly 'frames' and 'render'")
    frames = document["frames"]
    if not isinstance(frames, list) or tuple(frames) != catalog_module.FRAME_NAMES:
        raise invalid(
            artifact,
            "frames",
            "the rendered frame list is not the catalogue's frame list; "
            "re-render after a catalogue change",
        )
    block = document["render"]
    if not isinstance(block, dict) or set(block) != set(BLENDER_KEYS):
        missing = sorted(set(BLENDER_KEYS) - set(block if isinstance(block, dict) else {}))
        unknown = sorted(set(block if isinstance(block, dict) else {}) - set(BLENDER_KEYS))
        raise invalid(
            artifact,
            "render",
            f"the render record does not match this pipeline; missing {missing}, unknown {unknown}",
        )
    settings: dict[str, RenderSetting] = {}
    for key in BLENDER_KEYS:
        value = block[key]
        if not isinstance(value, str | int | float | bool):
            raise invalid(artifact, f"render.{key}", "a render setting must be a scalar")
        settings[key] = value
    return settings


def _supersample(render: dict[str, RenderSetting], frames_dir: Path) -> int:
    artifact = str(frames_dir / RENDER_MANIFEST)
    width, height = render["render.resolution_x"], render["render.resolution_y"]
    frame_width, frame_height = catalog_module.FRAME_SIZE
    if not isinstance(width, int) or not isinstance(height, int) or width != height:
        raise invalid(
            artifact, "render.resolution_x", "the render must be a square of whole pixels"
        )
    if width % frame_width or height % frame_height or width // frame_width < 1:
        raise invalid(
            artifact,
            "render.resolution_x",
            f"a {width}x{height} render is not a whole multiple of the "
            f"{frame_width}x{frame_height} frame size",
        )
    return width // frame_width


def _check_no_strays(frames_dir: Path) -> None:
    """Refuse a render directory holding anything the render script did not write.

    ``render_frames.py`` writes exactly one ``<frame name>.png`` per catalogue entry plus
    ``render.json``. Anything else means the directory is not the output of one run of the
    current catalogue, and the most likely cause is the one that silently produces wrong
    art: a sprite was renamed, the directory was re-rendered without being emptied, and
    the file under the old name is still sitting there. The manifest check cannot catch
    that -- it lists the new names and matches -- and :func:`_load_frames` cannot either,
    because it only ever opens the names it expects.

    Refusing is the conservative half of the trade. The stray file is very probably
    harmless, but a packer that ignores it cannot tell the author whether the renders it
    just used came from one scene or two, and the whole point of this directory is that
    the answer is knowable.
    """
    expected = {RENDER_MANIFEST} | {f"{name}.png" for name in catalog_module.FRAME_NAMES}
    strays = sorted(entry.name for entry in frames_dir.iterdir() if entry.name not in expected)
    if strays:
        shown = ", ".join(strays[:5]) + (f", and {len(strays) - 5} more" if len(strays) > 5 else "")
        raise invalid(
            str(frames_dir),
            "",
            f"the render directory holds {len(strays)} file(s) this catalogue did not "
            f"render: {shown}. Render into an empty directory",
        )


def _load_frames(frames_dir: Path, supersample: int) -> dict[str, Image]:
    frame_width, frame_height = catalog_module.FRAME_SIZE
    expected = (frame_width * supersample, frame_height * supersample)
    images: dict[str, Image] = {}
    for name in catalog_module.FRAME_NAMES:
        path = frames_dir / f"{name}.png"
        try:
            rendered = decode_png(path.read_bytes(), origin=str(path))
        except FileNotFoundError as error:
            raise invalid(str(path), "", "the render for this frame is missing") from error
        if rendered.size != expected:
            raise invalid(
                str(path),
                "",
                f"the render is {rendered.width}x{rendered.height}, "
                f"expected {expected[0]}x{expected[1]}",
            )
        images[name] = rendered.downsample(supersample).quantized(PALETTE_COLORS)
    return images
