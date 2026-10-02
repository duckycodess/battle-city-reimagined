"""A small, strict PNG reader and writer built on :mod:`zlib`.

The pipeline owns both ends of the image format on purpose.

*Reading* is how a Blender render becomes pixels without an image library, so the
validator runs in CI with nothing installed beyond the standard library. The reader
accepts exactly what Blender is configured to write -- eight-bit, non-interlaced, RGB or
RGBA -- and rejects everything else by name. Guessing at a palette or a sixteen-bit
channel would turn a configuration mistake into art that silently looks wrong.

*Writing* is how checked-in bytes become a function of pixels. Every row is written with
filter type ``0``, so the compressed stream depends on the image and the zlib level and
on nothing else about the machine. Note the honest limit: zlib's output can still differ
between zlib builds, so the integrity recorded in metadata is a digest of the *pixels*
(:meth:`Image.digest`), never of the file bytes.
"""

from __future__ import annotations

import struct
import zlib
from typing import Final

from .errors import invalid
from .raster import CHANNELS, Image

SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"
COMPRESSION_LEVEL: Final[int] = 9
"""Fixed so a rebuild of unchanged pixels produces unchanged bytes on one zlib build."""

_COLOR_TYPE_NAMES: Final[dict[int, str]] = {
    0: "greyscale",
    2: "truecolour (RGB)",
    3: "indexed (palette)",
    4: "greyscale with alpha",
    6: "truecolour with alpha (RGBA)",
}
_SUPPORTED_COLOR_TYPES: Final[dict[int, int]] = {2: 3, 6: 4}


def encode_png(image: Image, *, level: int = COMPRESSION_LEVEL) -> bytes:
    """Serialise ``image`` as an eight-bit RGBA PNG with no ancillary chunks."""
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    row_bytes = image.width * CHANNELS
    raw = bytearray()
    for row in range(image.height):
        raw.append(0)
        raw += image.pixels[row * row_bytes : (row + 1) * row_bytes]
    return b"".join(
        (
            SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(bytes(raw), level)),
            _chunk(b"IEND", b""),
        )
    )


def decode_png(data: bytes, *, origin: str = "<bytes>") -> Image:
    """Read an eight-bit, non-interlaced RGB or RGBA PNG into an :class:`Image`."""
    if not data.startswith(SIGNATURE):
        raise invalid(origin, "", "not a PNG file: the eight-byte signature is missing")
    width, height, color_type, source_channels, idat = _read_chunks(data, origin)
    stride = width * source_channels
    try:
        raw = zlib.decompress(idat)
    except zlib.error as error:
        raise invalid(origin, "IDAT", f"the compressed image data is damaged: {error}") from error
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise invalid(
            origin,
            "IDAT",
            f"decompressed to {len(raw)} bytes where a {width}x{height} "
            f"{_COLOR_TYPE_NAMES[color_type]} image needs {expected}",
        )
    return _unfilter(raw, width, height, source_channels, origin)


def _read_chunks(data: bytes, origin: str) -> tuple[int, int, int, int, bytes]:
    position = len(SIGNATURE)
    header: tuple[int, int, int, int] | None = None
    parts: list[bytes] = []
    while position + 8 <= len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        if len(body) != length:
            raise invalid(origin, kind.decode("ascii", "replace"), "the chunk is truncated")
        position += 12 + length
        if kind == b"IHDR":
            header = _read_header(body, origin)
        elif kind == b"IDAT":
            parts.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise invalid(origin, "IHDR", "the header chunk is missing")
    if not parts:
        raise invalid(origin, "IDAT", "the file carries no image data")
    width, height, color_type, channels = header
    return width, height, color_type, channels, b"".join(parts)


def _read_header(body: bytes, origin: str) -> tuple[int, int, int, int]:
    if len(body) != 13:
        raise invalid(origin, "IHDR", f"the header is {len(body)} bytes where PNG defines 13")
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", body
    )
    if width <= 0 or height <= 0:
        raise invalid(origin, "IHDR", f"the image is {width}x{height}")
    if depth != 8:
        raise invalid(origin, "IHDR", f"only eight-bit channels are read, this file is {depth}-bit")
    if color_type not in _SUPPORTED_COLOR_TYPES:
        name = _COLOR_TYPE_NAMES.get(color_type, f"colour type {color_type}")
        raise invalid(origin, "IHDR", f"only RGB and RGBA are read, this file is {name}")
    if compression != 0 or filter_method != 0:
        raise invalid(
            origin,
            "IHDR",
            f"unknown compression {compression} or filter method {filter_method}",
        )
    if interlace != 0:
        raise invalid(origin, "IHDR", "interlaced images are not read; render progressive PNGs")
    return width, height, color_type, _SUPPORTED_COLOR_TYPES[color_type]


def _unfilter(raw: bytes, width: int, height: int, channels: int, origin: str) -> Image:
    stride = width * channels
    previous = bytearray(stride)
    out = bytearray(width * height * CHANNELS)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1 : position + 1 + stride])
        position += 1 + stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                upper_left = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, previous[index], upper_left)) & 0xFF
        else:
            raise invalid(origin, f"row {row}", f"unknown filter type {filter_type}")
        target = row * width * CHANNELS
        for column in range(width):
            source = column * channels
            index = target + column * CHANNELS
            out[index] = line[source]
            out[index + 1] = line[source + 1]
            out[index + 2] = line[source + 2]
            out[index + 3] = line[source + 3] if channels == 4 else 255
        previous = line
    return Image(width, height, bytes(out))


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distance_left = abs(estimate - left)
    distance_above = abs(estimate - above)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_above and distance_left <= distance_upper_left:
        return left
    if distance_above <= distance_upper_left:
        return above
    return upper_left


def _chunk(kind: bytes, body: bytes) -> bytes:
    payload = kind + body
    return struct.pack(">I", len(body)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
