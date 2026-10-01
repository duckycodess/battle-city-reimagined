"""The editable level model: create, edit, and encode without touching a disk."""

from __future__ import annotations

import pytest
from battle_city_content import GridCell, LevelSource, PlayerSpawn, TileCode, Wave
from battle_city_tools import LevelDraft, validate_level_bytes
from battle_city_tools.serialization import serialize_document
from tools_helpers import make_draft, make_level, semantics


def test_a_blank_draft_is_already_a_valid_level() -> None:
    """``create`` must not hand an author a document they cannot save or play."""
    draft = LevelDraft.blank(level_id="fresh-level", name="Fresh Level")
    level = validate_level_bytes(serialize_document(draft.to_document()), document="fresh.json")
    assert level.level_id == "fresh-level"
    assert level.base_cell == GridCell(7, 15)
    assert len(level.player_spawns) == 1
    assert len(level.enemy_spawns) == 1


def test_painting_sets_exactly_one_cell() -> None:
    draft = make_draft()
    before = draft.grid_rows()
    draft.paint(GridCell(3, 4), TileCode.BRICK)
    assert draft.tile_at(GridCell(3, 4)) is TileCode.BRICK
    changed = [index for index, row in enumerate(draft.grid_rows()) if row != before[index]]
    assert changed == [4]


def test_painting_outside_the_grid_is_a_caller_bug() -> None:
    draft = make_draft()
    with pytest.raises(IndexError):
        draft.paint(GridCell(16, 0), TileCode.BRICK)


def test_a_player_slot_moves_rather_than_multiplying() -> None:
    draft = make_draft()
    draft.set_player_spawn(1, GridCell(2, 2))
    draft.set_player_spawn(1, GridCell(3, 3))
    assert draft.player_spawns == [PlayerSpawn(slot=1, cell=GridCell(3, 3))]


def test_player_spawns_stay_sorted_as_the_loader_sorts_them() -> None:
    """An unsorted draft would round-trip into a different order and look like an edit."""
    draft = make_draft()
    draft.set_player_spawn(3, GridCell(5, 5))
    draft.set_player_spawn(2, GridCell(6, 6))
    assert [spawn.slot for spawn in draft.player_spawns] == [1, 2, 3]


def test_the_next_free_slot_fills_the_lowest_gap() -> None:
    draft = make_draft(player_cells=((4, 14), (5, 14)))
    assert draft.next_free_slot() == 3
    draft.remove_player_spawn(1)
    assert draft.next_free_slot() == 1


def test_enemy_spawns_are_a_set_in_effect() -> None:
    draft = make_draft()
    assert draft.add_enemy_spawn(GridCell(9, 9)) is True
    assert draft.add_enemy_spawn(GridCell(9, 9)) is False
    assert draft.remove_enemy_spawn(GridCell(9, 9)) is True
    assert draft.remove_enemy_spawn(GridCell(9, 9)) is False


def test_removing_a_spawn_by_cell_finds_either_kind() -> None:
    draft = make_draft()
    player_cell = draft.player_spawns[0].cell
    enemy_cell = draft.enemy_spawns[0]
    assert draft.remove_spawn_at(player_cell) is True
    assert draft.remove_spawn_at(enemy_cell) is True
    assert draft.remove_spawn_at(GridCell(11, 11)) is False
    assert draft.player_spawns == []
    assert draft.enemy_spawns == []


def test_editing_preserves_identity_provenance_and_waves() -> None:
    """A converted level keeps the only record of where its layout came from."""
    source = LevelSource(repository="https://example.invalid/x", revision="0" * 40, path="s.py")
    draft = LevelDraft.from_level(make_level(source=source, waves=(Wave(enemies=4),)))
    draft.paint(GridCell(1, 1), TileCode.WATER)
    document = draft.to_document()
    assert document["id"] == "test-level"
    assert document["source"] == {
        "repository": "https://example.invalid/x",
        "revision": "0" * 40,
        "path": "s.py",
    }
    assert document["waves"] == [{"enemies": 4}]


def test_a_level_without_provenance_declares_no_source_key() -> None:
    assert "source" not in make_draft().to_document()
    assert "waves" not in make_draft().to_document()


def test_a_draft_round_trips_through_the_loader_unchanged() -> None:
    level = make_level(
        source=LevelSource(repository="https://example.invalid/x", revision="a" * 40, path="s"),
        waves=(Wave(enemies=2), Wave(enemies=5)),
        player_cells=((4, 14), (5, 14)),
        enemy_cells=((0, 0), (15, 0)),
    )
    draft = LevelDraft.from_level(level)
    reloaded = validate_level_bytes(
        serialize_document(draft.to_document()), document=str(level.origin)
    )
    assert semantics(reloaded) == semantics(level)


def test_the_encoding_is_stable_for_one_document() -> None:
    draft = make_draft()
    assert serialize_document(draft.to_document()) == serialize_document(draft.to_document())
    assert serialize_document(draft.to_document()).endswith(b"\n")
