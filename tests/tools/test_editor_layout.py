"""Editor geometry and the mouse arithmetic, as pure functions."""

from __future__ import annotations

import pytest
import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor import layout
from battle_city_content import CLASSIC_GRID_SIZE, GridCell, TileCode
from tools_helpers import (
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)


def test_the_palette_is_the_whole_tile_vocabulary() -> None:
    """A tile the loader accepts but the editor cannot paint is a level nobody can author."""
    assert tuple(TileCode) == layout.PALETTE_TILES
    assert [tile.value for tile in layout.PALETTE_TILES] == list("0123456789ABCD")


def test_the_palette_stays_inside_the_panel_as_the_vocabulary_grows() -> None:
    """Fourteen swatches have to fit beside the readings and the key help, not under them."""
    panel_x, panel_y, panel_w, panel_h = layout.panel_rect()
    span_w, span_h = layout.PALETTE_SPAN
    assert layout.PALETTE_ORIGIN[0] + span_w <= panel_x + panel_w
    assert layout.PALETTE_ORIGIN[1] + span_h < panel_y + panel_h
    assert len(layout.PALETTE_TILES) <= layout.PALETTE_ROWS * layout.SWATCH_COLUMNS
    assert layout.SWATCH_INSET >= 0
    assert layout.SWATCH_SIZE >= layout.TILE_SIZE


def test_the_frame_holds_the_grid_the_panel_and_the_status_bar() -> None:
    assert layout.GRID_SPAN == layout.TILE_SIZE * CLASSIC_GRID_SIZE
    grid_x, grid_y, grid_w, grid_h = layout.grid_rect()
    panel_x, _, panel_w, _ = layout.panel_rect()
    assert grid_x + grid_w <= panel_x
    assert panel_x + panel_w <= layout.LOGICAL_SIZE[0]
    status_x, status_y, status_w, status_h = layout.status_rect()
    assert status_y >= grid_y + grid_h
    assert status_x + status_w <= layout.LOGICAL_SIZE[0]
    assert status_y + status_h <= layout.LOGICAL_SIZE[1]


def test_the_editor_frame_is_not_the_game_frame() -> None:
    """The editor carries a panel the game has no use for, and owns its own size."""
    from battle_city_client import theme

    assert layout.LOGICAL_SIZE != theme.LOGICAL_SIZE


def test_every_cell_maps_to_itself_through_its_rectangle() -> None:
    for y in range(CLASSIC_GRID_SIZE):
        for x in range(CLASSIC_GRID_SIZE):
            cell = GridCell(x, y)
            rect_x, rect_y, width, height = layout.cell_rect(cell)
            assert layout.cell_at((rect_x, rect_y)) == cell
            assert layout.cell_at((rect_x + width - 1, rect_y + height - 1)) == cell


def test_points_outside_the_field_are_not_cells() -> None:
    grid_x, grid_y, width, height = layout.grid_rect()
    assert layout.cell_at((grid_x - 1, grid_y)) is None
    assert layout.cell_at((grid_x, grid_y - 1)) is None
    assert layout.cell_at((grid_x + width, grid_y)) is None
    assert layout.cell_at((grid_x, grid_y + height)) is None


def test_every_swatch_maps_to_itself_and_the_gaps_are_misses() -> None:
    for index in range(len(layout.PALETTE_TILES)):
        x, y, width, height = layout.swatch_rect(index)
        assert layout.swatch_at((x, y)) == index
        assert layout.swatch_at((x + width - 1, y + height - 1)) == index
    first_x, first_y, first_w, _ = layout.swatch_rect(0)
    assert layout.swatch_at((first_x + first_w, first_y)) is None


def test_swatches_do_not_overlap_the_field_or_leave_the_panel() -> None:
    panel = layout.panel_rect()
    for index in range(len(layout.PALETTE_TILES)):
        x, y, width, height = layout.swatch_rect(index)
        assert layout.contains(panel, (x, y))
        assert layout.contains(panel, (x + width - 1, y + height - 1))
        assert layout.cell_at((x, y)) is None


def test_a_click_is_divided_by_the_scale_and_offset_by_the_letterbox() -> None:
    point = layout.logical_point((20 + 7 * 3, 30 + 11 * 3), origin=(20, 30), scale=3)
    assert point == (7, 11)


def test_a_click_in_the_letterbox_is_not_a_click_on_the_frame() -> None:
    assert layout.logical_point((5, 5), origin=(20, 30), scale=3) is None
    bottom_right = (
        20 + layout.LOGICAL_SIZE[0] * 3,
        30 + layout.LOGICAL_SIZE[1] * 3,
    )
    assert layout.logical_point(bottom_right, origin=(20, 30), scale=3) is None


def test_every_window_pixel_of_a_cell_maps_back_to_that_cell() -> None:
    """At scale 3 a cell is 48 window pixels wide; all of them are the same cell."""
    cell = GridCell(4, 9)
    rect_x, rect_y, width, height = layout.cell_rect(cell)
    for offset_x in range(width * 3):
        for offset_y in (0, height * 3 - 1):
            point = (20 + rect_x * 3 + offset_x, 30 + rect_y * 3 + offset_y)
            logical = layout.logical_point(point, origin=(20, 30), scale=3)
            assert logical is not None
            assert layout.cell_at(logical) == cell


def test_a_scale_of_zero_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="scale must be positive"):
        layout.logical_point((0, 0), origin=(0, 0), scale=0)


def test_the_mapping_agrees_with_the_presenter_arithmetic() -> None:
    """The loop passes the presenter's own scale and offset; they must mean the same thing."""
    from battle_city_client.display import integer_scale, present_rect

    window = (layout.LOGICAL_SIZE[0] * 2 + 40, layout.LOGICAL_SIZE[1] * 2 + 60)
    scale = integer_scale(window, layout.LOGICAL_SIZE)
    rect = present_rect(window, layout.LOGICAL_SIZE, scale)
    assert scale == 2
    assert (rect.x, rect.y) == (20, 30)
    centre = (rect.x + rect.width // 2, rect.y + rect.height // 2)
    logical = layout.logical_point(centre, origin=(rect.x, rect.y), scale=scale)
    assert logical == (layout.LOGICAL_SIZE[0] // 2, layout.LOGICAL_SIZE[1] // 2)
