"""Palette and layout constants for the temporary procedural look.

Everything here is presentation, and all of it is temporary. The art pipeline
specification says shipped art comes from versioned Blender scenes exported as
spritesheets; this build has none, so :mod:`battle_city_client.assets` draws stand-ins.
Stating the palette and the frame geometry once, here, is what keeps those stand-ins
looking like one game and what makes the eventual swap a change of asset library rather
than a change of renderer.

The layout is a fixed logical frame that the window scales by a whole number. Nothing in
the client measures pixels against the window: the playfield is exactly
``tile_size * grid`` pixels, the HUD is a fixed column beside it, and
:mod:`battle_city_client.display` centres the result. A logical frame is also what makes
a screenshot reproducible, because it does not depend on the machine that took it.

Colour is never the only signal. Every readable state the palette distinguishes --
faction, tank variant, base intact or destroyed, invincibility, terrain kind -- also has a
shape or a word carrying it, because the accessibility specification requires feedback
that survives a viewer who cannot separate the two hues.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

Color = tuple[int, int, int]

TILE_SIZE: Final[int] = 16
"""Pixels per tile in the logical frame.

This mirrors :attr:`battle_city_sim.Rules.tile_size`. The renderer asserts the two agree
rather than assuming it, because a rules change that is not mirrored here would draw a
playfield that no longer lines up with the collision geometry it depicts.
"""

GRID_CELLS: Final[int] = 16
"""Classic stage width and height in tiles."""

MARGIN: Final[int] = 8
PLAYFIELD_SIZE: Final[int] = TILE_SIZE * GRID_CELLS
PLAYFIELD_ORIGIN: Final[tuple[int, int]] = (MARGIN, MARGIN)
HUD_WIDTH: Final[int] = 80
HUD_ORIGIN: Final[tuple[int, int]] = (MARGIN + PLAYFIELD_SIZE + MARGIN, MARGIN)
HUD_SIZE: Final[tuple[int, int]] = (HUD_WIDTH, PLAYFIELD_SIZE)
LOGICAL_SIZE: Final[tuple[int, int]] = (
    MARGIN + PLAYFIELD_SIZE + MARGIN + HUD_WIDTH + MARGIN,
    MARGIN + PLAYFIELD_SIZE + MARGIN,
)
"""The frame the client draws into, before any window scaling."""

MIN_SCALE: Final[int] = 1
MAX_SCALE: Final[int] = 8
DEFAULT_SCALE: Final[int] = 3

BACKGROUND: Final[Color] = (14, 16, 22)
LETTERBOX: Final[Color] = (6, 7, 10)
PANEL: Final[Color] = (24, 28, 38)
PANEL_EDGE: Final[Color] = (58, 68, 88)
OVERLAY: Final[Color] = (10, 12, 18)
TEXT: Final[Color] = (226, 232, 240)
TEXT_DIM: Final[Color] = (134, 146, 168)
ACCENT: Final[Color] = (246, 190, 64)
OK: Final[Color] = (124, 208, 132)
DANGER: Final[Color] = (232, 84, 72)

GROUND: Final[Color] = (18, 20, 27)
GROUND_SPECK: Final[Color] = (26, 29, 38)
STONE: Final[Color] = (158, 166, 180)
STONE_DARK: Final[Color] = (104, 112, 128)
BRICK: Final[Color] = (176, 96, 62)
BRICK_DARK: Final[Color] = (120, 62, 40)
BRICK_MORTAR: Final[Color] = (58, 34, 26)
CRACK: Final[Color] = (238, 226, 206)
WATER: Final[Color] = (44, 92, 152)
WATER_CREST: Final[Color] = (96, 154, 214)
FOREST: Final[Color] = (46, 106, 62)
FOREST_LEAF: Final[Color] = (84, 168, 100)
MIRROR: Final[Color] = (206, 216, 234)
MIRROR_DARK: Final[Color] = (120, 132, 156)
HOME: Final[Color] = (238, 214, 120)
HOME_DARK: Final[Color] = (146, 118, 40)

CONVEYOR: Final[Color] = (74, 80, 96)
CONVEYOR_RIB: Final[Color] = (112, 120, 142)
CONVEYOR_ARROW: Final[Color] = (236, 240, 248)
PAD: Final[Color] = (36, 60, 72)
PAD_RING: Final[Color] = (150, 206, 226)
PAD_LINK: Final[Color] = (240, 246, 250)
"""The opt-in gimmick terrain.

Hue is doing no work here. A conveyor is read from the arrow it carries and the ribs it
is drawn on, a pad from a two-ended link between two bright corners, and all five are
separated from each other and from the nine classic tiles by brightness alone -- which
``tests/client`` asserts, by comparing coarse luminance fingerprints rather than colours.
Nothing animates: the accessibility specification asks for cues that survive reduced
motion, and a belt that only reads as a belt while it is scrolling does not.
"""

PLAYER_TANK: Final[Color] = (108, 198, 244)
PLAYER_TANK_DARK: Final[Color] = (44, 118, 168)
ENEMY_TANK: Final[Color] = (236, 108, 96)
ENEMY_TANK_DARK: Final[Color] = (150, 54, 48)
TREAD: Final[Color] = (32, 36, 46)
PLAYER_SHOT: Final[Color] = (238, 244, 252)
ENEMY_SHOT: Final[Color] = (252, 214, 150)
POWERUP_FRAME: Final[Color] = (246, 190, 64)
POWERUP_FILL: Final[Color] = (52, 44, 22)


class ContrastMode(Enum):
    """Which palette the client draws with.

    A preference rather than a theme: the two palettes draw the same shapes in the same
    places, and nothing about the board's geometry, its readings or its rules changes
    between them. :data:`DEFAULT_PALETTE` is the look this build has always had, pixel
    for pixel, so a player who never opens the options screen sees no difference at all.
    """

    DEFAULT = "default"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour the client draws with, as one value that can be swapped.

    The module constants above are the defaults of these fields, so :data:`DEFAULT_PALETTE`
    is exactly the palette the renderer and the asset library used when there was only one.
    Threading the palette as an argument -- through the renderer *and* through the asset
    cache, which bakes colour into the surfaces it builds -- is what makes a contrast
    option a real change of pixels rather than a tint applied on the way out.

    Colour is still never the only signal. A high-contrast palette widens the luminance
    gaps a viewer reads the board by; it does not add a distinction, and it does not take
    one away, because every state the board distinguishes also carries a shape or a word.
    """

    background: Color = BACKGROUND
    letterbox: Color = LETTERBOX
    panel: Color = PANEL
    panel_edge: Color = PANEL_EDGE
    overlay: Color = OVERLAY
    text: Color = TEXT
    text_dim: Color = TEXT_DIM
    accent: Color = ACCENT
    ok: Color = OK
    danger: Color = DANGER

    ground: Color = GROUND
    ground_speck: Color = GROUND_SPECK
    stone: Color = STONE
    stone_dark: Color = STONE_DARK
    brick: Color = BRICK
    brick_dark: Color = BRICK_DARK
    brick_mortar: Color = BRICK_MORTAR
    crack: Color = CRACK
    water: Color = WATER
    water_crest: Color = WATER_CREST
    forest: Color = FOREST
    forest_leaf: Color = FOREST_LEAF
    mirror: Color = MIRROR
    mirror_dark: Color = MIRROR_DARK
    home: Color = HOME
    home_dark: Color = HOME_DARK

    conveyor: Color = CONVEYOR
    conveyor_rib: Color = CONVEYOR_RIB
    conveyor_arrow: Color = CONVEYOR_ARROW
    pad: Color = PAD
    pad_ring: Color = PAD_RING
    pad_link: Color = PAD_LINK

    player_tank: Color = PLAYER_TANK
    player_tank_dark: Color = PLAYER_TANK_DARK
    enemy_tank: Color = ENEMY_TANK
    enemy_tank_dark: Color = ENEMY_TANK_DARK
    tread: Color = TREAD
    player_shot: Color = PLAYER_SHOT
    enemy_shot: Color = ENEMY_SHOT
    powerup_frame: Color = POWERUP_FRAME
    powerup_fill: Color = POWERUP_FILL


DEFAULT_PALETTE: Final[Palette] = Palette()
"""The shipped look. Every field is the module constant of the same name."""

HIGH_CONTRAST_PALETTE: Final[Palette] = Palette(
    background=(0, 0, 0),
    letterbox=(0, 0, 0),
    panel=(0, 0, 0),
    panel_edge=(255, 255, 255),
    overlay=(0, 0, 0),
    text=(255, 255, 255),
    text_dim=(188, 188, 188),
    accent=(255, 214, 0),
    ok=(0, 255, 128),
    danger=(255, 72, 72),
    ground=(0, 0, 0),
    ground_speck=(64, 64, 64),
    stone=(255, 255, 255),
    stone_dark=(112, 112, 112),
    brick=(255, 128, 48),
    brick_dark=(140, 60, 16),
    brick_mortar=(0, 0, 0),
    crack=(255, 255, 255),
    water=(0, 96, 255),
    water_crest=(160, 208, 255),
    forest=(0, 112, 32),
    forest_leaf=(96, 255, 128),
    mirror=(255, 255, 255),
    mirror_dark=(96, 96, 96),
    home=(255, 224, 0),
    home_dark=(128, 96, 0),
    conveyor=(24, 24, 24),
    conveyor_rib=(128, 128, 128),
    conveyor_arrow=(255, 255, 255),
    pad=(0, 32, 48),
    pad_ring=(128, 208, 255),
    pad_link=(255, 255, 255),
    player_tank=(96, 224, 255),
    player_tank_dark=(0, 96, 160),
    enemy_tank=(255, 112, 96),
    enemy_tank_dark=(144, 24, 16),
    tread=(32, 32, 32),
    player_shot=(255, 255, 255),
    enemy_shot=(255, 224, 96),
    powerup_frame=(255, 214, 0),
    powerup_fill=(0, 0, 0),
)
"""A wider-luminance variant: near-black ground, white chrome, saturated actors.

Chosen for separation rather than for taste. The panels lose their fill so text sits on
black, terrain keeps its textures but widens the gap between light and dark, and the two
factions stay as far apart in brightness as they are in hue -- which is the half of the
distinction that survives a viewer who cannot separate the hues at all.
"""

PALETTES: Final[dict[ContrastMode, Palette]] = {
    ContrastMode.DEFAULT: DEFAULT_PALETTE,
    ContrastMode.HIGH: HIGH_CONTRAST_PALETTE,
}


def palette_for(contrast: ContrastMode) -> Palette:
    """The palette ``contrast`` selects."""
    return PALETTES[contrast]
