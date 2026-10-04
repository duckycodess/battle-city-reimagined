"""The conveyor and pad cues, and the editor that authors them.

The accessibility specification asks for feedback that survives a viewer who cannot
separate two hues and a viewer who has turned motion off. A scrolling belt would satisfy
neither, so the cue is a static shape, and "static shape" is only a claim until something
measures it. These cases measure it the way the sibling terrain test does: by comparing
coarse luminance fingerprints, which is what is left of a surface once the colour is gone.

The direction half matters as much as the distinctness half. An arrow that disagreed with
the push the simulation applies would be the worst kind of bug -- invisible to every test
that only checks the art is *different* -- so the drawing table is compared against the
simulation's own table, cell by cell.

Every separation claim is measured in *every* palette this build ships, not only the
default one. A contrast palette is a different set of colours threaded through the same
drawing code, so a gimmick tile that forgot to take the palette would keep its default
colours while the board around it changed -- a tile that reads correctly in the palette
the test happened to pick and wrongly in the one a player chose. Parameterising over
:data:`battle_city_client.theme.PALETTES` is what makes a palette added later have to
answer for these tiles too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pygame
import pytest
from battle_city_client import ProceduralAssetLibrary, Renderer, theme
from battle_city_client.assets import CONVEYOR_FACING
from battle_city_client.editor import layout
from battle_city_client.editor.document import EditorDocument, EditorRefusal
from battle_city_client.editor.layout import PALETTE_TILES
from battle_city_client.editor.render import TILE_ART, EditorRenderer
from battle_city_client.editor.state import EditorState, Tool
from battle_city_client.session import StageSession
from battle_city_client.shell import ClientShell
from battle_city_content import (
    GIMMICK_LEVEL_SCHEMA_VERSION,
    GIMMICK_TILES,
    LEVEL_SCHEMA_VERSION,
    GridCell,
    TileCode,
)
from battle_city_sim import (
    CONVEYOR_DIRECTIONS,
    DEFAULT_RULES,
    GridPos,
    PlayerSpawn,
    Stage,
    Tile,
)
from client_helpers import logical_surface, make_entry, make_shell, renderer, rows_with

BELT = GridPos(4, 11)
SOURCE_PAD = GridPos(12, 11)
PARTNER_PAD = GridPos(12, 4)
GIMMICK_CELLS = {
    BELT: Tile.CONVEYOR_E,
    GridPos(5, 11): Tile.CONVEYOR_N,
    GridPos(6, 11): Tile.CONVEYOR_S,
    GridPos(7, 11): Tile.CONVEYOR_W,
    SOURCE_PAD: Tile.TELEPORT_PAD,
    PARTNER_PAD: Tile.TELEPORT_PAD,
}


def luminance_pattern(surface: pygame.Surface) -> tuple[int, ...]:
    """A coarse brightness fingerprint, which is what survives losing hue."""
    return tuple(
        (surface.get_at((x, y)).r + surface.get_at((x, y)).g + surface.get_at((x, y)).b) // 96
        for y in range(surface.get_height())
        for x in range(surface.get_width())
    )


def pixels(surface: pygame.Surface) -> tuple[int, ...]:
    """Every channel of every pixel -- the reading a luminance fingerprint throws away."""
    return tuple(
        channel
        for y in range(surface.get_height())
        for x in range(surface.get_width())
        for channel in surface.get_at((x, y))
    )


def gimmick_stage() -> Stage:
    painted = dict(GIMMICK_CELLS)
    painted[GridPos(7, 15)] = Tile.HOME
    return Stage.create(
        stage_id="gimmick-stage",
        name="Gimmick Stage",
        rows=rows_with({(cell.x, cell.y): tile for cell, tile in painted.items()}),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(BELT.x, BELT.y - 1)),),
        enemy_spawns=(GridPos(1, 1),),
    )


# -- the cues -----------------------------------------------------------------


EVERY_PALETTE = pytest.mark.parametrize(
    "contrast", list(theme.PALETTES), ids=lambda mode: mode.value
)
"""Run a separation case once per shipped palette, named by the mode a player picks."""


def library(contrast: theme.ContrastMode = theme.ContrastMode.DEFAULT) -> ProceduralAssetLibrary:
    """The stand-in art drawn in the palette ``contrast`` selects."""
    return ProceduralAssetLibrary(DEFAULT_RULES, theme.palette_for(contrast))


@EVERY_PALETTE
def test_every_tile_including_the_new_five_is_distinct_without_hue(
    contrast: theme.ContrastMode,
) -> None:
    assets = library(contrast)
    shapes = {tile: luminance_pattern(assets.tile(tile)) for tile in Tile}
    assert len(set(shapes.values())) == len(Tile)


@EVERY_PALETTE
def test_the_four_arrows_differ_from_each_other_without_hue(contrast: theme.ContrastMode) -> None:
    """Four orientations of one shape, which is the whole point of using a shape."""
    assets = library(contrast)
    arrows = {tile: luminance_pattern(assets.tile(tile)) for tile in CONVEYOR_DIRECTIONS}
    assert len(set(arrows.values())) == 4


@EVERY_PALETTE
def test_a_pad_does_not_look_like_a_belt_or_like_ground(contrast: theme.ContrastMode) -> None:
    assets = library(contrast)
    pad = luminance_pattern(assets.tile(Tile.TELEPORT_PAD))
    assert pad != luminance_pattern(assets.tile(Tile.EMPTY))
    for tile in CONVEYOR_DIRECTIONS:
        assert pad != luminance_pattern(assets.tile(tile))


# -- the gimmick tiles are part of the palette, not beside it -----------------


def test_the_default_palette_draws_the_gimmick_tiles_exactly_as_it_always_did() -> None:
    """A player who never opens the options screen must see the same pixels as before.

    The palette's gimmick fields default to the module constants the drawing code used
    when there was only one palette, so the default library is pixel-for-pixel what it
    was before contrast became a setting.
    """
    assert theme.DEFAULT_PALETTE.conveyor == theme.CONVEYOR
    assert theme.DEFAULT_PALETTE.conveyor_rib == theme.CONVEYOR_RIB
    assert theme.DEFAULT_PALETTE.conveyor_arrow == theme.CONVEYOR_ARROW
    assert theme.DEFAULT_PALETTE.pad == theme.PAD
    assert theme.DEFAULT_PALETTE.pad_ring == theme.PAD_RING
    assert theme.DEFAULT_PALETTE.pad_link == theme.PAD_LINK


@pytest.mark.parametrize("tile", list(GIMMICK_TILES))
def test_a_contrast_palette_really_repaints_a_gimmick_tile(tile: TileCode) -> None:
    """The gap this closes: a tile that ignored the palette would be identical here.

    Every other terrain tile changes colour when the palette does. A gimmick tile that
    had kept reading the module constants would be the one cell on the board still drawn
    in the default look, and no separation test would have noticed.
    """
    art = TILE_ART[tile.value]
    default = library(theme.ContrastMode.DEFAULT).tile(art)
    high = library(theme.ContrastMode.HIGH).tile(art)
    assert pixels(default) != pixels(high)


@pytest.mark.parametrize("tile", list(GIMMICK_TILES))
def test_switching_contrast_rebuilds_the_gimmick_art(tile: TileCode) -> None:
    """``with_palette`` is how the running client switches, so it has to repaint these too.

    A cache that handed back the surface it built in the old palette would leave the
    board half-switched, which looks like a rendering fault rather than a setting.
    """
    art = TILE_ART[tile.value]
    started = library(theme.ContrastMode.DEFAULT)
    switched = started.with_palette(theme.HIGH_CONTRAST_PALETTE)
    assert switched is not started
    assert pixels(switched.tile(art)) == pixels(library(theme.ContrastMode.HIGH).tile(art))
    assert pixels(started.tile(art)) == pixels(library(theme.ContrastMode.DEFAULT).tile(art))


def test_the_drawn_arrow_agrees_with_the_push_the_simulation_applies() -> None:
    """An arrow pointing the wrong way is invisible to every "is it different?" check."""
    assert CONVEYOR_FACING == CONVEYOR_DIRECTIONS


@pytest.mark.parametrize("tile", list(GIMMICK_TILES))
def test_a_gimmick_tile_is_exactly_one_cell(tile: TileCode) -> None:
    """Art cannot change collision geometry, including the art added by this change."""
    assets = library()
    surface = assets.tile(TILE_ART[tile.value])
    assert surface.get_size() == (DEFAULT_RULES.tile_size, DEFAULT_RULES.tile_size)


def test_nothing_about_a_cue_depends_on_a_frame_counter() -> None:
    """Reduced motion is satisfied by there being no motion to reduce."""
    assets = library()
    for tile in GIMMICK_TILES:
        art = TILE_ART[tile.value]
        assert assets.tile(art) is assets.tile(art)
        assert luminance_pattern(assets.tile(art)) == luminance_pattern(library().tile(art))


def test_the_playfield_draws_the_gimmick_terrain() -> None:
    shell = make_shell((make_entry(gimmick_stage()),))
    shell.open_session(StageSession.start(gimmick_stage()))
    surface = logical_surface()
    renderer().render(surface, shell)

    assets = library()
    size = DEFAULT_RULES.tile_size
    origin_x, origin_y = theme.PLAYFIELD_ORIGIN
    for cell, tile in GIMMICK_CELLS.items():
        drawn = surface.subsurface(
            pygame.Rect(origin_x + cell.x * size, origin_y + cell.y * size, size, size)
        )
        assert luminance_pattern(drawn) == luminance_pattern(assets.tile(tile)), cell


def test_a_classic_stage_still_draws_exactly_as_it_did() -> None:
    """Nothing in the terrain pass changed for a board without gimmick terrain."""
    shell: ClientShell = make_shell()
    shell.open_session(StageSession.start(make_shell().catalog[0].stage))
    first = logical_surface()
    renderer().render(first, shell)
    second = logical_surface()
    Renderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(second, shell)
    assert luminance_pattern(first) == luminance_pattern(second)


# -- the editor ---------------------------------------------------------------


def test_the_palette_offers_every_gimmick_tile() -> None:
    assert set(GIMMICK_TILES) <= set(PALETTE_TILES)
    for tile in GIMMICK_TILES:
        assert TILE_ART[tile.value] is not None


def test_the_tile_art_join_is_the_row_character_not_an_integer() -> None:
    """``Tile(int("A"))`` would raise; ``Tile(int("9"))`` would be the wrong tile."""
    assert TILE_ART["9"] is Tile.CONVEYOR_N
    assert TILE_ART["A"] is Tile.CONVEYOR_E
    assert TILE_ART["D"] is Tile.TELEPORT_PAD
    assert set(TILE_ART) == {tile.value for tile in TileCode}


def test_painting_a_conveyor_makes_the_document_version_two() -> None:
    document = EditorDocument.blank()
    assert document.effective_schema_version == LEVEL_SCHEMA_VERSION
    assert document.paint(GridCell(3, 3), TileCode.CONVEYOR_E)
    assert document.effective_schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    assert document.to_document()["schema_version"] == GIMMICK_LEVEL_SCHEMA_VERSION


def test_erasing_the_last_conveyor_does_not_downgrade_a_version_two_document() -> None:
    """The version is something the author declared; erasing a tile is not a downgrade."""
    document = EditorDocument.blank()
    document.schema_version = GIMMICK_LEVEL_SCHEMA_VERSION
    document.paint(GridCell(3, 3), TileCode.CONVEYOR_E)
    document.paint(GridCell(3, 3), TileCode.EMPTY)
    assert not document.uses_gimmick_tiles
    assert document.effective_schema_version == GIMMICK_LEVEL_SCHEMA_VERSION


def test_a_gimmick_document_round_trips_through_the_loader(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(3, 3), TileCode.CONVEYOR_W)
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    document.paint(GridCell(12, 2), TileCode.TELEPORT_PAD)
    document.path = tmp_path / "authored.json"
    assert document.save() == document.path

    reopened = EditorDocument.open(document.path)
    assert reopened.schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    assert reopened.grid_rows() == document.grid_rows()
    assert reopened.teleport_pads == (GridCell(2, 2), GridCell(12, 2))


def test_a_saved_document_remembers_the_version_it_declared(tmp_path: Path) -> None:
    """The regression: a save wrote version 2 and left the document believing it was 1.

    Erasing the gimmick and saving again then wrote version 1 back, silently downgrading
    a file that a version 2 pack manifest declares -- and the *same* two edits produced a
    different file depending on whether the author had happened to reopen it in between.
    A version is raised by the content and then kept; it is never lowered by erasing what
    raised it, whether or not the file has been reopened.
    """
    document = EditorDocument.blank()
    assert document.schema_version == LEVEL_SCHEMA_VERSION
    document.paint(GridCell(3, 3), TileCode.CONVEYOR_E)
    document.path = tmp_path / "authored.json"
    document.save()
    assert document.schema_version == GIMMICK_LEVEL_SCHEMA_VERSION

    document.paint(GridCell(3, 3), TileCode.EMPTY)
    assert not document.uses_gimmick_tiles
    assert document.effective_schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    document.save(overwrite=True)
    assert json.loads(document.path.read_text())["schema_version"] == GIMMICK_LEVEL_SCHEMA_VERSION
    assert EditorDocument.open(document.path).schema_version == GIMMICK_LEVEL_SCHEMA_VERSION


def test_saving_without_reopening_writes_what_reopening_would(tmp_path: Path) -> None:
    """The same edits, one document reopened in between, must produce the same bytes."""
    straight = EditorDocument.blank()
    straight.path = tmp_path / "straight.json"
    straight.paint(GridCell(3, 3), TileCode.CONVEYOR_E)
    straight.save()
    straight.paint(GridCell(3, 3), TileCode.EMPTY)
    straight.save(overwrite=True)

    reopened = EditorDocument.blank()
    reopened.path = tmp_path / "reopened.json"
    reopened.paint(GridCell(3, 3), TileCode.CONVEYOR_E)
    reopened.save()
    carried = EditorDocument.open(reopened.path)
    carried.path = reopened.path
    carried.paint(GridCell(3, 3), TileCode.EMPTY)
    carried.save(overwrite=True)

    assert straight.path.read_bytes() == reopened.path.read_bytes()


def test_a_refused_save_does_not_promote_the_version(tmp_path: Path) -> None:
    """Nothing was written, so nothing about the document moved either."""
    document = EditorDocument.blank()
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    document.path = tmp_path / "half-pair.json"
    with pytest.raises(EditorRefusal):
        document.save()
    assert document.schema_version == LEVEL_SCHEMA_VERSION
    assert not document.path.exists()


def test_a_classic_document_still_saves_as_version_one(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(3, 3), TileCode.BRICK)
    document.path = tmp_path / "classic.json"
    document.save()
    assert EditorDocument.open(document.path).schema_version == LEVEL_SCHEMA_VERSION


def test_a_half_pair_cannot_be_saved_and_says_why(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    state = EditorState(document=document)

    diagnostic = state.check()
    assert diagnostic is not None
    assert "teleport pads" in diagnostic.message

    document.path = tmp_path / "broken.json"
    assert not state.save()
    assert state.status_is_error
    assert not document.path.exists()


def test_a_completed_pair_saves(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    document.paint(GridCell(12, 2), TileCode.TELEPORT_PAD)
    document.path = tmp_path / "paired.json"
    state = EditorState(document=document)
    assert state.check() is None
    assert state.save()
    assert document.path.exists()


def test_the_editor_outlines_an_incomplete_pair_before_it_is_an_error() -> None:
    """An author part-way through placing a pair is not wrong yet, but can see the cells."""
    document = EditorDocument.blank()
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    state = EditorState(document=document)

    surface = pygame.Surface(layout.LOGICAL_SIZE)
    EditorRenderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(surface, state)
    x, y, _, _ = layout.cell_rect(GridCell(2, 2))
    assert surface.get_at((x, y))[:3] == theme.DANGER


def test_a_complete_pair_is_not_outlined() -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(2, 2), TileCode.TELEPORT_PAD)
    document.paint(GridCell(12, 2), TileCode.TELEPORT_PAD)
    state = EditorState(document=document)

    surface = pygame.Surface(layout.LOGICAL_SIZE)
    EditorRenderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(surface, state)
    x, y, _, _ = layout.cell_rect(GridCell(2, 2))
    assert surface.get_at((x, y))[:3] != theme.DANGER


def test_the_paint_tool_reaches_every_palette_entry() -> None:
    """A tile the loader accepts but the editor cannot select is unauthorable."""
    document = EditorDocument.blank()
    state = EditorState(document=document, tool=Tool.PAINT)
    for index, tile in enumerate(PALETTE_TILES):
        state.select_tile(index)
        assert state.selected_tile is tile
        state.apply_at(GridCell(1, 1))
        assert document.tile_at(GridCell(1, 1)) is tile
