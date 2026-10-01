"""The editor's document model: editing, dirty state, validation and saving.

No pygame here. Everything the editor does to a level is decided in this module and in
``state``, so the whole authoring behaviour is testable without a window.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor.document import BUNDLED_ROOT, EditorDocument, EditorRefusal
from battle_city_content import GridCell, PlayerSpawn, TileCode, Wave, load_level
from battle_city_tools import LevelDraft, save_level
from tools_helpers import make_level, observed, semantics

CLASSIC_01 = BUNDLED_ROOT / "levels" / "classic-01.json"


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_blank_document_is_already_valid() -> None:
    document = EditorDocument.blank()
    assert document.validate() is None
    assert observed(document.dirty) is False


def test_opening_a_level_keeps_identity_provenance_and_waves(tmp_path: Path) -> None:
    level = make_level(waves=(Wave(enemies=3),))
    path = tmp_path / "level.json"
    save_level(LevelDraft.from_level(level), path)

    document = EditorDocument.open(path)
    assert document.level_id == level.level_id
    assert document.waves == level.waves
    assert document.source == level.source
    assert document.opened_from == path
    assert observed(document.dirty) is False


def test_opening_an_unloadable_file_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(EditorRefusal, match="cannot be opened"):
        EditorDocument.open(broken)


def test_renaming_is_an_edit_and_restating_the_same_name_is_not() -> None:
    """Identity is content: a new identifier is a change the document must remember."""
    document = EditorDocument.blank(level_id="before", name="Before")
    assert document.rename(level_id="before", name="Before") is False
    assert observed(document.dirty) is False

    assert document.rename(name="After") is True
    assert observed(document.dirty) is True
    assert document.level_id == "before"
    assert document.name == "After"


def test_renaming_nothing_is_a_no_op() -> None:
    document = EditorDocument.blank()
    assert document.rename() is False
    assert document.dirty is False


def test_painting_marks_the_document_dirty_only_when_it_changes() -> None:
    document = EditorDocument.blank()
    assert document.paint(GridCell(2, 2), TileCode.EMPTY) is False
    assert observed(document.dirty) is False
    assert document.paint(GridCell(2, 2), TileCode.BRICK) is True
    assert observed(document.dirty) is True
    assert document.tile_at(GridCell(2, 2)) is TileCode.BRICK


def test_painting_outside_the_grid_does_nothing() -> None:
    document = EditorDocument.blank()
    assert document.paint(GridCell(16, 16), TileCode.BRICK) is False
    assert observed(document.dirty) is False


def test_a_player_slot_moves_rather_than_multiplying() -> None:
    document = EditorDocument.blank()
    assert document.place_player_spawn(1, GridCell(2, 2)) is True
    assert document.place_player_spawn(1, GridCell(3, 3)) is True
    assert document.player_spawns == [PlayerSpawn(slot=1, cell=GridCell(3, 3))]


def test_a_cell_holds_at_most_one_spawn() -> None:
    document = EditorDocument.blank()
    document.place_player_spawn(2, GridCell(6, 6))
    assert document.place_enemy_spawn(GridCell(6, 6)) is False
    assert document.place_player_spawn(3, GridCell(6, 6)) is False


def test_spawns_are_deleted_by_the_cell_they_stand_on() -> None:
    document = EditorDocument.blank()
    player_cell = document.player_spawns[0].cell
    enemy_cell = document.enemy_spawns[0]
    assert document.spawn_label_at(player_cell) == "P1"
    assert document.spawn_label_at(enemy_cell) == "E"
    assert document.remove_spawn_at(player_cell) is True
    assert document.remove_spawn_at(enemy_cell) is True
    assert document.remove_spawn_at(GridCell(9, 9)) is False
    assert document.spawn_label_at(player_cell) is None


def test_the_next_free_slot_fills_the_lowest_gap() -> None:
    document = EditorDocument.blank()
    assert document.next_free_slot() == 2
    document.place_player_spawn(2, GridCell(6, 6))
    assert document.next_free_slot() == 3


def test_validation_names_the_exact_field() -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(7, 15), TileCode.EMPTY)
    diagnostic = document.validate()
    assert diagnostic is not None
    assert diagnostic.field == "grid.rows"
    assert "exactly one home base" in diagnostic.message


def test_validation_names_the_offending_spawn() -> None:
    document = EditorDocument.blank()
    document.paint(document.player_spawns[0].cell, TileCode.WATER)
    diagnostic = document.validate()
    assert diagnostic is not None
    assert diagnostic.field == "spawns.players[0]"
    assert "must be empty ground" in diagnostic.message


def test_a_diagnostic_never_names_the_scratch_copy(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.path = tmp_path / "level.json"
    document.paint(GridCell(7, 15), TileCode.EMPTY)
    diagnostic = document.validate()
    assert diagnostic is not None
    assert diagnostic.document == str(tmp_path / "level.json")


def test_saving_without_a_target_is_refused() -> None:
    document = EditorDocument.blank()
    with pytest.raises(EditorRefusal, match="no save target"):
        document.save()


def test_saving_writes_a_loadable_level_and_clears_the_dirty_flag(tmp_path: Path) -> None:
    document = EditorDocument.blank(level_id="edited", name="Edited")
    document.paint(GridCell(4, 4), TileCode.STONE)
    document.path = tmp_path / "edited.json"
    assert observed(document.dirty) is True

    written = document.save()
    assert written == tmp_path / "edited.json"
    assert observed(document.dirty) is False
    assert load_level(written).level_id == "edited"


def test_an_existing_file_needs_overwrite(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.path = tmp_path / "level.json"
    document.save()
    document.paint(GridCell(1, 1), TileCode.BRICK)
    with pytest.raises(EditorRefusal, match="already exists"):
        document.save()
    assert document.save(overwrite=True) == document.path


def test_the_bundled_pack_is_refused_even_with_overwrite() -> None:
    document = EditorDocument.blank()
    document.path = BUNDLED_ROOT / "levels" / "classic-01.json"
    with pytest.raises(EditorRefusal, match="bundled content root"):
        document.save(overwrite=True)


def test_an_invalid_document_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.paint(GridCell(7, 15), TileCode.EMPTY)
    document.path = tmp_path / "level.json"
    with pytest.raises(EditorRefusal, match="would be invalid"):
        document.save()
    assert not document.path.exists()
    assert list(tmp_path.iterdir()) == []


def test_reloading_discards_edits_and_keeps_the_save_target(tmp_path: Path) -> None:
    document = EditorDocument.open(CLASSIC_01)
    document.path = tmp_path / "copy.json"
    document.paint(GridCell(1, 1), TileCode.WATER)
    assert observed(document.dirty) is True

    reloaded = document.reopen()
    assert reloaded.tile_at(GridCell(1, 1)) is not TileCode.WATER
    assert observed(reloaded.dirty) is False
    assert reloaded.path == tmp_path / "copy.json"


def test_reloading_a_document_with_no_file_behind_it_is_refused() -> None:
    with pytest.raises(EditorRefusal, match="not opened from a file"):
        EditorDocument.blank().reopen()


def test_a_bundled_level_survives_an_edit_free_round_trip(tmp_path: Path) -> None:
    """The editor reads and writes the same level the game does, byte for byte."""
    document = EditorDocument.open(CLASSIC_01)
    document.path = tmp_path / "classic-01.json"
    document.save()
    assert semantics(load_level(document.path)) == semantics(load_level(CLASSIC_01))
    assert document.path.read_bytes() == CLASSIC_01.read_bytes()


def test_the_label_says_when_there_is_nowhere_to_save() -> None:
    assert EditorDocument.blank().label == "(unsaved)"
    assert "no --output" in EditorDocument.open(CLASSIC_01).label


def test_a_saved_level_is_created_the_way_the_umask_says(tmp_path: Path) -> None:
    """A level the editor writes is ordinary output, not a private file."""
    document = EditorDocument.blank()
    document.path = tmp_path / "default.json"
    previous = os.umask(0o022)
    try:
        document.save()
    finally:
        os.umask(previous)
    assert mode_of(document.path) == 0o644


def test_a_restrictive_umask_is_not_relaxed_by_a_save(tmp_path: Path) -> None:
    document = EditorDocument.blank()
    document.path = tmp_path / "private.json"
    previous = os.umask(0o077)
    try:
        document.save()
    finally:
        os.umask(previous)
    assert mode_of(document.path) == 0o600


@pytest.mark.parametrize("existing", [0o644, 0o664, 0o600])
def test_saving_over_a_level_keeps_the_mode_it_had(tmp_path: Path, existing: int) -> None:
    """Staging creates the file that lands, so the replaced file's mode has to be carried."""
    target = tmp_path / "level.json"
    seed = EditorDocument.blank()
    seed.path = target
    seed.save()
    target.chmod(existing)

    document = EditorDocument.open(target)
    document.path = target
    document.paint(GridCell(4, 4), TileCode.BRICK)
    previous = os.umask(0o077)
    try:
        document.save(overwrite=True)
    finally:
        os.umask(previous)
    assert mode_of(target) == existing


def test_a_failed_save_leaves_no_temporary_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = EditorDocument.blank()
    document.path = tmp_path / "level.json"
    document.paint(GridCell(4, 4), TileCode.BRICK)

    def failing(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="disk went away"):
        document.save()

    assert list(tmp_path.iterdir()) == []
    assert observed(document.dirty) is True
