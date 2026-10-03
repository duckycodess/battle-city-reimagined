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
from battle_city_sim import Faction, RunOutcome, SimulationState, Tile, TileGrid

from . import theme
from .assets import AssetLibrary
from .campaign import CampaignRun
from .glyphs import GLYPH_HEIGHT, line_step, text_width
from .keymap import CONTROL_HELP
from .online import OnlinePhase, OnlineSession
from .remote import RemoteBoard, shot_origin
from .session import StageSession
from .shell import (
    MAIN_MENU_LABELS,
    ONLINE_HELP,
    OUTCOME_HEADLINES,
    PAUSE_CAUSE_NOTICES,
    PAUSE_LABELS,
    SAVED_BLOCK_NOTES,
    ClientShell,
    MainMenuItem,
    PauseItem,
    Screen,
)

TITLE: str = "BATTLE CITY"
SUBTITLE: str = "REIMAGINED"
CURSOR: str = ">"
HUD_PADDING: int = 4
MARGIN: int = 8
BADGE_HUD_OFFSET: int = 22
"""Gap between the base readout and the badge line, clearing the base sprite."""

CONTROLS_PANEL_TOP: int = 34
CONTROLS_PANEL_PADDING: int = 16
CONTROLS_KEYS_WIDTH: int = 204
"""Geometry of the controls-and-options screen, stated once rather than inline.

The two panels are sized from the binding table and the margin, so a row added to
:data:`~battle_city_client.keymap.CONTROL_HELP` grows both of them together instead of
spilling out of one.
"""

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
                self._draw_controls(surface, shell)
            case Screen.PLAYING:
                self._draw_run(surface, shell)
            case Screen.PAUSED:
                self._draw_run(surface, shell)
                self._draw_pause_overlay(surface, shell)
            case Screen.RUN_OVER:
                self._draw_run(surface, shell)
                self._draw_run_over_overlay(surface, shell)
            case Screen.ONLINE_LOBBY:
                self._draw_online_lobby(surface, shell)
            case Screen.ONLINE_PLAY:
                self._draw_online_run(surface, shell)

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
        recovery = shell.profile.recovery_notice
        if recovery:
            # Said once, on the first screen, rather than left for the player to discover
            # when their progress turns out not to be there.
            self.text_centered(surface, recovery[:46], center_x, 226, theme.DANGER)
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
        self._draw_saved_stage(surface, shell, x, y + 6)
        self.text(surface, "ENTER PLAY", (x, panel.bottom - 24), theme.TEXT_DIM)
        self.text(surface, "ESC BACK", (x, panel.bottom - 14), theme.TEXT_DIM)

    def _draw_saved_stage(
        self, surface: pygame.Surface, shell: ClientShell, x: int, y: int
    ) -> None:
        """What the profile saved, and the key that picks it up.

        Deliberately beside the list rather than inside it: every row of the list starts
        its stage fresh, which is the recorded checkpoint rule, and a row that sometimes
        meant something else would be the one thing a player could not predict. Resuming
        is a separate key, and this block is what says the key has something to do.
        """
        checkpoint = shell.checkpoint
        index = shell.resume_index
        self.text(surface, "SAVED", (x, y), theme.ACCENT)
        y += line_step()
        blocked = shell.resume_notice
        if checkpoint is None or index is None:
            # The short form of the same refusal the key gives, from the one table that
            # holds both. A save that cannot be used says why rather than going quiet.
            for line in SAVED_BLOCK_NOTES.get(blocked, ("UNAVAILABLE",)):
                self.text(surface, line[:12], (x, y), theme.TEXT_DIM)
                y += line_step()
            return
        for line in (
            f"STAGE {index + 1}",
            f"SCORE {checkpoint.score}",
            f"LIVES {checkpoint.lives}",
        ):
            self.text(surface, line[:12], (x, y), theme.TEXT)
            y += line_step()
        self.text(surface, "R RESUMES", (x, y), theme.ACCENT)

    def _draw_controls(self, surface: pygame.Surface, shell: ClientShell) -> None:
        """Two panels: what the keys do, and what this profile holds.

        The second panel is why this screen is the one that grew rather than a new one:
        the settings a player can see are the settings they already change with these
        keys, and a badge is chosen with the same two keys that move every other cursor
        in the client. A screen of its own would have been a screen with three lines on
        it and a member in :class:`~battle_city_client.shell.Screen` to reach it.
        """
        center_x = theme.LOGICAL_SIZE[0] // 2
        self.text_centered(surface, "CONTROLS", center_x, 12, theme.ACCENT, 2)
        height = CONTROLS_PANEL_PADDING + len(CONTROL_HELP) * line_step()
        keys_panel = pygame.Rect(MARGIN, CONTROLS_PANEL_TOP, CONTROLS_KEYS_WIDTH, height)
        self._panel(surface, keys_panel)
        y = keys_panel.y + 8
        for label, keys in CONTROL_HELP:
            self.text(surface, label, (keys_panel.x + 8, y), theme.TEXT_DIM)
            self.text_right(surface, keys, keys_panel.right - 8, y, theme.TEXT)
            y += line_step()

        options_panel = pygame.Rect(
            keys_panel.right + MARGIN,
            CONTROLS_PANEL_TOP,
            theme.LOGICAL_SIZE[0] - keys_panel.right - 2 * MARGIN,
            height,
        )
        self._draw_options(surface, shell, options_panel)

        self.text_centered(
            surface, "UP AND DOWN CHOOSE A BADGE", center_x, keys_panel.bottom + 10, theme.TEXT_DIM
        )
        recovery = shell.profile.recovery_notice
        if recovery:
            self.text_centered(
                surface, recovery[:46], center_x, keys_panel.bottom + 24, theme.DANGER
            )
        self.text_centered(surface, "KEYBOARD ONLY IN THIS BUILD", center_x, 232, theme.TEXT_DIM)
        self.text_centered(surface, "ENTER OR ESC RETURNS", center_x, 246, theme.TEXT_DIM)

    def _draw_options(
        self, surface: pygame.Surface, shell: ClientShell, panel: pygame.Rect
    ) -> None:
        """The saved settings, and the badges this profile has earned.

        A locked badge is listed with what it asks for rather than hidden, so the screen
        says what progression there is instead of growing entries out of nowhere. The
        cursor marks the selection, as everywhere else in the client, so the choice is
        never carried by colour alone.
        """
        self._panel(surface, panel)
        profile = shell.profile
        settings = profile.settings
        left = panel.x + 6
        right = panel.right - 6
        y = panel.y + 8
        self.text(surface, "OPTIONS", (left, y), theme.ACCENT)
        y += line_step()
        for label, value in (
            ("SCALE", str(settings.scale)),
            ("FRAME CAP", str(settings.frame_cap)),
            ("NAME", settings.display_name.upper()[:10]),
        ):
            self.text(surface, label, (left, y), theme.TEXT_DIM)
            self.text_right(surface, value, right, y, theme.TEXT)
            y += line_step()

        y += 4
        self.text(surface, "BADGE", (left, y), theme.ACCENT)
        y += line_step()
        chosen = profile.badge
        for badge, unlocked in profile.badge_options():
            selected = unlocked and badge.badge_id == chosen.badge_id
            marker = CURSOR if selected else " "
            color = theme.ACCENT if selected else theme.TEXT_DIM
            self.text(surface, f"{marker}{badge.label}"[:12], (left, y), color)
            if not unlocked:
                self.text_right(surface, badge.requirement[:12], right, y, theme.TEXT_DIM)
            y += line_step()

    # -- the run ---------------------------------------------------------------

    def _draw_run(self, surface: pygame.Surface, shell: ClientShell) -> None:
        session = shell.session
        if session is None:
            return
        self._draw_playfield(surface, session)
        self._draw_hud(surface, session, shell.campaign, shell.profile.badge.label)

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
        self,
        surface: pygame.Surface,
        session: StageSession,
        campaign: CampaignRun | None,
        badge: str = "",
    ) -> None:
        """The local HUD. ``badge`` is cosmetic and local: see :meth:`_draw_remote_hud`."""
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

        if badge:
            # The one place a cosmetic is drawn, and it is a local run's own HUD. The
            # online HUD below has no equivalent and must not grow one: the product
            # specification's rule is that competitive cosmetics affect neither
            # simulation state nor visibility, and a badge nobody else can see or be
            # shown is the shape that rule takes here.
            y += BADGE_HUD_OFFSET
            self.text(surface, "BADGE", (left, y), theme.TEXT_DIM)
            y += line_step()
            self.text(surface, badge[:12], (left, y), theme.ACCENT)

        self.text(surface, "ESC PAUSE", (left, panel.bottom - 14), theme.TEXT_DIM)

    def _draw_life_pips(self, surface: pygame.Surface, x: int, y: int, lives: int) -> None:
        """Lives as counted marks beside the number, for a reading without reading."""
        for index in range(min(lives, 5)):
            surface.fill(theme.OK, pygame.Rect(x + index * 5, y, 3, 6))

    # -- online ----------------------------------------------------------------

    def _draw_online_lobby(self, surface: pygame.Surface, shell: ClientShell) -> None:
        """The lobby: who is in it, what was agreed, and whether it can start.

        The blocked line is the honest half of this screen. A lobby configured for a
        mode this build will not run says so here, with the server's own reason code,
        instead of letting a host press start and be refused with no explanation.
        """
        session = shell.online
        center_x = theme.LOGICAL_SIZE[0] // 2
        self.text_centered(surface, "ONLINE LOBBY", center_x, 16, theme.ACCENT, 2)
        panel = pygame.Rect(MARGIN, 40, theme.LOGICAL_SIZE[0] - 2 * MARGIN, 150)
        self._panel(surface, panel)
        left = panel.x + 8
        right = panel.right - 8
        y = panel.y + 8

        if session is None:
            self.text_centered(surface, "NOT CONNECTED", center_x, y + 40, theme.DANGER)
            return

        y = self._draw_lobby_settings(surface, session, left, right, y)
        y = self._draw_lobby_roster(surface, session, left, right, y + 4)
        self._draw_lobby_status(surface, session, center_x, panel.bottom - 22)
        self._draw_lobby_help(surface, panel.bottom + 6)

    def _draw_lobby_settings(
        self, surface: pygame.Surface, session: OnlineSession, left: int, right: int, y: int
    ) -> int:
        lobby = session.lobby
        phase = "WAITING FOR SERVER" if lobby is None else f"REVISION {lobby.revision}"
        self.text(surface, "SESSION", (left, y), theme.TEXT_DIM)
        self.text_right(surface, session.session_id.upper()[:16], right, y, theme.TEXT)
        y += line_step()
        self.text(surface, "STATUS", (left, y), theme.TEXT_DIM)
        self.text_right(surface, phase, right, y, theme.TEXT)
        y += line_step()
        if lobby is None:
            return y
        mode = lobby.settings.mode
        playable = session.mode_playable(mode)
        self.text(surface, "MODE", (left, y), theme.TEXT_DIM)
        self.text_right(
            surface,
            mode.value.replace("_", " ").upper(),
            right,
            y,
            theme.TEXT if playable else theme.DANGER,
        )
        y += line_step()
        if not playable:
            self.text(surface, "NOT PLAYABLE IN THIS BUILD", (left, y), theme.DANGER)
            y += line_step()
        self.text(surface, "STAGE", (left, y), theme.TEXT_DIM)
        self.text_right(surface, lobby.settings.level_id.upper()[:16], right, y, theme.TEXT)
        y += line_step()
        self.text(surface, "CHEATS", (left, y), theme.TEXT_DIM)
        self.text_right(
            surface, "ON" if lobby.settings.cheats_enabled else "OFF", right, y, theme.TEXT
        )
        return y + line_step()

    def _draw_lobby_roster(
        self, surface: pygame.Surface, session: OnlineSession, left: int, right: int, y: int
    ) -> int:
        lobby = session.lobby
        self.text(surface, "ROSTER", (left, y), theme.ACCENT)
        y += line_step()
        if lobby is None or not lobby.members:
            self.text(surface, "EMPTY", (left, y), theme.TEXT_DIM)
            return y + line_step()
        for member in lobby.members:
            mine = member.slot == session.slot
            marker = CURSOR if mine else " "
            team = "" if member.team is None else f" T{member.team}"
            role = " HOST" if member.host else ""
            label = f"{marker}P{member.slot} {member.display_name.upper()}{team}{role}"
            self.text(surface, label[:26], (left, y), theme.TEXT if mine else theme.TEXT_DIM)
            self.text_right(
                surface,
                "READY" if member.ready else "WAITING",
                right,
                y,
                theme.OK if member.ready else theme.TEXT_DIM,
            )
            y += line_step()
        return y

    def _draw_lobby_status(
        self, surface: pygame.Surface, session: OnlineSession, center_x: int, y: int
    ) -> None:
        if session.notice:
            self.text_centered(surface, session.notice[:46], center_x, y, theme.DANGER)
            return
        lobby = session.lobby
        if lobby is None:
            return
        if lobby.startable:
            self.text_centered(surface, "READY TO START", center_x, y, theme.OK)
            return
        blocked = lobby.blocked
        reason = "" if blocked is None else blocked.value.replace("_", " ").upper()
        self.text_centered(surface, reason or "WAITING", center_x, y, theme.TEXT_DIM)

    def _draw_lobby_help(self, surface: pygame.Surface, y: int) -> None:
        left = MARGIN + 8
        right = theme.LOGICAL_SIZE[0] - MARGIN - 8
        for label, keys in ONLINE_HELP:
            self.text(surface, label, (left, y), theme.TEXT_DIM)
            self.text_right(surface, keys, right, y, theme.TEXT)
            y += line_step()

    def _draw_online_run(self, surface: pygame.Surface, shell: ClientShell) -> None:
        """A run the server is running. Every pixel here came off the wire."""
        session = shell.online
        if session is None:
            return
        board = session.board
        if board is None:
            self._draw_online_waiting(surface, session)
            return
        # The playfield is drawn from the interpolated view and the HUD from the newest
        # snapshot. The numbers a player reads -- tick, lives, state hash -- are the
        # server's latest word and are never a blend of two of them.
        drawn = session.render_board
        self._draw_remote_playfield(surface, board if drawn is None else drawn)
        self._draw_remote_hud(surface, session, board)
        if session.phase is OnlinePhase.ENDED:
            self._draw_online_ended_overlay(surface, session)

    def _draw_online_waiting(self, surface: pygame.Surface, session: OnlineSession) -> None:
        center_x = theme.LOGICAL_SIZE[0] // 2
        self.text_centered(surface, "JOINING THE MATCH", center_x, 108, theme.ACCENT, 2)
        self.text_centered(
            surface,
            session.notice[:46] or "WAITING FOR THE FIRST SNAPSHOT",
            center_x,
            136,
            theme.TEXT_DIM,
        )

    def _draw_remote_playfield(self, surface: pygame.Surface, board: RemoteBoard) -> None:
        origin_x, origin_y = theme.PLAYFIELD_ORIGIN
        size = self.assets.rules.tile_size
        self._draw_terrain(surface, board.grid, base_destroyed=board.base_destroyed)

        for pickup in board.powerups:
            art = self.assets.powerup(pickup.kind)
            surface.blit(art, (origin_x + pickup.cell.x * size, origin_y + pickup.cell.y * size))

        for tank in board.tanks:
            art = self.assets.tank(tank.variant, tank.facing, invincible=tank.invincible)
            surface.blit(art, (origin_x + tank.x, origin_y + tank.y))

        for shot in board.shots:
            art = self.assets.projectile(shot.faction)
            shot_x, shot_y = shot_origin(shot, self.assets.rules)
            surface.blit(art, (origin_x + shot_x - 1, origin_y + shot_y - 1))

        art = self.assets.tile(Tile.FOREST)
        for cell in board.grid.positions_of(Tile.FOREST):
            surface.blit(art, (origin_x + cell.x * size, origin_y + cell.y * size))
        pygame.draw.rect(
            surface,
            theme.PANEL_EDGE,
            pygame.Rect(*theme.PLAYFIELD_ORIGIN, theme.PLAYFIELD_SIZE, theme.PLAYFIELD_SIZE),
            1,
        )

    def _draw_remote_hud(
        self, surface: pygame.Surface, session: OnlineSession, board: RemoteBoard
    ) -> None:
        panel = pygame.Rect(*theme.HUD_ORIGIN, *theme.HUD_SIZE)
        self._panel(surface, panel)
        left = panel.x + HUD_PADDING
        right = panel.right - HUD_PADDING
        y = panel.y + 6

        settings = session.settings
        self.text(surface, "ONLINE", (left, y), theme.ACCENT)
        y += line_step()
        if settings is not None:
            self.text(surface, settings.mode.value.upper()[:12], (left, y), theme.TEXT_DIM)
            y += line_step()
            self.text(surface, settings.level_id.upper()[:12], (left, y), theme.TEXT)
            y += line_step() + 4

        slot = session.slot
        player = None if slot is None else board.player(slot)
        tank = None if slot is None else board.tank_of(slot)
        readings: tuple[tuple[str, str, theme.Color], ...] = (
            ("TICK", str(board.tick), theme.TEXT),
            ("SLOT", "-" if slot is None else f"P{slot}", theme.TEXT),
            ("LIVES", "-" if player is None else str(player.lives), theme.TEXT),
            (
                "TANK",
                "ALIVE" if tank is not None else "LOST",
                theme.TEXT if tank is not None else theme.DANGER,
            ),
            ("PLAYERS", str(len(board.players)), theme.TEXT),
            ("SHOTS", str(len(board.shots)), theme.TEXT),
            ("TANKS", str(len(board.tanks)), theme.TEXT),
        )
        for label, value, color in readings:
            self.text(surface, label, (left, y), theme.TEXT_DIM)
            self.text_right(surface, value, right, y, color)
            y += line_step()

        y += 4
        self.text(surface, "BASE", (left, y), theme.TEXT_DIM)
        self.text_right(
            surface,
            "LOST" if board.base_destroyed else "OK",
            right,
            y,
            theme.DANGER if board.base_destroyed else theme.OK,
        )
        y += line_step() + 2
        # The hash the server published for this tick. Two clients showing different
        # hashes for one tick are looking at different runs, which is worth being able
        # to read off the screen rather than out of a packet capture.
        self.text(surface, "HASH", (left, y), theme.TEXT_DIM)
        y += line_step()
        self.text(surface, board.state_hash[:12], (left, y), theme.TEXT_DIM)

        self.text(surface, "ESC LEAVE", (left, panel.bottom - 14), theme.TEXT_DIM)

    def _draw_online_ended_overlay(self, surface: pygame.Surface, session: OnlineSession) -> None:
        """What the *server* said happened. The client never writes this line itself."""
        self._dim(surface)
        center_x = theme.LOGICAL_SIZE[0] // 2
        panel = pygame.Rect(0, 0, 248, 112)
        panel.center = (center_x, theme.LOGICAL_SIZE[1] // 2)
        self._panel(surface, panel)
        self.text_centered(surface, "SESSION ENDED", center_x, panel.y + 12, theme.DANGER, 2)
        outcome = session.outcome
        headline = "NO OUTCOME REPORTED"
        if outcome is not None:
            headline = OUTCOME_HEADLINES.get(RunOutcome(outcome), "RUN ENDED")
        self.text_centered(surface, headline, center_x, panel.y + 38, theme.TEXT)
        self.text_centered(
            surface,
            session.notice[:40] or "THE SERVER CLOSED THE SESSION",
            center_x,
            panel.y + 38 + line_step(),
            theme.TEXT_DIM,
        )
        self.text_centered(surface, "ESC RETURNS", center_x, panel.bottom - 16, theme.TEXT_DIM)

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
