"""Render every screen to a PNG, and check the captures tracked in git.

Two jobs, kept apart on purpose.

Rendering runs on every test run, into a scratch directory, so a broken screen fails the
suite rather than waiting to be noticed in a pull request. The captures committed under
``screenshots/`` are only rewritten when someone asks for it with
``BATTLE_CITY_REFRESH_CAPTURES=1``; they record the SDL build, the Python version and the
platform that produced them, so regenerating them from an ordinary ``pytest`` or ``make
ci`` would dirty the working tree on every run and make the images say something
different on every contributor's machine.

The terminal capture is taken from a state the test assembled and is stamped with a
banner that says so. The client cannot reach an outcome in this build, and a screenshot
that did not say which kind it was would be a claim the code does not support.

The three gimmick captures are ordinary gameplay and ordinary editing. The stage is the
bundled sample pack, loaded through :func:`battle_city_client.stage_adapter.resolve_pack`
exactly as ``--pack gimmick-demo`` loads it, handed to a real ``ClientShell``, started
through the menu and advanced with the intents a held key produces. The editor capture
opens that same level in a real ``EditorDocument``, paints through a real ``EditorState``
and is drawn by the real ``EditorRenderer``. Nothing here is composited, overlaid or
drawn by the test: a synthetic picture of a feature is not evidence that the feature
works, which is why the one assembled state in this file is labelled in its own filename.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pygame
import pytest
from battle_city_client import Action, PlayerIntent, theme
from battle_city_client.editor.document import EditorDocument
from battle_city_client.editor.state import EditorState, Tool
from battle_city_client.shell import ClientShell, Screen
from battle_city_client.stage_adapter import (
    bundled_stage_catalog,
    pack_stage_catalog,
    resolve_pack,
)
from battle_city_content import GIMMICK_DEMO_PACK_PATH, GridCell, TileCode
from battle_city_sim import DEFAULT_RULES, Direction, RunOutcome, Tile, centre_cell
from client_helpers import (
    REFRESH_CAPTURES_ENV,
    SCREENSHOT_DIR,
    capture,
    capture_directory,
    capture_editor_state,
    finished_session,
    make_shell,
    observed,
)

CAPTURE_SCALE = 2
STAGE_TICKS = 150
EXPECTED_CAPTURES = (
    "01-main-menu.png",
    "02-stage-select.png",
    "03-controls.png",
    "04-stage-and-hud.png",
    "05-pause.png",
    "06-run-over-TEST-FIXTURE.png",
    "07-gimmick-conveyors.png",
    "08-gimmick-teleport-pads.png",
    "09-gimmick-editor.png",
)
MAX_CAPTURE_BYTES = 256 * 1024

GIMMICK_PACK = "gimmick-demo"
GIMMICK_LEVEL = "gimmick-demo-01"

BELT_TICKS = 16
"""Held UP from the spawn: far enough to be standing on the loop's westward belt.

The tank is driving north while the belt carries it west, which is the behaviour the
capture is for. The exact number is pinned because the image is: a different count is a
different picture, and the assertions below say which one.
"""

PAD_APPROACH_TICKS = 12
PAD_TRANSIT_TICKS = 37
"""Held RIGHT then held UP: the shortest route from the spawn onto the inner pad.

On the last of those ticks the tank's centre enters the pad at (8, 7) and the server-side
rule transports it to the partner at (2, 2), keeping its pixel offset -- which is how a
tank driving north out of the middle of the board ends the tick in the top-left corner.
The capture is of the arrival.
"""
PAD_ARRIVAL_CELL = (2, 2)


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    """The directory this run writes to: scratch, unless a refresh was asked for."""
    return capture_directory(tmp_path)


def _bundled_shell() -> ClientShell:
    return ClientShell(catalog=bundled_stage_catalog())


def _gimmick_shell() -> ClientShell:
    """The bundled sample pack, reached the way ``--pack gimmick-demo`` reaches it."""
    return ClientShell(catalog=pack_stage_catalog(GIMMICK_PACK))


def _gimmick_run(*held: tuple[int, Direction]) -> ClientShell:
    """Start the sample stage from the menu and play it with held directions, in order."""
    shell = _gimmick_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    for ticks, direction in held:
        shell.advance(ticks, PlayerIntent(direction=direction))
    return shell


def _player_cell(shell: ClientShell) -> tuple[GridCell, Tile]:
    """Which cell the player's tank is standing in, and what is under it."""
    session = shell.session
    assert session is not None
    tank = next(iter(session.state.tanks))
    cell = centre_cell(tank.position, DEFAULT_RULES)
    return GridCell(cell.x, cell.y), session.state.grid.at(cell)


def _authored_editor_state() -> EditorState:
    """The sample level open in the editor, with the pad swatch selected and checked."""
    pack = resolve_pack(GIMMICK_PACK)
    level = pack.level(GIMMICK_LEVEL)
    state = EditorState(document=EditorDocument.from_level(level))
    state.select_tile(list(TileCode).index(TileCode.TELEPORT_PAD))
    state.set_tool(Tool.PAINT)
    state.hover = GridCell(8, 7)
    state.check()
    return state


def _played_shell() -> ClientShell:
    """Classic stage 1, driven with the same intents a keyboard produces."""
    shell = _bundled_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(STAGE_TICKS, PlayerIntent(direction=Direction.DOWN, fire=True))
    return shell


def test_capture_the_main_menu(captures: Path) -> None:
    path = capture(_bundled_shell(), "01-main-menu", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_stage_selection(captures: Path) -> None:
    shell = _bundled_shell()
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.STAGE_SELECT
    path = capture(shell, "02-stage-select", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_controls_screen(captures: Path) -> None:
    shell = _bundled_shell()
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.CONTROLS
    path = capture(shell, "03-controls", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_an_active_stage_with_its_hud(captures: Path) -> None:
    shell = _played_shell()
    assert observed(shell.screen) is Screen.PLAYING
    assert shell.session is not None
    assert shell.session.state.tick == STAGE_TICKS
    path = capture(shell, "04-stage-and-hud", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_pause_screen(captures: Path) -> None:
    shell = _played_shell()
    shell.handle(Action.TOGGLE_PAUSE)
    assert observed(shell.screen) is Screen.PAUSED
    path = capture(shell, "05-pause", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_injected_terminal_screen(captures: Path) -> None:
    """Labelled in the filename and stamped on the image: this state was assembled."""
    shell = _bundled_shell()
    shell.open_session(
        finished_session(RunOutcome.BASE_DESTROYED, stage=shell.catalog[0].stage, ticks=90)
    )
    assert observed(shell.screen) is Screen.RUN_OVER
    path = capture(
        shell,
        "06-run-over-TEST-FIXTURE",
        scale=CAPTURE_SCALE,
        fixture=True,
        directory=captures,
    )
    assert path.stat().st_size > 0


def test_capture_a_tank_riding_a_conveyor(captures: Path) -> None:
    """Real gameplay on the sample stage: the player is on the belt loop, being carried."""
    shell = _gimmick_run((BELT_TICKS, Direction.UP))
    assert observed(shell.screen) is Screen.PLAYING
    _, tile = _player_cell(shell)
    assert tile is Tile.CONVEYOR_W, "the capture is of a tank standing on the belt"
    path = capture(shell, "07-gimmick-conveyors", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_paired_teleport_pads(captures: Path) -> None:
    """Real gameplay again: the player drove into one pad and came out of the other."""
    shell = _gimmick_run((PAD_APPROACH_TICKS, Direction.RIGHT), (PAD_TRANSIT_TICKS, Direction.UP))
    assert observed(shell.screen) is Screen.PLAYING
    session = shell.session
    assert session is not None
    assert len(session.state.grid.positions_of(Tile.TELEPORT_PAD)) == 2

    cell, tile = _player_cell(shell)
    assert tile is Tile.TELEPORT_PAD
    assert (cell.x, cell.y) == PAD_ARRIVAL_CELL, (
        "the tank is on the partner pad, which it can only have reached by transport"
    )
    path = capture(shell, "08-gimmick-teleport-pads", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_sample_stage_open_in_the_editor(captures: Path) -> None:
    """A real editor frame of the authored sample: palette, pads, and a passing check."""
    state = _authored_editor_state()
    assert state.valid
    assert state.selected_tile is TileCode.TELEPORT_PAD
    assert len(state.document.teleport_pads) == 2
    assert state.document.effective_schema_version == 2
    path = capture_editor_state(state, "09-gimmick-editor", scale=CAPTURE_SCALE, directory=captures)
    assert path.stat().st_size > 0


def test_write_the_capture_notes(captures: Path) -> None:
    """Record what produced the images, since a screenshot without them proves little."""
    path = captures / "README.md"
    path.write_text(_capture_notes(), encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert "TEST-FIXTURE" in written
    assert "injected" in written.lower()
    assert REFRESH_CAPTURES_ENV in written


def test_rendering_is_reproducible_within_a_run(tmp_path: Path) -> None:
    """Two renders of one state must be identical, or a refreshed capture is noise.

    Always scratch, never the tracked directory: these two are a comparison, not a
    capture, and a refresh run must not leave them behind next to the real images.
    """
    first = capture(_played_shell(), "repeat-a", scale=1, directory=tmp_path)
    second = capture(_played_shell(), "repeat-b", scale=1, directory=tmp_path)
    assert first.read_bytes() == second.read_bytes()


def test_the_committed_captures_are_present_and_reasonably_sized() -> None:
    """The images the pull request points at are tracked, and have not ballooned."""
    assert tuple(sorted(path.name for path in SCREENSHOT_DIR.glob("*.png"))) == EXPECTED_CAPTURES
    for name in EXPECTED_CAPTURES:
        size = (SCREENSHOT_DIR / name).stat().st_size
        assert 0 < size < MAX_CAPTURE_BYTES, name


def test_the_committed_notes_label_the_fixture_capture() -> None:
    notes = (SCREENSHOT_DIR / "README.md").read_text(encoding="utf-8")
    assert "06-run-over-TEST-FIXTURE.png" in notes
    assert "injected" in notes.lower()
    assert REFRESH_CAPTURES_ENV in notes


def test_the_capture_directory_is_where_it_is_expected() -> None:
    assert Path(__file__).parent / "screenshots" == SCREENSHOT_DIR


def _capture_notes() -> str:
    driver = pygame.display.get_driver()
    window = theme.LOGICAL_SIZE[0] * CAPTURE_SCALE, theme.LOGICAL_SIZE[1] * CAPTURE_SCALE
    sdl = ".".join(str(part) for part in pygame.version.SDL)
    return f"""# Client screen captures

Generated by `tests/client/test_client_screenshots.py`. Do not edit by hand.

Ordinary test runs render these screens into a scratch directory and leave this one
alone. To refresh the tracked images after a deliberate visual change:

```sh
{REFRESH_CAPTURES_ENV}=1 uv run --locked pytest tests/client/test_client_screenshots.py
```

| Capture | Screen |
| --- | --- |
| `01-main-menu.png` | Main menu |
| `02-stage-select.png` | Bundled stage selection, with a live terrain preview |
| `03-controls.png` | Controls reference |
| `04-stage-and-hud.png` | Classic stage 1 after {STAGE_TICKS} ticks of held movement and fire |
| `05-pause.png` | Pause menu over the same run |
| `06-run-over-TEST-FIXTURE.png` | Terminal screen from an **injected** simulation state |
| `07-gimmick-conveyors.png` | Sample stage, {BELT_TICKS} ticks of held UP, tank on the belt |
| `08-gimmick-teleport-pads.png` | Sample stage, the tank just after a paired-pad transport |
| `09-gimmick-editor.png` | The same level in the editor, pad swatch selected, check passing |

## How they were taken

| | |
| --- | --- |
| Logical frame | {theme.LOGICAL_SIZE[0]}x{theme.LOGICAL_SIZE[1]} |
| Capture scale | {CAPTURE_SCALE}x (image is {window[0]}x{window[1]}) |
| SDL video driver | `{driver}` (`SDL_VIDEODRIVER=dummy`) |
| SDL audio driver | `dummy`; the client never initialises audio |
| Input device | Keyboard only. Intents are supplied directly, as the key tables map them |
| pygame-ce | {pygame.version.ver}, SDL {sdl} |
| Python | {platform.python_version()} on {sys.platform} |
| Tile size | {DEFAULT_RULES.tile_size} px, from `battle_city_sim.DEFAULT_RULES` |
| Seed | {make_shell().seed} |

These last rows are why the images are not regenerated on every run: they describe one
machine, and rewriting them from an unrelated test run would replace a deliberate record
with whichever machine happened to run the suite.

## The gimmick captures

`07`, `08` and `09` are the opt-in level schema version 2 terrain. The stage is the
bundled sample pack `{GIMMICK_PACK}` (`{GIMMICK_DEMO_PACK_PATH}`), loaded through the same
`resolve_pack` path `--pack {GIMMICK_PACK}` uses. The two gameplay shots are a real run:
the menu is confirmed twice and the run is advanced with the intents a held key produces,
so the tank's position in each image is one the simulation reached. In `08` the tank is
standing on the partner pad at {PAD_ARRIVAL_CELL}, which it can only have reached by being
transported there. The editor shot is a
real `EditorRenderer` frame of that level open in a real `EditorDocument`, with the
teleport-pad swatch selected and an explicit check passing. None of the three is a
composite, and nothing is drawn onto them by the test -- only `06`, the assembled
terminal state, carries a stamp, and it says so in its own filename.

The default menu still lists the three classic stages. The sample is reached by naming
it: `--pack {GIMMICK_PACK}` at launch, `pack_stage_catalog` from the catalog API, or the
editor.

## The fixture capture

`06-run-over-TEST-FIXTURE.png` shows a state the test assembled: the run was advanced for
real and then stamped with `BASE_DESTROYED`, together with the matching base damage. No
live run can reach a terminal outcome in this build, because it ships no enemy behaviour
and both outcomes the simulation defines need a hostile projectile. The client only ever
reads an outcome; it never sets one. Enemy waves and stage victory arrive with the AI and
campaign phases.
"""
