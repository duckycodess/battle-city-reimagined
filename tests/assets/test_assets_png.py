"""The standard-library PNG codec: round trips, every filter type, and loud refusals.

The decoder is the one genuinely fiddly piece in this pipeline, because it has to undo
whatever adaptive filtering the encoder chose. These tests drive all five filter types
explicitly rather than hoping a sample image happened to use them, and they check that the
formats the pipeline does not read are refused by name instead of being guessed at.
"""

from __future__ import annotations

import struct
import zlib

import pytest
from assets_helpers import shipped_atlas
from battle_city_tools.assets.errors import AssetInvalid
from battle_city_tools.assets.png import SIGNATURE, decode_png, encode_png
from battle_city_tools.assets.raster import Canvas, Image


def _sample() -> Image:
    canvas = Canvas(7, 5)
    for y in range(5):
        for x in range(7):
            canvas.set_pixel(x, y, (x * 30 % 256, y * 50 % 256, (x + y) * 20 % 256, 255))
    canvas.set_pixel(3, 2, (0, 0, 0, 0))
    return canvas.freeze()


def _png(width: int, height: int, raw: bytes, *, depth: int = 8, color: int = 6,
         interlace: int = 0) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)

    def chunk(kind: bytes, body: bytes) -> bytes:
        payload = kind + body
        return (
            struct.pack(">I", len(body))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    return b"".join(
        (SIGNATURE, chunk(b"IHDR", header), chunk(b"IDAT", zlib.compress(raw)), chunk(b"IEND", b""))
    )


def test_a_png_round_trips_through_the_codec() -> None:
    image = _sample()
    assert decode_png(encode_png(image)) == image


def test_the_checked_in_atlas_round_trips_without_changing_a_pixel() -> None:
    atlas = shipped_atlas()
    assert decode_png(encode_png(atlas)) == atlas


def test_the_same_pixels_always_encode_to_the_same_bytes() -> None:
    image = _sample()
    assert encode_png(image) == encode_png(image)


@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_every_filter_type_decodes_to_the_same_pixels(filter_type: int) -> None:
    """Refilter a known image by hand and check the decoder undoes exactly that filter."""
    image = _sample()
    stride = image.width * 4
    rows = [image.pixels[y * stride : (y + 1) * stride] for y in range(image.height)]
    raw = bytearray()
    previous = bytes(stride)
    for row in rows:
        raw.append(filter_type)
        encoded = bytearray(stride)
        for index in range(stride):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 0:
                encoded[index] = row[index]
            elif filter_type == 1:
                encoded[index] = (row[index] - left) & 0xFF
            elif filter_type == 2:
                encoded[index] = (row[index] - above) & 0xFF
            elif filter_type == 3:
                encoded[index] = (row[index] - ((left + above) >> 1)) & 0xFF
            else:
                estimate = left + above - upper_left
                if abs(estimate - left) <= abs(estimate - above) and abs(
                    estimate - left
                ) <= abs(estimate - upper_left):
                    predictor = left
                elif abs(estimate - above) <= abs(estimate - upper_left):
                    predictor = above
                else:
                    predictor = upper_left
                encoded[index] = (row[index] - predictor) & 0xFF
        raw += encoded
        previous = row
    assert decode_png(_png(image.width, image.height, bytes(raw))) == image


def test_an_rgb_png_without_alpha_decodes_as_fully_opaque() -> None:
    raw = bytearray()
    for y in range(2):
        raw.append(0)
        for x in range(3):
            raw += bytes((x * 10, y * 20, 30))
    image = decode_png(_png(3, 2, bytes(raw), color=2))
    assert image.pixel(2, 1) == (20, 20, 30, 255)


def test_a_file_without_the_signature_is_refused() -> None:
    with pytest.raises(AssetInvalid, match="signature"):
        decode_png(b"not a png at all")


def test_an_interlaced_png_is_refused_by_name() -> None:
    with pytest.raises(AssetInvalid, match="interlaced"):
        decode_png(_png(2, 2, bytes(2 * (1 + 8)), interlace=1))


def test_a_sixteen_bit_png_is_refused_by_name() -> None:
    with pytest.raises(AssetInvalid, match="eight-bit"):
        decode_png(_png(1, 1, bytes(1 + 8), depth=16))


def test_a_palette_png_is_refused_by_name() -> None:
    with pytest.raises(AssetInvalid, match="indexed"):
        decode_png(_png(1, 1, bytes(2), color=3))


def test_a_greyscale_png_is_refused_by_name() -> None:
    with pytest.raises(AssetInvalid, match="greyscale"):
        decode_png(_png(1, 1, bytes(2), color=0))


def test_an_unknown_filter_type_is_refused() -> None:
    raw = bytes((9,)) + bytes(4)
    with pytest.raises(AssetInvalid, match="filter type 9"):
        decode_png(_png(1, 1, raw))


def test_a_short_image_stream_is_refused() -> None:
    with pytest.raises(AssetInvalid, match="decompressed to"):
        decode_png(_png(4, 4, bytes(5)))


def test_damaged_compressed_data_is_refused() -> None:
    good = encode_png(_sample())
    damaged = bytearray(good)
    start = good.index(b"IDAT") + 4
    damaged[start : start + 6] = b"\x00\x00\x00\x00\x00\x00"
    with pytest.raises(AssetInvalid, match="damaged"):
        decode_png(bytes(damaged))


def test_a_truncated_chunk_is_refused() -> None:
    good = encode_png(_sample())
    with pytest.raises(AssetInvalid, match="truncated"):
        decode_png(good[: len(good) - 40])


def test_a_file_with_no_image_data_is_refused() -> None:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, body: bytes) -> bytes:
        payload = kind + body
        return (
            struct.pack(">I", len(body))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    with pytest.raises(AssetInvalid, match="no image data"):
        decode_png(SIGNATURE + chunk(b"IHDR", header) + chunk(b"IEND", b""))
