"""The real loop, headless: window events in, simulation and frames out.

Nothing is stubbed. ``build_app`` opens a dummy window, loads the bundled pack through
the adapter and wires the real renderer; the test posts the same events a window system
would and drives ``ClientApp.step``. What this proves that the unit tests cannot is that
the pieces are actually connected -- a key reaches the shell, a frame's elapsed time
reaches the simulation, and a frame is drawn and presented.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pygame
import pytest
from battle_city_client import theme
from battle_city_client.app import (
    DEFAULT_FRAME_CAP,
    MAX_FRAME_CAP,
    MIN_FRAME_CAP,
    ClientApp,
    build_app,
    main,
    parse_args,
)
from battle_city_client.intents import Action
from battle_city_client.shell import PauseCause, Screen
from battle_city_client.stage_adapter import StageAdapterError
from battle_city_client.timing import NOMINAL_TICK_RATE, FixedTickAccumulator
from battle_city_sim import RunOutcome
from client_helpers import ensure_display, finished_session, observed, with_outcome


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


# -- the menu key must not reach the tank -------------------------------------


def _silent_run(app: ClientApp, frames: int = 20) -> None:
    """Advance a run and assert no projectile ever appears."""
    for _ in range(frames):
        app.step(16)
        session = app.shell.session
        assert session is not None
        assert session.state.projectiles == (), "a menu keystroke reached the tank"


def test_starting_a_stage_with_space_does_not_fire_the_first_shot(app: ClientApp) -> None:
    """``SPACE`` confirms a menu and fires a tank; the confirming press must not do both.

    Regression: held actions used to be banked on every key press regardless of screen,
    so the press that chose the stage was still down when the run began and the tank
    opened fire on tick zero.
    """
    _key(app, pygame.K_SPACE)
    _key(app, pygame.K_SPACE)
    assert observed(app.shell.screen) is Screen.PLAYING
    assert Action.FIRE not in app.held
    _silent_run(app)


def test_resuming_with_space_does_not_fire(app: ClientApp) -> None:
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    app.step(16)
    _key(app, pygame.K_ESCAPE)
    assert observed(app.shell.screen) is Screen.PAUSED
    _key(app, pygame.K_SPACE)
    assert observed(app.shell.screen) is Screen.PLAYING
    assert Action.FIRE not in app.held
    _silent_run(app)


def test_retrying_a_finished_run_with_space_does_not_fire(app: ClientApp) -> None:
    app.shell.open_session(finished_session(stage=app.shell.catalog[0].stage, ticks=10))
    assert observed(app.shell.screen) is Screen.RUN_OVER
    _key(app, pygame.K_SPACE)
    assert observed(app.shell.screen) is Screen.PLAYING
    assert Action.FIRE not in app.held
    _silent_run(app)


def test_space_during_a_run_still_fires(app: ClientApp) -> None:
    """The other half of the fix: the control must still work where it is a control."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_SPACE)
    assert Action.FIRE in app.held
    fired = False
    for _ in range(20):
        app.step(16)
        session = app.shell.session
        assert session is not None
        fired = fired or bool(session.state.projectiles)
    assert fired


def test_pausing_forgets_held_keys(app: ClientApp) -> None:
    """Nothing samples input on the pause screen, so what was held there is stale."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_DOWN)
    assert Action.MOVE_DOWN in app.held
    _key(app, pygame.K_ESCAPE)
    assert observed(app.shell.screen) is Screen.PAUSED
    assert app.held.ordered() == ()


def test_menu_navigation_never_banks_a_gameplay_control(app: ClientApp) -> None:
    """``W``, ``S`` and ``SPACE`` drive a cursor outside a run and nothing else."""
    for key in (pygame.K_w, pygame.K_s, pygame.K_SPACE):
        _key(app, key)
        assert app.held.ordered() == ()
    assert observed(app.shell.screen) is not Screen.MAIN_MENU


# -- launch options and shutdown ----------------------------------------------


@pytest.mark.parametrize("value", ["0", "-1", str(MAX_FRAME_CAP + 1), "fast"])
def test_an_unusable_frame_cap_is_refused(value: str) -> None:
    """``Clock.tick(0)`` means "never wait", which is a busy spin rather than an error."""
    with pytest.raises(SystemExit):
        parse_args(["--frame-cap", value])


@pytest.mark.parametrize("value", [MIN_FRAME_CAP, 60, MAX_FRAME_CAP])
def test_a_usable_frame_cap_is_accepted(value: int) -> None:
    assert parse_args(["--frame-cap", str(value)]).frame_cap == value


def test_main_shuts_pygame_down_when_the_content_pack_is_unusable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The early exit used to leave SDL initialised, and a window with nothing driving it."""
    ensure_display()

    def refuse(**_: object) -> ClientApp:
        raise StageAdapterError("bundled pack: levels/classic-01.json: broken")

    monkeypatch.setattr("battle_city_client.app.build_app", refuse)
    assert main([]) == 1
    assert "broken" in capsys.readouterr().out
    assert not pygame.get_init()


def _end_the_run_in_place(app: ClientApp) -> None:
    """Give the live session a terminal outcome without leaving the playing screen.

    The shell then moves itself to the terminal screen from inside ``advance``, which is
    the transition under test: no keystroke is involved, so nothing tidies up after it
    unless the frame does. Assembled by the test because this build has no enemy
    behaviour and no live run can end on its own.
    """
    session = app.shell.session
    assert session is not None
    app.shell.session = replace(
        session, state=with_outcome(session.state, RunOutcome.BASE_DESTROYED)
    )


def test_a_run_ending_forgets_held_keys(app: ClientApp) -> None:
    """The shell can leave the playing screen without a key press behind it."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_SPACE)
    assert Action.FIRE in app.held

    _end_the_run_in_place(app)
    app.advance_frame(16)

    assert observed(app.shell.screen) is Screen.RUN_OVER
    assert app.held.ordered() == ()


def test_retrying_after_a_run_ends_does_not_inherit_a_held_fire_key(app: ClientApp) -> None:
    """Regression: the fire key held as the run ended used to survive into the retry."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_SPACE)
    _end_the_run_in_place(app)
    app.advance_frame(16)
    assert observed(app.shell.screen) is Screen.RUN_OVER

    _key(app, pygame.K_RETURN)
    assert observed(app.shell.screen) is Screen.PLAYING
    assert Action.FIRE not in app.held
    _silent_run(app)


def test_banked_wall_time_does_not_survive_a_run_ending(app: ClientApp) -> None:
    """The other half of the same moment: a retry must not open with catch-up ticks."""
    _key(app, pygame.K_RETURN)
    _key(app, pygame.K_RETURN)
    app.advance_frame(10)
    assert app.accumulator.pending_milliseconds > 0

    _end_the_run_in_place(app)
    app.advance_frame(16)
    assert app.accumulator.pending_milliseconds == 0

    _key(app, pygame.K_RETURN)
    assert observed(app.shell.screen) is Screen.PLAYING
    assert app.advance_frame(8) == 0
