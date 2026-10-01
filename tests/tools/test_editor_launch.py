"""``python -m battle_city_client.editor``: what the options do, and what main returns."""

from __future__ import annotations

from pathlib import Path

import pytest
import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor.document import BUNDLED_ROOT
from battle_city_content import TileCode
from tools_helpers import (
    ensure_display,
    observed,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)

CLASSIC_01 = BUNDLED_ROOT / "levels" / "classic-01.json"


def test_with_no_options_the_editor_opens_a_blank_valid_level() -> None:
    from battle_city_client.editor.app import open_state, parse_args

    state = open_state(parse_args([]))
    assert state.document.level_id == "new-level"
    assert state.document.path is None
    assert state.document.validate() is None


def test_a_level_is_opened_read_only_until_an_output_is_named() -> None:
    """The save target is never inferred from the file that was opened."""
    from battle_city_client.editor.app import open_state, parse_args

    state = open_state(parse_args(["--level", str(CLASSIC_01)]))
    assert state.document.level_id == "classic-01"
    assert state.document.opened_from == CLASSIC_01
    assert state.document.path is None
    assert state.save() is False
    assert "NO SAVE TARGET" in state.status


def test_an_output_makes_saving_possible(tmp_path: Path) -> None:
    from battle_city_client.editor.app import open_state, parse_args

    target = tmp_path / "copy.json"
    state = open_state(parse_args(["--level", str(CLASSIC_01), "--output", str(target)]))
    assert state.save() is True
    assert target.read_bytes() == CLASSIC_01.read_bytes()


def test_overwrite_is_opt_in(tmp_path: Path) -> None:
    from battle_city_client.editor.app import open_state, parse_args

    target = tmp_path / "copy.json"
    target.write_text("{}", encoding="utf-8")
    arguments = ["--level", str(CLASSIC_01), "--output", str(target)]
    assert open_state(parse_args(arguments)).save() is False
    assert open_state(parse_args([*arguments, "--overwrite"])).save() is True


def test_identity_can_be_restated_which_is_how_a_level_is_renamed() -> None:
    """The content specification calls a rename a new identifier, so it is explicit."""
    from battle_city_client.editor.app import open_state, parse_args

    state = open_state(
        parse_args(["--level", str(CLASSIC_01), "--id", "my-fork", "--name", "My Fork"])
    )
    assert state.document.level_id == "my-fork"
    assert state.document.name == "My Fork"
    assert state.document.source is not None


def test_a_rename_on_the_command_line_is_unsaved_work() -> None:
    """A document renamed and then closed has lost work, so closing must say so."""
    from battle_city_client.editor.app import open_state, parse_args

    state = open_state(parse_args(["--level", str(CLASSIC_01), "--id", "my-fork"]))
    assert state.document.dirty is True

    state.quit()
    assert observed(state.running) is True
    assert "UNSAVED" in state.status
    state.quit()
    assert observed(state.running) is False


def test_restating_the_identity_a_document_already_has_changes_nothing() -> None:
    from battle_city_client.editor.app import open_state, parse_args

    arguments = ["--level", str(CLASSIC_01), "--id", "classic-01", "--name", "Classic Stage 1"]
    state = open_state(parse_args(arguments))
    assert state.document.dirty is False
    state.quit()
    assert state.running is False


def test_a_blank_level_named_on_the_command_line_is_unsaved_work() -> None:
    """A fresh document is clean; naming it is the first edit made to it."""
    from battle_city_client.editor.app import open_state, parse_args

    assert open_state(parse_args([])).document.dirty is False
    assert open_state(parse_args(["--id", "my-level"])).document.dirty is True


def test_an_unreadable_level_is_reported_as_a_sentence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from battle_city_client.editor.app import main

    ensure_display()
    assert main(["--level", str(BUNDLED_ROOT / "levels" / "missing.json")]) == 1
    assert "cannot be opened" in capsys.readouterr().out


def test_main_launches_and_returns_cleanly() -> None:
    """The documented entry point, driven to completion by a close request."""
    import pygame
    from battle_city_client.editor.app import main

    ensure_display()
    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert main(["--scale", "1"]) == 0


def test_main_reports_a_missing_display_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine with no usable video driver gets a sentence, not a traceback."""
    import pygame
    from battle_city_client.editor.app import main

    ensure_display()

    def refuse() -> None:
        raise pygame.error("no available video device")

    monkeypatch.setattr(pygame.display, "init", refuse)
    assert main([]) == 2
    assert "no usable display" in capsys.readouterr().out


def test_the_blank_level_the_editor_opens_is_the_smallest_the_schema_accepts() -> None:
    from battle_city_client.editor.document import EditorDocument

    document = EditorDocument.blank()
    assert len(document.player_spawns) == 1
    assert len(document.enemy_spawns) == 1
    assert sum(row.count(TileCode.HOME.value) for row in document.grid_rows()) == 1
