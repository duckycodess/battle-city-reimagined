"""The real loop, headless: window events in, simulation and frames out.

Nothing is stubbed. ``build_app`` opens a dummy window, loads the bundled pack through
the adapter and wires the real renderer; the test posts the same events a window system
would and drives ``ClientApp.step``. What this proves that the unit tests cannot is that
the pieces are actually connected -- a key reaches the shell, a frame's elapsed time
reaches the simulation, and a frame is drawn and presented.
"""

from __future__ import annotations

from collections.abc import Iterator

import pygame
import pytest
from battle_city_client import theme
from battle_city_client.app import DEFAULT_FRAME_CAP, ClientApp, build_app, main, parse_args
from battle_city_client.shell import PauseCause, Screen
from battle_city_client.timing import NOMINAL_TICK_RATE, FixedTickAccumulator
from client_helpers import ensure_display, observed


@pytest.fixture
def app() -> Iterator[ClientApp]:
    ensure_display()
    pygame.event.clear()
    built = build_app(scale=1)
    yield built
    pygame.event.clear()


def _key(app: ClientApp, key: int, *, down: bool = True) -> None:
    app.handle_event(pygame.event.Event(pygame.KEYDOWN if down else pygame.KEYUP, key=key))


def test_the_client_boots_with_the_bundled_stages(app: ClientApp) -> None:
    assert [entry.level_id for entry in app.shell.catalog] == [
        "classic-01",
        "classic-02",
        "classic-03",
    ]
    assert observed(app.shell.screen) is Screen.MAIN_MENU
    assert app.presenter.surface.get_size() == theme.LOGICAL_SIZE


def test_a_frame_draws_without_a_display_device(app: ClientApp) -> None:
    app.step(16)
    assert app.presenter.window.get_size() == (theme.LOGICAL_SIZE[0], theme.LOGICAL_SIZE[1])


def test_keys_carry_the_player_from_the_menu_into_a_moving_tank(app: ClientApp) -> None:
    _key(app, pygame.K_RETURN)
    assert observed(app.shell.screen) is Screen.STAGE_SELECT
    _key(app, pygame.K_RETURN)
    assert observed(app.shell.screen) is Screen.PLAYING

    session = app.shell.session
    assert session is not None
    start = session.player_tank
    assert start is not None

    # Classic stage 1 walls the spawn in on three sides; down is the way out.
    _key(app, pygame.K_DOWN)
    for _ in range(40):
        app.step(16)
    _key(app, pygame.K_DOWN, down=False)

    session = app.shell.session
    assert session is not None
    moved = session.player_tank
    assert moved is not None
    assert session.state.tick > 0
    assert moved.facing.name == "DOWN"
    assert moved.position.y > start.position.y


def test_firing_from_a_keystroke_produces_a_real_projectile(app: ClientApp) -> None:
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_SPACE)
    fired = False
    for _ in range(20):
        app.step(16)
        session = app.shell.session
        assert session is not None
        fired = fired or bool(session.state.projectiles)
    assert fired


def test_no_ticks_are_consumed_outside_a_run(app: ClientApp) -> None:
    assert app.advance_frame(1000) == 0
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    assert app.advance_frame(1000) > 0


def test_frame_cadence_does_not_change_the_pace_of_a_run(app: ClientApp) -> None:
    """Two hundred frames of 5 ms and twenty of 50 ms both buy one second of ticks."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    app.accumulator = FixedTickAccumulator(max_ticks_per_advance=10_000)
    fast = sum(app.advance_frame(5) for _ in range(200))

    other = build_app(scale=1)
    other.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
    other.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
    other.accumulator = FixedTickAccumulator(max_ticks_per_advance=10_000)
    slow = sum(other.advance_frame(50) for _ in range(20))

    assert fast == slow == NOMINAL_TICK_RATE


def test_losing_focus_pauses_and_forgets_held_keys(app: ClientApp) -> None:
    """An unfocused window delivers no key releases, so a held key would stick down."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_LEFT)
    app.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    assert observed(app.shell.screen) is Screen.PAUSED
    assert observed(app.shell.pause_cause) is PauseCause.FOCUS_LOSS
    assert app.held.ordered() == ()

    app.handle_event(pygame.event.Event(pygame.WINDOWFOCUSGAINED))
    assert observed(app.shell.screen) is Screen.PAUSED


def test_resizing_the_window_changes_only_the_scale(app: ClientApp) -> None:
    app.handle_event(
        pygame.event.Event(
            pygame.VIDEORESIZE,
            w=theme.LOGICAL_SIZE[0] * 3 + 17,
            h=theme.LOGICAL_SIZE[1] * 3 + 17,
        )
    )
    assert app.presenter.scale == 3
    assert app.presenter.surface.get_size() == theme.LOGICAL_SIZE
    app.step(16)


def test_the_scale_keys_resize_the_window(app: ClientApp) -> None:
    before = app.presenter.requested_scale
    _key(app, pygame.K_EQUALS)
    assert app.presenter.requested_scale == before + 1
    _key(app, pygame.K_MINUS)
    assert app.presenter.requested_scale == before


def test_a_close_request_stops_the_loop(app: ClientApp) -> None:
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    app.run()
    assert not app.shell.running


def test_launch_options_are_parsed() -> None:
    options = parse_args(["--seed", "7", "--scale", "2", "--frame-cap", "30"])
    assert (options.seed, options.scale, options.frame_cap) == (7, 2, 30)
    assert parse_args([]).frame_cap == DEFAULT_FRAME_CAP
    assert parse_args([]).scale is None


def test_main_launches_and_returns_cleanly() -> None:
    """The documented entry point, driven to completion by a close request."""
    ensure_display()
    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert main(["--scale", "1"]) == 0


def test_main_reports_a_missing_display_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine with no usable video driver gets a sentence, not a traceback."""

    def refuse() -> None:
        raise pygame.error("no available video device")

    monkeypatch.setattr(pygame.display, "init", refuse)
    assert main([]) == 2
    assert "no usable display" in capsys.readouterr().out
