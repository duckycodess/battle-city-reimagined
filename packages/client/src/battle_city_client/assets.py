"""The drawing seam: everything the renderer paints comes from an asset library.

The renderer never calls ``pygame.draw``. It asks an :class:`AssetLibrary` for a surface
per tile, tank, projectile, powerup and glyph, and blits what it gets. That indirection
is the point of this module. The art pipeline specification says shipped art is rendered
from versioned Blender scenes and packed into spritesheets with explicit frame sizes and
pivots; this build has no such sheets, so :class:`ProceduralAssetLibrary` draws
stand-ins at the same fixed sizes. Replacing them is implementing this protocol over an
atlas -- no renderer change, no layout change, and no chance of the temporary shapes
having quietly become a rule in the meantime.

Two constraints keep that promise honest:

* **Art cannot change collision geometry.** Every surface here is exactly the size the
  simulation's rules give the thing it depicts, and the library is handed those rules
  rather than assuming them. A sprite that wants to overhang its hitbox is a pivot and
  metadata problem for the art pipeline, not licence for the renderer to draw outside the
  body the simulation collides with.
* **Nothing is distinguished by hue alone.** Factions differ by marking as well as
  colour, tank variants carry a shield ring or a broken ring, the base carries an emblem
  or a cross, and terrain kinds have distinct textures. A viewer who cannot separate two
  hues still reads the board.

Surfaces are built once and cached. They are per-pixel alpha surfaces, not display
converted, so a library can be built before a window exists -- which is what lets a test
render a frame into an offscreen surface.
"""

from __future__ import annotations

from typing import Protocol

import pygame
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    Faction,
    PowerupKind,
    Rules,
    TankVariant,
    Tile,
)

from . import theme
from .glyphs import GLYPH_HEIGHT, GLYPH_WIDTH, glyph_rows


class AssetLibrary(Protocol):
    """Everything the renderer needs to draw, as surfaces."""

    @property
    def rules(self) -> Rules:
        """The rules whose sizes these surfaces were built for."""

    def tile(self, tile: Tile) -> pygame.Surface:
        """One terrain cell, ``rules.tile_size`` square."""

    def base(self, *, destroyed: bool) -> pygame.Surface:
        """The home base cell, intact or destroyed."""

    def tank(
        self, variant: TankVariant, facing: Direction, *, invincible: bool = False
    ) -> pygame.Surface:
        """A tank of ``variant`` facing ``facing``, ``rules.tank_size`` square."""

    def projectile(self, faction: Faction) -> pygame.Surface:
        """A projectile, sized from ``rules.projectile_radius``."""

    def powerup(self, kind: PowerupKind) -> pygame.Surface:
        """An uncollected powerup, one cell square."""

    def glyph(self, character: str, color: theme.Color, scale: int = 1) -> pygame.Surface:
        """One text glyph in ``color`` at ``scale``."""


def _surface(width: int, height: int) -> pygame.Surface:
    return pygame.Surface((width, height), pygame.SRCALPHA)


class ProceduralAssetLibrary:
    """Stand-in art drawn from primitives, cached per request.

    Every shape is stated as explicit pixel arithmetic rather than sampled from noise, so
    two runs of the client draw the same board and a screenshot is reproducible.
    """

    def __init__(self, rules: Rules = DEFAULT_RULES) -> None:
        self._rules = rules
        self._tiles: dict[Tile, pygame.Surface] = {}
        self._bases: dict[bool, pygame.Surface] = {}
        self._tanks: dict[tuple[TankVariant, Direction, bool], pygame.Surface] = {}
        self._projectiles: dict[Faction, pygame.Surface] = {}
        self._powerups: dict[PowerupKind, pygame.Surface] = {}
        self._glyphs: dict[tuple[str, theme.Color, int], pygame.Surface] = {}

    @property
    def rules(self) -> Rules:
        return self._rules

    # -- terrain ---------------------------------------------------------------

    def tile(self, tile: Tile) -> pygame.Surface:
        cached = self._tiles.get(tile)
        if cached is None:
            cached = self._build_tile(tile)
            self._tiles[tile] = cached
        return cached

    def base(self, *, destroyed: bool) -> pygame.Surface:
        cached = self._bases.get(destroyed)
        if cached is None:
            cached = self._build_base(destroyed=destroyed)
            self._bases[destroyed] = cached
        return cached

    def _build_tile(self, tile: Tile) -> pygame.Surface:
        size = self._rules.tile_size
        surface = _surface(size, size)
        match tile:
            case Tile.EMPTY:
                _draw_ground(surface, size)
            case Tile.STONE:
                _draw_stone(surface, size)
            case Tile.BRICK:
                _draw_brick(surface, size, cracked=False)
            case Tile.CRACKED_BRICK:
                _draw_brick(surface, size, cracked=True)
            case Tile.MIRROR_NE:
                _draw_mirror(surface, size, backslash=True)
            case Tile.MIRROR_SE:
                _draw_mirror(surface, size, backslash=False)
            case Tile.WATER:
                _draw_water(surface, size)
            case Tile.FOREST:
                _draw_forest(surface, size)
            case Tile.HOME:
                return self.base(destroyed=False)
        return surface

    def _build_base(self, *, destroyed: bool) -> pygame.Surface:
        size = self._rules.tile_size
        surface = _surface(size, size)
        _draw_ground(surface, size)
        if destroyed:
            pygame.draw.rect(surface, theme.HOME_DARK, pygame.Rect(2, size - 5, size - 4, 3))
            pygame.draw.line(surface, theme.DANGER, (3, 3), (size - 4, size - 4), 2)
            pygame.draw.line(surface, theme.DANGER, (size - 4, 3), (3, size - 4), 2)
            return surface
        pygame.draw.rect(surface, theme.HOME_DARK, pygame.Rect(2, size - 5, size - 4, 3))
        pygame.draw.polygon(
            surface,
            theme.HOME,
            [
                (size // 2, 2),
                (size - 3, size // 2),
                (size - 5, size - 5),
                (4, size - 5),
                (2, size // 2),
            ],
        )
        pygame.draw.line(surface, theme.HOME_DARK, (size // 2, 4), (size // 2, size - 5))
        return surface

    # -- actors ----------------------------------------------------------------

    def tank(
        self, variant: TankVariant, facing: Direction, *, invincible: bool = False
    ) -> pygame.Surface:
        key = (variant, facing, invincible)
        cached = self._tanks.get(key)
        if cached is None:
            cached = _build_tank(self._rules.tank_size, variant, facing, invincible=invincible)
            self._tanks[key] = cached
        return cached

    def projectile(self, faction: Faction) -> pygame.Surface:
        cached = self._projectiles.get(faction)
        if cached is None:
            span = 2 * self._rules.projectile_radius + 1
            # Drawn one pixel proud of the collision square on each side: a three-pixel
            # dot is invisible at scale 1 and the outline is presentation only, which is
            # why the renderer still positions it from the simulation's own body.
            surface = _surface(span + 2, span + 2)
            colour = theme.PLAYER_SHOT if faction is Faction.PLAYER else theme.ENEMY_SHOT
            surface.fill((0, 0, 0, 0))
            pygame.draw.rect(surface, colour, pygame.Rect(1, 1, span, span))
            pygame.draw.rect(surface, (*colour, 120), pygame.Rect(0, 0, span + 2, span + 2), 1)
            cached = surface
            self._projectiles[faction] = cached
        return cached

    def powerup(self, kind: PowerupKind) -> pygame.Surface:
        cached = self._powerups.get(kind)
        if cached is None:
            cached = self._build_powerup(kind)
            self._powerups[kind] = cached
        return cached

    def _build_powerup(self, kind: PowerupKind) -> pygame.Surface:
        size = self._rules.tile_size
        surface = _surface(size, size)
        pygame.draw.rect(surface, theme.POWERUP_FILL, pygame.Rect(1, 1, size - 2, size - 2))
        pygame.draw.rect(surface, theme.POWERUP_FRAME, pygame.Rect(1, 1, size - 2, size - 2), 1)
        letter = POWERUP_LETTERS[kind]
        glyph = self.glyph(letter, theme.POWERUP_FRAME)
        surface.blit(glyph, ((size - GLYPH_WIDTH) // 2, (size - GLYPH_HEIGHT) // 2))
        return surface

    # -- text ------------------------------------------------------------------

    def glyph(self, character: str, color: theme.Color, scale: int = 1) -> pygame.Surface:
        key = (character.upper(), color, scale)
        cached = self._glyphs.get(key)
        if cached is None:
            cached = _build_glyph(key[0], color, scale)
            self._glyphs[key] = cached
        return cached


POWERUP_LETTERS: dict[PowerupKind, str] = {
    PowerupKind.GATLING: "G",
    PowerupKind.INVINCIBILITY: "I",
    PowerupKind.EXTRA_LIFE: "L",
}
"""A letter on every powerup, so the three are told apart without relying on colour."""


def _build_glyph(character: str, color: theme.Color, scale: int) -> pygame.Surface:
    surface = _surface(GLYPH_WIDTH * scale, GLYPH_HEIGHT * scale)
    for row, pixels in enumerate(glyph_rows(character)):
        for column, pixel in enumerate(pixels):
            if pixel == "#":
                surface.fill(color, pygame.Rect(column * scale, row * scale, scale, scale))
    return surface


def _draw_ground(surface: pygame.Surface, size: int) -> None:
    surface.fill(theme.GROUND)
    for x, y in ((3, 5), (11, 2), (7, 12), (14, 9)):
        if x < size and y < size:
            surface.fill(theme.GROUND_SPECK, pygame.Rect(x, y, 1, 1))


def _draw_stone(surface: pygame.Surface, size: int) -> None:
    surface.fill(theme.STONE_DARK)
    half = size // 2
    for x in (0, half):
        for y in (0, half):
            pygame.draw.rect(surface, theme.STONE, pygame.Rect(x + 1, y + 1, half - 2, half - 2))
            pygame.draw.line(
                surface, theme.STONE_DARK, (x + 1, y + half - 2), (x + half - 2, y + half - 2)
            )


def _draw_brick(surface: pygame.Surface, size: int, *, cracked: bool) -> None:
    surface.fill(theme.BRICK_MORTAR)
    course = size // 4
    body = theme.BRICK_DARK if cracked else theme.BRICK
    for index in range(4):
        top = index * course
        offset = 0 if index % 2 == 0 else size // 4
        for start in (-offset, size // 2 - offset):
            pygame.draw.rect(
                surface, body, pygame.Rect(start + 1, top + 1, size // 2 - 2, course - 2)
            )
    if cracked:
        pygame.draw.line(surface, theme.CRACK, (2, size - 3), (size // 2, 2))
        pygame.draw.line(surface, theme.CRACK, (size // 2, 2), (size - 3, size - 4))
        surface.fill(theme.BRICK_MORTAR, pygame.Rect(size - 5, 0, 2, 3))
        surface.fill(theme.BRICK_MORTAR, pygame.Rect(1, size - 4, 3, 2))


def _draw_mirror(surface: pygame.Surface, size: int, *, backslash: bool) -> None:
    """Draw a deflector as the slope it actually is.

    Tile 3 is named ``MIRROR_NE`` but turns a rightward shot downward, which is a ``\\``
    slope, and tile 4 is the ``/``. The simulation keeps those historical names on
    purpose; drawing the slope the shot really takes is how a player learns the board
    without reading the table.
    """
    surface.fill(theme.MIRROR_DARK)
    start = (1, 1) if backslash else (size - 2, 1)
    end = (size - 2, size - 2) if backslash else (1, size - 2)
    pygame.draw.line(surface, theme.MIRROR, start, end, 3)
    pygame.draw.line(surface, theme.GROUND, start, end, 1)
    corner = (size - 4, 2) if backslash else (2, 2)
    pygame.draw.rect(surface, theme.MIRROR, pygame.Rect(corner[0], corner[1], 2, 2))


def _draw_water(surface: pygame.Surface, size: int) -> None:
    surface.fill(theme.WATER)
    for row in range(2, size, 5):
        for column in range(0, size, 6):
            pygame.draw.line(
                surface, theme.WATER_CREST, (column, row), (min(column + 3, size - 1), row)
            )


def _draw_forest(surface: pygame.Surface, size: int) -> None:
    surface.fill(theme.FOREST)
    for x, y in ((3, 4), (10, 3), (6, 10), (13, 11)):
        pygame.draw.rect(surface, theme.FOREST_LEAF, pygame.Rect(x - 2, y - 2, 4, 4))
        pygame.draw.rect(surface, theme.GROUND, pygame.Rect(x, y, 1, 1))
    pygame.draw.rect(surface, theme.FOREST_LEAF, pygame.Rect(0, 0, size, 1))


def _build_tank(
    size: int, variant: TankVariant, facing: Direction, *, invincible: bool
) -> pygame.Surface:
    surface = _surface(size, size)
    player = variant is TankVariant.PLAYER
    body = theme.PLAYER_TANK if player else theme.ENEMY_TANK
    trim = theme.PLAYER_TANK_DARK if player else theme.ENEMY_TANK_DARK
    horizontal = facing in (Direction.LEFT, Direction.RIGHT)

    if horizontal:
        pygame.draw.rect(surface, theme.TREAD, pygame.Rect(0, 0, size, 3))
        pygame.draw.rect(surface, theme.TREAD, pygame.Rect(0, size - 3, size, 3))
        pygame.draw.rect(surface, body, pygame.Rect(2, 3, size - 4, size - 6))
    else:
        pygame.draw.rect(surface, theme.TREAD, pygame.Rect(0, 0, 3, size))
        pygame.draw.rect(surface, theme.TREAD, pygame.Rect(size - 3, 0, 3, size))
        pygame.draw.rect(surface, body, pygame.Rect(3, 2, size - 6, size - 4))

    pygame.draw.rect(surface, trim, pygame.Rect(5, 5, size - 10, size - 10))
    _draw_barrel(surface, size, facing, trim)
    _draw_variant_mark(surface, size, variant)
    if invincible:
        pygame.draw.rect(surface, theme.TEXT, pygame.Rect(0, 0, size, size), 1)
        for x, y in ((0, 0), (size - 2, 0), (0, size - 2), (size - 2, size - 2)):
            surface.fill(theme.TEXT, pygame.Rect(x, y, 2, 2))
    return surface


def _draw_barrel(
    surface: pygame.Surface, size: int, facing: Direction, colour: theme.Color
) -> None:
    centre = size // 2
    match facing:
        case Direction.UP:
            rect = pygame.Rect(centre - 1, 0, 3, centre)
        case Direction.DOWN:
            rect = pygame.Rect(centre - 1, centre, 3, centre)
        case Direction.LEFT:
            rect = pygame.Rect(0, centre - 1, centre, 3)
        case Direction.RIGHT:
            rect = pygame.Rect(centre, centre - 1, centre, 3)
    pygame.draw.rect(surface, colour, rect)


def _draw_variant_mark(surface: pygame.Surface, size: int, variant: TankVariant) -> None:
    """Mark each variant with a shape, so the faction and the shield state are not hues.

    A shielded enemy shows a closed ring, an unshielded one shows that ring broken --
    which is literally what the simulation did to it -- a normal enemy shows a solid pip,
    and the player shows a cross.
    """
    centre = size // 2
    match variant:
        case TankVariant.PLAYER:
            pygame.draw.line(surface, theme.TEXT, (centre - 2, centre), (centre + 1, centre))
            pygame.draw.line(surface, theme.TEXT, (centre, centre - 2), (centre, centre + 1))
        case TankVariant.ENEMY_NORMAL:
            surface.fill(theme.TEXT, pygame.Rect(centre - 1, centre - 1, 3, 3))
        case TankVariant.ENEMY_SHIELDED:
            pygame.draw.rect(surface, theme.TEXT, pygame.Rect(centre - 3, centre - 3, 6, 6), 1)
        case TankVariant.ENEMY_UNSHIELDED:
            pygame.draw.line(
                surface, theme.TEXT, (centre - 3, centre - 3), (centre + 2, centre - 3)
            )
            pygame.draw.line(
                surface, theme.TEXT, (centre - 3, centre + 2), (centre + 2, centre + 2)
            )
