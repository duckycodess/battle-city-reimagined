"""Pixel operations: the three that decide what a render becomes.

Downsampling, the binary alpha snap and palette quantisation are the whole of the
rendering-to-pixel-art step, and each one has a property worth pinning down. Coverage has
to weight colour or sprite edges go grey; transparency has to come out with one spelling
or two identical-looking frames hash differently; and quantisation has to break ties the
same way every time or the atlas depends on dictionary order.
"""

from __future__ import annotations

import pytest
from battle_city_tools.assets.palette import PALETTE_COLORS
from battle_city_tools.assets.raster import ALPHA_THRESHOLD, Canvas, Image, luminance


def _solid(width: int, height: int, color: tuple[int, int, int, int]) -> Image:
    canvas = Canvas(width, height, color)
    return canvas.freeze()


def test_an_image_rejects_a_pixel_buffer_of_the_wrong_length() -> None:
    with pytest.raises(ValueError, match="needs 16 bytes"):
        Image(2, 2, bytes(12))


def test_downsampling_averages_a_block() -> None:
    canvas = Canvas(2, 2)
    canvas.set_pixel(0, 0, (100, 0, 0, 255))
    canvas.set_pixel(1, 0, (200, 0, 0, 255))
    canvas.set_pixel(0, 1, (100, 0, 0, 255))
    canvas.set_pixel(1, 1, (200, 0, 0, 255))
    assert canvas.freeze().downsample(2).pixel(0, 0) == (150, 0, 0, 255)


def test_downsampling_weights_colour_by_coverage_not_by_pixel_count() -> None:
    """A partly covered edge keeps the sprite's colour instead of being dragged to black."""
    canvas = Canvas(2, 2)
    for x, y in ((0, 0), (1, 0), (0, 1)):
        canvas.set_pixel(x, y, (240, 240, 240, 255))
    canvas.set_pixel(1, 1, (0, 0, 0, 0))
    assert canvas.freeze().downsample(2).pixel(0, 0) == (240, 240, 240, 255)


def test_coverage_below_the_threshold_becomes_fully_transparent() -> None:
    canvas = Canvas(2, 2)
    canvas.set_pixel(0, 0, (255, 255, 255, 255))
    canvas.set_pixel(1, 0, (255, 255, 255, ALPHA_THRESHOLD * 2 - 1))
    assert canvas.freeze().downsample(2).pixel(0, 0) == (0, 0, 0, 0)


def test_coverage_exactly_at_the_threshold_becomes_fully_opaque() -> None:
    """Mean coverage of the block, not a majority of its pixels, decides."""
    canvas = Canvas(2, 2)
    canvas.set_pixel(0, 0, (255, 255, 255, 255))
    canvas.set_pixel(1, 0, (255, 255, 255, 255))
    canvas.set_pixel(0, 1, (255, 255, 255, 2))
    assert canvas.freeze().downsample(2).pixel(0, 0)[3] == 255


def test_downsampling_refuses_a_size_that_does_not_divide() -> None:
    with pytest.raises(ValueError, match="not divisible"):
        _solid(5, 5, (1, 2, 3, 255)).downsample(2)


def test_quantising_snaps_to_the_nearest_palette_colour() -> None:
    near = PALETTE_COLORS[5]
    nudged = (near[0] + 2, near[1], near[2])
    image = _solid(1, 1, (*nudged, 255))
    assert image.quantized(PALETTE_COLORS).pixel(0, 0) == (*near, 255)


def test_quantising_breaks_a_tie_by_palette_order() -> None:
    palette = ((100, 100, 100), (10, 0, 0), (0, 10, 0))
    midpoint = _solid(1, 1, (5, 5, 0, 255)).quantized(palette).pixel(0, 0)
    assert midpoint == (10, 0, 0, 255)


def test_quantising_gives_every_transparent_pixel_one_spelling() -> None:
    image = _solid(1, 1, (200, 10, 10, 0))
    assert image.quantized(PALETTE_COLORS).pixel(0, 0) == (0, 0, 0, 0)


def test_a_digest_covers_the_dimensions_as_well_as_the_pixels() -> None:
    wide = Image(4, 1, bytes(16))
    tall = Image(1, 4, bytes(16))
    assert wide.pixels == tall.pixels
    assert wide.digest() != tall.digest()


def test_alpha_bounds_are_the_tightest_box_holding_the_art() -> None:
    canvas = Canvas(8, 8)
    canvas.fill_rect(2, 3, 3, 2, (255, 255, 255, 255))
    assert canvas.freeze().alpha_bounds() == (2, 3, 3, 2)


def test_a_blank_image_has_no_alpha_bounds() -> None:
    assert _solid(4, 4, (0, 0, 0, 0)).alpha_bounds() is None


def test_luma_counts_a_transparent_pixel_as_black() -> None:
    canvas = Canvas(2, 1)
    canvas.set_pixel(0, 0, (255, 255, 255, 255))
    values = canvas.freeze().luma()
    assert values == bytes((luminance(255, 255, 255), 0))


def test_scaling_repeats_pixels_and_never_invents_a_colour() -> None:
    canvas = Canvas(2, 1)
    canvas.set_pixel(0, 0, (10, 20, 30, 255))
    canvas.set_pixel(1, 0, (200, 210, 220, 255))
    scaled = canvas.freeze().scaled(3)
    assert scaled.size == (6, 3)
    assert scaled.colors() == {(10, 20, 30), (200, 210, 220)}
    assert scaled.pixel(2, 2) == (10, 20, 30, 255)
    assert scaled.pixel(3, 0) == (200, 210, 220, 255)


def test_compositing_leaves_the_destination_alone_under_a_transparent_pixel() -> None:
    canvas = Canvas(2, 1, (9, 9, 9, 255))
    canvas.blit(Image(2, 1, bytes((1, 2, 3, 255, 0, 0, 0, 0))), 0, 0)
    assert canvas.freeze().pixel(0, 0) == (1, 2, 3, 255)
    assert canvas.freeze().pixel(1, 0) == (9, 9, 9, 255)


def test_compositing_clips_instead_of_wrapping() -> None:
    canvas = Canvas(2, 2)
    canvas.blit(_solid(2, 2, (5, 5, 5, 255)), 1, 1)
    assert canvas.freeze().pixel(0, 0) == (0, 0, 0, 0)
    assert canvas.freeze().pixel(1, 1) == (5, 5, 5, 255)


def test_cropping_outside_the_image_is_refused() -> None:
    with pytest.raises(ValueError, match="leaves a"):
        _solid(4, 4, (1, 1, 1, 255)).crop(2, 2, 4, 4)
