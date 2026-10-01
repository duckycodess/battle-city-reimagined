"""The editor's pygame loop: window, mouse, keys, and nothing else.

Like the game's loop, this is a pump. It owns the window through a
:class:`~battle_city_client.display.Presenter` built at the *editor's* logical size --
the editor frame is wider than the game's and the presenter takes that size as an
argument, so neither screen has to know about the other -- turns events into calls on
:class:`~battle_city_client.editor.state.EditorState`, and draws the result. Every
decision about what a click means was made in ``layout`` and ``state``.

Mouse mapping is the one piece of real arithmetic here, and it is deliberately asked of
the presenter rather than assumed. The window shows the frame at a whole-number scale,
centred, on a letterbox; a click in the letterbox is not a click on a cell, and a click
at scale 3 is a cell a third as far from the origin as its window coordinates suggest.
The loop reads the live scale and the live offset each time, so resizing the window or
stepping the scale needs no cooperation from anything else.

The editor is not the game: it never builds a simulation, never steps a tick and never
reads a clock for anything but frame pacing. It reads and writes declarative content, and
the content loader decides whether that content is valid.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pygame
from battle_city_sim import DEFAULT_RULES

from .. import theme
from ..assets import AssetLibrary, ProceduralAssetLibrary
from ..display import Presenter, present_rect
from . import layout
from .document import EditorDocument, EditorRefusal
from .render import EditorRenderer
from .state import EditorState, Tool

WINDOW_CAPTION: Final[str] = "Battle City Reimagined - Level Editor"
DEFAULT_FRAME_CAP: Final[int] = 60
"""The editor redraws on a timer because nothing else drives it; 60 is plenty."""

TILE_KEYS: Final[Mapping[int, int]] = {
    pygame.K_0: 0,
    pygame.K_1: 1,
    pygame.K_2: 2,
    pygame.K_3: 3,
    pygame.K_4: 4,
    pygame.K_5: 5,
    pygame.K_6: 6,
    pygame.K_7: 7,
    pygame.K_8: 8,
}
"""Tile codes are typed as themselves: ``2`` selects brick, which is what ``2`` means."""

TOOL_KEYS: Final[Mapping[int, Tool]] = {
    pygame.K_b: Tool.PAINT,
    pygame.K_p: Tool.PLAYER_SPAWN,
    pygame.K_e: Tool.ENEMY_SPAWN,
    pygame.K_x: Tool.DELETE_SPAWN,
}

SCALE_KEYS: Final[Mapping[int, int]] = {
    pygame.K_MINUS: -1,
    pygame.K_KP_MINUS: -1,
    pygame.K_EQUALS: 1,
    pygame.K_PLUS: 1,
    pygame.K_KP_PLUS: 1,
}


class EditorApp:
    """The loop, assembled from parts that are each usable without it."""

    def __init__(
        self,
        state: EditorState,
        presenter: Presenter,
        renderer: EditorRenderer,
        *,
        frame_cap: int = DEFAULT_FRAME_CAP,
    ) -> None:
        self.state = state
        self.presenter = presenter
        self.renderer = renderer
        self.frame_cap = frame_cap
        self.clock = pygame.time.Clock()

    # -- coordinates -----------------------------------------------------------

    def logical_point(self, window_point: tuple[int, int]) -> tuple[int, int] | None:
        """Map a window pixel to a logical one, honouring the scale and the letterbox."""
        window_size = self.presenter.window.get_size()
        scale = self.presenter.scale
        rect = present_rect(window_size, self.presenter.logical_size, scale)
        return layout.logical_point(
            window_point,
            origin=(rect.x, rect.y),
            scale=scale,
            logical_size=self.presenter.logical_size,
        )

    # -- events ----------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> None:
        """Apply one window event."""
        match event.type:
            case pygame.QUIT:
                self.state.running = False
            case pygame.VIDEORESIZE:
                self.presenter.resize((event.w, event.h))
            case pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    self.click(event.pos)
            case pygame.MOUSEMOTION:
                self.hover(event.pos, dragging=bool(event.buttons[0]))
            case pygame.KEYDOWN:
                self.key_down(event.key)
            case _:
                return

    def pump_events(self) -> None:
        """Drain the event queue into the editor."""
        for event in pygame.event.get():
            self.handle_event(event)

    def click(self, window_point: tuple[int, int]) -> None:
        """A left click: choose a palette entry, or apply the tool to a cell."""
        point = self.logical_point(window_point)
        if point is None:
            return
        swatch = layout.swatch_at(point)
        if swatch is not None:
            self.state.select_tile(swatch)
            self.state.note(f"TILE {self.state.selected_tile.name}")
            return
        cell = layout.cell_at(point)
        if cell is not None:
            self.state.apply_at(cell)

    def hover(self, window_point: tuple[int, int], *, dragging: bool = False) -> None:
        """Track the cell under the pointer, painting through a drag."""
        point = self.logical_point(window_point)
        self.state.hover = None if point is None else layout.cell_at(point)
        if dragging and self.state.hover is not None and self.state.tool is Tool.PAINT:
            self.state.apply_at(self.state.hover)

    def key_down(self, key: int) -> None:
        """Apply one key press."""
        tile = TILE_KEYS.get(key)
        if tile is not None:
            self.state.select_tile(tile)
            self.state.note(f"TILE {self.state.selected_tile.name}")
            return
        tool = TOOL_KEYS.get(key)
        if tool is not None:
            self.state.set_tool(tool)
            return
        step = SCALE_KEYS.get(key)
        if step is not None:
            self.presenter.step_scale(step)
            return
        match key:
            case pygame.K_TAB:
                self.state.cycle_slot()
            case pygame.K_v:
                self.state.check()
            case pygame.K_s:
                self.state.save()
            case pygame.K_r:
                self.state.reload()
            case pygame.K_ESCAPE:
                self.state.quit()
            case _:
                return

    # -- frame -----------------------------------------------------------------

    def draw(self) -> None:
        """Render the current state and show it."""
        self.renderer.render(self.presenter.surface, self.state)
        self.presenter.present()

    def step(self) -> None:
        """One whole frame: events, then presentation. Nothing is simulated."""
        self.pump_events()
        self.draw()

    def run(self) -> None:
        """Loop until the editor stops running."""
        while self.state.running:
            self.clock.tick(self.frame_cap)
            self.step()


def build_app(
    state: EditorState,
    *,
    scale: int = theme.DEFAULT_SCALE,
    assets: AssetLibrary | None = None,
    frame_cap: int = DEFAULT_FRAME_CAP,
) -> EditorApp:
    """Open a window at the editor's own logical size and wire the editor together.

    A display must already be initialised. :func:`main` does that; a test does it with
    the dummy driver.
    """
    presenter = Presenter(scale=scale, logical_size=layout.LOGICAL_SIZE, caption=WINDOW_CAPTION)
    renderer = EditorRenderer(assets or ProceduralAssetLibrary(DEFAULT_RULES))
    return EditorApp(state, presenter, renderer, frame_cap=frame_cap)


def open_state(options: argparse.Namespace) -> EditorState:
    """Build the editor state the options ask for. Raises :class:`EditorRefusal`."""
    if options.level is not None:
        document = EditorDocument.open(options.level)
    else:
        document = EditorDocument.blank()
    if options.level_id is not None:
        document.level_id = options.level_id
    if options.name is not None:
        document.name = options.name
    document.path = options.output
    return EditorState(document=document, overwrite=options.overwrite)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the launch options."""
    parser = argparse.ArgumentParser(
        prog="battle_city_client.editor",
        description="Edit a 16x16 Battle City Reimagined level and validate it before saving.",
    )
    parser.add_argument("--level", type=Path, default=None, help="level file to open")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "where a save writes. Required to save anything: the editor never writes "
            "back over the file it opened unless that file is named here as well"
        ),
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="allow a save to replace an existing file"
    )
    parser.add_argument("--id", dest="level_id", default=None, help="set the level identifier")
    parser.add_argument("--name", default=None, help="set the display name")
    parser.add_argument(
        "--scale",
        type=int,
        default=theme.DEFAULT_SCALE,
        choices=range(theme.MIN_SCALE, theme.MAX_SCALE + 1),
        help="whole-number window scale (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the editor. Returns a process exit status."""
    options = parse_args(argv)
    try:
        pygame.display.init()
    except pygame.error as error:
        print(f"battle_city_client.editor: no usable display: {error}")
        return 2
    try:
        try:
            state = open_state(options)
        except EditorRefusal as refusal:
            print(f"battle_city_client.editor: {refusal}")
            return 1
        build_app(state, scale=options.scale).run()
    finally:
        pygame.display.quit()
        pygame.quit()
    return 0
