"""A 5x7 bitmap font, stated as data.

The client draws every word with this table instead of with ``pygame.font``. Three
reasons, in order of weight:

1. **No device assumption.** ``pygame.font`` initialises SDL_ttf. A build or a machine
   without it turns every label in the game into an exception, and a client that cannot
   survive a missing subsystem is exactly what the phase's risk list warns about. A
   lookup table cannot fail to initialise.
2. **Reproducible screenshots.** Freetype hinting differs between versions, so text
   rendered through it is not byte-stable across machines. These glyphs are.
3. **It matches the look.** The playfield is 16-pixel tiles at whole-number scale; a
   hinted outline font sitting on top of that reads as a different program.

Each glyph is seven rows of five characters, ``#`` for a lit pixel. Rows are strings so a
glyph is legible in the source as the shape it draws, which is the point of keeping the
font as data: editing a letter is editing a picture of that letter.

Anything not in the table renders as :data:`MISSING_GLYPH`, a hollow box, so an
unexpected character shows up as a visible gap rather than as a crash or as nothing.
"""

from __future__ import annotations

from typing import Final

GLYPH_WIDTH: Final[int] = 5
GLYPH_HEIGHT: Final[int] = 7
GLYPH_SPACING: Final[int] = 1
"""Blank columns between glyphs at scale 1."""

LINE_SPACING: Final[int] = 3
"""Blank rows between text lines at scale 1."""

MISSING_GLYPH: Final[tuple[str, ...]] = (
    "#####",
    "#...#",
    "#...#",
    "#...#",
    "#...#",
    "#...#",
    "#####",
)

GLYPHS: Final[dict[str, tuple[str, ...]]] = {
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("###..", "#..#.", "#...#", "#...#", "#...#", "#..#.", "###.."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "[": (".###.", ".#...", ".#...", ".#...", ".#...", ".#...", ".###."),
    "]": (".###.", "...#.", "...#.", "...#.", "...#.", "...#.", ".###."),
    "'": ("..#..", "..#..", ".....", ".....", ".....", ".....", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "<": ("...#.", "..#..", ".#...", "#....", ".#...", "..#..", "...#."),
    ">": (".#...", "..#..", "...#.", "....#", "...#.", "..#..", ".#..."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    "%": ("##..#", "##.#.", "..#..", ".#...", "#.##.", "..##.", "....."),
    "*": (".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
}


def glyph_rows(character: str) -> tuple[str, ...]:
    """Pixel rows for ``character``, upper-cased, falling back to the missing-glyph box."""
    return GLYPHS.get(character.upper(), MISSING_GLYPH)


def text_width(text: str, scale: int = 1) -> int:
    """Width in logical pixels of ``text`` drawn at ``scale``, trailing spacing excluded."""
    if not text:
        return 0
    per_glyph = (GLYPH_WIDTH + GLYPH_SPACING) * scale
    return per_glyph * len(text) - GLYPH_SPACING * scale


def text_height(scale: int = 1) -> int:
    """Height in logical pixels of one line of text drawn at ``scale``."""
    return GLYPH_HEIGHT * scale


def line_step(scale: int = 1) -> int:
    """Vertical distance between the tops of two consecutive lines at ``scale``."""
    return (GLYPH_HEIGHT + LINE_SPACING) * scale
