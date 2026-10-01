"""What the editor is doing: tools, selection, verdicts and the unsaved-work guard.

Still no pygame. The window only forwards clicks and keys to this object, so paint,
spawn editing, validation and saving are all decided and tested here.
"""

from __future__ import annotations

from pathlib import Path

import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor.document import EditorDocument
from battle_city_client.editor.layout import PALETTE_TILES
from battle_city_client.editor.state import EditorState, Tool, error_cell
from battle_city_content import GridCell, TileCode


def state_for(document: EditorDocument | None = None, *, overwrite: bool = False) -> EditorState:
    return EditorState(document=document or EditorDocument.blank(), overwrite=overwrite)


def test_the_selected_tile_follows_the_palette_index() -> None:
    state = state_for()
    state.select_tile(2)
    assert state.selected_tile is PALETTE_TILES[2]
    assert state.selected_tile is TileCode.BRICK


def test_an_index_outside_the_palette_is_ignored() -> None:
    state = state_for()
    state.select_tile(99)
    state.select_tile(-1)
    assert state.tile_index == 0


def test_the_paint_tool_lays_down_the_selected_tile() -> None:
    state = state_for()
    state.select_tile(5)
    assert state.apply_at(GridCell(3, 3)) is True
    assert state.document.tile_at(GridCell(3, 3)) is TileCode.WATER


def test_the_spawn_tools_add_move_and_delete() -> None:
    state = state_for()
    state.set_tool(Tool.PLAYER_SPAWN)
    state.active_slot = 2
    assert state.apply_at(GridCell(6, 6)) is True
    assert state.document.spawn_label_at(GridCell(6, 6)) == "P2"

    state.set_tool(Tool.ENEMY_SPAWN)
    assert state.apply_at(GridCell(7, 7)) is True
    assert state.document.spawn_label_at(GridCell(7, 7)) == "E"

    state.set_tool(Tool.DELETE_SPAWN)
    assert state.apply_at(GridCell(7, 7)) is True
    assert state.document.spawn_label_at(GridCell(7, 7)) is None


def test_a_refused_edit_says_why() -> None:
    state = state_for()
    state.set_tool(Tool.ENEMY_SPAWN)
    occupied = state.document.player_spawns[0].cell
    assert state.apply_at(occupied) is False
    assert state.status_is_error is True
    assert "ALREADY" in state.status

    state.set_tool(Tool.DELETE_SPAWN)
    assert state.apply_at(GridCell(9, 9)) is False
    assert "NO SPAWN HERE" in state.status


def test_the_slot_cycles_through_declared_slots_then_the_next_free_one() -> None:
    state = state_for()
    state.document.place_player_spawn(2, GridCell(6, 6))
    assert state.active_slot == 1
    state.cycle_slot()
    assert state.active_slot == 2
    state.cycle_slot()
    assert state.active_slot == 3
    state.cycle_slot()
    assert state.active_slot == 1


def test_a_verdict_is_only_ever_the_last_explicit_check() -> None:
    """Editing clears the verdict rather than recomputing it, so it is never stale."""
    state = state_for()
    assert state.checked is False
    assert state.check() is None
    assert state.valid is True

    state.select_tile(0)
    state.apply_at(state.document.player_spawns[0].cell)
    state.select_tile(2)
    assert state.apply_at(GridCell(2, 2)) is True
    assert state.checked is False
    assert state.valid is False


def test_an_invalid_document_reports_the_field_and_the_cell() -> None:
    state = state_for()
    state.select_tile(0)
    assert state.apply_at(GridCell(7, 15)) is True
    diagnostic = state.check()
    assert diagnostic is not None
    assert diagnostic.field == "grid.rows"
    assert state.status_is_error is True


def test_a_cell_level_diagnostic_points_at_one_cell() -> None:
    state = state_for()
    state.document.rows[4][6] = "x"
    diagnostic = state.check()
    assert diagnostic is not None
    assert error_cell(diagnostic) == GridCell(x=6, y=4)


def test_a_document_level_diagnostic_points_at_no_cell() -> None:
    state = state_for()
    state.select_tile(0)
    state.apply_at(GridCell(7, 15))
    assert error_cell(state.check()) is None


def test_saving_without_a_target_reports_the_refusal_without_raising() -> None:
    state = state_for()
    assert state.save() is False
    assert "NO SAVE TARGET" in state.status
    assert state.status_is_error is True


def test_saving_writes_and_marks_the_document_clean(tmp_path: Path) -> None:
    state = state_for()
    state.document.path = tmp_path / "level.json"
    state.select_tile(1)
    state.apply_at(GridCell(2, 2))
    assert state.save() is True
    assert state.document.dirty is False
    assert state.valid is True
    assert (tmp_path / "level.json").is_file()


def test_saving_an_invalid_document_is_refused_in_the_status_bar(tmp_path: Path) -> None:
    state = state_for()
    state.document.path = tmp_path / "level.json"
    state.select_tile(0)
    state.apply_at(GridCell(7, 15))
    assert state.save() is False
    assert "WOULD BE INVALID" in state.status
    assert not (tmp_path / "level.json").exists()


def test_an_existing_target_needs_the_overwrite_flag(tmp_path: Path) -> None:
    target = tmp_path / "level.json"
    first = state_for()
    first.document.path = target
    assert first.save() is True

    second = state_for()
    second.document.path = target
    assert second.save() is False
    assert "ALREADY EXISTS" in second.status

    third = state_for(overwrite=True)
    third.document.path = target
    assert third.save() is True


def test_reloading_restores_the_file_and_forgets_the_verdict(tmp_path: Path) -> None:
    target = tmp_path / "level.json"
    seed = state_for()
    seed.document.path = target
    seed.save()

    state = state_for(EditorDocument.open(target))
    state.check()
    state.select_tile(2)
    state.apply_at(GridCell(5, 5))
    assert state.reload() is True
    assert state.document.dirty is False
    assert state.checked is False
    assert state.document.tile_at(GridCell(5, 5)) is TileCode.EMPTY


def test_reloading_a_document_with_no_file_reports_the_refusal() -> None:
    state = state_for()
    assert state.reload() is False
    assert state.status_is_error is True


def test_quitting_a_clean_document_is_immediate() -> None:
    state = state_for()
    state.quit()
    assert state.running is False


def test_quitting_an_edited_document_asks_once() -> None:
    state = state_for()
    state.select_tile(2)
    state.apply_at(GridCell(2, 2))
    state.quit()
    assert state.running is True
    assert "UNSAVED" in state.status
    state.quit()
    assert state.running is False


def test_any_other_action_cancels_a_pending_quit() -> None:
    """A confirmation that outlives the question is not a confirmation."""
    state = state_for()
    state.select_tile(2)
    state.apply_at(GridCell(2, 2))
    state.quit()
    state.apply_at(GridCell(3, 3))
    state.quit()
    assert state.running is True
