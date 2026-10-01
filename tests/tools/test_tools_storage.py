"""Saving a level: validated first, written atomically, and never where it was told not to."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
from battle_city_content import GridCell, TileCode, load_level
from battle_city_tools import (
    BUNDLED_ROOT,
    DocumentInvalid,
    LevelDraft,
    ToolRefusal,
    read_level_draft,
    save_level,
)
from battle_city_tools.validation import SCRATCH_PREFIX
from tools_helpers import make_draft, make_level, semantics, write_level


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_edit_save_and_reload_round_trip(tmp_path: Path) -> None:
    """The whole authoring loop: a new level, an edit, a save, and a reload that agrees."""
    draft = LevelDraft.blank(level_id="round-trip", name="Round Trip")
    draft.paint(GridCell(2, 2), TileCode.BRICK)
    draft.paint(GridCell(3, 2), TileCode.CRACKED_BRICK)
    draft.set_player_spawn(2, GridCell(9, 14))
    draft.add_enemy_spawn(GridCell(15, 0))

    target = tmp_path / "round-trip.json"
    report = save_level(draft, target)
    assert report.path == target
    assert report.level_id == "round-trip"
    assert report.bytes_written == target.stat().st_size

    reopened = read_level_draft(target)
    assert reopened.grid_rows() == draft.grid_rows()
    assert reopened.player_spawns == draft.player_spawns
    assert reopened.enemy_spawns == draft.enemy_spawns

    saved_again = tmp_path / "again.json"
    save_level(reopened, saved_again)
    assert saved_again.read_bytes() == target.read_bytes()


def test_a_saved_level_loads_as_the_same_level(tmp_path: Path) -> None:
    """Everything but the file it came from survives the trip through JSON."""
    level = make_level(level_id="semantic", name="Semantic", player_cells=((4, 14), (5, 14)))
    target = tmp_path / "semantic.json"
    save_level(LevelDraft.from_level(level), target)
    assert semantics(load_level(target)) == semantics(level)


def test_an_existing_file_is_refused_until_overwrite_is_asked_for(tmp_path: Path) -> None:
    draft = make_draft()
    target = tmp_path / "level.json"
    save_level(draft, target)
    original = target.read_bytes()

    draft.paint(GridCell(1, 1), TileCode.STONE)
    with pytest.raises(ToolRefusal, match="already exists"):
        save_level(draft, target)
    assert target.read_bytes() == original

    save_level(draft, target, overwrite=True)
    assert target.read_bytes() != original


def test_the_bundled_pack_is_refused_even_with_overwrite() -> None:
    """The packaged levels are the regression fixtures; no flag unlocks writing them."""
    target = BUNDLED_ROOT / "levels" / "classic-01.json"
    with pytest.raises(ToolRefusal, match="bundled content root"):
        save_level(make_draft(), target, overwrite=True)


def test_a_symlink_into_the_bundled_pack_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "sneaky.json"
    link.symlink_to(BUNDLED_ROOT / "levels" / "classic-01.json")
    with pytest.raises(ToolRefusal, match="bundled content root"):
        save_level(make_draft(), link, overwrite=True)


def test_an_invalid_draft_is_reported_by_field_and_nothing_is_written(tmp_path: Path) -> None:
    draft = make_draft()
    draft.paint(GridCell(7, 15), TileCode.EMPTY)
    target = tmp_path / "no-base.json"
    with pytest.raises(DocumentInvalid) as raised:
        save_level(draft, target)
    assert raised.value.diagnostic.field == "grid.rows"
    assert "exactly one home base" in raised.value.diagnostic.message
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_spawn_on_blocking_terrain_names_the_spawn(tmp_path: Path) -> None:
    draft = make_draft()
    draft.paint(draft.player_spawns[0].cell, TileCode.BRICK)
    with pytest.raises(DocumentInvalid) as raised:
        save_level(draft, tmp_path / "blocked.json")
    assert raised.value.diagnostic.field == "spawns.players[0]"
    assert "must be empty ground" in raised.value.diagnostic.message


def test_a_diagnostic_names_the_target_and_never_the_scratch_copy(tmp_path: Path) -> None:
    draft = make_draft()
    draft.paint(GridCell(7, 15), TileCode.EMPTY)
    target = tmp_path / "named.json"
    with pytest.raises(DocumentInvalid) as raised:
        save_level(draft, target)
    assert raised.value.diagnostic.document == str(target)
    assert SCRATCH_PREFIX not in str(raised.value)


def test_validation_leaves_no_scratch_directory_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_root))

    save_level(make_draft(), tmp_path / "ok.json")
    broken = make_draft()
    broken.paint(GridCell(7, 15), TileCode.EMPTY)
    with pytest.raises(DocumentInvalid):
        save_level(broken, tmp_path / "bad.json")

    assert [path.name for path in scratch_root.iterdir() if SCRATCH_PREFIX in path.name] == []


def test_the_atomic_temporary_file_lives_in_the_destination_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` cannot rename across filesystems, so the staging file stays local."""
    seen: list[str] = []
    real = os.replace

    def recording(source: Any, destination: Any, **kwargs: Any) -> None:
        seen.append(str(Path(source).parent))
        real(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", recording)
    target = tmp_path / "nested" / "level.json"
    save_level(make_draft(), target)
    assert seen == [str(target.parent)]


def test_a_new_file_is_created_the_way_the_umask_says(tmp_path: Path) -> None:
    """A level is ordinary output: ``0o666`` narrowed by the umask, not a private file."""
    previous = os.umask(0o022)
    try:
        save_level(make_draft(), tmp_path / "default.json")
    finally:
        os.umask(previous)
    assert mode_of(tmp_path / "default.json") == 0o644


def test_a_restrictive_umask_is_not_relaxed(tmp_path: Path) -> None:
    previous = os.umask(0o077)
    try:
        save_level(make_draft(), tmp_path / "private.json")
    finally:
        os.umask(previous)
    assert mode_of(tmp_path / "private.json") == 0o600


@pytest.mark.parametrize("existing", [0o644, 0o664, 0o600, 0o640])
def test_overwriting_keeps_the_mode_the_file_already_had(tmp_path: Path, existing: int) -> None:
    """Staging creates the file that lands, so the replaced file's mode has to be carried."""
    target = write_level(tmp_path / "level.json", make_draft())
    target.chmod(existing)
    previous = os.umask(0o077)
    try:
        save_level(make_draft(), target, overwrite=True)
    finally:
        os.umask(previous)
    assert mode_of(target) == existing


def test_a_failed_write_leaves_no_temporary_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "level.json"

    def failing(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="disk went away"):
        save_level(make_draft(), target)

    assert list(tmp_path.iterdir()) == []


def test_a_symbolic_link_is_replaced_rather_than_followed(tmp_path: Path) -> None:
    """The link's own ``0o777`` bits are not a mode to inherit, and the target is left alone."""
    behind = write_level(tmp_path / "behind.json", make_draft())
    behind.chmod(0o640)
    link = tmp_path / "link.json"
    link.symlink_to(behind)

    previous = os.umask(0o022)
    try:
        save_level(make_draft(), link, overwrite=True)
    finally:
        os.umask(previous)

    assert link.is_symlink() is False
    assert mode_of(link) == 0o644
    assert mode_of(behind) == 0o640


def test_a_malformed_file_is_refused_when_opened(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(DocumentInvalid):
        read_level_draft(broken)


def test_a_file_that_is_json_but_not_a_level_names_a_field(tmp_path: Path) -> None:
    draft = make_draft()
    draft.rows[0] = list("x" * 16)
    path = write_level(tmp_path / "bad-tile.json", draft)
    with pytest.raises(DocumentInvalid) as raised:
        read_level_draft(path)
    assert "grid.rows[0]" in str(raised.value)
