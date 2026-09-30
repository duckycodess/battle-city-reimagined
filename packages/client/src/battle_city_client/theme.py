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

PLAYER_TANK: Final[Color] = (108, 198, 244)
PLAYER_TANK_DARK: Final[Color] = (44, 118, 168)
ENEMY_TANK: Final[Color] = (236, 108, 96)
ENEMY_TANK_DARK: Final[Color] = (150, 54, 48)
TREAD: Final[Color] = (32, 36, 46)
PLAYER_SHOT: Final[Color] = (238, 244, 252)
ENEMY_SHOT: Final[Color] = (252, 214, 150)
POWERUP_FRAME: Final[Color] = (246, 190, 64)
POWERUP_FILL: Final[Color] = (52, 44, 22)
