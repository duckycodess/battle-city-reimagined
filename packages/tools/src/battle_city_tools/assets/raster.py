"""Immutable RGBA rasters and the pixel operations the pipeline is allowed to perform.

Everything downstream of Blender happens here, in the standard library, so that a render
can be turned into checked-in pixels on a machine with no GPU and no image library. Two
properties matter more than speed.

*The operations are total and deterministic.* Downsampling averages a fixed block,
compositing is a fixed formula, and quantisation breaks ties by palette order. The same
render always produces the same atlas; no step consults a clock, a locale, or the
iteration order of a set.

*Transparent pixels have one spelling.* A fully transparent pixel is ``(0, 0, 0, 0)``
after any operation here. Blender writes colour under zero alpha, and leaving it there
would make two visually identical frames hash differently, which would then be reported
as a duplicate-frame failure that nobody can see.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

Rgb = tuple[int, int, int]
Rgba = tuple[int, int, int, int]

CHANNELS: Final[int] = 4
TRANSPARENT: Final[Rgba] = (0, 0, 0, 0)
OPAQUE_ALPHA: Final[int] = 255
ALPHA_THRESHOLD: Final[int] = 128
"""Coverage at or above this becomes fully opaque; below it becomes fully transparent.

Sprite edges are binary on purpose. A 16-pixel sprite drawn with partial coverage reads
as a smear at gameplay scale, and partial alpha would also make "every opaque pixel is a
palette colour" unenforceable, because a blended edge is by definition not a palette
entry.
"""


def luminance(red: int, green: int, blue: int) -> int:
    """Rec. 601 luma, rounded to an integer, for colour-independent readability checks."""
    return (299 * red + 587 * green + 114 * blue + 500) // 1000


@dataclass(frozen=True, slots=True)
class Image:
    """A width x height RGBA raster stored row-major, eight bits per channel."""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"image dimensions must be positive, got {self.width}x{self.height}")
        expected = self.width * self.height * CHANNELS
        if len(self.pixels) != expected:
            raise ValueError(
                f"image of {self.width}x{self.height} needs {expected} bytes, "
                f"got {len(self.pixels)}"
            )

    @property
    def size(self) -> tuple[int, int]:
        """``(width, height)``."""
        return self.width, self.height

    def pixel(self, x: int, y: int) -> Rgba:
        """The pixel at ``(x, y)``, with ``(0, 0)`` at the top left."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x}, {y}) is outside a {self.width}x{self.height} image")
        start = (y * self.width + x) * CHANNELS
        red, green, blue, alpha = self.pixels[start : start + CHANNELS]
        return red, green, blue, alpha

    def crop(self, x: int, y: int, width: int, height: int) -> Image:
        """The sub-rectangle at ``(x, y)``. The rectangle must lie inside the image."""
        if width <= 0 or height <= 0:
            raise ValueError(f"crop size must be positive, got {width}x{height}")
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError(
                f"crop {width}x{height} at ({x}, {y}) leaves a {self.width}x{self.height} image"
            )
        out = bytearray(width * height * CHANNELS)
        row_bytes = width * CHANNELS
        for row in range(height):
            start = ((y + row) * self.width + x) * CHANNELS
            out[row * row_bytes : (row + 1) * row_bytes] = self.pixels[start : start + row_bytes]
        return Image(width, height, bytes(out))

    def alpha_bounds(self) -> tuple[int, int, int, int] | None:
        """The tightest ``(x, y, width, height)`` holding every non-transparent pixel.

        ``None`` when the image is entirely transparent, which the validator reports as a
        blank frame rather than as an empty rectangle.
        """
        left, top = self.width, self.height
        right, bottom = -1, -1
        for y in range(self.height):
            row = y * self.width
            for x in range(self.width):
                if self.pixels[(row + x) * CHANNELS + 3] == 0:
                    continue
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
        if right < 0:
            return None
        return left, top, right - left + 1, bottom - top + 1

    def colors(self) -> frozenset[Rgb]:
        """Every colour carried by a non-transparent pixel."""
        found: set[Rgb] = set()
        for index in range(0, len(self.pixels), CHANNELS):
            if self.pixels[index + 3] == 0:
                continue
            found.add((self.pixels[index], self.pixels[index + 1], self.pixels[index + 2]))
        return frozenset(found)

    def alpha_mask(self) -> bytes:
        """One byte per pixel: ``1`` where the pixel is not transparent, ``0`` where it is."""
        return bytes(
            1 if self.pixels[index + 3] else 0 for index in range(0, len(self.pixels), CHANNELS)
        )

    def luma(self) -> bytes:
        """One luma byte per pixel. Transparent pixels contribute ``0``."""
        out = bytearray(self.width * self.height)
        for position in range(self.width * self.height):
            index = position * CHANNELS
            if self.pixels[index + 3] == 0:
                continue
            out[position] = luminance(
                self.pixels[index], self.pixels[index + 1], self.pixels[index + 2]
            )
        return bytes(out)

    def digest(self) -> str:
        """A content hash over the dimensions and the pixels, as lowercase hex.

        The dimensions are hashed too, so a 2x8 frame and an 8x2 frame holding the same
        bytes are not reported as duplicates of one another.
        """
        hasher = hashlib.sha256()
        hasher.update(f"{self.width}x{self.height}:".encode("ascii"))
        hasher.update(self.pixels)
        return hasher.hexdigest()

    def downsample(self, factor: int) -> Image:
        """Average ``factor`` x ``factor`` blocks, weighting colour by coverage.

        Colour is averaged in premultiplied form and then un-premultiplied, so a sprite
        edge takes the colour of the sprite rather than being dragged toward the
        transparent black it was rendered against. Coverage is then snapped to
        :data:`ALPHA_THRESHOLD`, which is what makes the result read as pixel art instead
        of as a blurred photograph of pixel art.
        """
        if factor <= 0:
            raise ValueError(f"downsample factor must be positive, got {factor}")
        if self.width % factor or self.height % factor:
            raise ValueError(
                f"a {self.width}x{self.height} image is not divisible by a factor of {factor}"
            )
        width, height = self.width // factor, self.height // factor
        out = bytearray(width * height * CHANNELS)
        block = factor * factor
        for y in range(height):
            for x in range(width):
                red = green = blue = alpha = 0
                for sub_y in range(factor):
                    row = (y * factor + sub_y) * self.width + x * factor
                    for sub_x in range(factor):
                        index = (row + sub_x) * CHANNELS
                        coverage = self.pixels[index + 3]
                        red += self.pixels[index] * coverage
                        green += self.pixels[index + 1] * coverage
                        blue += self.pixels[index + 2] * coverage
                        alpha += coverage
                target = (y * width + x) * CHANNELS
                if alpha < block * ALPHA_THRESHOLD:
                    continue
                out[target] = (red + alpha // 2) // alpha
                out[target + 1] = (green + alpha // 2) // alpha
                out[target + 2] = (blue + alpha // 2) // alpha
                out[target + 3] = OPAQUE_ALPHA
        return Image(width, height, bytes(out))

    def scaled(self, factor: int) -> Image:
        """Repeat every pixel ``factor`` times in each direction. Nearest neighbour only.

        Pixel art is enlarged by repetition or not at all: any smoothing would invent
        colours that are not in the palette and soften the silhouettes the pack is
        checked against.
        """
        if factor <= 0:
            raise ValueError(f"scale factor must be positive, got {factor}")
        if factor == 1:
            return self
        width, height = self.width * factor, self.height * factor
        out = bytearray(width * height * CHANNELS)
        for y in range(self.height):
            row = bytearray()
            for x in range(self.width):
                index = (y * self.width + x) * CHANNELS
                row += self.pixels[index : index + CHANNELS] * factor
            for repeat in range(factor):
                start = ((y * factor + repeat) * width) * CHANNELS
                out[start : start + len(row)] = row
        return Image(width, height, bytes(out))

    def quantized(self, palette: tuple[Rgb, ...]) -> Image:
        """Snap every opaque pixel to its nearest palette colour.

        Distance is squared Euclidean in sRGB and ties go to the earlier palette entry, so
        the mapping is a pure function of the palette's declared order. Quantisation is
        what lets the validator state, and check, that the atlas uses one declared palette.
        """
        if not palette:
            raise ValueError("a palette must hold at least one colour")
        out = bytearray(self.pixels)
        cache: dict[Rgb, Rgb] = {}
        for index in range(0, len(out), CHANNELS):
            if out[index + 3] == 0:
                out[index] = out[index + 1] = out[index + 2] = 0
                continue
            key = (out[index], out[index + 1], out[index + 2])
            nearest = cache.get(key)
            if nearest is None:
                nearest = _nearest(key, palette)
                cache[key] = nearest
            out[index], out[index + 1], out[index + 2] = nearest
        return Image(self.width, self.height, bytes(out))


def _nearest(color: Rgb, palette: tuple[Rgb, ...]) -> Rgb:
    red, green, blue = color
    best = palette[0]
    best_distance = -1
    for candidate in palette:
        delta = (candidate[0] - red) ** 2 + (candidate[1] - green) ** 2 + (candidate[2] - blue) ** 2
        if best_distance < 0 or delta < best_distance:
            best, best_distance = candidate, delta
            if delta == 0:
                break
    return best


class Canvas:
    """A mutable raster that composites sprites and then freezes into an :class:`Image`.

    Compositing is source-over with binary alpha: a source pixel either replaces the
    destination or leaves it alone. The pipeline never blends, so an atlas, a preview and
    a composite are all exactly the pixels the frames carry.
    """

    def __init__(self, width: int, height: int, fill: Rgba = TRANSPARENT) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"canvas dimensions must be positive, got {width}x{height}")
        self.width = width
        self.height = height
        self._pixels = bytearray(bytes(fill) * (width * height))

    def set_pixel(self, x: int, y: int, color: Rgba) -> None:
        """Write one pixel. Coordinates outside the canvas are ignored."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        index = (y * self.width + x) * CHANNELS
        self._pixels[index : index + CHANNELS] = bytes(color)

    def fill_rect(self, x: int, y: int, width: int, height: int, color: Rgba) -> None:
        """Paint a solid rectangle, clipped to the canvas."""
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.set_pixel(column, row, color)

    def blit(self, image: Image, x: int, y: int) -> None:
        """Composite ``image`` with its top left at ``(x, y)``, clipped to the canvas."""
        for row in range(image.height):
            target_y = y + row
            if not 0 <= target_y < self.height:
                continue
            for column in range(image.width):
                target_x = x + column
                if not 0 <= target_x < self.width:
                    continue
                index = (row * image.width + column) * CHANNELS
                if image.pixels[index + 3] == 0:
                    continue
                target = (target_y * self.width + target_x) * CHANNELS
                self._pixels[target : target + CHANNELS] = image.pixels[index : index + CHANNELS]

    def freeze(self) -> Image:
        """The finished raster."""
        return Image(self.width, self.height, bytes(self._pixels))
