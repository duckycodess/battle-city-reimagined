"""Render the editor's screens to PNGs, and check the captures tracked in git.

Two jobs, kept apart on purpose, exactly as ``tests/client`` does it.

Rendering runs on every test run, into a scratch directory, so a broken screen fails the
suite rather than waiting to be noticed in a pull request. The captures committed under
``screenshots/`` are only rewritten when someone asks for it with
``BATTLE_CITY_REFRESH_CAPTURES=1``: they record the SDL build, the Python version and the
platform that produced them, so regenerating them from an ordinary ``pytest`` or
``make ci`` would dirty the working tree on every run.

Every capture is of a real document. The grid shots are the bundled classic stage 1 read
through the content loader, and the error shot is a real validation failure from a real
edit -- the base was painted over -- rather than a diagnostic a test invented.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest
import tools_helpers  # noqa: F401  -- sets the SDL driver variables before anything else
from battle_city_client.editor.document import BUNDLED_ROOT, EditorDocument
from battle_city_client.editor.state import EditorState, Tool
from battle_city_content import GridCell, TileCode
from tools_helpers import (
    REFRESH_CAPTURES_ENV,
    SCREENSHOT_DIR,
    capture_directory,
    capture_editor,
    ensure_display,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)

CLASSIC_01 = BUNDLED_ROOT / "levels" / "classic-01.json"
CAPTURE_SCALE = 2
EXPECTED_CAPTURES = (
    "01-grid-editor.png",
    "02-tile-palette.png",
    "03-spawn-editing.png",
    "04-validation-error.png",
)
MAX_CAPTURE_BYTES = 256 * 1024


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    """The directory this run writes to: scratch, unless a refresh was asked for."""
    ensure_display()
    return capture_directory(tmp_path)


def _opened() -> EditorState:
    """Classic stage 1, opened for editing with no save target."""
    return EditorState(document=EditorDocument.open(CLASSIC_01))


def grid_state() -> EditorState:
    state = _opened()
    state.hover = GridCell(6, 5)
    state.check()
    return state


def palette_state() -> EditorState:
    """A tile picked out of the palette and painted, which is what the palette is for."""
    state = _opened()
    state.select_tile(5)
    for x in range(4, 12):
        state.apply_at(GridCell(x, 8))
    state.select_tile(7)
    for cell in (GridCell(4, 7), GridCell(5, 7), GridCell(10, 7), GridCell(11, 7)):
        state.apply_at(cell)
    state.hover = GridCell(11, 7)
    state.check()
    return state


def spawn_state() -> EditorState:
    state = _opened()
    state.set_tool(Tool.PLAYER_SPAWN)
    state.cycle_slot()
    state.apply_at(GridCell(1, 11))
    state.set_tool(Tool.ENEMY_SPAWN)
    state.apply_at(GridCell(7, 1))
    state.hover = GridCell(7, 1)
    state.check()
    return state


def error_state() -> EditorState:
    """A real failure from a real edit: the base was painted over."""
    state = _opened()
    state.select_tile(0)
    state.apply_at(GridCell(7, 15))
    state.check()
    return state


def test_capture_the_grid_editor(captures: Path) -> None:
    state = grid_state()
    assert state.valid is True
    assert (
        capture_editor(state, "01-grid-editor", directory=captures, scale=CAPTURE_SCALE)
        .stat()
        .st_size
        > 0
    )


def test_capture_the_tile_palette(captures: Path) -> None:
    state = palette_state()
    assert state.selected_tile is TileCode.FOREST
    assert state.document.tile_at(GridCell(7, 8)) is TileCode.WATER
    assert (
        capture_editor(state, "02-tile-palette", directory=captures, scale=CAPTURE_SCALE)
        .stat()
        .st_size
        > 0
    )


def test_capture_spawn_editing(captures: Path) -> None:
    state = spawn_state()
    assert state.document.spawn_label_at(GridCell(1, 11)) == "P2"
    assert state.document.spawn_label_at(GridCell(7, 1)) == "E"
    assert (
        capture_editor(state, "03-spawn-editing", directory=captures, scale=CAPTURE_SCALE)
        .stat()
        .st_size
        > 0
    )


def test_capture_a_validation_error(captures: Path) -> None:
    state = error_state()
    assert state.diagnostic is not None
    assert state.diagnostic.field == "grid.rows"
    assert (
        capture_editor(state, "04-validation-error", directory=captures, scale=CAPTURE_SCALE)
        .stat()
        .st_size
        > 0
    )


def test_write_the_capture_notes(captures: Path) -> None:
    """Record what produced the images, since a screenshot without them proves little."""
    captures.mkdir(parents=True, exist_ok=True)
    path = captures / "README.md"
    path.write_text(_capture_notes(), encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in written
    assert "04-validation-error.png" in written


def test_rendering_is_reproducible_within_a_run(tmp_path: Path) -> None:
    """Two renders of one state must be identical, or a refreshed capture is noise."""
    ensure_display()
    first = capture_editor(grid_state(), "repeat-a", directory=tmp_path, scale=1)
    second = capture_editor(grid_state(), "repeat-b", directory=tmp_path, scale=1)
    assert first.read_bytes() == second.read_bytes()


def test_the_screens_differ_from_one_another(tmp_path: Path) -> None:
    """Four captures of the same frame would document nothing."""
    ensure_display()
    rendered = {
        name: capture_editor(builder(), name, directory=tmp_path, scale=1).read_bytes()
        for name, builder in (
            ("grid", grid_state),
            ("palette", palette_state),
            ("spawn", spawn_state),
            ("error", error_state),
        )
    }
    assert len(set(rendered.values())) == len(rendered)


def test_the_committed_captures_are_present_and_reasonably_sized() -> None:
    assert tuple(sorted(path.name for path in SCREENSHOT_DIR.glob("*.png"))) == EXPECTED_CAPTURES
    for name in EXPECTED_CAPTURES:
        size = (SCREENSHOT_DIR / name).stat().st_size
        assert 0 < size < MAX_CAPTURE_BYTES, name


def test_the_committed_notes_explain_how_to_refresh_them() -> None:
    notes = (SCREENSHOT_DIR / "README.md").read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in notes
    for name in EXPECTED_CAPTURES:
        assert name in notes


def test_the_capture_directory_is_where_it_is_expected() -> None:
    assert Path(__file__).parent / "screenshots" == SCREENSHOT_DIR


def _capture_notes() -> str:
    import pygame
    from battle_city_client.editor import layout

    driver = pygame.display.get_driver()
    image = layout.LOGICAL_SIZE[0] * CAPTURE_SCALE, layout.LOGICAL_SIZE[1] * CAPTURE_SCALE
    sdl = ".".join(str(part) for part in pygame.version.SDL)
    return f"""# Level editor screen captures

Generated by `tests/tools/test_editor_screenshots.py`. Do not edit by hand.

Ordinary test runs render these screens into a scratch directory and leave this one
alone. To refresh the tracked images after a deliberate visual change:

```sh
{REFRESH_CAPTURES_ENV}=1 uv run --locked pytest tests/tools/test_editor_screenshots.py
```

| Capture | Screen |
| --- | --- |
| `01-grid-editor.png` | Classic stage 1 open for editing, cursor on a cell, checked valid |
| `02-tile-palette.png` | Water and forest chosen from the palette and painted in |
| `03-spawn-editing.png` | Player slot 2 and an extra enemy spawn placed with the spawn tools |
| `04-validation-error.png` | A real failure: the home base was painted over, then checked |

Every capture is of a real document. The layout is the bundled classic stage 1 read
through `battle_city_content.load_level`, the edits are the editor's own tools, and the
error is the content loader's own diagnostic for the edit above it -- not a message the
test invented.

## How they were taken

| | |
| --- | --- |
| Logical frame | {layout.LOGICAL_SIZE[0]}x{layout.LOGICAL_SIZE[1]} |
| Capture scale | {CAPTURE_SCALE}x (image is {image[0]}x{image[1]}) |
| SDL video driver | `{driver}` (`SDL_VIDEODRIVER=dummy`) |
| SDL audio driver | `dummy`; the editor never initialises audio |
| Input device | Mouse and keyboard; the state is driven directly, as the tables map it |
| pygame-ce | {pygame.version.ver}, SDL {sdl} |
| Python | {platform.python_version()} on {sys.platform} |
| Tile size | {layout.TILE_SIZE} px |

These last rows are why the images are not regenerated on every run: they describe one
machine, and rewriting them from an unrelated test run would replace a deliberate record
with whichever machine happened to run the suite.
"""
