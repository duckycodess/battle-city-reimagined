"""Render the screens persistence added, and check the captures tracked in git.

Two jobs, kept apart as the client's and the campaign's capture suites keep them.
Rendering runs on every test run, into a scratch directory, so a broken screen fails the
suite rather than waiting to be noticed in a pull request. The images committed under
``screenshots/`` are rewritten only when someone asks for it::

    BATTLE_CITY_REFRESH_CAPTURES=1 \\
        uv run --locked pytest tests/persistence/test_persistence_screenshots.py

Nothing here is staged with a fabricated state. The options screen is drawn from a profile
that cleared a stage, the stage list from a checkpoint a campaign recorded, the HUD from a
run that is actually being played, and the recovery notice from a profile that was pointed
at a genuinely unreadable file on disk.

This is the only module in ``tests/persistence`` that loads pygame, and it loads it inside
the functions that need it. :func:`pygame_module_boundary` puts the interpreter back when
this module's last test finishes, so no ordering of test directories carries a display
library into ``tests/sim/test_purity.py``. There is deliberately no ``conftest.py`` and no
shared helper module in this directory; ``tests/campaign`` and ``tests/tools`` record the
reason, which is that ``mypy packages tests`` rejects a second module by a name that is
already taken.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import platform  # noqa: E402
import sys  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from battle_city_client.campaign import CampaignPhase, CampaignRules  # noqa: E402
from battle_city_client.intents import Action, PlayerIntent  # noqa: E402
from battle_city_client.persistence import (  # noqa: E402
    CampaignProgress,
    LocalProfile,
    LocalSettings,
    ProfileStore,
)
from battle_city_client.shell import ClientShell, Screen  # noqa: E402
from battle_city_client.stage_adapter import StageEntry  # noqa: E402
from battle_city_sim import (  # noqa: E402
    DEFAULT_RULES,
    GridPos,
    PlayerSpawn,
    Stage,
    TankVariant,
    Tile,
)

SCREENSHOT_DIR: Path = Path(__file__).parent / "screenshots"
REFRESH_CAPTURES_ENV: str = "BATTLE_CITY_REFRESH_CAPTURES"
CAPTURE_SCALE = 2
MAX_CAPTURE_BYTES = 256 * 1024
EXPECTED_CAPTURES = (
    "01-options-and-badges.png",
    "02-stage-select-with-a-save.png",
    "03-hud-with-the-chosen-badge.png",
    "04-unreadable-save-recovery.png",
    "05-saved-stage-no-longer-there.png",
)

FIRING = PlayerIntent(fire=True)
RANGE_RULES = CampaignRules(spawn_interval_ticks=5, spawn_variants=(TankVariant.ENEMY_NORMAL,))
CLEAR_BUDGET = 400


# -- the pygame boundary ------------------------------------------------------


def _release_pygame() -> None:
    """Shut pygame down and drop it, together with the client modules holding it.

    The dropped modules hold a reference to the module object being discarded, and a
    later re-import creates a new one; leaving the old reference would have half the
    client driving a pygame that has been shut down. Membership is decided by asking each
    loaded client module whether it has a ``pygame`` attribute, so a module that starts
    importing pygame is covered without anyone remembering this list.
    """
    module = sys.modules.get("pygame")
    if module is None:
        return
    if module.get_init():
        module.quit()
    stale = [
        name
        for name, loaded in sys.modules.items()
        if name == "pygame"
        or name.startswith("pygame.")
        or (name.startswith("battle_city_client") and getattr(loaded, "pygame", None) is not None)
    ]
    for name in stale:
        del sys.modules[name]


@pytest.fixture(scope="module", autouse=True)
def pygame_module_boundary() -> Iterator[None]:
    """Release pygame when this module's last test finishes."""
    yield
    _release_pygame()


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    """Where this run writes: scratch, unless a refresh was explicitly asked for."""
    if os.environ.get(REFRESH_CAPTURES_ENV) == "1":
        return SCREENSHOT_DIR
    return tmp_path


# -- rendering ----------------------------------------------------------------


def _capture(shell: ClientShell, name: str, *, directory: Path) -> Path:
    """Render ``shell`` into the logical frame and write a PNG."""
    import pygame
    from battle_city_client import theme
    from battle_city_client.assets import ProceduralAssetLibrary
    from battle_city_client.rendering import Renderer

    if not pygame.display.get_init():
        pygame.display.init()
    surface = pygame.Surface(theme.LOGICAL_SIZE)
    Renderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(surface, shell)
    scaled = pygame.transform.scale(
        surface,
        (theme.LOGICAL_SIZE[0] * CAPTURE_SCALE, theme.LOGICAL_SIZE[1] * CAPTURE_SCALE),
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path


# -- the shells the captures are taken from -----------------------------------


def _rows() -> tuple[str, ...]:
    grid = [[Tile.EMPTY for _ in range(16)] for _ in range(16)]
    grid[15][7] = Tile.HOME
    return tuple("".join(str(tile.value) for tile in row) for row in grid)


def _entry(level_id: str, *, waves: tuple[int, ...] = (1,)) -> StageEntry:
    stage = Stage.create(
        stage_id=level_id,
        name=level_id.upper(),
        rows=_rows(),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(7, 12)),),
        enemy_spawns=(GridPos(7, 6),),
    )
    return StageEntry(level_id=level_id, name=stage.name, stage=stage, waves=waves)


CATALOG = (_entry("range-01"), _entry("range-02"))


def _shell(profile: LocalProfile) -> ClientShell:
    return ClientShell(catalog=CATALOG, campaign_rules=RANGE_RULES, profile=profile)


def _played_through_a_stage(profile: LocalProfile) -> ClientShell:
    """A real campaign: clear the first stage, take the second, and stand in it."""
    shell = _shell(profile)
    assert shell.start_selected_stage()
    for _ in range(CLEAR_BUDGET):
        shell.advance(1, FIRING)
        if shell.phase is not CampaignPhase.PLAYING:
            break
    assert shell.phase is CampaignPhase.STAGE_CLEARED
    shell.handle(Action.UI_CONFIRM)
    shell.advance(20, FIRING)
    return shell


# -- captures -----------------------------------------------------------------


def test_capture_the_options_and_badges(captures: Path) -> None:
    """The controls screen, with the saved settings and one badge still locked."""
    profile = LocalProfile(
        settings=LocalSettings(scale=4, frame_cap=60, display_name="ducky"),
        progress=CampaignProgress(stages_cleared=1, best_score=900, selected_badge="veteran"),
    )
    shell = _shell(profile)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert shell.screen is Screen.CONTROLS
    assert shell.profile.badge.badge_id == "veteran"

    path = _capture(shell, "01-options-and-badges", directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_stage_list_with_a_save(captures: Path) -> None:
    """The saved stage beside the list, which still starts every stage fresh."""
    shell = _played_through_a_stage(LocalProfile())
    shell.handle(Action.TOGGLE_PAUSE)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)  # QUIT TO MENU
    shell.handle(Action.UI_CONFIRM)  # PLAY
    assert shell.screen is Screen.STAGE_SELECT
    assert shell.checkpoint is not None

    path = _capture(shell, "02-stage-select-with-a-save", directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_hud_with_the_chosen_badge(captures: Path) -> None:
    """A live run, with the badge on the local HUD and nowhere else."""
    profile = LocalProfile(progress=CampaignProgress(stages_cleared=1, selected_badge="veteran"))
    shell = _played_through_a_stage(profile)
    assert shell.screen is Screen.PLAYING
    assert shell.profile.badge.label == "VETERAN"

    path = _capture(shell, "03-hud-with-the-chosen-badge", directory=captures)
    assert path.stat().st_size > 0


def test_capture_the_recovery_notice(captures: Path, tmp_path: Path) -> None:
    """A genuinely unreadable file on disk, reported on the first screen."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "campaign.json").write_bytes(b"{ this file did not survive")
    profile = LocalProfile.load(ProfileStore(lambda: profile_dir))
    assert "UNREADABLE" in profile.recovery_notice

    path = _capture(_shell(profile), "04-unreadable-save-recovery", directory=captures)
    assert path.stat().st_size > 0


def test_capture_a_save_whose_stage_is_no_longer_there(captures: Path) -> None:
    """The same pack, the same level identifier, different stage data: refused, and kept."""
    played = _played_through_a_stage(LocalProfile())
    edited = ClientShell(
        catalog=(CATALOG[0], _entry("range-02", waves=(9,))),
        campaign_rules=RANGE_RULES,
        profile=LocalProfile(progress=played.profile.progress),
    )
    edited.handle(Action.UI_CONFIRM)
    edited.handle(Action.RESUME_SAVE)
    assert edited.screen is Screen.STAGE_SELECT
    assert edited.session is None
    assert edited.checkpoint is not None

    path = _capture(edited, "05-saved-stage-no-longer-there", directory=captures)
    assert path.stat().st_size > 0


# -- the tracked captures -----------------------------------------------------


def test_write_the_capture_notes(captures: Path) -> None:
    path = captures / "README.md"
    path.write_text(_capture_notes(), encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in written
    assert "badge" in written.lower()


def test_rendering_is_reproducible_within_a_run(tmp_path: Path) -> None:
    """Two renders of one state must be identical, or a refreshed capture is noise."""
    profile = LocalProfile(progress=CampaignProgress(stages_cleared=1, selected_badge="veteran"))
    first = _capture(_shell(profile), "repeat-a", directory=tmp_path)
    second = _capture(_shell(profile), "repeat-b", directory=tmp_path)
    assert first.read_bytes() == second.read_bytes()


def test_the_committed_captures_are_present_and_reasonably_sized() -> None:
    assert tuple(sorted(path.name for path in SCREENSHOT_DIR.glob("*.png"))) == EXPECTED_CAPTURES
    for name in EXPECTED_CAPTURES:
        size = (SCREENSHOT_DIR / name).stat().st_size
        assert 0 < size < MAX_CAPTURE_BYTES, name


def test_the_committed_notes_name_the_refresh_command() -> None:
    notes = (SCREENSHOT_DIR / "README.md").read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in notes
    for name in EXPECTED_CAPTURES:
        assert name in notes


def _capture_notes() -> str:
    import pygame

    driver: Any = pygame.display.get_driver()
    sdl = ".".join(str(part) for part in pygame.version.SDL)
    window = (
        theme_logical_size()[0] * CAPTURE_SCALE,
        theme_logical_size()[1] * CAPTURE_SCALE,
    )
    return f"""# Persistence screen captures

Generated by `tests/persistence/test_persistence_screenshots.py`. Do not edit by hand.

Ordinary test runs render these screens into a scratch directory and leave this one
alone. To refresh the tracked images after a deliberate visual change:

```sh
{REFRESH_CAPTURES_ENV}=1 uv run --locked pytest tests/persistence/test_persistence_screenshots.py
```

| Capture | Screen |
| --- | --- |
| `01-options-and-badges.png` | Controls and options: saved settings, a chosen badge, a locked one |
| `02-stage-select-with-a-save.png` | The stage list beside the saved stage |
| `03-hud-with-the-chosen-badge.png` | A live local run with the badge on its own HUD |
| `04-unreadable-save-recovery.png` | The main menu after a save file that could not be read |
| `05-saved-stage-no-longer-there.png` | A save refused, and kept, because its stage changed |

Every one is a real state. The campaign in captures 2, 3 and 5 cleared a stage by playing
it, the profile in capture 4 was pointed at a genuinely unreadable file on disk, and the
pack in capture 5 really does declare a different stage under the saved level's name.

## How they were taken

| | |
| --- | --- |
| Logical frame | {theme_logical_size()[0]}x{theme_logical_size()[1]} |
| Capture scale | {CAPTURE_SCALE}x (image is {window[0]}x{window[1]}) |
| SDL video driver | `{driver}` (`SDL_VIDEODRIVER=dummy`) |
| SDL audio driver | `dummy`; the client never initialises audio |
| pygame-ce | {pygame.version.ver} (SDL {sdl}) |
| Python | {platform.python_version()} on {platform.system()} {platform.machine()} |
"""


def theme_logical_size() -> tuple[int, int]:
    """The client's logical frame, imported where pygame is already loaded."""
    from battle_city_client import theme

    size: tuple[int, int] = theme.LOGICAL_SIZE
    return size
