"""Render the accessibility screens and options to PNGs, and check the tracked captures.

Two jobs, kept apart as the client's, the campaign's and the persistence suites keep
them. Rendering runs on every test run, into a scratch directory, so a broken screen
fails the suite rather than waiting to be noticed in a pull request. The images
committed under ``screenshots/`` are rewritten only when someone asks for it::

    BATTLE_CITY_REFRESH_CAPTURES=1 \\
        uv run --locked pytest tests/accessibility/test_accessibility_screenshots.py

Nothing here is staged. The options screen is drawn from the preferences a player would
have set with the keys that set them, the enlarged capture is the same frame at the
window scale that enlarges it, the contrast captures are the two palettes the option
switches between, and the non-colour capture is a real seated client reading a real
server snapshot and a real server roster. ``README.md`` beside the images records the
display and device conditions, which is the half of a screenshot that makes it evidence.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import pygame
import pytest
from accessibility_helpers import (
    CAPTURE_SCALE,
    ENLARGED_CAPTURE_SCALE,
    REFRESH_CAPTURES_ENV,
    SCREENSHOT_DIR,
    capture_directory,
    make_shell,
    online_shell,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)
from battle_city_client import theme
from battle_city_client.accessibility import (
    AccessibilityPreferences,
    GamepadControl,
    GamepadControlKind,
)
from battle_city_client.assets import ProceduralAssetLibrary
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.rendering import Renderer
from battle_city_client.shell import ClientShell, Screen
from battle_city_sim import DEFAULT_RULES, Direction

MAX_CAPTURE_BYTES = 256 * 1024
EXPECTED_CAPTURES = (
    "01-options-default.png",
    "02-options-enlarged.png",
    "03-options-high-contrast.png",
    "04-run-default-contrast.png",
    "05-run-high-contrast.png",
    "06-online-non-colour-cues.png",
    "07-options-capturing-a-binding.png",
)


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    """Where this run writes: scratch, unless a refresh was explicitly asked for."""
    return capture_directory(tmp_path)


def _capture(
    shell: ClientShell,
    name: str,
    *,
    directory: Path,
    scale: int = CAPTURE_SCALE,
    palette: theme.Palette = theme.DEFAULT_PALETTE,
) -> Path:
    """Render ``shell`` into the logical frame at ``palette`` and write a PNG."""
    if not pygame.display.get_init():
        pygame.display.init()
    surface = pygame.Surface(theme.LOGICAL_SIZE)
    Renderer(ProceduralAssetLibrary(DEFAULT_RULES, palette)).render(surface, shell)
    scaled = pygame.transform.scale(
        surface, (theme.LOGICAL_SIZE[0] * scale, theme.LOGICAL_SIZE[1] * scale)
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path


# -- the shells the captures are taken from -----------------------------------


def _options_shell(*, contrast: theme.ContrastMode = theme.ContrastMode.DEFAULT) -> ClientShell:
    """The options screen, reached the way a player reaches it."""
    shell = make_shell()
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert shell.screen is Screen.OPTIONS
    if contrast is theme.ContrastMode.HIGH:
        shell.handle(Action.UI_RIGHT)
        assert shell.accessibility.contrast is theme.ContrastMode.HIGH
    return shell


def _capturing_shell() -> ClientShell:
    """The options screen with the *fire* row armed and a conflict already reported."""
    shell = _options_shell()
    while shell.options.row(shell.accessibility).action is not Action.FIRE:
        shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert shell.capturing
    assert not shell.capture_control(GamepadControl(GamepadControlKind.AXIS, 1, -1))
    return shell


def _run_shell() -> ClientShell:
    """A live local run, so the contrast captures show the board rather than a menu."""
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(90, PlayerIntent(direction=Direction.DOWN, fire=True))
    assert shell.screen is Screen.PLAYING
    return shell


# -- captures ------------------------------------------------------------------


def test_capture_the_options_screen(captures: Path) -> None:
    """Every preference, every binding, and the four things the screen qualifies."""
    path = _capture(_options_shell(), "01-options-default", directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_options_screen_enlarged(captures: Path) -> None:
    """The same frame at the window scale that enlarges the interface.

    Nothing in the frame moved: the logical frame is one size and the window shows a
    whole multiple of it, which is what keeps the text pixel-exact when it is bigger.
    """
    path = _capture(
        _options_shell(),
        "02-options-enlarged",
        directory=captures,
        scale=ENLARGED_CAPTURE_SCALE,
    )
    assert path.stat().st_size > 0


def test_capture_the_options_screen_in_high_contrast(captures: Path) -> None:
    shell = _options_shell(contrast=theme.ContrastMode.HIGH)
    path = _capture(
        shell,
        "03-options-high-contrast",
        directory=captures,
        palette=theme.palette_for(shell.accessibility.contrast),
    )
    assert path.stat().st_size > 0


def test_capture_a_run_in_both_palettes(captures: Path) -> None:
    """The same run, the same tick, the two palettes the contrast option chooses."""
    shell = _run_shell()
    default = _capture(shell, "04-run-default-contrast", directory=captures)
    high = _capture(
        shell,
        "05-run-high-contrast",
        directory=captures,
        palette=theme.HIGH_CONTRAST_PALETTE,
    )
    assert default.read_bytes() != high.read_bytes()


def test_capture_the_non_colour_team_and_seat_cues(captures: Path) -> None:
    """A real seated client: two tanks, two teams, told apart by shape and by words."""
    shell = online_shell(teams=True)
    path = _capture(shell, "06-online-non-colour-cues", directory=captures)
    assert path.stat().st_size > 0


def test_capture_a_binding_being_captured(captures: Path) -> None:
    """An armed row, the prompt, what cancels it, and a conflict in the words it uses."""
    shell = _capturing_shell()
    assert "ALREADY" in shell.options.notice
    path = _capture(shell, "07-options-capturing-a-binding", directory=captures)
    assert path.stat().st_size > 0


# -- the tracked captures ---------------------------------------------------------


def test_write_the_capture_notes(captures: Path) -> None:
    """Record the display and device conditions; a screenshot without them proves little."""
    path = captures / "README.md"
    path.write_text(_capture_notes(), encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in written
    assert "no pad" in written.lower()
    for name in EXPECTED_CAPTURES:
        assert name in written


def test_rendering_is_reproducible_within_a_run(tmp_path: Path) -> None:
    """Two renders of one state must be identical, or a refreshed capture is noise."""
    first = _capture(_options_shell(), "repeat-a", directory=tmp_path, scale=1)
    second = _capture(_options_shell(), "repeat-b", directory=tmp_path, scale=1)
    assert first.read_bytes() == second.read_bytes()


def test_the_enlarged_capture_is_the_larger_image() -> None:
    """The claim the pair of captures makes, stated as arithmetic rather than as a look."""
    assert ENLARGED_CAPTURE_SCALE > CAPTURE_SCALE
    assert theme.LOGICAL_SIZE[0] * ENLARGED_CAPTURE_SCALE > theme.LOGICAL_SIZE[0] * CAPTURE_SCALE


def test_the_committed_captures_are_present_and_reasonably_sized() -> None:
    assert tuple(sorted(path.name for path in SCREENSHOT_DIR.glob("*.png"))) == EXPECTED_CAPTURES
    for name in EXPECTED_CAPTURES:
        size = (SCREENSHOT_DIR / name).stat().st_size
        assert 0 < size < MAX_CAPTURE_BYTES, name


def test_the_committed_notes_record_the_conditions() -> None:
    notes = (SCREENSHOT_DIR / "README.md").read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in notes
    assert "no pad" in notes.lower()
    for name in EXPECTED_CAPTURES:
        assert name in notes


def _capture_notes() -> str:
    driver: Any = pygame.display.get_driver()
    sdl = ".".join(str(part) for part in pygame.version.SDL)
    frame = theme.LOGICAL_SIZE
    preferences = AccessibilityPreferences()
    return f"""# Accessibility screen captures

Generated by `tests/accessibility/test_accessibility_screenshots.py`. Do not edit by
hand.

Ordinary test runs render these screens into a scratch directory and leave this one
alone. To refresh the tracked images after a deliberate visual change:

```sh
{REFRESH_CAPTURES_ENV}=1 \\
    uv run --locked pytest tests/accessibility/test_accessibility_screenshots.py
```

| Capture | Screen |
| --- | --- |
| `01-options-default.png` | Options at the default contrast: every preference and binding |
| `02-options-enlarged.png` | The same frame at {ENLARGED_CAPTURE_SCALE}x window scale |
| `03-options-high-contrast.png` | The same screen after one press of `RIGHT` on the contrast row |
| `04-run-default-contrast.png` | A live run at the default palette |
| `05-run-high-contrast.png` | The same run, same tick, high-contrast palette |
| `06-online-non-colour-cues.png` | A seated online client: bracket, team pips, seat list |
| `07-options-capturing-a-binding.png` | The *fire* row armed, and a refused pad control |

## How they were taken

| | |
| --- | --- |
| Logical frame | {frame[0]}x{frame[1]} at every scale; the window shows a whole multiple of it |
| Capture scale | {CAPTURE_SCALE}x, and {ENLARGED_CAPTURE_SCALE}x for `02-options-enlarged.png` |
| SDL video driver | `{driver}` (`SDL_VIDEODRIVER=dummy`) |
| SDL audio driver | `dummy`; the client initialises no mixer and plays no sound |
| Input devices | Keyboard. **No pad was attached**, so every pad row shows a shipped default |
| pygame-ce | {pygame.version.ver} (SDL {sdl}) |
| Python | {platform.python_version()} on {platform.system()} {platform.machine()} |
| Tile size | {DEFAULT_RULES.tile_size} px, from `battle_city_sim.DEFAULT_RULES` |
| Default dead zone | {preferences.dead_zone_percent}% |
| Default repeat | {preferences.repeat.delay_ms} ms then every {preferences.repeat.interval_ms} ms |

These last rows are why the images are not regenerated on every run: they describe one
machine, and rewriting them from an unrelated test run would replace a deliberate record
with whichever machine happened to run the suite.

## What these images do not show

- **No animation, anywhere.** Reduced motion has nothing to hold still in this build, so
  there is no pair of captures for it; the preference is carried to the renderer and
  `test_accessibility_presentation` asserts both halves -- that it arrives, and that it
  changes no pixel today.
- **No audio.** The sound rows are a preference model with no engine behind them, and
  the screen says so rather than implying a slider that does something.
- **No real pad.** Every pad binding shown is the shipped default. Confirming that a
  particular controller reports the axis and button numbering this build assumes needs
  hardware the suite does not have; that is why all of them are rebindable, and it is
  recorded as a remaining risk.
"""
