"""Pads, focus, repeat and the loop: the half that can only be shown with real events.

The whole client runs here -- ``build_app`` opens a dummy window, loads the bundled pack
and wires the real renderer -- and the tests post the events a window system and a pad
would post. No device is opened: SDL joystick events carry an instance identifier and a
reading, and the hub reads exactly those, so the translation, the dead zone, the
hot-plug neutralisation and the repeat are all provable on a machine with no pad in it.

What that leaves untested is recorded rather than implied: no real pad has confirmed the
axis and button numbering this build ships as its defaults, which is why every one of
them is rebindable and why the remaining-risk note says so.
"""

from __future__ import annotations

import pygame
import pytest
from accessibility_helpers import (
    axis_event,
    button_event,
    focus_event,
    hat_event,
    key_event,
    observed,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
    removal_event,
)
from battle_city_client.accessibility import (
    AccessibilityPreferences,
    GamepadControl,
    GamepadControlKind,
    RepeatOptions,
)
from battle_city_client.app import ClientApp, build_app
from battle_city_client.gamepad import (
    ControlEvent,
    GamepadHub,
    close_gamepads,
    open_gamepads,
)
from battle_city_client.intents import KEYBOARD_DEVICE, Action
from battle_city_client.keymap import DEFAULT_BINDINGS
from battle_city_client.shell import PauseCause, Screen
from battle_city_sim import Direction

PAD = 3
"""The instance identifier the posted events claim. Any non-negative number will do."""


@pytest.fixture
def app() -> ClientApp:
    """The real client, headless, with no pads open."""
    if not pygame.display.get_init():
        pygame.display.init()
    pygame.event.clear()
    return build_app(scale=1, gamepads=GamepadHub())


def _start_a_run(app: ClientApp) -> None:
    app.handle_event(key_event(pygame.K_RETURN))
    app.handle_event(key_event(pygame.K_RETURN))
    assert app.shell.screen is Screen.PLAYING


# -- the subsystem guard --------------------------------------------------------


def test_opening_the_pads_never_raises_and_always_returns_a_hub() -> None:
    """A machine with no joystick subsystem must still get a client."""
    hub = open_gamepads()
    assert isinstance(hub, GamepadHub)
    assert hub.device_count >= 0
    close_gamepads(hub)
    close_gamepads(hub)


def test_a_hub_with_no_pads_answers_every_question_a_loop_asks() -> None:
    hub = GamepadHub()
    assert hub.devices == ()
    assert hub.names == ()
    assert hub.released(PAD) == ()
    assert not hub.detach(PAD)
    hub.forget_readings(PAD)
    hub.close()


def test_the_client_never_initialises_the_mixer(app: ClientApp) -> None:
    """The sound controls are a preference model; this build brings up no audio device.

    ``pygame.init()`` would start the mixer, which a container or a CI runner may have
    no device for. The client initialises the display and the joystick subsystem and
    nothing else, and the options screen says the sound rows are inactive rather than
    pretending otherwise.
    """
    app.step(16)
    assert pygame.mixer.get_init() is None
    assert app.shell.accessibility.audio.effective_sfx >= 0


# -- translation ----------------------------------------------------------------


def test_a_stick_inside_the_dead_zone_does_nothing(app: ClientApp) -> None:
    before = app.shell.main_index
    app.handle_event(axis_event(PAD, 1, 0.2))
    assert app.shell.main_index == before


def test_a_stick_outside_the_dead_zone_moves_a_cursor(app: ClientApp) -> None:
    app.handle_event(axis_event(PAD, 1, 1.0))
    assert app.shell.main_index == 1


def test_a_held_stick_reports_once_however_often_sdl_repeats_it(app: ClientApp) -> None:
    """SDL reports an axis continuously; one push must be one press."""
    for value in (0.8, 0.85, 0.9, 1.0):
        app.handle_event(axis_event(PAD, 1, value))
    assert app.shell.main_index == 1


def test_a_hat_pushed_up_moves_a_cursor_up(app: ClientApp) -> None:
    """SDL reports hat up as ``+1`` and the client's screen grows downward."""
    app.handle_event(hat_event(PAD, 0, (0, 1)))
    assert app.shell.main_index == len(type(app.shell.main_item)) - 1


def test_a_lowered_dead_zone_makes_a_smaller_push_count(app: ClientApp) -> None:
    app.shell.accessibility = app.shell.accessibility.with_dead_zone(5)
    app.handle_event(axis_event(PAD, 1, 0.1))
    assert app.shell.main_index == 1


def test_the_pad_drives_a_tank_and_releasing_the_stick_stops_it(app: ClientApp) -> None:
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, -1.0))
    assert observed(app.held.intent().direction) is Direction.LEFT
    app.handle_event(axis_event(PAD, 0, 0.0))
    assert app.held.intent().idle


def test_the_pad_fires_and_pauses(app: ClientApp) -> None:
    _start_a_run(app)
    app.handle_event(button_event(PAD, 0))
    assert app.held.intent().fire
    app.handle_event(button_event(PAD, 7))
    assert observed(app.shell.screen) is Screen.PAUSED


# -- transitions one reading carries ----------------------------------------------
#
# Regression. These all used to be dropped. ``_axis_event`` recorded the new position
# and then returned only the *release* of the old one, so the press for the side the
# stick had arrived at never came: the next reading compared equal to what was stored
# and reported nothing at all, leaving the player with a tank that stopped when they
# threw the stick the other way. ``_hat_event`` had the same shape -- it stored both new
# coordinates and returned the first changed component -- so every diagonal and every
# flip lost one of its two axes, permanently.


def test_a_stick_thrown_across_centre_releases_one_side_and_presses_the_other(
    app: ClientApp,
) -> None:
    hub = app.gamepads
    assert hub.handle_event(axis_event(PAD, 0, 1.0), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.AXIS, 0, 1), True),
    )
    # The release of the side being left, then the press of the side being reached.
    # SDL need never report the centre in between, so this one event carries both.
    assert hub.handle_event(axis_event(PAD, 0, -1.0), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.AXIS, 0, 1), False),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.AXIS, 0, -1), True),
    )


def test_reversing_the_stick_mid_run_reverses_the_tank(app: ClientApp) -> None:
    """The bug as a player met it: throwing the stick the other way stopped the tank."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, 1.0))
    assert observed(app.held.intent().direction) is Direction.RIGHT
    app.handle_event(axis_event(PAD, 0, -1.0))
    assert observed(app.held.intent().direction) is Direction.LEFT
    assert app.held.ordered() == (Action.MOVE_LEFT,)


def test_a_reversed_stick_is_still_only_held_once(app: ClientApp) -> None:
    """The released side must really be released, not merely outranked by recency."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, 1.0))
    app.handle_event(axis_event(PAD, 0, -1.0))
    app.handle_event(axis_event(PAD, 0, 0.0))
    assert app.held.ordered() == ()
    assert app.held.intent().idle


def test_a_hat_flipped_across_centre_reports_both_halves(app: ClientApp) -> None:
    hub = app.gamepads
    hub.handle_event(hat_event(PAD, 0, (1, 0)), 25)
    assert hub.handle_event(hat_event(PAD, 0, (-1, 0)), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, 1, 0), False),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, -1, 0), True),
    )


def test_a_hat_moved_onto_a_diagonal_presses_both_axes(app: ClientApp) -> None:
    """One reading, two axes. SDL reports hat up as ``+1``; the client's screen grows
    downward, so ``(1, 1)`` is right and up."""
    hub = app.gamepads
    assert hub.handle_event(hat_event(PAD, 0, (1, 1)), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, 1, 0), True),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, -1, 1), True),
    )


def test_a_hat_released_from_a_diagonal_releases_both_axes(app: ClientApp) -> None:
    hub = app.gamepads
    hub.handle_event(hat_event(PAD, 0, (1, 1)), 25)
    assert hub.handle_event(hat_event(PAD, 0, (0, 0)), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, 1, 0), False),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, -1, 1), False),
    )


def test_a_hat_flipped_across_a_diagonal_releases_before_it_presses(app: ClientApp) -> None:
    """Four transitions from one reading, and the order is what keeps them coherent."""
    hub = app.gamepads
    hub.handle_event(hat_event(PAD, 0, (1, 1)), 25)
    assert hub.handle_event(hat_event(PAD, 0, (-1, -1)), 25) == (
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, 1, 0), False),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, -1, 0), True),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, -1, 1), False),
        ControlEvent(PAD, GamepadControl(GamepadControlKind.HAT, 0, 1, 1), True),
    )


def test_a_hat_diagonal_drives_the_tank_and_leaves_nothing_behind(app: ClientApp) -> None:
    _start_a_run(app)
    app.handle_event(hat_event(PAD, 0, (1, 1)))
    assert app.held.ordered() == (Action.MOVE_RIGHT, Action.MOVE_UP)
    app.handle_event(hat_event(PAD, 0, (-1, -1)))
    assert app.held.ordered() == (Action.MOVE_LEFT, Action.MOVE_DOWN)
    app.handle_event(hat_event(PAD, 0, (0, 0)))
    assert app.held.ordered() == ()


def test_a_reading_that_did_not_move_carries_no_transition(app: ClientApp) -> None:
    """A held stick is reported many times a second; one push stays one press."""
    hub = app.gamepads
    assert len(hub.handle_event(axis_event(PAD, 0, 0.9), 25)) == 1
    for value in (0.92, 0.95, 1.0):
        assert hub.handle_event(axis_event(PAD, 0, value), 25) == ()
    assert hub.handle_event(hat_event(PAD, 0, (1, 0)), 25)
    assert hub.handle_event(hat_event(PAD, 0, (1, 0)), 25) == ()


def test_an_unrelated_event_carries_no_transition(app: ClientApp) -> None:
    assert app.gamepads.handle_event(key_event(pygame.K_a), 25) == ()


def test_the_transition_order_is_the_one_the_contract_states(app: ClientApp) -> None:
    """Axis by axis, horizontal first, and within one axis the release before the press.

    Per axis is the level the guarantee is made at, and the level that matters: the two
    sides of one control must not arrive press-first, or a caller feeding them into held
    state has the press undone by the release it came with. Across axes they are
    independent controls.
    """
    hub = app.gamepads
    hub.handle_event(hat_event(PAD, 0, (1, 1)), 25)
    events = hub.handle_event(hat_event(PAD, 0, (-1, -1)), 25)
    assert [event.control.axis for event in events] == [0, 0, 1, 1]
    for first, second in ((events[0], events[1]), (events[2], events[3])):
        assert first.control.axis == second.control.axis
        assert not first.pressed
        assert second.pressed


# -- devices coming and going ----------------------------------------------------


def test_an_unplugged_pad_releases_only_what_it_was_holding(app: ClientApp) -> None:
    """SDL sends no release for a stick that was pushed when the pad was pulled out."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, -1.0))
    app.handle_event(key_event(pygame.K_DOWN))
    assert app.held.ordered() == (Action.MOVE_LEFT, Action.MOVE_DOWN)

    app.handle_event(removal_event(PAD))
    assert app.held.ordered() == (Action.MOVE_DOWN,)
    assert observed(app.held.intent().direction) is Direction.DOWN
    assert app.held.devices() == (KEYBOARD_DEVICE,)


def test_an_unplugged_pad_does_not_pause_or_disturb_the_run(app: ClientApp) -> None:
    """Losing a device is not losing focus: the run keeps running, minus that device."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, -1.0))
    before = app.advance_frame(100)
    app.handle_event(removal_event(PAD))
    assert observed(app.shell.screen) is Screen.PLAYING
    assert app.advance_frame(100) == before


def test_losing_focus_neutralises_every_device(app: ClientApp) -> None:
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, -1.0))
    app.handle_event(key_event(pygame.K_SPACE))
    app.handle_event(focus_event(gained=False))
    assert app.held.ordered() == ()
    assert observed(app.shell.pause_cause) is PauseCause.FOCUS_LOSS


def test_a_stick_that_centred_while_unfocused_is_not_still_pushed(app: ClientApp) -> None:
    """An unfocused window is told nothing, so the next reading must be judged as new."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 0, -1.0))
    app.handle_event(focus_event(gained=False))
    app.handle_event(focus_event(gained=True))
    app.handle_event(key_event(pygame.K_ESCAPE))  # resume
    app.handle_event(axis_event(PAD, 0, -1.0))
    assert observed(app.held.intent().direction) is Direction.LEFT


def test_an_unplugged_pad_cannot_leave_a_reading_behind_for_the_next_one(
    app: ClientApp,
) -> None:
    hub = app.gamepads
    app.handle_event(axis_event(PAD, 0, -1.0))
    assert hub.released(PAD) == (GamepadControl(GamepadControlKind.AXIS, 0, -1),)
    app.handle_event(removal_event(PAD))
    assert hub.released(PAD) == ()


# -- repeat ---------------------------------------------------------------------


def test_a_held_direction_repeats_on_a_menu(app: ClientApp) -> None:
    app.shell.accessibility = AccessibilityPreferences(
        bindings=DEFAULT_BINDINGS, repeat=RepeatOptions(delay_ms=200, interval_ms=100)
    )
    app.sync_preferences()
    app.handle_event(axis_event(PAD, 1, 1.0))
    assert app.shell.main_index == 1
    assert app.pump_repeat(199) == ()
    assert app.pump_repeat(1) == (Action.UI_DOWN,)
    assert app.shell.main_index == 2


def test_a_repeat_stops_when_the_stick_is_released(app: ClientApp) -> None:
    app.shell.accessibility = AccessibilityPreferences(
        bindings=DEFAULT_BINDINGS, repeat=RepeatOptions(delay_ms=100, interval_ms=100)
    )
    app.sync_preferences()
    app.handle_event(axis_event(PAD, 1, 1.0))
    app.handle_event(axis_event(PAD, 1, 0.0))
    assert app.pump_repeat(5_000) == ()


def test_a_disabled_repeat_leaves_one_push_as_one_step(app: ClientApp) -> None:
    app.shell.accessibility = AccessibilityPreferences(
        bindings=DEFAULT_BINDINGS, repeat=RepeatOptions(enabled=False)
    )
    app.sync_preferences()
    app.handle_event(axis_event(PAD, 1, 1.0))
    assert app.shell.main_index == 1
    assert app.pump_repeat(10_000) == ()
    assert app.shell.main_index == 1


def test_no_repeat_is_released_while_a_run_is_being_driven(app: ClientApp) -> None:
    """The promise the simulation depends on: a repeat never reaches a tick."""
    _start_a_run(app)
    app.handle_event(axis_event(PAD, 1, 1.0))
    assert app.pump_repeat(10_000) == ()
    assert app.repeat.active == ()
    assert app.shell.session is not None
    assert app.shell.session.state.tick == 0


def test_a_repeat_does_not_follow_the_cursor_onto_the_next_screen(app: ClientApp) -> None:
    """A direction held through a confirm must not keep stepping the screen it opened."""
    app.shell.accessibility = AccessibilityPreferences(
        bindings=DEFAULT_BINDINGS, repeat=RepeatOptions(delay_ms=100, interval_ms=100)
    )
    app.sync_preferences()
    app.handle_event(axis_event(PAD, 1, 1.0))
    app.handle_event(button_event(PAD, 0))
    assert observed(app.shell.screen) is Screen.CONTROLS
    assert app.repeat.active == ()
    assert app.pump_repeat(5_000) == ()


def test_a_repeat_is_held_off_while_a_binding_row_is_armed(app: ClientApp) -> None:
    app.handle_event(key_event(pygame.K_DOWN))
    app.handle_event(key_event(pygame.K_DOWN))
    app.handle_event(key_event(pygame.K_RETURN))
    assert observed(app.shell.screen) is Screen.OPTIONS
    app.handle_event(axis_event(PAD, 1, 1.0))
    app.shell.options.index = 0
    while app.shell.options.row(app.shell.accessibility).action is None:
        app.shell.options.index += 1
    app.handle_event(key_event(pygame.K_RETURN))
    assert app.shell.capturing
    assert app.pump_repeat(5_000) == ()


# -- the frame is unchanged ------------------------------------------------------


def test_the_tick_accumulator_is_untouched_by_any_of_this(app: ClientApp) -> None:
    """Accessibility must not change simulation timing. One second is sixty ticks."""
    _start_a_run(app)
    app.shell.accessibility = app.shell.accessibility.with_dead_zone(5).with_reduced_motion(True)
    app.handle_event(axis_event(PAD, 0, -1.0))
    app.accumulator.max_ticks_per_advance = 10_000
    assert sum(app.advance_frame(50) for _ in range(20)) == 60


def test_a_pad_press_produces_the_same_intent_a_key_press_does(app: ClientApp) -> None:
    """One vocabulary. The simulation cannot tell which device a tick came from."""
    _start_a_run(app)
    app.handle_event(key_event(pygame.K_LEFT))
    from_key = app.held.intent()
    app.handle_event(key_event(pygame.K_LEFT, down=False))
    app.handle_event(axis_event(PAD, 0, -1.0))
    assert app.held.intent() == from_key


def _arm_the_fire_row(app: ClientApp) -> None:
    """Open the options screen through the menu and arm the *fire* binding row."""
    app.handle_event(key_event(pygame.K_DOWN))
    app.handle_event(key_event(pygame.K_DOWN))
    app.handle_event(key_event(pygame.K_RETURN))
    assert app.shell.screen is Screen.OPTIONS
    app.shell.options.index = 0
    while app.shell.options.row(app.shell.accessibility).action is not Action.FIRE:
        app.shell.options.index += 1
    app.handle_event(key_event(pygame.K_RETURN))
    assert app.shell.capturing


def test_a_captured_key_is_taken_whole_rather_than_acted_on(app: ClientApp) -> None:
    """The keystroke that becomes a binding must not also be a keystroke."""
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_z))
    assert observed(app.shell.screen) is Screen.OPTIONS
    assert app.held.ordered() == ()
    assert app.shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_z,)


def test_the_confirm_key_does_not_leave_the_screen_while_a_row_is_armed(
    app: ClientApp,
) -> None:
    """``RETURN`` is confirm, and confirm already drives something, so it is refused --
    but it is refused *as a capture*, which is what proves it did not act as confirm."""
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_RETURN))
    assert observed(app.shell.screen) is Screen.OPTIONS
    assert app.shell.capturing
    assert "ALREADY" in app.shell.options.notice
    assert app.shell.accessibility.bindings.keys_for(Action.FIRE) == (
        pygame.K_SPACE,
        pygame.K_j,
    )


def test_the_reset_key_works_from_inside_a_capture(app: ClientApp) -> None:
    """The recovery path has to work from the state it exists to recover from.

    A player who arms a row and then cannot press the control they meant to bind is on
    the one screen that can put things back, and ``F5`` is the key that does it. It is
    reserved from capture precisely so the armed row cannot swallow it.
    """
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_F5))
    assert not app.shell.capturing
    assert "RESET" in app.shell.options.notice
    assert not app.shell.accessibility.bindings.remapped
    assert observed(app.shell.screen) is Screen.OPTIONS


def test_a_reset_from_inside_a_capture_undoes_the_bindings_already_made(
    app: ClientApp,
) -> None:
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_z))
    assert app.shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_z,)
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_F5))
    assert app.shell.accessibility.bindings.keys_for(Action.FIRE) == (
        pygame.K_SPACE,
        pygame.K_j,
    )


def test_cancel_still_leaves_a_capture_without_resetting_anything(app: ClientApp) -> None:
    """The two ways out are different: one abandons the row, one puts everything back."""
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_z))
    _arm_the_fire_row(app)
    app.handle_event(key_event(pygame.K_ESCAPE))
    assert not app.shell.capturing
    assert app.shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_z,)


def test_the_pads_are_released_when_the_app_closes(app: ClientApp) -> None:
    app.close()
    app.close()
    assert app.gamepads.device_count == 0
