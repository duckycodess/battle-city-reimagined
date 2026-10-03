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

    @property
    def palette(self) -> theme.Palette:
        """The palette these surfaces were built with.

        Published so the renderer can refuse a library that was built for a different
        one. Colour is baked into a cached surface at the moment it is drawn, so a
        contrast option that changed only the renderer's palette would leave every tile,
        tank and projectile in the old one -- a half-applied palette that looks like a
        rendering bug rather than a setting.
        """

    def with_palette(self, palette: theme.Palette) -> AssetLibrary:
        """The same art in ``palette``, with its own cache.

        Returning a new library rather than repainting this one is what makes switching
        contrast cheap *and* safe: the surfaces already handed out stay valid for the
        frame being drawn, and switching back finds a cache that is still warm.
        """

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

    def slot_marker(self, *, local: bool, team: int | None) -> pygame.Surface:
        """An overlay naming whose tank this is, as shape and count, never as colour."""


def _surface(width: int, height: int) -> pygame.Surface:
    return pygame.Surface((width, height), pygame.SRCALPHA)


class ProceduralAssetLibrary:
    """Stand-in art drawn from primitives, cached per request.

    Every shape is stated as explicit pixel arithmetic rather than sampled from noise, so
    two runs of the client draw the same board and a screenshot is reproducible.
    """

    def __init__(
        self, rules: Rules = DEFAULT_RULES, palette: theme.Palette = theme.DEFAULT_PALETTE
    ) -> None:
        self._rules = rules
        self._palette = palette
        self._tiles: dict[Tile, pygame.Surface] = {}
        self._bases: dict[bool, pygame.Surface] = {}
        self._tanks: dict[tuple[TankVariant, Direction, bool], pygame.Surface] = {}
        self._projectiles: dict[Faction, pygame.Surface] = {}
        self._powerups: dict[PowerupKind, pygame.Surface] = {}
        self._glyphs: dict[tuple[str, theme.Color, int], pygame.Surface] = {}
        self._markers: dict[tuple[bool, int | None], pygame.Surface] = {}

    @property
    def rules(self) -> Rules:
        return self._rules

    @property
    def palette(self) -> theme.Palette:
        return self._palette

    def with_palette(self, palette: theme.Palette) -> ProceduralAssetLibrary:
        """A library drawing the same shapes in ``palette``, with an empty cache."""
        if palette == self._palette:
            return self
        return ProceduralAssetLibrary(self._rules, palette)

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
        palette = self._palette
        surface = _surface(size, size)
        match tile:
            case Tile.EMPTY:
                _draw_ground(surface, size, palette)
            case Tile.STONE:
                _draw_stone(surface, size, palette)
            case Tile.BRICK:
                _draw_brick(surface, size, palette, cracked=False)
            case Tile.CRACKED_BRICK:
                _draw_brick(surface, size, palette, cracked=True)
            case Tile.MIRROR_NE:
                _draw_mirror(surface, size, palette, backslash=True)
            case Tile.MIRROR_SE:
                _draw_mirror(surface, size, palette, backslash=False)
            case Tile.WATER:
                _draw_water(surface, size, palette)
            case Tile.FOREST:
                _draw_forest(surface, size, palette)
            case Tile.HOME:
                return self.base(destroyed=False)
        return surface

    def _build_base(self, *, destroyed: bool) -> pygame.Surface:
        size = self._rules.tile_size
        palette = self._palette
        surface = _surface(size, size)
        _draw_ground(surface, size, palette)
        if destroyed:
            pygame.draw.rect(surface, palette.home_dark, pygame.Rect(2, size - 5, size - 4, 3))
            pygame.draw.line(surface, palette.danger, (3, 3), (size - 4, size - 4), 2)
            pygame.draw.line(surface, palette.danger, (size - 4, 3), (3, size - 4), 2)
            return surface
        pygame.draw.rect(surface, palette.home_dark, pygame.Rect(2, size - 5, size - 4, 3))
        pygame.draw.polygon(
            surface,
            palette.home,
            [
                (size // 2, 2),
                (size - 3, size // 2),
                (size - 5, size - 5),
                (4, size - 5),
                (2, size // 2),
            ],
        )
        pygame.draw.line(surface, palette.home_dark, (size // 2, 4), (size // 2, size - 5))
        return surface

    # -- actors ----------------------------------------------------------------

    def tank(
        self, variant: TankVariant, facing: Direction, *, invincible: bool = False
    ) -> pygame.Surface:
        key = (variant, facing, invincible)
        cached = self._tanks.get(key)
        if cached is None:
            cached = _build_tank(
                self._rules.tank_size, variant, facing, self._palette, invincible=invincible
            )
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
            palette = self._palette
            colour = palette.player_shot if faction is Faction.PLAYER else palette.enemy_shot
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
        palette = self._palette
        surface = _surface(size, size)
        pygame.draw.rect(surface, palette.powerup_fill, pygame.Rect(1, 1, size - 2, size - 2))
        pygame.draw.rect(surface, palette.powerup_frame, pygame.Rect(1, 1, size - 2, size - 2), 1)
        letter = POWERUP_LETTERS[kind]
        glyph = self.glyph(letter, palette.powerup_frame)
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

    # -- markers ---------------------------------------------------------------

    def slot_marker(self, *, local: bool, team: int | None) -> pygame.Surface:
        key = (local, team)
        cached = self._markers.get(key)
        if cached is None:
            cached = _build_slot_marker(self._rules.tank_size, self._palette, local, team)
            self._markers[key] = cached
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


def _draw_ground(surface: pygame.Surface, size: int, palette: theme.Palette) -> None:
    surface.fill(palette.ground)
    for x, y in ((3, 5), (11, 2), (7, 12), (14, 9)):
        if x < size and y < size:
            surface.fill(palette.ground_speck, pygame.Rect(x, y, 1, 1))


def _draw_stone(surface: pygame.Surface, size: int, palette: theme.Palette) -> None:
    surface.fill(palette.stone_dark)
    half = size // 2
    for x in (0, half):
        for y in (0, half):
            pygame.draw.rect(surface, palette.stone, pygame.Rect(x + 1, y + 1, half - 2, half - 2))
            pygame.draw.line(
                surface, palette.stone_dark, (x + 1, y + half - 2), (x + half - 2, y + half - 2)
            )


def _draw_brick(
    surface: pygame.Surface, size: int, palette: theme.Palette, *, cracked: bool
) -> None:
    surface.fill(palette.brick_mortar)
    course = size // 4
    body = palette.brick_dark if cracked else palette.brick
    for index in range(4):
        top = index * course
        offset = 0 if index % 2 == 0 else size // 4
        for start in (-offset, size // 2 - offset):
            pygame.draw.rect(
                surface, body, pygame.Rect(start + 1, top + 1, size // 2 - 2, course - 2)
            )
    if cracked:
        pygame.draw.line(surface, palette.crack, (2, size - 3), (size // 2, 2))
        pygame.draw.line(surface, palette.crack, (size // 2, 2), (size - 3, size - 4))
        surface.fill(palette.brick_mortar, pygame.Rect(size - 5, 0, 2, 3))
        surface.fill(palette.brick_mortar, pygame.Rect(1, size - 4, 3, 2))


def _draw_mirror(
    surface: pygame.Surface, size: int, palette: theme.Palette, *, backslash: bool
) -> None:
    """Draw a deflector as the slope it actually is.

    Tile 3 is named ``MIRROR_NE`` but turns a rightward shot downward, which is a ``\\``
    slope, and tile 4 is the ``/``. The simulation keeps those historical names on
    purpose; drawing the slope the shot really takes is how a player learns the board
    without reading the table.
    """
    surface.fill(palette.mirror_dark)
    start = (1, 1) if backslash else (size - 2, 1)
    end = (size - 2, size - 2) if backslash else (1, size - 2)
    pygame.draw.line(surface, palette.mirror, start, end, 3)
    pygame.draw.line(surface, palette.ground, start, end, 1)
    corner = (size - 4, 2) if backslash else (2, 2)
    pygame.draw.rect(surface, palette.mirror, pygame.Rect(corner[0], corner[1], 2, 2))


def _draw_water(surface: pygame.Surface, size: int, palette: theme.Palette) -> None:
    surface.fill(palette.water)
    for row in range(2, size, 5):
        for column in range(0, size, 6):
            pygame.draw.line(
                surface, palette.water_crest, (column, row), (min(column + 3, size - 1), row)
            )


def _draw_forest(surface: pygame.Surface, size: int, palette: theme.Palette) -> None:
    surface.fill(palette.forest)
    for x, y in ((3, 4), (10, 3), (6, 10), (13, 11)):
        pygame.draw.rect(surface, palette.forest_leaf, pygame.Rect(x - 2, y - 2, 4, 4))
        pygame.draw.rect(surface, palette.ground, pygame.Rect(x, y, 1, 1))
    pygame.draw.rect(surface, palette.forest_leaf, pygame.Rect(0, 0, size, 1))


def _build_tank(
    size: int,
    variant: TankVariant,
    facing: Direction,
    palette: theme.Palette,
    *,
    invincible: bool,
) -> pygame.Surface:
    surface = _surface(size, size)
    player = variant is TankVariant.PLAYER
    body = palette.player_tank if player else palette.enemy_tank
    trim = palette.player_tank_dark if player else palette.enemy_tank_dark
    horizontal = facing in (Direction.LEFT, Direction.RIGHT)

    if horizontal:
        pygame.draw.rect(surface, palette.tread, pygame.Rect(0, 0, size, 3))
        pygame.draw.rect(surface, palette.tread, pygame.Rect(0, size - 3, size, 3))
        pygame.draw.rect(surface, body, pygame.Rect(2, 3, size - 4, size - 6))
    else:
        pygame.draw.rect(surface, palette.tread, pygame.Rect(0, 0, 3, size))
        pygame.draw.rect(surface, palette.tread, pygame.Rect(size - 3, 0, 3, size))
        pygame.draw.rect(surface, body, pygame.Rect(3, 2, size - 6, size - 4))

    pygame.draw.rect(surface, trim, pygame.Rect(5, 5, size - 10, size - 10))
    _draw_barrel(surface, size, facing, trim)
    _draw_variant_mark(surface, size, variant, palette)
    if invincible:
        pygame.draw.rect(surface, palette.text, pygame.Rect(0, 0, size, size), 1)
        for x, y in ((0, 0), (size - 2, 0), (0, size - 2), (size - 2, size - 2)):
            surface.fill(palette.text, pygame.Rect(x, y, 2, 2))
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


def _draw_variant_mark(
    surface: pygame.Surface, size: int, variant: TankVariant, palette: theme.Palette
) -> None:
    """Mark each variant with a shape, so the faction and the shield state are not hues.

    A shielded enemy shows a closed ring, an unshielded one shows that ring broken --
    which is literally what the simulation did to it -- a normal enemy shows a solid pip,
    and the player shows a cross.
    """
    centre = size // 2
    match variant:
        case TankVariant.PLAYER:
            pygame.draw.line(surface, palette.text, (centre - 2, centre), (centre + 1, centre))
            pygame.draw.line(surface, palette.text, (centre, centre - 2), (centre, centre + 1))
        case TankVariant.ENEMY_NORMAL:
            surface.fill(palette.text, pygame.Rect(centre - 1, centre - 1, 3, 3))
        case TankVariant.ENEMY_SHIELDED:
            pygame.draw.rect(surface, palette.text, pygame.Rect(centre - 3, centre - 3, 6, 6), 1)
        case TankVariant.ENEMY_UNSHIELDED:
            pygame.draw.line(
                surface, palette.text, (centre - 3, centre - 3), (centre + 2, centre - 3)
            )
            pygame.draw.line(
                surface, palette.text, (centre - 3, centre + 2), (centre + 2, centre + 2)
            )


def _build_slot_marker(
    size: int, palette: theme.Palette, local: bool, team: int | None
) -> pygame.Surface:
    """An overlay saying whose tank this is: a bracket for mine, pips for a team.

    Two separate readings, neither of them a colour. The local tank wears an open corner
    bracket, which is a shape no other state draws -- invincibility is a *closed* outline
    with filled corners, so the two never read as each other. A team is a count of pips
    along the bottom edge, which is read by counting rather than by telling two hues
    apart, and a tank with no team draws none at all.

    This is presentation over authoritative data and nothing more. It is drawn from the
    slot the server put on the tank and the team the server put in the lobby roster; it
    changes no rule, reveals nothing the snapshot did not already carry, and is blitted
    with the actors so the forest overlay still conceals whatever it conceals.
    """
    surface = _surface(size, size)
    if local:
        arm = max(3, size // 4)
        for x, y, width, height in (
            (0, 0, arm, 1),
            (0, 0, 1, arm),
            (size - arm, 0, arm, 1),
            (size - 1, 0, 1, arm),
            (0, size - arm, 1, arm),
            (0, size - 1, arm, 1),
            (size - 1, size - arm, 1, arm),
            (size - arm, size - 1, arm, 1),
        ):
            surface.fill(palette.text, pygame.Rect(x, y, width, height))
    if team is not None and team > 0:
        pips = min(team, 4)
        for index in range(pips):
            surface.fill(palette.text, pygame.Rect(2 + index * 3, size - 4, 2, 2))
    return surface
