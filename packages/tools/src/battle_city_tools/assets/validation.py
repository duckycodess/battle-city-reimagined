"""Check a shipped atlas against everything its sidecar claims about it.

This is the gate the art has to pass in continuous integration, and it runs with no
Blender, no GPU and no image library: it decodes the checked-in PNG with
:mod:`~battle_city_tools.assets.png` and re-derives every claim from the pixels.

It collects problems instead of stopping at the first one, because an atlas is rebuilt in
one step and an author wants the whole list. Each problem is an
:class:`~battle_city_tools.assets.errors.AssetDiagnostic` naming the artifact and the
field, so a failure reads as a location.

What is checked, and why each one is here:

* the sidecar parses at the version this reader understands, with no missing or unknown key
* the atlas file's dimensions and pixel digest are the ones recorded
* ``atlas.path`` names a file inside the pack, which is resolved before it is opened
* re-running the packer over the declared frame sizes reproduces the declared rectangles,
  which is what makes "deterministic packing" a fact rather than a sentence in a README
* no two frame rectangles overlap, and no opaque atlas pixel lies outside every rectangle
* every frame is the declared frame size, is not blank, and hashes to its recorded digest
* no two frames hold identical pixels, so a sprite that was never redrawn cannot ship
  twice under two names
* every pivot lies inside its frame, and every frame names a hitbox the sidecar defines
* every opaque pixel is a declared palette colour
* every frame's source identifier resolves, every source is used, and every source carries
  authorship and a licence value
* every animation and state mapping names a frame that exists
* the readability thresholds still hold on grey-scale pixels. These are the pack's own
  operational floors, not numbers any specification sets, and clearing them is evidence
  that faction, mirror lean, cover, damage and powerup kind survive without colour rather
  than proof of it or a conformance claim. :mod:`~battle_city_tools.assets.catalog` says
  where the values come from
* optionally, that all of the above still agrees with the shipped catalogue, which is what
  catches a sidecar that was hand-edited into agreeing with itself
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

from . import catalog as catalog_module
from .errors import AssetDiagnostic, AssetInvalid
from .layout import pack
from .metadata import METADATA_FILENAME, AtlasMetadata, load
from .palette import PALETTE
from .png import decode_png
from .raster import Image

KNOWN_ALGORITHMS: Final[frozenset[str]] = frozenset({"shelf-v1"})
KNOWN_SORT_KEYS: Final[frozenset[str]] = frozenset({"name"})


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Everything the validator looked at, and everything it found wrong."""

    artifact: str
    checks: tuple[str, ...]
    problems: tuple[AssetDiagnostic, ...]

    @property
    def ok(self) -> bool:
        """True when nothing is wrong."""
        return not self.problems

    def summary(self) -> str:
        """One line per problem, or a line saying how many checks passed."""
        if self.ok:
            return f"{self.artifact}: {len(self.checks)} checks passed"
        lines = [f"{self.artifact}: {len(self.problems)} problem(s)"]
        lines.extend(f"  {problem}" for problem in self.problems)
        return "\n".join(lines)


class _Collector:
    def __init__(self, artifact: str) -> None:
        self.artifact = artifact
        self.checks: list[str] = []
        self.problems: list[AssetDiagnostic] = []

    def check(self, name: str) -> None:
        self.checks.append(name)

    def fail(self, field: str, message: str) -> None:
        self.problems.append(AssetDiagnostic(artifact=self.artifact, field=field, message=message))

    def report(self) -> ValidationReport:
        return ValidationReport(
            artifact=self.artifact, checks=tuple(self.checks), problems=tuple(self.problems)
        )


def unowned_atlas_path(declared: str) -> str | None:
    """Why ``declared`` cannot name a file this pack owns, or ``None`` when it can.

    ``atlas.path`` is read out of a text file and then used to open one, so it decides
    which bytes the validator reports on. A pack owns the directory its sidecar sits in
    and nothing else, and this is the first half of holding it to that: a purely textual
    check that needs no filesystem and gives the same answer on every host.

    Host-independence is the reason a backslash is refused outright. ``..\\secrets.png``
    is one odd filename on Linux and an escape on Windows, so a sidecar carrying one does
    not mean the same thing in two checkouts; a path that means two things is not a
    contract. For the same reason both path flavours are asked whether the name is
    absolute, rather than only the one this interpreter happens to be running on.

    The second half -- that the name does not leave the directory *through a symlink* --
    cannot be answered from the text and is :func:`_atlas_file` 's job.
    """
    if "\\" in declared:
        return (
            "a backslash separates directories on Windows, so this name would not mean "
            "the same thing in every checkout"
        )
    windows, posix = PureWindowsPath(declared), PurePosixPath(declared)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return "an absolute path records one machine's layout and can point outside the pack"
    if ".." in posix.parts:
        return "a '..' component climbs out of the pack, which owns only its own directory"
    return None


def _atlas_file(directory: Path, declared: str) -> tuple[Path | None, str | None]:
    """Resolve ``declared`` inside ``directory``, or say why it may not be opened.

    Called before the image is read rather than after, and before the catalogue check is
    even considered, because ``--ignore-catalog`` asks the validator to skip a comparison
    against the shipped frame list -- not to open whatever file a sidecar names.
    """
    reason = unowned_atlas_path(declared)
    if reason is not None:
        return None, reason
    candidate = directory / declared
    try:
        inside = candidate.resolve().is_relative_to(directory.resolve())
    except OSError as error:  # pragma: no cover - needs an unreadable directory
        return None, f"the path cannot be resolved: {error}"
    if not inside:
        return None, (
            "the path leads outside the pack directory, which this pack does not own; "
            "a symbolic link in a pack is still a file the pack does not own"
        )
    return candidate, None


def validate_directory(directory: Path, *, require_catalog: bool = True) -> ValidationReport:
    """Load ``atlas.json`` and ``atlas.png`` from ``directory`` and check them together."""
    sidecar = directory / METADATA_FILENAME
    try:
        metadata = load(sidecar.read_bytes(), artifact=str(sidecar))
    except FileNotFoundError:
        return ValidationReport(
            artifact=str(sidecar),
            checks=(),
            problems=(AssetDiagnostic(str(sidecar), "", "the sidecar is missing"),),
        )
    except AssetInvalid as error:
        return ValidationReport(artifact=str(sidecar), checks=(), problems=(error.diagnostic,))

    atlas_path, refusal = _atlas_file(directory, metadata.atlas.path)
    if atlas_path is None:
        return ValidationReport(
            artifact=str(sidecar),
            checks=(),
            problems=(
                AssetDiagnostic(
                    str(sidecar),
                    "atlas.path",
                    f"{metadata.atlas.path!r} was not opened: {refusal}",
                ),
            ),
        )
    try:
        atlas = decode_png(atlas_path.read_bytes(), origin=str(atlas_path))
    except FileNotFoundError:
        return ValidationReport(
            artifact=str(sidecar),
            checks=(),
            problems=(AssetDiagnostic(str(atlas_path), "", "the atlas image is missing"),),
        )
    except AssetInvalid as error:
        return ValidationReport(artifact=str(sidecar), checks=(), problems=(error.diagnostic,))

    return validate(metadata, atlas, artifact=str(sidecar), require_catalog=require_catalog)


def validate(
    metadata: AtlasMetadata,
    atlas: Image,
    *,
    artifact: str = METADATA_FILENAME,
    require_catalog: bool = True,
) -> ValidationReport:
    """Check ``metadata`` against the pixels in ``atlas``."""
    collector = _Collector(artifact)
    _check_atlas(collector, metadata, atlas)
    _check_layout(collector, metadata)
    frames = _check_frames(collector, metadata, atlas)
    _check_duplicates(collector, metadata, frames)
    _check_palette(collector, metadata, frames)
    _check_sources(collector, metadata)
    _check_references(collector, metadata)
    _check_readability(collector, metadata, frames)
    if require_catalog:
        _check_catalog(collector, metadata)
    return collector.report()


def _check_atlas(collector: _Collector, metadata: AtlasMetadata, atlas: Image) -> None:
    collector.check("atlas dimensions")
    if (atlas.width, atlas.height) != (metadata.atlas.width, metadata.atlas.height):
        collector.fail(
            "atlas",
            f"the image is {atlas.width}x{atlas.height} where the sidecar declares "
            f"{metadata.atlas.width}x{metadata.atlas.height}",
        )
    collector.check("atlas pixel digest")
    digest = atlas.digest()
    if digest != metadata.atlas.sha256_pixels:
        collector.fail(
            "atlas.sha256_pixels",
            f"the image hashes to {digest}, the sidecar declares {metadata.atlas.sha256_pixels}",
        )
    collector.check("generator and pack identity")
    if not metadata.generator or not metadata.pack_id:
        collector.fail("generator", "the sidecar must name its generator and its pack")
    collector.check("the atlas path names a file inside the pack")
    unowned = unowned_atlas_path(metadata.atlas.path)
    if unowned is not None:
        collector.fail(
            "atlas.path", f"{metadata.atlas.path!r} is not a name this pack owns: {unowned}"
        )


def _check_layout(collector: _Collector, metadata: AtlasMetadata) -> None:
    collector.check("layout algorithm is one this validator can reproduce")
    if metadata.layout.algorithm not in KNOWN_ALGORITHMS:
        collector.fail(
            "layout.algorithm",
            f"this validator cannot reproduce {metadata.layout.algorithm!r}; "
            f"it knows {', '.join(sorted(KNOWN_ALGORITHMS))}",
        )
        return
    if metadata.layout.sort_key not in KNOWN_SORT_KEYS:
        collector.fail(
            "layout.sort_key",
            f"this validator cannot reproduce the sort key {metadata.layout.sort_key!r}",
        )
        return

    collector.check("declared rectangles equal a fresh deterministic packing")
    sizes = {frame.name: (frame.rect[2], frame.rect[3]) for frame in metadata.frames}
    try:
        repacked = pack(
            sizes, atlas_width=metadata.layout.atlas_width, padding=metadata.layout.padding
        )
    except ValueError as error:
        collector.fail("layout", f"the declared frames cannot be packed: {error}")
        return
    expected = {placement.name: placement.rect for placement in repacked.placements}
    for frame in metadata.frames:
        if frame.rect != expected[frame.name]:
            collector.fail(
                f"frames.{frame.name}.rect",
                f"packing puts this frame at {list(expected[frame.name])}, "
                f"the sidecar declares {list(frame.rect)}",
            )
    if (repacked.width, repacked.height) != (metadata.atlas.width, metadata.atlas.height):
        collector.fail(
            "atlas",
            f"packing produces a {repacked.width}x{repacked.height} sheet, "
            f"the sidecar declares {metadata.atlas.width}x{metadata.atlas.height}",
        )


def _check_frames(collector: _Collector, metadata: AtlasMetadata, atlas: Image) -> dict[str, Image]:
    collector.check("every frame lies inside the atlas and matches its recorded pixels")
    width, height = metadata.frame_size
    covered = bytearray(atlas.width * atlas.height)
    frames: dict[str, Image] = {}
    for frame in metadata.frames:
        field = f"frames.{frame.name}"
        x, y, frame_width, frame_height = frame.rect
        if (frame_width, frame_height) != (width, height):
            collector.fail(
                f"{field}.rect",
                f"the frame is {frame_width}x{frame_height} where the pack declares "
                f"{width}x{height}",
            )
            continue
        if x < 0 or y < 0 or x + frame_width > atlas.width or y + frame_height > atlas.height:
            collector.fail(f"{field}.rect", f"{list(frame.rect)} falls outside the atlas")
            continue
        for row in range(y, y + frame_height):
            for column in range(x, x + frame_width):
                index = row * atlas.width + column
                if covered[index]:
                    collector.fail(f"{field}.rect", "this rectangle overlaps another frame")
                    break
                covered[index] = 1
            else:
                continue
            break
        image = atlas.crop(x, y, frame_width, frame_height)
        frames[frame.name] = image
        digest = image.digest()
        if digest != frame.sha256_pixels:
            collector.fail(
                f"{field}.sha256_pixels",
                f"the pixels hash to {digest}, the sidecar declares {frame.sha256_pixels}",
            )
        bounds = image.alpha_bounds()
        if bounds is None:
            collector.fail(field, "the frame is entirely transparent")
        pivot_x, pivot_y = frame.pivot
        if not (0 <= pivot_x <= frame_width and 0 <= pivot_y <= frame_height):
            collector.fail(
                f"{field}.pivot",
                f"{list(frame.pivot)} lies outside a {frame_width}x{frame_height} frame",
            )
        if frame.hitbox not in metadata.hitboxes:
            collector.fail(
                f"{field}.hitbox", f"no hitbox named {frame.hitbox!r} is declared in the sidecar"
            )

    collector.check("no opaque atlas pixel sits outside every frame")
    stray = 0
    for index in range(atlas.width * atlas.height):
        if covered[index] == 0 and atlas.pixels[index * 4 + 3] != 0:
            stray += 1
    if stray:
        collector.fail(
            "atlas", f"{stray} opaque pixel(s) lie outside every declared frame rectangle"
        )
    return frames


def _check_duplicates(
    collector: _Collector, metadata: AtlasMetadata, frames: dict[str, Image]
) -> None:
    collector.check("no two frames hold identical pixels")
    by_digest: dict[str, list[str]] = {}
    for name, image in frames.items():
        by_digest.setdefault(image.digest(), []).append(name)
    for digest, names in sorted(by_digest.items()):
        if len(names) > 1:
            collector.fail(
                "frames",
                f"{', '.join(sorted(names))} are pixel-for-pixel identical ({digest[:12]})",
            )
    del metadata


def _check_palette(
    collector: _Collector, metadata: AtlasMetadata, frames: dict[str, Image]
) -> None:
    collector.check("every opaque pixel is a declared palette colour")
    allowed = set(metadata.palette_colors)
    for name in sorted(frames):
        extra = sorted(frames[name].colors() - allowed)
        if extra:
            shown = ", ".join(str(list(color)) for color in extra[:4])
            collector.fail(
                f"frames.{name}",
                f"{len(extra)} colour(s) outside the declared palette, including {shown}",
            )


def _check_sources(collector: _Collector, metadata: AtlasMetadata) -> None:
    collector.check("every frame's source resolves, and every source is used")
    known = {source.source_id: source for source in metadata.sources}
    used: set[str] = set()
    for frame in metadata.frames:
        if frame.source_id not in known:
            collector.fail(
                f"frames.{frame.name}.source_id",
                f"no source named {frame.source_id!r} is declared in the sidecar",
            )
            continue
        used.add(frame.source_id)
    for source_id in sorted(set(known) - used):
        collector.fail("sources", f"the source {source_id!r} is declared but no frame uses it")

    collector.check("every source carries authorship and a licence value")
    for source in metadata.sources:
        if not source.authors:
            collector.fail(f"sources.{source.source_id}.authors", "no author is named")
        if not source.spdx_id:
            collector.fail(f"sources.{source.source_id}.spdx_id", "no licence value is declared")
        if not source.scene or not source.build_script or not source.render_script:
            collector.fail(
                f"sources.{source.source_id}",
                "the scene and both scripts must be named so a frame can be reproduced",
            )
    if not metadata.authors or not metadata.license.spdx_id or not metadata.license.notice:
        collector.fail("license", "the pack must declare authors, an SPDX value and a notice")


def _check_references(collector: _Collector, metadata: AtlasMetadata) -> None:
    collector.check("animations and state mappings name frames that exist")
    names = set(metadata.frame_names)
    for animation in metadata.animations:
        for frame_name in animation.frames:
            if frame_name not in names:
                collector.fail(
                    f"animations.{animation.name}",
                    f"the sequence names {frame_name!r}, which is not a frame in this atlas",
                )
        if len(set(animation.frames)) != len(animation.frames):
            collector.fail(
                f"animations.{animation.name}", "the sequence repeats a frame within itself"
            )
    for state, frame_name in sorted(metadata.states.items()):
        if frame_name not in names:
            collector.fail(
                f"states.{state}",
                f"the state maps to {frame_name!r}, which is not a frame in this atlas",
            )


def _mean_luma(image: Image) -> int:
    values = image.luma()
    return sum(values) // len(values)


def _differing(first: bytes, second: bytes, step: int) -> int:
    return sum(1 for a, b in zip(first, second, strict=True) if abs(a - b) >= step)


def _quadrant_metric(image: Image) -> int:
    half_x, half_y = image.width // 2, image.height // 2
    left = image.crop(0, 0, half_x, half_y)
    right = image.crop(half_x, 0, image.width - half_x, half_y)
    return _mean_luma(right) - _mean_luma(left)


def _resolve(
    collector: _Collector, frames: dict[str, Image], field: str, names: Iterable[str]
) -> list[Image] | None:
    resolved: list[Image] = []
    for name in names:
        image = frames.get(name)
        if image is None:
            collector.fail(field, f"the rule names {name!r}, which is not a frame in this atlas")
            return None
        resolved.append(image)
    return resolved


def _check_readability(
    collector: _Collector, metadata: AtlasMetadata, frames: dict[str, Image]
) -> None:
    rules = metadata.readability

    collector.check("declared pairs stay apart in mean luma")
    for first, second, threshold in rules.luma_contrast:
        field = f"readability.luma_contrast.{first}:{second}"
        images = _resolve(collector, frames, field, (first, second))
        if images is None:
            continue
        delta = abs(_mean_luma(images[0]) - _mean_luma(images[1]))
        if delta < threshold:
            collector.fail(
                field,
                f"mean luma differs by {delta}, below the declared minimum of {threshold}; "
                "these two must be distinguishable without colour",
            )

    collector.check("declared pairs have different silhouettes")
    for first, second, threshold in rules.silhouette_distinct:
        field = f"readability.silhouette_distinct.{first}:{second}"
        images = _resolve(collector, frames, field, (first, second))
        if images is None:
            continue
        area = images[0].width * images[0].height
        if (images[1].width * images[1].height) != area:
            collector.fail(field, "the two frames are not the same size")
            continue
        differing = _differing(images[0].alpha_mask(), images[1].alpha_mask(), 1)
        permille = differing * 1000 // area
        if permille < threshold:
            collector.fail(
                field,
                f"the opaque silhouettes differ on {permille} pixels per thousand, "
                f"below the declared minimum of {threshold}",
            )

    collector.check("declared pairs differ in grey scale")
    for first, second, threshold in rules.luma_distinct:
        field = f"readability.luma_distinct.{first}:{second}"
        images = _resolve(collector, frames, field, (first, second))
        if images is None:
            continue
        area = images[0].width * images[0].height
        if (images[1].width * images[1].height) != area:
            collector.fail(field, "the two frames are not the same size")
            continue
        differing = _differing(images[0].luma(), images[1].luma(), rules.luma_distinct_step)
        permille = differing * 1000 // area
        if permille < threshold:
            collector.fail(
                field,
                f"grey scale differs on {permille} pixels per thousand, "
                f"below the declared minimum of {threshold}",
            )

    collector.check("mirrors lean the way the deflection table says they do")
    for name, sign, magnitude in rules.quadrant_sign:
        field = f"readability.quadrant_sign.{name}"
        images = _resolve(collector, frames, field, (name,))
        if images is None:
            continue
        metric = _quadrant_metric(images[0])
        if metric * sign < magnitude:
            collector.fail(
                field,
                f"top-right minus top-left luma is {metric}, which does not lean "
                f"{'right' if sign > 0 else 'left'} by at least {magnitude}",
            )


def _check_catalog(collector: _Collector, metadata: AtlasMetadata) -> None:
    collector.check("the sidecar still agrees with the shipped catalogue")
    expected = catalog_module.FRAME_NAMES
    if metadata.frame_names != expected:
        missing = sorted(set(expected) - set(metadata.frame_names))
        extra = sorted(set(metadata.frame_names) - set(expected))
        if missing:
            collector.fail("frames", f"the catalogue declares frames the atlas lacks: {missing}")
        if extra:
            collector.fail("frames", f"the atlas holds frames the catalogue does not: {extra}")
        if not missing and not extra:
            collector.fail("frames", "the atlas lists the catalogue's frames in a different order")
        return
    if metadata.frame_size != catalog_module.FRAME_SIZE:
        collector.fail(
            "frame_size",
            f"the catalogue declares {list(catalog_module.FRAME_SIZE)}, "
            f"the sidecar declares {list(metadata.frame_size)}",
        )
    for frame in metadata.frames:
        spec = catalog_module.FRAMES_BY_NAME[frame.name]
        field = f"frames.{frame.name}"
        if frame.pivot != spec.pivot:
            collector.fail(
                f"{field}.pivot",
                f"the catalogue declares {list(spec.pivot)}, the sidecar declares "
                f"{list(frame.pivot)}",
            )
        if frame.hitbox != spec.hitbox:
            collector.fail(
                f"{field}.hitbox",
                f"the catalogue declares {spec.hitbox!r}, the sidecar declares {frame.hitbox!r}",
            )
        if frame.group != spec.group:
            collector.fail(
                f"{field}.group",
                f"the catalogue declares {spec.group!r}, the sidecar declares {frame.group!r}",
            )
        if frame.source_id != spec.source_id:
            collector.fail(
                f"{field}.source_id",
                f"the catalogue declares {spec.source_id!r}, the sidecar declares "
                f"{frame.source_id!r}",
            )
    if metadata.palette != PALETTE:
        collector.fail("palette", "the sidecar's palette is not the shipped palette")
    expected_hitboxes = {
        key: value for key, value in catalog_module.HITBOX_SIZES.items() if value is not None
    }
    for key, size in expected_hitboxes.items():
        if metadata.hitboxes.get(key) != size:
            collector.fail(
                f"hitboxes.{key}",
                f"the catalogue declares {list(size)}, the sidecar declares "
                f"{metadata.hitboxes.get(key)}",
            )


__all__ = ["ValidationReport", "validate", "validate_directory"]
