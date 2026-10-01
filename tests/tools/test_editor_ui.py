"""The editor window: clicks at a scale, through a letterbox, and keystrokes.

pygame is imported inside these tests rather than at module scope, so collecting this
file loads no display library. See ``tools_helpers`` for why that matters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor import layout
from battle_city_client.editor.state import Tool
from battle_city_content import GridCell, TileCode
from tools_helpers import editor_app, observed

SCALE = 3


def window_point(
    logical: tuple[int, int], *, origin: tuple[int, int], scale: int
) -> tuple[int, int]:
    """The window pixel at the top-left of a logical pixel. The inverse of the mapping."""
    return (origin[0] + logical[0] * scale, origin[1] + logical[1] * scale)


def cell_click(
    cell: GridCell, *, origin: tuple[int, int] = (0, 0), scale: int = SCALE
) -> tuple[int, int]:
    """A window point near the middle of ``cell``, so the hit test is not an edge case."""
    x, y, width, height = layout.cell_rect(cell)
    return window_point((x + width // 2, y + height // 2), origin=origin, scale=scale)


def test_the_window_opens_at_the_editors_own_logical_size() -> None:
    app = editor_app(scale=SCALE)
    assert app.presenter.logical_size == layout.LOGICAL_SIZE
    assert app.presenter.window.get_size() == (
        layout.LOGICAL_SIZE[0] * SCALE,
        layout.LOGICAL_SIZE[1] * SCALE,
    )
    assert app.presenter.scale == SCALE


def test_a_scaled_click_paints_the_cell_under_the_pointer() -> None:
    app = editor_app(scale=SCALE)
    app.state.select_tile(2)
    app.click(cell_click(GridCell(5, 9)))
    assert app.state.document.tile_at(GridCell(5, 9)) is TileCode.BRICK


def test_every_cell_is_reachable_at_scale() -> None:
    app = editor_app(scale=SCALE)
    app.state.select_tile(1)
    for y in range(layout.GRID_CELLS):
        for x in range(layout.GRID_CELLS):
            app.click(cell_click(GridCell(x, y)))
    rows = app.state.document.grid_rows()
    assert set("".join(rows)) == {TileCode.STONE.value}


def test_a_click_through_the_letterbox_still_lands_on_the_right_cell() -> None:
    """A resized window centres the frame; the offset must not shift the painted cell."""
    app = editor_app(scale=SCALE)
    app.presenter.resize((layout.LOGICAL_SIZE[0] * SCALE + 40, layout.LOGICAL_SIZE[1] * SCALE + 60))
    assert observed(app.presenter.scale) == SCALE
    app.state.select_tile(5)
    app.click(cell_click(GridCell(2, 11), origin=(20, 30)))
    assert app.state.document.tile_at(GridCell(2, 11)) is TileCode.WATER


def test_a_click_in_the_letterbox_itself_changes_nothing() -> None:
    app = editor_app(scale=SCALE)
    app.presenter.resize((layout.LOGICAL_SIZE[0] * SCALE + 40, layout.LOGICAL_SIZE[1] * SCALE + 60))
    app.state.select_tile(2)
    before = app.state.document.grid_rows()
    app.click((2, 2))
    assert app.state.document.grid_rows() == before


def test_clicking_a_swatch_selects_that_tile() -> None:
    app = editor_app(scale=SCALE)
    for index in range(len(layout.PALETTE_TILES)):
        x, y, width, height = layout.swatch_rect(index)
        app.click(window_point((x + width // 2, y + height // 2), origin=(0, 0), scale=SCALE))
        assert app.state.tile_index == index
        assert app.state.selected_tile is layout.PALETTE_TILES[index]


def test_clicking_a_swatch_does_not_also_paint() -> None:
    app = editor_app(scale=SCALE)
    before = app.state.document.grid_rows()
    x, y, _, _ = layout.swatch_rect(4)
    app.click(window_point((x + 2, y + 2), origin=(0, 0), scale=SCALE))
    assert app.state.document.grid_rows() == before


def test_dragging_paints_a_stroke_but_only_with_the_paint_tool() -> None:
    app = editor_app(scale=SCALE)
    app.state.select_tile(2)
    for x in range(3, 8):
        app.hover(cell_click(GridCell(x, 6)), dragging=True)
    assert app.state.document.grid_rows()[6][3:8] == TileCode.BRICK.value * 5

    app.state.set_tool(Tool.ENEMY_SPAWN)
    app.hover(cell_click(GridCell(9, 6)), dragging=True)
    assert GridCell(9, 6) not in app.state.document.enemy_spawns


def test_hovering_tracks_the_cell_and_forgets_it_off_the_field() -> None:
    app = editor_app(scale=SCALE)
    app.hover(cell_click(GridCell(1, 2)))
    assert app.state.hover == GridCell(1, 2)
    x, y, _, _ = layout.swatch_rect(0)
    app.hover(window_point((x, y), origin=(0, 0), scale=SCALE))
    assert app.state.hover is None


def test_digit_keys_select_tiles_and_letters_select_tools() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.key_down(pygame.K_6)
    assert app.state.selected_tile is TileCode.CRACKED_BRICK
    for key, tool in (
        (pygame.K_p, Tool.PLAYER_SPAWN),
        (pygame.K_e, Tool.ENEMY_SPAWN),
        (pygame.K_x, Tool.DELETE_SPAWN),
        (pygame.K_b, Tool.PAINT),
    ):
        app.key_down(key)
        assert app.state.tool is tool


def test_tab_cycles_the_player_slot_and_a_click_places_it() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.key_down(pygame.K_p)
    app.key_down(pygame.K_TAB)
    assert app.state.active_slot == 2
    app.click(cell_click(GridCell(10, 10)))
    assert app.state.document.spawn_label_at(GridCell(10, 10)) == "P2"


def test_the_check_key_validates_and_the_save_key_writes(tmp_path: Path) -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.state.document.path = tmp_path / "level.json"
    app.key_down(pygame.K_v)
    assert app.state.valid is True
    app.key_down(pygame.K_s)
    assert (tmp_path / "level.json").is_file()


def test_the_scale_keys_resize_the_window() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.key_down(pygame.K_EQUALS)
    assert app.presenter.requested_scale == SCALE + 1
    app.key_down(pygame.K_MINUS)
    assert app.presenter.requested_scale == SCALE


def test_escape_guards_unsaved_work() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.state.select_tile(2)
    app.click(cell_click(GridCell(4, 4)))
    app.key_down(pygame.K_ESCAPE)
    assert app.state.running is True
    app.key_down(pygame.K_ESCAPE)
    assert app.state.running is False


def test_an_unbound_key_does_nothing() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    before = (app.state.tile_index, app.state.tool, app.state.document.grid_rows())
    app.key_down(pygame.K_F5)
    assert (app.state.tile_index, app.state.tool, app.state.document.grid_rows()) == before


def test_window_events_are_routed_to_the_right_place() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    app.state.select_tile(2)
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=cell_click(GridCell(8, 8)))
    )
    assert app.state.document.tile_at(GridCell(8, 8)) is TileCode.BRICK

    app.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=cell_click(GridCell(1, 1)), buttons=(0, 0, 0))
    )
    assert app.state.hover == GridCell(1, 1)

    app.handle_event(pygame.event.Event(pygame.VIDEORESIZE, w=900, h=700))
    assert app.presenter.window.get_size() == (900, 700)

    app.handle_event(pygame.event.Event(pygame.QUIT))
    assert app.state.running is False


def test_a_right_click_does_not_edit() -> None:
    import pygame

    app = editor_app(scale=SCALE)
    before = app.state.document.grid_rows()
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=cell_click(GridCell(8, 8)))
    )
    assert app.state.document.grid_rows() == before


def test_a_frame_draws_without_a_real_window() -> None:
    app = editor_app(scale=1)
    app.state.check()
    app.draw()
    surface: Any = app.presenter.surface
    colours = {surface.get_at((x, y))[:3] for y in range(0, 300, 7) for x in range(0, 400, 7)}
    assert len(colours) > 4


def test_the_renderer_refuses_art_that_does_not_match_the_grid() -> None:
    """A tile size mismatch would draw cells that do not line up with the grid rule."""
    from dataclasses import replace

    import pytest
    from battle_city_client.assets import ProceduralAssetLibrary
    from battle_city_client.editor.render import EditorRenderer
    from battle_city_sim import DEFAULT_RULES

    with pytest.raises(ValueError, match="does not match the editor layout"):
        EditorRenderer(ProceduralAssetLibrary(replace(DEFAULT_RULES, tile_size=8)))
