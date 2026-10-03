"""Render the campaign's screens to PNGs, and check the captures tracked in git.

Two jobs, kept apart. Rendering runs on every test run, into a scratch directory, so a
broken screen fails the suite rather than waiting to be noticed in a pull request. The
captures committed under ``screenshots/`` are rewritten only when someone asks for it with
``BATTLE_CITY_REFRESH_CAPTURES=1``; they record the SDL build, the Python version and the
platform that produced them.

This is the only module in ``tests/campaign`` that loads pygame, and it loads it inside
the functions that need it. ``pygame_module_boundary`` puts the interpreter back when the
module's last test finishes, so no ordering of test directories carries a display library
into ``tests/sim/test_purity.py``.

Nothing here is a fixture capture. Every image is a real run: the campaign reaches a stage
clear, a completion and a loss on its own, which is the whole difference this phase makes.
The stage clear, completion and loss are taken on a stage built for a test, because the
classic mazes cannot be cleared without a pathfinder; the filename says so.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

import pytest
from battle_city_client.campaign import CampaignPhase, CampaignRules, IdleEnemyDriver
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.shell import ClientShell, Screen
from battle_city_client.stage_adapter import bundled_stage_catalog
from battle_city_sim import DEFAULT_RULES, Direction, TankVariant
from campaign_helpers import (
    FIRING,
    QUICK_INTERVAL,
    REFRESH_CAPTURES_ENV,
    TEST_SEED,
    ScriptedEnemyDriver,
    capture_directory,
    ensure_display,
    entry_for,
    firing_range_stage,
    observed,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)

CAPTURE_SCALE = 2
CLASSIC_TICKS = 150
EXPECTED_CAPTURES = (
    "01-campaign-select.png",
    "02-classic-stage-and-hud.png",
    "03-score-and-lives.png",
    "04-stage-clear.png",
    "05-campaign-complete.png",
    "06-game-over.png",
)

RANGE_RULES = CampaignRules(
    spawn_interval_ticks=QUICK_INTERVAL, spawn_variants=(TankVariant.ENEMY_NORMAL,)
)


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    """The directory this run writes to: scratch, unless a refresh was asked for."""
    return capture_directory(tmp_path)


# -- shells -------------------------------------------------------------------


def classic_shell() -> ClientShell:
    return ClientShell(catalog=bundled_stage_catalog())


def classic_run() -> ClientShell:
    """Classic stage 1, driven with the same intents a keyboard produces."""
    shell = classic_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(CLASSIC_TICKS, PlayerIntent(direction=Direction.DOWN, fire=True))
    return shell


def range_shell(*, stages: int = 2, quota: int = 2, driver: Any = None) -> ClientShell:
    catalog = tuple(
        entry_for(firing_range_stage(f"range-{index}"), waves=(quota,)) for index in range(stages)
    )
    shell = ClientShell(
        catalog=catalog,
        seed=TEST_SEED,
        campaign_rules=RANGE_RULES,
        driver=IdleEnemyDriver() if driver is None else driver,
    )
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    return shell


def played(shell: ClientShell, *, ticks: int = 600, intent: PlayerIntent = FIRING) -> ClientShell:
    for _ in range(ticks):
        if not shell.consumes_ticks:
            break
        shell.advance(1, intent)
    return shell


# -- capture ------------------------------------------------------------------


def capture(shell: ClientShell, name: str, *, directory: Path) -> Path:
    """Render ``shell`` into the logical frame and write a scaled PNG.

    pygame and the renderer are imported here rather than at module scope: pytest imports
    every selected module during collection, and this directory's rule is that collection
    pulls in no display library.
    """
    import pygame
    from battle_city_client import theme
    from battle_city_client.assets import ProceduralAssetLibrary
    from battle_city_client.rendering import Renderer

    ensure_display()
    directory.mkdir(parents=True, exist_ok=True)
    surface = pygame.Surface(theme.LOGICAL_SIZE)
    Renderer(ProceduralAssetLibrary(DEFAULT_RULES)).render(surface, shell)
    scaled = pygame.transform.scale(
        surface,
        (theme.LOGICAL_SIZE[0] * CAPTURE_SCALE, theme.LOGICAL_SIZE[1] * CAPTURE_SCALE),
    )
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path


# -- the captures -------------------------------------------------------------


def test_capture_campaign_selection(captures: Path) -> None:
    """Where a campaign is chosen, and where it says the enemies do not fire."""
    shell = classic_shell()
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.STAGE_SELECT
    assert capture(shell, "01-campaign-select", directory=captures).stat().st_size > 0


def test_capture_a_classic_stage_with_its_campaign_hud(captures: Path) -> None:
    shell = classic_run()
    assert observed(shell.screen) is Screen.PLAYING
    assert shell.campaign is not None
    assert shell.campaign.stage_number == 1
    assert shell.campaign.stage_count == 3
    assert shell.campaign.enemies_remaining == 5
    assert capture(shell, "02-classic-stage-and-hud", directory=captures).stat().st_size > 0


def test_capture_a_hud_with_a_score_on_it(captures: Path) -> None:
    """Classic stage 1 cannot be scored on inside a test; this stage can."""
    shell = range_shell()
    shell.advance(QUICK_INTERVAL, FIRING)
    assert shell.campaign is not None
    assert shell.campaign.score > 0
    assert shell.campaign.lives == RANGE_RULES.starting_lives
    assert capture(shell, "03-score-and-lives", directory=captures).stat().st_size > 0


def test_capture_a_stage_transition(captures: Path) -> None:
    shell = played(range_shell(stages=2))
    assert shell.phase is CampaignPhase.STAGE_CLEARED
    assert shell.interstitial_labels[0] == "NEXT STAGE"
    assert capture(shell, "04-stage-clear", directory=captures).stat().st_size > 0


def test_capture_a_completed_campaign(captures: Path) -> None:
    shell = played(range_shell(stages=1))
    assert shell.phase is CampaignPhase.COMPLETED
    assert capture(shell, "05-campaign-complete", directory=captures).stat().st_size > 0


def test_capture_a_lost_campaign(captures: Path) -> None:
    """A real loss, driven by a scripted enemy: nothing here is an injected state."""
    shell = played(
        range_shell(stages=1, quota=3, driver=ScriptedEnemyDriver()),
        ticks=4000,
        intent=PlayerIntent(),
    )
    assert shell.phase is CampaignPhase.FAILED
    assert shell.outcome is not None
    assert capture(shell, "06-game-over", directory=captures).stat().st_size > 0


def test_write_the_capture_notes(captures: Path) -> None:
    """Record what produced the images, since a screenshot without them proves little."""
    path = captures / "README.md"
    path.write_text(capture_notes(), encoding="utf-8")
    written = path.read_text(encoding="utf-8")
    assert REFRESH_CAPTURES_ENV in written
    assert "do not fire" in written
    for name in EXPECTED_CAPTURES:
        assert name in written


def capture_notes() -> str:
    import pygame

    return f"""# Campaign screen captures

Generated by `tests/campaign/test_campaign_screenshots.py`. Do not edit by hand.

Ordinary test runs render these screens into a scratch directory and leave this one
alone. To refresh the tracked images after a deliberate visual change:

```sh
{REFRESH_CAPTURES_ENV}=1 uv run --locked pytest tests/campaign/test_campaign_screenshots.py
```

| Capture | What it shows |
| --- | --- |
| `01-campaign-select.png` | Choosing where the campaign starts, over the bundled classic pack |
| `02-classic-stage-and-hud.png` | Classic stage 1 after {CLASSIC_TICKS} ticks, campaign HUD |
| `03-score-and-lives.png` | Score and lives after a kill, on a stage built for the test |
| `04-stage-clear.png` | The interstitial after a stage was cleared |
| `05-campaign-complete.png` | The interstitial after the last stage was cleared |
| `06-game-over.png` | The interstitial after the player ran out of lives |

Every one of these is a real run. Nothing is an injected simulation state: the campaign
reaches a stage clear, a completion and a loss on its own.

Captures 3 to 6 are taken on a stage this directory builds -- an open field with the
enemy spawn in the player's line of fire -- rather than on a classic layout. The classic
mazes cannot be cleared inside a test: with the shipped driver the enemies never move, and
with a bot-driven player neither side reliably finds the other. Captures 1 and 2 are the
real classic pack.

## The enemies in these images do not fire

The shipped client injects `IdleEnemyDriver`, so the enemies it spawns hold position and
never shoot. Steering belongs to `battle_city_ai`, which the client may not depend on. The
loss in `06-game-over.png` is driven by a scripted enemy the test supplies through the
same seam. See issue #35.

## How they were taken

| | |
| --- | --- |
| Logical frame | 360x272 |
| Capture scale | {CAPTURE_SCALE}x (image is 720x544) |
| SDL video driver | `dummy` (`SDL_VIDEODRIVER=dummy`) |
| SDL audio driver | `dummy`; the client never initialises audio |
| pygame-ce | {pygame.version.ver}, SDL {".".join(str(part) for part in pygame.version.SDL)} |
| Python | {platform.python_version()} on {sys.platform} |
| Tile size | {DEFAULT_RULES.tile_size} px, from `battle_city_sim.DEFAULT_RULES` |
| Campaign seed | {TEST_SEED} for the built stages, 1 for the classic pack |
| Spawn interval | 600 ticks for the classic pack, {QUICK_INTERVAL} for the built stages |

These last rows are why the images are not regenerated on every run: they describe one
machine, and rewriting them from an unrelated test run would replace a deliberate record
with whichever machine happened to run the suite.
"""
