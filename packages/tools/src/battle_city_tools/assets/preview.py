"""The tracked contact sheet: every frame, enlarged, labelled, with its metadata beside it.

The atlas itself is 128 pixels wide and unreadable at a glance, so the reviewable artifact
is this sheet. It is generated from the atlas and the sidecar, never drawn by hand, so it
cannot describe a frame the pack does not actually contain.

Transparency is drawn as a checkerboard. A sprite's silhouette is half of what the pack
promises -- the readability rules are mostly about shape -- and a preview on a flat
background hides exactly the mistake a reviewer is looking for.
"""

from __future__ import annotations

from typing import Final

from .font import GLYPH_HEIGHT, draw_text
from .metadata import AtlasMetadata
from .raster import Canvas, Image, Rgba

SWATCH_SCALE: Final[int] = 3
COLUMNS: Final[int] = 2
MARGIN: Final[int] = 8
GUTTER: Final[int] = 10
TEXT_WIDTH: Final[int] = 224
HEADER_HEIGHT: Final[int] = 30

BACKGROUND: Final[Rgba] = (18, 20, 27, 255)
CHECKER_LIGHT: Final[Rgba] = (58, 62, 74, 255)
CHECKER_DARK: Final[Rgba] = (44, 48, 58, 255)
BORDER: Final[Rgba] = (90, 98, 118, 255)
TITLE: Final[Rgba] = (238, 244, 252, 255)
LABEL: Final[Rgba] = (226, 232, 240, 255)
DETAIL: Final[Rgba] = (134, 146, 168, 255)


def build_preview(metadata: AtlasMetadata, atlas: Image) -> Image:
    """A labelled contact sheet of every frame in ``metadata``."""
    frame_width, frame_height = metadata.frame_size
    swatch_width = frame_width * SWATCH_SCALE
    swatch_height = frame_height * SWATCH_SCALE
    entry_width = swatch_width + GUTTER + TEXT_WIDTH
    entry_height = max(swatch_height, 3 * (GLYPH_HEIGHT + 3)) + GUTTER
    rows = (len(metadata.frames) + COLUMNS - 1) // COLUMNS

    width = MARGIN * 2 + COLUMNS * entry_width + (COLUMNS - 1) * GUTTER
    height = MARGIN * 2 + HEADER_HEIGHT + rows * entry_height
    canvas = Canvas(width, height, BACKGROUND)

    draw_text(canvas, f"{metadata.pack_id} sprite atlas", MARGIN, MARGIN, TITLE, scale=2)
    draw_text(
        canvas,
        f"{len(metadata.frames)} frames of {frame_width}x{frame_height} "
        f"in a {metadata.atlas.width}x{metadata.atlas.height} sheet",
        MARGIN,
        MARGIN + 2 * GLYPH_HEIGHT + 4,
        DETAIL,
    )

    for index, frame in enumerate(metadata.frames):
        column, row = index % COLUMNS, index // COLUMNS
        x = MARGIN + column * (entry_width + GUTTER)
        y = MARGIN + HEADER_HEIGHT + row * entry_height
        _checkerboard(canvas, x, y, swatch_width, swatch_height)
        _outline(canvas, x - 1, y - 1, swatch_width + 2, swatch_height + 2)
        _blit_scaled(canvas, atlas, frame.rect, x, y)
        text_x = x + swatch_width + GUTTER
        draw_text(canvas, frame.name, text_x, y + 2, LABEL)
        draw_text(
            canvas,
            f"{frame.group} pivot {frame.pivot[0]},{frame.pivot[1]} hit {frame.hitbox}",
            text_x,
            y + 2 + GLYPH_HEIGHT + 3,
            DETAIL,
        )
        draw_text(
            canvas,
            f"rect {frame.rect[0]},{frame.rect[1]} {frame.rect[2]}x{frame.rect[3]}",
            text_x,
            y + 2 + 2 * (GLYPH_HEIGHT + 3),
            DETAIL,
        )
    return canvas.freeze()


def _checkerboard(canvas: Canvas, x: int, y: int, width: int, height: int) -> None:
    for row in range(0, height, SWATCH_SCALE):
        for column in range(0, width, SWATCH_SCALE):
            shade = CHECKER_LIGHT if ((row + column) // SWATCH_SCALE) % 2 else CHECKER_DARK
            canvas.fill_rect(x + column, y + row, SWATCH_SCALE, SWATCH_SCALE, shade)


def _outline(canvas: Canvas, x: int, y: int, width: int, height: int) -> None:
    canvas.fill_rect(x, y, width, 1, BORDER)
    canvas.fill_rect(x, y + height - 1, width, 1, BORDER)
    canvas.fill_rect(x, y, 1, height, BORDER)
    canvas.fill_rect(x + width - 1, y, 1, height, BORDER)


def _blit_scaled(
    canvas: Canvas, atlas: Image, rect: tuple[int, int, int, int], x: int, y: int
) -> None:
    source_x, source_y, width, height = rect
    for row in range(height):
        for column in range(width):
            red, green, blue, alpha = atlas.pixel(source_x + column, source_y + row)
            if alpha == 0:
                continue
            canvas.fill_rect(
                x + column * SWATCH_SCALE,
                y + row * SWATCH_SCALE,
                SWATCH_SCALE,
                SWATCH_SCALE,
                (red, green, blue, 255),
            )
