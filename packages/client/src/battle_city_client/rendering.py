"""Draw a :class:`~battle_city_client.shell.ClientShell` into the logical frame.

The renderer is a pure function of the shell in everything but its use of a surface: it
reads state and blits, and it never writes to the shell, never steps the simulation and
never decides anything a rule would decide. It also owns no shapes -- every pixel comes
from an :class:`~battle_city_client.assets.AssetLibrary` -- so replacing the temporary
art is replacing that library.

Two drawing decisions follow the specifications rather than taste. Forest is drawn after
the actors because the content specification calls it an overlay that permits movement
and may conceal; the simulation has an explicit visibility rule with no gameplay effect
yet, so concealment here is exactly what it says it is, a thing drawn on top. And every
HUD reading is a fact something else already holds: tick, lives, effect timers, live
shots and the base come from the simulation state, and score, stage position and enemies
remaining come from the campaign run. The renderer adds up nothing and remembers nothing
between frames.

The interstitial -- stage clear, campaign complete, or a failure -- is drawn from the
wording the shell publishes rather than from a table here, because what the first entry
*does* depends on the campaign phase and a label that disagreed with it would be worse
than no label. See :attr:`ClientShell.interstitial_labels`.
"""

from __future__ import annotations

from collections.abc import Sequence

import pygame
from battle_city_sim import Faction, SimulationState, Tile, TileGrid

from . import theme
from .assets import AssetLibrary
from .campaign import CampaignRun
from .glyphs import GLYPH_HEIGHT, line_step, text_width
from .keymap import CONTROL_HELP
from .session import StageSession
from .shell import (
    MAIN_MENU_LABELS,
    PAUSE_CAUSE_NOTICES,
    PAUSE_LABELS,
    ClientShell,
    MainMenuItem,
    PauseItem,
    Screen,
)

TITLE: str = "BATTLE CITY"
SUBTITLE: str = "REIMAGINED"
CURSOR: str = ">"
HUD_PADDING: int = 4
STAGE_SELECT_NOTE: tuple[str, ...] = (
    "STARTS THE",
    "CAMPAIGN AT",
    "THIS STAGE.",
    "",
    "ENEMIES DO",
    "NOT FIRE IN",
    "THIS BUILD.",
)
"""Said plainly on the stage list rather than left for a player to work out.

Two facts a player would otherwise discover the hard way. Choosing a stage starts the
whole campaign there, with the starting lives and a score of zero, because there is no
saved progress to resume from. And the enemies this build spawns hold position and never
fire: steering belongs to the AI package, which the client may not depend on, so the
campaign takes an injected driver and ships the one that commands nobody.
"""


class Renderer:
    """Paints one frame."""

    def __init__(self, assets: AssetLibrary) -> None:
        if assets.rules.tile_size != theme.TILE_SIZE:
            raise ValueError(
                f"asset tile size {assets.rules.tile_size} does not match the client layout's "
                f"{theme.TILE_SIZE}; the playfield would not line up with collision geometry"
            )
        self.assets = assets

    # -- entry point -----------------------------------------------------------

    def render(self, surface: pygame.Surface, shell: ClientShell) -> None:
        """Draw ``shell`` onto ``surface``, which must be the logical frame size."""
        surface.fill(theme.BACKGROUND)
        match shell.screen:
            case Screen.MAIN_MENU:
                self._draw_main_menu(surface, shell)
            case Screen.STAGE_SELECT:
                self._draw_stage_select(surface, shell)
            case Screen.CONTROLS:
                self._draw_controls(surface)
            case Screen.PLAYING:
                self._draw_run(surface, shell)
            case Screen.PAUSED:
                self._draw_run(surface, shell)
                self._draw_pause_overlay(surface, shell)
            case Screen.RUN_OVER:
                self._draw_run(surface, shell)
                self._draw_run_over_overlay(surface, shell)

    # -- text ------------------------------------------------------------------

    def text(
        self,
        surface: pygame.Surface,
        value: str,
        position: tuple[int, int],
        color: theme.Color,
        scale: int = 1,
    ) -> None:
        """Draw ``value`` with its top-left at ``position``."""
        x, y = position
        step = (5 + 1) * scale
        for index, character in enumerate(value):
            surface.blit(self.assets.glyph(character, color, scale), (x + index * step, y))

    def text_centered(
        self,
        surface: pygame.Surface,
        value: str,
        center_x: int,
        y: int,
        color: theme.Color,
        scale: int = 1,
    ) -> None:
        """Draw ``value`` horizontally centred on ``center_x``."""
        self.text(surface, value, (center_x - text_width(value, scale) // 2, y), color, scale)

    def text_right(
        self,
        surface: pygame.Surface,
        value: str,
        right_x: int,
        y: int,
        color: theme.Color,
        scale: int = 1,
    ) -> None:
        """Draw ``value`` with its right edge at ``right_x``."""
        self.text(surface, value, (right_x - text_width(value, scale), y), color, scale)

    # -- shared chrome ---------------------------------------------------------

    def _panel(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, theme.PANEL, rect)
        pygame.draw.rect(surface, theme.PANEL_EDGE, rect, 1)

    def _dim(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((*theme.OVERLAY, 205))
        surface.blit(veil, (0, 0))

    def _menu(
        self,
        surface: pygame.Surface,
        labels: Sequence[str],
        selected: int,
        center_x: int,
        top: int,
        scale: int = 2,
    ) -> None:
        """Draw a vertical menu. The cursor is a glyph, not a colour change alone."""
        step = line_step(scale)
        for index, label in enumerate(labels):
            chosen = index == selected
            color = theme.ACCENT if chosen else theme.TEXT_DIM
            y = top + index * step
            self.text_centered(surface, label, center_x, y, color, scale)
            if chosen:
                gap = 3 * scale
                marker_x = (
                    center_x - text_width(label, scale) // 2 - text_width(CURSOR, scale) - gap
                )
                self.text(surface, CURSOR, (marker_x, y), theme.ACCENT, scale)

    # -- screens ---------------------------------------------------------------

    def _draw_main_menu(self, surface: pygame.Surface, shell: ClientShell) -> None:
        center_x = theme.LOGICAL_SIZE[0] // 2
        self.text_centered(surface, TITLE, center_x, 44, theme.ACCENT, 4)
        self.text_centered(surface, SUBTITLE, center_x, 84, theme.TEXT, 2)
        labels = [MAIN_MENU_LABELS[item] for item in MainMenuItem]
        self._menu(surface, labels, shell.main_index, center_x, 140)
        if shell.notice:
            self.text_centered(surface, shell.notice, center_x, 214, theme.DANGER)
        self.text_centered(surface, "ARROWS OR WASD - ENTER SELECTS", center_x, 240, theme.TEXT_DIM)

    def _draw_stage_select(self, surface: pygame.Surface, shell: ClientShell) -> None:
        entry = shell.selected_entry
        field = pygame.Rect(*theme.PLAYFIELD_ORIGIN, theme.PLAYFIELD_SIZE, theme.PLAYFIELD_SIZE)
        if entry is None:
            self._panel(surface, field)
            self.text_centered(
                surface, "NO STAGES", field.centerx, field.centery - 12, theme.DANGER, 2
            )
            self.text_centered(
                surface,
                "CHECK THE BUNDLED PACK",
                field.centerx,
                field.centery + 8,
                theme.TEXT_DIM,
            )
        else:
            self._draw_terrain(surface, entry.stage.grid, base_destroyed=False)
            pygame.draw.rect(surface, theme.PANEL_EDGE, field, 1)

        panel = pygame.Rect(*theme.HUD_ORIGIN, *theme.HUD_SIZE)
        self._panel(surface, panel)
        x = panel.x + HUD_PADDING
        y = panel.y + 6
        self.text(surface, "SELECT", (x, y), theme.ACCENT)
        y += line_step()
        self.text(surface, "STAGE", (x, y), theme.ACCENT)
        y += line_step() + 4
        for index, candidate in enumerate(shell.catalog):
            chosen = index == shell.stage_index
            color = theme.TEXT if chosen else theme.TEXT_DIM
            marker = CURSOR if chosen else " "
            self.text(surface, f"{marker}{candidate.level_id.upper()}"[:12], (x, y), color)
            y += line_step()
        y += 6
        for line in STAGE_SELECT_NOTE:
            self.text(surface, line, (x, y), theme.TEXT_DIM)
            y += line_step()
        self.text(surface, "ENTER PLAY", (x, panel.bottom - 24), theme.TEXT_DIM)
        self.text(surface, "ESC BACK", (x, panel.bottom - 14), theme.TEXT_DIM)

    def _draw_controls(self, surface: pygame.Surface) -> None:
        center_x = theme.LOGICAL_SIZE[0] // 2
        self.text_centered(surface, "CONTROLS", center_x, 32, theme.ACCENT, 3)
        panel = pygame.Rect(
            44, 76, theme.LOGICAL_SIZE[0] - 88, 24 + len(CONTROL_HELP) * line_step()
        )
        self._panel(surface, panel)
        y = panel.y + 12
        for label, keys in CONTROL_HELP:
            self.text(surface, label, (panel.x + 12, y), theme.TEXT_DIM)
            self.text_right(surface, keys, panel.right - 12, y, theme.TEXT)
            y += line_step()
        self.text_centered(surface, "KEYBOARD ONLY IN THIS BUILD", center_x, 232, theme.TEXT_DIM)
        self.text_centered(surface, "ENTER OR ESC RETURNS", center_x, 246, theme.TEXT_DIM)

    # -- the run ---------------------------------------------------------------

    def _draw_run(self, surface: pygame.Surface, shell: ClientShell) -> None:
        session = shell.session
        if session is None:
            return
        self._draw_playfield(surface, session)
        self._draw_hud(surface, session, shell.campaign)

    def _draw_terrain(
        self, surface: pygame.Surface, grid: TileGrid, *, base_destroyed: bool
    ) -> None:
        """Blit the terrain pass, leaving forest for :meth:`_draw_forest`."""
        origin_x, origin_y = theme.PLAYFIELD_ORIGIN
        size = self.assets.rules.tile_size
        for y in range(grid.height):
            for x in range(grid.width):
                tile = grid.rows[y][x]
                if tile is Tile.FOREST:
                    art = self.assets.tile(Tile.EMPTY)
                elif tile is Tile.HOME:
                    art = self.assets.base(destroyed=base_destroyed)
                else:
                    art = self.assets.tile(tile)
                surface.blit(art, (origin_x + x * size, origin_y + y * size))

    def _draw_forest(self, surface: pygame.Surface, state: SimulationState) -> None:
        origin_x, origin_y = theme.PLAYFIELD_ORIGIN
        size = self.assets.rules.tile_size
        art = self.assets.tile(Tile.FOREST)
        for cell in state.grid.positions_of(Tile.FOREST):
            surface.blit(art, (origin_x + cell.x * size, origin_y + cell.y * size))

    def _draw_playfield(self, surface: pygame.Surface, session: StageSession) -> None:
        state = session.state
        origin_x, origin_y = theme.PLAYFIELD_ORIGIN
        self._draw_terrain(surface, state.grid, base_destroyed=state.base.destroyed)

        for pickup in state.powerups:
            art = self.assets.powerup(pickup.kind)
            size = self.assets.rules.tile_size
            surface.blit(art, (origin_x + pickup.cell.x * size, origin_y + pickup.cell.y * size))

        for tank in state.tanks:
            art = self.assets.tank(tank.variant, tank.facing, invincible=tank.invincible_ticks > 0)
            surface.blit(art, (origin_x + tank.position.x, origin_y + tank.position.y))

        for shot in state.projectiles:
            art = self.assets.projectile(shot.faction)
            body = shot.body(self.assets.rules)
            surface.blit(art, (origin_x + body.x - 1, origin_y + body.y - 1))

        self._draw_forest(surface, state)
        pygame.draw.rect(
            surface,
            theme.PANEL_EDGE,
            pygame.Rect(*theme.PLAYFIELD_ORIGIN, theme.PLAYFIELD_SIZE, theme.PLAYFIELD_SIZE),
            1,
        )

    def _draw_hud(
        self, surface: pygame.Surface, session: StageSession, campaign: CampaignRun | None
    ) -> None:
        state = session.state
        panel = pygame.Rect(*theme.HUD_ORIGIN, *theme.HUD_SIZE)
        self._panel(surface, panel)
        left = panel.x + HUD_PADDING
        right = panel.right - HUD_PADDING
        y = panel.y + 6

        self.text(surface, "STAGE", (left, y), theme.TEXT_DIM)
        y += line_step()
        self.text(surface, state.stage_id.upper()[:12], (left, y), theme.ACCENT)
        y += line_step() + 4

        tank = session.player_tank
        campaign_readings: tuple[tuple[str, str, theme.Color], ...] = (
            ()
            if campaign is None
            else (
                ("SCORE", str(campaign.score), theme.ACCENT),
                ("STAGE", f"{campaign.stage_number}/{campaign.stage_count}", theme.TEXT),
                ("ENEMIES", str(campaign.enemies_remaining), theme.TEXT),
            )
        )
        readings: tuple[tuple[str, str, theme.Color], ...] = campaign_readings + (
            ("TICK", str(state.tick), theme.TEXT),
            ("LIVES", str(session.player.lives), theme.TEXT),
            (
                "TANK",
                "ALIVE" if tank is not None else "LOST",
                theme.TEXT if tank is not None else theme.DANGER,
            ),
            ("GATLING", str(tank.gatling_ticks if tank else 0), theme.TEXT),
            ("SHIELD", str(tank.invincible_ticks if tank else 0), theme.TEXT),
            ("SHOTS", str(len(state.projectiles)), theme.TEXT),
        )
        for label, value, color in readings:
            self.text(surface, label, (left, y), theme.TEXT_DIM)
            self.text_right(surface, value, right, y, color)
            y += line_step()

        y += 4
        self.text(surface, "BASE", (left, y), theme.TEXT_DIM)
        destroyed = state.base.destroyed
        self.text_right(
            surface,
            "LOST" if destroyed else "OK",
            right,
            y,
            theme.DANGER if destroyed else theme.OK,
        )
        y += line_step()
        surface.blit(self.assets.base(destroyed=destroyed), (left, y))
        self._draw_life_pips(surface, left + 20, y + 5, session.player.lives)

        self.text(surface, "ESC PAUSE", (left, panel.bottom - 14), theme.TEXT_DIM)

    def _draw_life_pips(self, surface: pygame.Surface, x: int, y: int, lives: int) -> None:
        """Lives as counted marks beside the number, for a reading without reading."""
        for index in range(min(lives, 5)):
            surface.fill(theme.OK, pygame.Rect(x + index * 5, y, 3, 6))

    # -- overlays --------------------------------------------------------------

    def _draw_pause_overlay(self, surface: pygame.Surface, shell: ClientShell) -> None:
        self._dim(surface)
        center_x = theme.LOGICAL_SIZE[0] // 2
        panel = pygame.Rect(0, 0, 220, 128)
        panel.center = (center_x, theme.LOGICAL_SIZE[1] // 2)
        self._panel(surface, panel)
        cause = shell.pause_cause
        headline = "PAUSED" if cause is None else PAUSE_CAUSE_NOTICES[cause]
        self.text_centered(surface, "PAUSED", center_x, panel.y + 12, theme.ACCENT, 2)
        self.text_centered(surface, headline, center_x, panel.y + 34, theme.TEXT_DIM)
        labels = [PAUSE_LABELS[item] for item in PauseItem]
        self._menu(surface, labels, shell.pause_index, center_x, panel.y + 56, scale=1)
        self.text_centered(surface, "ESC RESUMES", center_x, panel.bottom - 16, theme.TEXT_DIM)

    def _draw_run_over_overlay(self, surface: pygame.Surface, shell: ClientShell) -> None:
        """The interstitial: a stage cleared, a campaign completed, or a run lost.

        One overlay for all three, because :class:`Screen` gains no members in this phase
        (see the shell's module docstring and issue #36). Every word on it comes from the
        shell, so the label on the first entry and the action behind it are decided in one
        place and cannot drift apart.
        """
        self._dim(surface)
        center_x = theme.LOGICAL_SIZE[0] // 2
        panel = pygame.Rect(0, 0, 240, 144)
        panel.center = (center_x, theme.LOGICAL_SIZE[1] // 2)
        self._panel(surface, panel)
        title_color = theme.DANGER if shell.interstitial_is_failure else theme.OK
        self.text_centered(
            surface, shell.interstitial_title, center_x, panel.y + 12, title_color, 2
        )
        headline = shell.interstitial_headline
        self.text_centered(surface, headline, center_x, panel.y + 36, theme.TEXT)
        y = panel.y + 36 + line_step()
        campaign = shell.campaign
        if campaign is not None:
            self.text_centered(surface, f"SCORE {campaign.score}", center_x, y, theme.ACCENT)
            y += line_step()
        session = shell.session
        if session is not None:
            self.text_centered(surface, f"TICK {session.state.tick}", center_x, y, theme.TEXT_DIM)
        self._menu(
            surface,
            shell.interstitial_labels,
            shell.run_over_index,
            center_x,
            panel.y + 86,
            scale=1,
        )


def faction_color(faction: Faction) -> theme.Color:
    """Palette colour for ``faction``. Exposed so a HUD extension stays consistent."""
    return theme.PLAYER_TANK if faction is Faction.PLAYER else theme.ENEMY_TANK


def glyph_line_height(scale: int = 1) -> int:
    """Height of one text line, for callers laying out beside the renderer."""
    return GLYPH_HEIGHT * scale
