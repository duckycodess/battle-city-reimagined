"""Draw an :class:`~battle_city_client.editor.state.EditorState` into the editor frame.

The editor borrows the game's art and the game's palette rather than inventing a second
look: a tile painted here is the tile the player will see, which is the only way the
preview is worth anything. Every surface therefore comes from the same
:class:`~battle_city_client.assets.AssetLibrary` the renderer uses, and the home base is
asked for through :meth:`AssetLibrary.base` rather than through ``tile`` so the editor
keeps showing the base even if the terrain pass ever stops special-casing it.

What the editor draws on top of the art is editor furniture, and none of it is a rule:
a grid rule so cells can be counted, a marker on every spawn naming its slot, a cursor on
the hovered cell, an outline on the cell a validation error blames, and a status bar
saying whether the document is valid, dirty, and where a save would go.
"""

from __future__ import annotations

import pygame
from battle_city_content import TileCode
from battle_city_sim import Tile

from .. import theme
from ..assets import AssetLibrary
from ..glyphs import line_step, text_width
from . import layout
from .state import TOOL_LABELS, EditorState, error_cell

TILE_ART: dict[str, Tile] = {code.value: Tile(int(code.value)) for code in TileCode}
"""The simulation tile each content tile code depicts.

The two packages keep separate vocabularies on purpose -- the simulation may not import
a project package -- and both are the classic encoding, so the join is the code itself.
Built here rather than assumed cell by cell, so a vocabulary that ever stopped agreeing
fails on import instead of drawing the wrong terrain.
"""

NAME_LIMIT: int = 14
LABEL_LIMIT: int = 30
"""Header widths. Two labels on one line of a fixed frame need a stated budget each."""

GRID_RULE: theme.Color = (38, 44, 58)
"""A rule between cells. Dim on purpose: it must not read as terrain."""

SPAWN_PLAYER: theme.Color = theme.PLAYER_TANK
SPAWN_ENEMY: theme.Color = theme.ENEMY_TANK

KEY_HELP: tuple[tuple[str, str], ...] = (
    ("0-8", "TILE"),
    ("B", "PAINT"),
    ("P", "PLAYER"),
    ("E", "ENEMY"),
    ("X", "DELETE"),
    ("TAB", "SLOT"),
    ("V", "CHECK"),
    ("S", "SAVE"),
    ("R", "RELOAD"),
)
"""The keys the panel lists. Beside the handler in ``app`` so the two cannot drift."""


class EditorRenderer:
    """Paints one editor frame."""

    def __init__(self, assets: AssetLibrary) -> None:
        if assets.rules.tile_size != layout.TILE_SIZE:
            raise ValueError(
                f"asset tile size {assets.rules.tile_size} does not match the editor layout's "
                f"{layout.TILE_SIZE}; painted cells would not line up with the grid"
            )
        self.assets = assets

    # -- entry point -----------------------------------------------------------

    def render(self, surface: pygame.Surface, state: EditorState) -> None:
        """Draw ``state`` onto ``surface``, which must be the editor's logical frame."""
        surface.fill(theme.BACKGROUND)
        self._draw_header(surface, state)
        self._draw_grid(surface, state)
        self._draw_panel(surface, state)
        self._draw_status(surface, state)

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

    # -- regions ---------------------------------------------------------------

    def _draw_header(self, surface: pygame.Surface, state: EditorState) -> None:
        document = state.document
        rect = pygame.Rect(layout.header_rect())
        identity = f"{'*' if document.dirty else ' '}{document.level_id.upper()}"
        self.text(surface, identity, (rect.x, rect.y), theme.ACCENT)
        self.text(
            surface,
            document.name.upper()[:NAME_LIMIT],
            (rect.x + text_width(identity) + 8, rect.y),
            theme.TEXT,
        )
        self.text_right(
            surface,
            _shorten(document.label.upper(), LABEL_LIMIT),
            rect.right,
            rect.y,
            theme.TEXT_DIM,
        )

    def _draw_grid(self, surface: pygame.Surface, state: EditorState) -> None:
        document = state.document
        origin_x, origin_y = layout.GRID_ORIGIN
        for y, row in enumerate(document.rows):
            for x, code in enumerate(row):
                art = (
                    self.assets.base(destroyed=False)
                    if code == TileCode.HOME.value
                    else self.assets.tile(TILE_ART[code])
                )
                surface.blit(
                    art, (origin_x + x * layout.TILE_SIZE, origin_y + y * layout.TILE_SIZE)
                )

        field = pygame.Rect(layout.grid_rect())
        for index in range(1, layout.GRID_CELLS):
            offset = index * layout.TILE_SIZE
            pygame.draw.line(
                surface,
                GRID_RULE,
                (field.x + offset, field.y),
                (field.x + offset, field.bottom - 1),
            )
            pygame.draw.line(
                surface, GRID_RULE, (field.x, field.y + offset), (field.right - 1, field.y + offset)
            )

        self._draw_spawns(surface, state)

        highlight = error_cell(state.diagnostic)
        if highlight is not None and document.contains(highlight):
            pygame.draw.rect(surface, theme.DANGER, pygame.Rect(layout.cell_rect(highlight)), 2)
        if state.hover is not None and document.contains(state.hover):
            pygame.draw.rect(surface, theme.ACCENT, pygame.Rect(layout.cell_rect(state.hover)), 1)
        pygame.draw.rect(surface, theme.PANEL_EDGE, field, 1)

    def _draw_spawns(self, surface: pygame.Surface, state: EditorState) -> None:
        """Mark every spawn with a letter, so a spawn is never a colour alone."""
        document = state.document
        for spawn in document.player_spawns:
            self._mark(surface, layout.cell_rect(spawn.cell), f"P{spawn.slot}", SPAWN_PLAYER)
        for cell in document.enemy_spawns:
            self._mark(surface, layout.cell_rect(cell), "E", SPAWN_ENEMY)

    def _mark(
        self, surface: pygame.Surface, rect: layout.Rect, label: str, color: theme.Color
    ) -> None:
        box = pygame.Rect(rect)
        pygame.draw.rect(surface, color, box, 1)
        self.text(
            surface,
            label,
            (box.x + (box.width - text_width(label)) // 2, box.y + 5),
            color,
        )

    def _draw_panel(self, surface: pygame.Surface, state: EditorState) -> None:
        panel = pygame.Rect(layout.panel_rect())
        pygame.draw.rect(surface, theme.PANEL, panel)
        pygame.draw.rect(surface, theme.PANEL_EDGE, panel, 1)
        left = panel.x + 6
        self.text(surface, "PALETTE", (left, panel.y + 6), theme.ACCENT)

        for index, tile in enumerate(layout.PALETTE_TILES):
            rect = pygame.Rect(layout.swatch_rect(index))
            art = (
                self.assets.base(destroyed=False)
                if tile is TileCode.HOME
                else self.assets.tile(TILE_ART[tile.value])
            )
            surface.blit(art, (rect.x + 3, rect.y + 3))
            chosen = index == state.tile_index
            pygame.draw.rect(
                surface, theme.ACCENT if chosen else theme.PANEL_EDGE, rect, 2 if chosen else 1
            )
            self.text(surface, tile.value, (rect.x + 1, rect.y + 1), theme.TEXT)

        y = layout.PALETTE_ORIGIN[1] + layout.PALETTE_SPAN[1] + 8
        document = state.document
        readings: tuple[tuple[str, str], ...] = (
            ("TILE", state.selected_tile.name),
            ("TOOL", TOOL_LABELS[state.tool]),
            ("SLOT", str(state.active_slot)),
            ("PLAY", str(len(document.player_spawns))),
            ("FOE", str(len(document.enemy_spawns))),
        )
        for label, value in readings:
            self.text(surface, label, (left, y), theme.TEXT_DIM)
            self.text_right(surface, value, panel.right - 6, y, theme.TEXT)
            y += line_step()

        y += 4
        for key, action in KEY_HELP:
            self.text(surface, key, (left, y), theme.ACCENT)
            self.text_right(surface, action, panel.right - 6, y, theme.TEXT_DIM)
            y += line_step()

    def _draw_status(self, surface: pygame.Surface, state: EditorState) -> None:
        rect = pygame.Rect(layout.status_rect())
        pygame.draw.rect(surface, theme.PANEL, rect)
        pygame.draw.rect(surface, theme.PANEL_EDGE, rect, 1)
        verdict, color = _verdict(state)
        self.text(surface, verdict, (rect.x + 6, rect.y + 6), color)
        self.text(
            surface,
            _shorten(state.status, 64),
            (rect.x + 6, rect.y + 6 + line_step()),
            theme.DANGER if state.status_is_error else theme.TEXT_DIM,
        )
        self.text_right(
            surface,
            "UNSAVED" if state.document.dirty else "NO EDITS",
            rect.right - 6,
            rect.y + 6,
            theme.ACCENT if state.document.dirty else theme.OK,
        )


def _verdict(state: EditorState) -> tuple[str, theme.Color]:
    """The headline: what the last explicit check said, and nothing newer."""
    if not state.checked:
        return "NOT CHECKED - PRESS V", theme.TEXT_DIM
    if state.diagnostic is None:
        return "VALID", theme.OK
    return f"INVALID {state.diagnostic.headline}"[:48], theme.DANGER


def _shorten(value: str, limit: int) -> str:
    """Trim a line to the width the frame can show, keeping the informative end."""
    return value if len(value) <= limit else f"...{value[-(limit - 3) :]}"
