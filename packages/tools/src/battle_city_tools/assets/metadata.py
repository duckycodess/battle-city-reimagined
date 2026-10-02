"""The atlas sidecar: what it says, how it is written, and how it is read back.

An atlas is a picture; the metadata is the contract. It states the frame rectangles, the
pivots, the collision box each frame depicts, the palette, the animation sequences, where
the pixels came from and what is claimed about the rights in them. The simulation never
reads it -- art cannot change gameplay -- but every consumer of the art does, so it is
versioned and loaded the way content is: ``schema_version`` first, all or nothing, and a
rejection that names the file and the field.

Reading is strict in both directions. A missing key is a failure and so is an unexpected
one, because a key nobody recognises is either a newer writer talking to an older reader
or a hand edit, and silently ignoring it is how an atlas and its sidecar drift apart.

Integrity is recorded as :meth:`~battle_city_tools.assets.raster.Image.digest` over the
*pixels*, for the atlas and for every frame. A digest of the file bytes would be a
promise this pipeline cannot keep: zlib's output is allowed to differ between zlib builds,
so the same pixels can compress to different files. Pixels are the thing that must not
change silently, and pixels are what is hashed. The metadata does not hash itself -- a
document cannot contain its own digest -- so the sidecar's own integrity is whatever the
repository provides for a tracked text file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ..serialization import JsonValue
from .errors import AssetInvalid, invalid
from .raster import Rgb

SCHEMA_VERSION: Final[int] = 1
GENERATOR: Final[str] = "battle_city_tools.assets"
METADATA_FILENAME: Final[str] = "atlas.json"
ATLAS_FILENAME: Final[str] = "atlas.png"
PACK_ID: Final[str] = "starter"

JSON_INDENT: Final[int] = 2

RENDER_KEYS: Final[tuple[str, ...]] = (
    "blender.build_date",
    "blender.build_hash",
    "blender.build_platform",
    "blender.version",
    "camera.clip_end",
    "camera.clip_start",
    "camera.location_x",
    "camera.location_y",
    "camera.location_z",
    "camera.ortho_scale",
    "camera.rotation_x",
    "camera.rotation_y",
    "camera.rotation_z",
    "camera.type",
    "color.dither",
    "color.display_device",
    "color.exposure",
    "color.gamma",
    "color.look",
    "color.sequencer_colorspace",
    "color.view_transform",
    "cycles.adaptive_sampling",
    "cycles.denoising",
    "cycles.device",
    "cycles.diffuse_bounces",
    "cycles.max_bounces",
    "cycles.samples",
    "cycles.seed",
    "engine",
    "filter_width",
    "output.color_depth",
    "output.color_mode",
    "output.compression",
    "output.file_format",
    "pixel_filter",
    "post.alpha_threshold",
    "post.downsample",
    "post.palette_size",
    "post.quantize",
    "post.supersample",
    "render.resolution_percentage",
    "render.resolution_x",
    "render.resolution_y",
    "render.threads",
    "render.threads_mode",
    "world.color_b",
    "world.color_g",
    "world.color_r",
    "world.strength",
)
"""Every setting the render is pinned to, as a flat sorted table of scalars.

Flat because a reader comparing two renders wants a diff, not a tree walk, and because a
closed key set is checkable: anything outside this tuple is rejected rather than carried.
The ``post.*`` keys are the pipeline's own, applied after Blender exits; everything else
is read back out of Blender at render time rather than copied from the script that asked
for it, so the record describes what actually rendered.
"""

RENDER_KEY_SET: Final[frozenset[str]] = frozenset(RENDER_KEYS)

RenderSetting = str | int | float | bool


@dataclass(frozen=True, slots=True)
class AtlasRecord:
    """The sheet itself."""

    path: str
    width: int
    height: int
    sha256_pixels: str


@dataclass(frozen=True, slots=True)
class LayoutRecord:
    """How the sheet was packed, so a reader can tell a re-pack from a re-render."""

    algorithm: str
    sort_key: str
    atlas_width: int
    padding: int


@dataclass(frozen=True, slots=True)
class LicenseRecord:
    """An SPDX identifier and the prose that explains it."""

    spdx_id: str
    notice: str


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A group of frames traced back to the scene and scripts that produced them."""

    source_id: str
    scene: str
    build_script: str
    render_script: str
    collection: str
    authors: tuple[str, ...]
    spdx_id: str
    notice: str


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """One sprite in the sheet.

    ``rect`` is the atlas rectangle. ``pivot`` is the art anchor inside the frame, in frame
    pixels. ``hitbox`` names the collision box the frame depicts and is a key into
    :attr:`AtlasMetadata.hitboxes`; it is a statement about the simulation's geometry, not
    about these pixels, and the two are allowed to differ in size.
    """

    name: str
    group: str
    source_id: str
    hitbox: str
    rect: tuple[int, int, int, int]
    pivot: tuple[int, int]
    sha256_pixels: str
    description: str


@dataclass(frozen=True, slots=True)
class AnimationRecord:
    """A named sequence, with no durations.

    See :class:`~battle_city_tools.assets.catalog.AnimationSpec` for why timing is absent.
    """

    name: str
    frames: tuple[str, ...]
    loop: bool


@dataclass(frozen=True, slots=True)
class ReadabilityRecord:
    """The claims about legibility without colour that the validator re-checks."""

    luma_contrast: tuple[tuple[str, str, int], ...]
    silhouette_distinct: tuple[tuple[str, str, int], ...]
    luma_distinct: tuple[tuple[str, str, int], ...]
    luma_distinct_step: int
    quadrant_sign: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True, slots=True)
class AtlasMetadata:
    """A whole sidecar, loaded or about to be written."""

    schema_version: int
    pack_id: str
    generator: str
    atlas: AtlasRecord
    frame_size: tuple[int, int]
    layout: LayoutRecord
    render: Mapping[str, RenderSetting]
    authors: tuple[str, ...]
    license: LicenseRecord
    palette: tuple[tuple[str, Rgb], ...]
    hitboxes: Mapping[str, tuple[int, int] | None]
    sources: tuple[SourceRecord, ...]
    frames: tuple[FrameRecord, ...]
    animations: tuple[AnimationRecord, ...]
    states: Mapping[str, str]
    readability: ReadabilityRecord

    def frame(self, name: str) -> FrameRecord:
        """The frame called ``name``. Raises :class:`KeyError` when there is none."""
        for record in self.frames:
            if record.name == name:
                return record
        raise KeyError(name)

    @property
    def frame_names(self) -> tuple[str, ...]:
        """Every frame name, in the order the document lists them."""
        return tuple(record.name for record in self.frames)

    @property
    def palette_colors(self) -> tuple[Rgb, ...]:
        """The palette's colours in declared order."""
        return tuple(color for _, color in self.palette)


def serialize(metadata: AtlasMetadata) -> bytes:
    """Encode a sidecar: two-space indentation, declared key order, trailing newline, UTF-8."""
    text = json.dumps(to_document(metadata), indent=JSON_INDENT, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def to_document(metadata: AtlasMetadata) -> dict[str, JsonValue]:
    """The sidecar as plain JSON values, in the order the file writes them."""
    return {
        "schema_version": metadata.schema_version,
        "pack_id": metadata.pack_id,
        "generator": metadata.generator,
        "atlas": {
            "path": metadata.atlas.path,
            "width": metadata.atlas.width,
            "height": metadata.atlas.height,
            "sha256_pixels": metadata.atlas.sha256_pixels,
        },
        "frame_size": list(metadata.frame_size),
        "layout": {
            "algorithm": metadata.layout.algorithm,
            "sort_key": metadata.layout.sort_key,
            "atlas_width": metadata.layout.atlas_width,
            "padding": metadata.layout.padding,
        },
        "render": {key: metadata.render[key] for key in RENDER_KEYS},
        "authors": list(metadata.authors),
        "license": {
            "spdx_id": metadata.license.spdx_id,
            "notice": metadata.license.notice,
        },
        "palette": [{"name": name, "rgb": list(color)} for name, color in metadata.palette],
        "hitboxes": {
            key: (None if size is None else list(size))
            for key, size in sorted(metadata.hitboxes.items())
        },
        "sources": [
            {
                "source_id": source.source_id,
                "scene": source.scene,
                "build_script": source.build_script,
                "render_script": source.render_script,
                "collection": source.collection,
                "authors": list(source.authors),
                "spdx_id": source.spdx_id,
                "notice": source.notice,
            }
            for source in metadata.sources
        ],
        "frames": [
            {
                "name": frame.name,
                "group": frame.group,
                "source_id": frame.source_id,
                "hitbox": frame.hitbox,
                "rect": list(frame.rect),
                "pivot": list(frame.pivot),
                "sha256_pixels": frame.sha256_pixels,
                "description": frame.description,
            }
            for frame in metadata.frames
        ],
        "animations": [
            {"name": animation.name, "frames": list(animation.frames), "loop": animation.loop}
            for animation in metadata.animations
        ],
        "states": dict(sorted(metadata.states.items())),
        "readability": {
            "luma_contrast": [list(rule) for rule in metadata.readability.luma_contrast],
            "silhouette_distinct": [
                list(rule) for rule in metadata.readability.silhouette_distinct
            ],
            "luma_distinct": [list(rule) for rule in metadata.readability.luma_distinct],
            "luma_distinct_step": metadata.readability.luma_distinct_step,
            "quadrant_sign": [list(rule) for rule in metadata.readability.quadrant_sign],
        },
    }


def load(payload: bytes, *, artifact: str = METADATA_FILENAME) -> AtlasMetadata:
    """Parse and check a sidecar. Raises :class:`AssetInvalid` naming the offending field."""
    try:
        document = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise invalid(artifact, "", f"the file is not valid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise invalid(artifact, "", f"the file is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise invalid(artifact, "", "the document must be a JSON object")
    return _read_metadata(document, artifact)


_TOP_LEVEL: Final[tuple[str, ...]] = (
    "schema_version",
    "pack_id",
    "generator",
    "atlas",
    "frame_size",
    "layout",
    "render",
    "authors",
    "license",
    "palette",
    "hitboxes",
    "sources",
    "frames",
    "animations",
    "states",
    "readability",
)


def _read_metadata(document: dict[str, JsonValue], artifact: str) -> AtlasMetadata:
    _exact_keys(document, _TOP_LEVEL, artifact, "")
    version = _integer(document, "schema_version", artifact, "")
    if version != SCHEMA_VERSION:
        raise invalid(
            artifact,
            "schema_version",
            f"this reader understands version {SCHEMA_VERSION}, the document declares {version}",
        )
    atlas = _object(document["atlas"], artifact, "atlas")
    _exact_keys(atlas, ("path", "width", "height", "sha256_pixels"), artifact, "atlas")
    layout = _object(document["layout"], artifact, "layout")
    _exact_keys(layout, ("algorithm", "sort_key", "atlas_width", "padding"), artifact, "layout")
    license_block = _object(document["license"], artifact, "license")
    _exact_keys(license_block, ("spdx_id", "notice"), artifact, "license")
    return AtlasMetadata(
        schema_version=version,
        pack_id=_text(document, "pack_id", artifact, ""),
        generator=_text(document, "generator", artifact, ""),
        atlas=AtlasRecord(
            path=_text(atlas, "path", artifact, "atlas"),
            width=_integer(atlas, "width", artifact, "atlas"),
            height=_integer(atlas, "height", artifact, "atlas"),
            sha256_pixels=_digest(atlas, "sha256_pixels", artifact, "atlas"),
        ),
        frame_size=_pair(document["frame_size"], artifact, "frame_size"),
        layout=LayoutRecord(
            algorithm=_text(layout, "algorithm", artifact, "layout"),
            sort_key=_text(layout, "sort_key", artifact, "layout"),
            atlas_width=_integer(layout, "atlas_width", artifact, "layout"),
            padding=_integer(layout, "padding", artifact, "layout"),
        ),
        render=_read_render(document["render"], artifact),
        authors=_text_list(document["authors"], artifact, "authors"),
        license=LicenseRecord(
            spdx_id=_text(license_block, "spdx_id", artifact, "license"),
            notice=_text(license_block, "notice", artifact, "license"),
        ),
        palette=_read_palette(document["palette"], artifact),
        hitboxes=_read_hitboxes(document["hitboxes"], artifact),
        sources=_read_sources(document["sources"], artifact),
        frames=_read_frames(document["frames"], artifact),
        animations=_read_animations(document["animations"], artifact),
        states=_read_states(document["states"], artifact),
        readability=_read_readability(document["readability"], artifact),
    )


def _read_render(value: JsonValue, artifact: str) -> dict[str, RenderSetting]:
    block = _object(value, artifact, "render")
    _exact_keys(block, RENDER_KEYS, artifact, "render")
    settings: dict[str, RenderSetting] = {}
    for key in RENDER_KEYS:
        entry = block[key]
        if not isinstance(entry, str | int | float | bool):
            raise invalid(
                artifact, f"render.{key}", "a render setting must be a string, number or boolean"
            )
        settings[key] = entry
    return settings


def _read_palette(value: JsonValue, artifact: str) -> tuple[tuple[str, Rgb], ...]:
    entries = _array(value, artifact, "palette")
    if not entries:
        raise invalid(artifact, "palette", "a palette must declare at least one colour")
    palette: list[tuple[str, Rgb]] = []
    seen_names: set[str] = set()
    seen_colors: set[Rgb] = set()
    for index, entry in enumerate(entries):
        field = f"palette[{index}]"
        block = _object(entry, artifact, field)
        _exact_keys(block, ("name", "rgb"), artifact, field)
        name = _text(block, "name", artifact, field)
        channels = _array(block["rgb"], artifact, f"{field}.rgb")
        if len(channels) != 3:
            raise invalid(artifact, f"{field}.rgb", "a colour needs exactly three channels")
        red, green, blue = (_channel(channel, artifact, f"{field}.rgb") for channel in channels)
        color = (red, green, blue)
        if name in seen_names:
            raise invalid(artifact, field, f"the palette name {name!r} appears twice")
        if color in seen_colors:
            raise invalid(artifact, field, f"the colour {list(color)} appears twice")
        seen_names.add(name)
        seen_colors.add(color)
        palette.append((name, color))
    return tuple(palette)


def _read_hitboxes(value: JsonValue, artifact: str) -> dict[str, tuple[int, int] | None]:
    block = _object(value, artifact, "hitboxes")
    boxes: dict[str, tuple[int, int] | None] = {}
    for key in sorted(block):
        entry = block[key]
        boxes[key] = None if entry is None else _pair(entry, artifact, f"hitboxes.{key}")
    return boxes


def _read_sources(value: JsonValue, artifact: str) -> tuple[SourceRecord, ...]:
    entries = _array(value, artifact, "sources")
    keys = (
        "source_id",
        "scene",
        "build_script",
        "render_script",
        "collection",
        "authors",
        "spdx_id",
        "notice",
    )
    sources: list[SourceRecord] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"sources[{index}]"
        block = _object(entry, artifact, field)
        _exact_keys(block, keys, artifact, field)
        source_id = _text(block, "source_id", artifact, field)
        if source_id in seen:
            raise invalid(artifact, field, f"the source id {source_id!r} appears twice")
        seen.add(source_id)
        sources.append(
            SourceRecord(
                source_id=source_id,
                scene=_text(block, "scene", artifact, field),
                build_script=_text(block, "build_script", artifact, field),
                render_script=_text(block, "render_script", artifact, field),
                collection=_text(block, "collection", artifact, field),
                authors=_text_list(block["authors"], artifact, f"{field}.authors"),
                spdx_id=_text(block, "spdx_id", artifact, field),
                notice=_text(block, "notice", artifact, field),
            )
        )
    return tuple(sources)


def _read_frames(value: JsonValue, artifact: str) -> tuple[FrameRecord, ...]:
    entries = _array(value, artifact, "frames")
    if not entries:
        raise invalid(artifact, "frames", "an atlas must declare at least one frame")
    keys = (
        "name",
        "group",
        "source_id",
        "hitbox",
        "rect",
        "pivot",
        "sha256_pixels",
        "description",
    )
    frames: list[FrameRecord] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"frames[{index}]"
        block = _object(entry, artifact, field)
        _exact_keys(block, keys, artifact, field)
        name = _text(block, "name", artifact, field)
        if name in seen:
            raise invalid(artifact, field, f"the frame name {name!r} appears twice")
        seen.add(name)
        frames.append(
            FrameRecord(
                name=name,
                group=_text(block, "group", artifact, field),
                source_id=_text(block, "source_id", artifact, field),
                hitbox=_text(block, "hitbox", artifact, field),
                rect=_quad(block["rect"], artifact, f"{field}.rect"),
                pivot=_pair(block["pivot"], artifact, f"{field}.pivot"),
                sha256_pixels=_digest(block, "sha256_pixels", artifact, field),
                description=_text(block, "description", artifact, field),
            )
        )
    return tuple(frames)


def _read_animations(value: JsonValue, artifact: str) -> tuple[AnimationRecord, ...]:
    entries = _array(value, artifact, "animations")
    animations: list[AnimationRecord] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"animations[{index}]"
        block = _object(entry, artifact, field)
        _exact_keys(block, ("name", "frames", "loop"), artifact, field)
        name = _text(block, "name", artifact, field)
        if name in seen:
            raise invalid(artifact, field, f"the animation name {name!r} appears twice")
        seen.add(name)
        loop = block["loop"]
        if not isinstance(loop, bool):
            raise invalid(artifact, f"{field}.loop", "loop must be true or false")
        sequence = _text_list(block["frames"], artifact, f"{field}.frames")
        if len(sequence) < 2:
            raise invalid(artifact, f"{field}.frames", "an animation needs at least two frames")
        animations.append(AnimationRecord(name=name, frames=sequence, loop=loop))
    return tuple(animations)


def _read_states(value: JsonValue, artifact: str) -> dict[str, str]:
    block = _object(value, artifact, "states")
    states: dict[str, str] = {}
    for key in sorted(block):
        states[key] = _text(block, key, artifact, "states")
    return states


def _read_readability(value: JsonValue, artifact: str) -> ReadabilityRecord:
    block = _object(value, artifact, "readability")
    keys = (
        "luma_contrast",
        "silhouette_distinct",
        "luma_distinct",
        "luma_distinct_step",
        "quadrant_sign",
    )
    _exact_keys(block, keys, artifact, "readability")
    return ReadabilityRecord(
        luma_contrast=_pair_rules(block["luma_contrast"], artifact, "readability.luma_contrast"),
        silhouette_distinct=_pair_rules(
            block["silhouette_distinct"], artifact, "readability.silhouette_distinct"
        ),
        luma_distinct=_pair_rules(block["luma_distinct"], artifact, "readability.luma_distinct"),
        luma_distinct_step=_integer(block, "luma_distinct_step", artifact, "readability"),
        quadrant_sign=_sign_rules(block["quadrant_sign"], artifact, "readability.quadrant_sign"),
    )


def _pair_rules(value: JsonValue, artifact: str, field: str) -> tuple[tuple[str, str, int], ...]:
    rules: list[tuple[str, str, int]] = []
    for index, entry in enumerate(_array(value, artifact, field)):
        row = _array(entry, artifact, f"{field}[{index}]")
        if len(row) != 3 or not isinstance(row[0], str) or not isinstance(row[1], str):
            raise invalid(
                artifact, f"{field}[{index}]", "a rule is [frame name, frame name, threshold]"
            )
        rules.append((row[0], row[1], _whole(row[2], artifact, f"{field}[{index}]")))
    return tuple(rules)


def _sign_rules(value: JsonValue, artifact: str, field: str) -> tuple[tuple[str, int, int], ...]:
    rules: list[tuple[str, int, int]] = []
    for index, entry in enumerate(_array(value, artifact, field)):
        row = _array(entry, artifact, f"{field}[{index}]")
        if len(row) != 3 or not isinstance(row[0], str):
            raise invalid(artifact, f"{field}[{index}]", "a rule is [frame name, sign, magnitude]")
        sign = _whole(row[1], artifact, f"{field}[{index}]")
        if sign not in (-1, 1):
            raise invalid(artifact, f"{field}[{index}]", f"the sign must be -1 or 1, got {sign}")
        rules.append((row[0], sign, _whole(row[2], artifact, f"{field}[{index}]")))
    return tuple(rules)


def _exact_keys(
    block: Mapping[str, JsonValue], expected: Sequence[str], artifact: str, field: str
) -> None:
    present, wanted = set(block), set(expected)
    missing = sorted(wanted - present)
    unknown = sorted(present - wanted)
    if missing:
        raise invalid(artifact, field or "<document>", f"missing key(s): {', '.join(missing)}")
    if unknown:
        raise invalid(artifact, field or "<document>", f"unknown key(s): {', '.join(unknown)}")


def _object(value: JsonValue, artifact: str, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise invalid(artifact, field, "expected a JSON object")
    return value


def _array(value: JsonValue, artifact: str, field: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise invalid(artifact, field, "expected a JSON array")
    return value


def _text(block: Mapping[str, JsonValue], key: str, artifact: str, field: str) -> str:
    value = block[key]
    if not isinstance(value, str) or not value:
        raise invalid(artifact, f"{field}.{key}" if field else key, "expected a non-empty string")
    return value


def _text_list(value: JsonValue, artifact: str, field: str) -> tuple[str, ...]:
    entries = _array(value, artifact, field)
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            raise invalid(artifact, field, "every entry must be a non-empty string")
    return tuple(entry for entry in entries if isinstance(entry, str))


def _integer(block: Mapping[str, JsonValue], key: str, artifact: str, field: str) -> int:
    return _whole(block[key], artifact, f"{field}.{key}" if field else key)


def _whole(value: JsonValue, artifact: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid(artifact, field, "expected a whole number")
    return value


def _channel(value: JsonValue, artifact: str, field: str) -> int:
    channel = _whole(value, artifact, field)
    if not 0 <= channel <= 255:
        raise invalid(artifact, field, f"a colour channel must be 0..255, got {channel}")
    return channel


def _pair(value: JsonValue, artifact: str, field: str) -> tuple[int, int]:
    entries = _array(value, artifact, field)
    if len(entries) != 2:
        raise invalid(artifact, field, "expected two whole numbers")
    return _whole(entries[0], artifact, field), _whole(entries[1], artifact, field)


def _quad(value: JsonValue, artifact: str, field: str) -> tuple[int, int, int, int]:
    entries = _array(value, artifact, field)
    if len(entries) != 4:
        raise invalid(artifact, field, "expected four whole numbers")
    numbers = tuple(_whole(entry, artifact, field) for entry in entries)
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _digest(block: Mapping[str, JsonValue], key: str, artifact: str, field: str) -> str:
    value = _text(block, key, artifact, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise invalid(
            artifact,
            f"{field}.{key}" if field else key,
            "expected a 64-character lowercase hexadecimal SHA-256 digest",
        )
    return value


__all__ = [
    "ATLAS_FILENAME",
    "GENERATOR",
    "METADATA_FILENAME",
    "PACK_ID",
    "RENDER_KEYS",
    "SCHEMA_VERSION",
    "AnimationRecord",
    "AssetInvalid",
    "AtlasMetadata",
    "AtlasRecord",
    "FrameRecord",
    "LayoutRecord",
    "LicenseRecord",
    "ReadabilityRecord",
    "RenderSetting",
    "SourceRecord",
    "load",
    "serialize",
    "to_document",
]
