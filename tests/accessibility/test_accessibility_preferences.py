"""The preference model: validation, bindings, repeat, dead zone and sound.

Every test here runs in a bare interpreter. No display, no pad, no clock and no file:
the timings are stated by the caller, the device codes are plain integers, and the pad
controls are the client's own descriptors. That is the point of the split -- the rules an
accessibility feature stands on are the rules that must be provable without the hardware
the feature is for.
"""

from __future__ import annotations

import math

import pytest
from accessibility_helpers import axis, button, hat, observed
from battle_city_client import theme
from battle_city_client.accessibility import (
    DEFAULT_ACCESSIBILITY,
    KEYBOARD_DEVICE,
    MAX_DEAD_ZONE_PERCENT,
    MAX_REPEAT_DELAY_MS,
    MAX_VOLUME,
    MIN_DEAD_ZONE_PERCENT,
    MIN_REPEAT_DELAY_MS,
    MIN_VOLUME,
    REMAPPABLE_ACTIONS,
    REPEATABLE_ACTIONS,
    RESERVED_ACTIONS,
    AccessibilityPreferences,
    AudioPreferences,
    BindingConflict,
    BindingSet,
    GamepadControl,
    GamepadControlKind,
    RepeatOptions,
    RepeatTimer,
    axis_direction,
    edge_control_action,
    held_control_action,
    resolved_table,
)
from battle_city_client.intents import KEYBOARD_DEVICE as INTENTS_KEYBOARD_DEVICE
from battle_city_client.intents import Action, HeldActions
from battle_city_sim import Direction

# -- the value itself ---------------------------------------------------------


def test_the_defaults_are_the_shipped_look_and_nothing_rebound() -> None:
    preferences = DEFAULT_ACCESSIBILITY
    assert observed(preferences.contrast) is theme.ContrastMode.DEFAULT
    assert not preferences.reduced_motion
    assert not preferences.bindings.remapped
    assert preferences.repeat.enabled


def test_every_preference_is_replaced_rather_than_mutated() -> None:
    """Immutability is what makes "a preference that arrived was checked" true."""
    before = AccessibilityPreferences()
    after = before.with_contrast(theme.ContrastMode.HIGH).with_reduced_motion(True)
    assert observed(before.contrast) is theme.ContrastMode.DEFAULT
    assert not before.reduced_motion
    assert observed(after.contrast) is theme.ContrastMode.HIGH
    assert after.reduced_motion


@pytest.mark.parametrize("percent", [-1, MAX_DEAD_ZONE_PERCENT + 1, 1000])
def test_a_dead_zone_outside_the_bounds_is_refused_on_construction(percent: int) -> None:
    with pytest.raises(ValueError, match="dead zone"):
        AccessibilityPreferences(dead_zone_percent=percent)


def test_a_stepped_dead_zone_is_clamped_rather_than_refused() -> None:
    """A parsed value is refused; a key the player is holding down is clamped."""
    preferences = AccessibilityPreferences(dead_zone_percent=MIN_DEAD_ZONE_PERCENT)
    assert preferences.with_dead_zone(-50).dead_zone_percent == MIN_DEAD_ZONE_PERCENT
    assert preferences.with_dead_zone(500).dead_zone_percent == MAX_DEAD_ZONE_PERCENT


@pytest.mark.parametrize("delay", [MIN_REPEAT_DELAY_MS - 1, MAX_REPEAT_DELAY_MS + 1])
def test_a_repeat_delay_outside_the_bounds_is_refused(delay: int) -> None:
    with pytest.raises(ValueError, match="repeat delay"):
        RepeatOptions(delay_ms=delay)


def test_resetting_keeps_the_device_tables_and_drops_the_remapping() -> None:
    """The recovery path: what the player changed goes, what the build ships stays."""
    bindings = BindingSet(
        default_keys=((Action.FIRE, (32,)),), reserved_keys=frozenset({27})
    ).with_keyboard(Action.FIRE, 107)
    preferences = AccessibilityPreferences(
        bindings=bindings, dead_zone_percent=80, reduced_motion=True
    ).reset()
    assert not preferences.bindings.remapped
    assert preferences.bindings.default_keys == ((Action.FIRE, (32,)),)
    assert preferences.bindings.reserved_keys == frozenset({27})
    assert preferences.dead_zone_percent == DEFAULT_ACCESSIBILITY.dead_zone_percent
    assert not preferences.reduced_motion


# -- sound --------------------------------------------------------------------


def test_effects_and_music_are_two_independent_controls() -> None:
    audio = AudioPreferences(sfx_volume=90, music_volume=10)
    assert audio.effective_sfx == 90
    assert audio.effective_music == 10


def test_muting_does_not_forget_the_level_that_was_set() -> None:
    """Mute is beside the volume rather than folded into it, so unmuting comes back."""
    audio = AudioPreferences(sfx_volume=80, sfx_muted=True)
    assert audio.effective_sfx == 0
    assert audio.sfx_volume == 80


@pytest.mark.parametrize("volume", [MIN_VOLUME - 1, MAX_VOLUME + 1])
def test_a_volume_outside_the_bounds_is_refused(volume: int) -> None:
    with pytest.raises(ValueError, match="volume"):
        AudioPreferences(sfx_volume=volume)


def test_muting_one_channel_leaves_the_other_playing() -> None:
    audio = AudioPreferences(sfx_muted=True, music_volume=40)
    assert audio.effective_sfx == 0
    assert audio.effective_music == 40


# -- dead zone ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "dead_zone", "expected"),
    [
        (0.0, 25, 0),
        (0.2, 25, 0),
        (0.25, 25, 0),
        (0.26, 25, 1),
        (-0.9, 25, -1),
        (0.1, 0, 1),
        (0.0, 0, 0),
        (-0.1, 50, 0),
    ],
)
def test_a_stick_is_a_direction_only_outside_the_dead_zone(
    value: float, dead_zone: int, expected: int
) -> None:
    assert axis_direction(value, dead_zone) == expected


@pytest.mark.parametrize("value", [5.0, -5.0, 1e9])
def test_an_out_of_range_reading_is_bounded_rather_than_believed(value: float) -> None:
    """SDL promises -1 to 1 and a miscalibrated pad does not always deliver it."""
    assert abs(axis_direction(value, 25)) == 1


def test_a_reading_that_is_not_a_number_is_centred() -> None:
    """NaN compares false against everything, so it would otherwise fall through."""
    assert axis_direction(math.nan, 25) == 0


# -- repeat -------------------------------------------------------------------


def test_a_repeat_waits_the_delay_and_then_runs_at_the_interval() -> None:
    timer = RepeatTimer(RepeatOptions(delay_ms=300, interval_ms=100))
    timer.press(Action.UI_DOWN)
    assert timer.advance(299) == ()
    assert timer.advance(1) == (Action.UI_DOWN,)
    assert timer.advance(99) == ()
    assert timer.advance(1) == (Action.UI_DOWN,)


def test_a_disabled_repeat_never_fires() -> None:
    timer = RepeatTimer(RepeatOptions(enabled=False))
    timer.press(Action.UI_UP)
    assert timer.advance(10_000) == ()


def test_a_long_stall_releases_one_repeat_rather_than_a_burst() -> None:
    """A frame that took a second must not move a cursor eight places."""
    timer = RepeatTimer(RepeatOptions(delay_ms=200, interval_ms=100))
    timer.press(Action.UI_DOWN)
    assert timer.advance(5_000) == (Action.UI_DOWN,)
    assert timer.advance(99) == ()


def test_releasing_and_clearing_stop_a_repeat() -> None:
    timer = RepeatTimer(RepeatOptions(delay_ms=100, interval_ms=100))
    timer.press(Action.UI_UP)
    timer.release(Action.UI_UP)
    assert timer.advance(1_000) == ()
    timer.press(Action.UI_DOWN)
    timer.clear()
    assert timer.advance(1_000) == ()
    assert timer.active == ()


def test_two_directions_held_at_once_repeat_in_press_order() -> None:
    """Never in hash order: iteration order of a set must not decide what a cursor does."""
    timer = RepeatTimer(RepeatOptions(delay_ms=100, interval_ms=100))
    timer.press(Action.UI_UP)
    timer.press(Action.UI_DOWN)
    assert timer.active == (Action.UI_UP, Action.UI_DOWN)
    assert timer.advance(100) == (Action.UI_UP, Action.UI_DOWN)


@pytest.mark.parametrize(
    "action", [Action.FIRE, Action.MOVE_UP, Action.UI_CONFIRM, Action.TOGGLE_PAUSE, Action.QUIT]
)
def test_only_a_cursor_action_may_repeat(action: Action) -> None:
    """The structural half of "a repeat cannot reach the simulation"."""
    assert action not in REPEATABLE_ACTIONS
    with pytest.raises(ValueError, match="must not repeat"):
        RepeatTimer().press(action)


def test_a_negative_elapsed_time_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        RepeatTimer().advance(-1)


# -- bindings -----------------------------------------------------------------

SHIPPED = BindingSet(
    default_keys=(
        (Action.MOVE_UP, (119, 1073741906)),
        (Action.MOVE_DOWN, (115,)),
        (Action.FIRE, (32, 106)),
        (Action.UI_CONFIRM, (13,)),
    ),
    default_controls=((Action.FIRE, (GamepadControl(GamepadControlKind.BUTTON, 0),)),),
    reserved_keys=frozenset({27, 8}),
)
"""A small stand-in for the shipped tables, stated here rather than imported.

The real one lives in :mod:`battle_city_client.keymap` and carries pygame's keycodes;
restating four of them as the integers they are keeps this file free of a display
library, and ``test_accessibility_remapping`` checks the real tables through the client.
"""


def test_a_binding_replaces_the_shipped_keys_for_that_action() -> None:
    """A row on the options screen is the whole truth about the action it names."""
    bindings = SHIPPED.with_keyboard(Action.MOVE_UP, 105)
    assert bindings.keys_for(Action.MOVE_UP) == (105,)
    assert bindings.keys_for(Action.FIRE) == (32, 106)


def test_a_key_is_freed_by_moving_its_action_before_another_can_take_it() -> None:
    """The conflict is reported first, and the swap is what the player does about it.

    Taking a key that something else drives is refused rather than silently stolen, so
    the two-step is the whole flow: move the action that holds the key, then the key is
    free. What the resolution rule then guarantees is that the freed key really is
    free -- the action that was rebound no longer answers to it.
    """
    shipped = BindingSet(default_keys=((Action.MOVE_UP, (119,)), (Action.MOVE_DOWN, (115,))))
    with pytest.raises(BindingConflict, match="ALREADY UP"):
        shipped.with_keyboard(Action.MOVE_DOWN, 119)

    bindings = shipped.with_keyboard(Action.MOVE_UP, 105).with_keyboard(Action.MOVE_DOWN, 119)
    assert bindings.keys_for(Action.MOVE_UP) == (105,)
    assert bindings.keys_for(Action.MOVE_DOWN) == (119,)


def test_a_conflict_names_the_action_that_already_has_the_key() -> None:
    with pytest.raises(BindingConflict, match="ALREADY FIRE"):
        SHIPPED.with_keyboard(Action.MOVE_UP, 32)


@pytest.mark.parametrize("key", [27, 8])
def test_a_reserved_key_cannot_be_captured(key: int) -> None:
    """The way out of a screen has to work from the screen that rebinds keys."""
    with pytest.raises(BindingConflict, match="RESERVED"):
        SHIPPED.with_keyboard(Action.FIRE, key)


@pytest.mark.parametrize("action", RESERVED_ACTIONS)
def test_a_reserved_action_cannot_be_moved(action: Action) -> None:
    assert action not in REMAPPABLE_ACTIONS
    with pytest.raises(BindingConflict, match="RESERVED"):
        SHIPPED.with_keyboard(action, 120)


def test_rebinding_the_same_action_twice_keeps_one_binding() -> None:
    bindings = SHIPPED.with_keyboard(Action.FIRE, 120).with_keyboard(Action.FIRE, 121)
    assert bindings.keyboard == ((Action.FIRE, 121),)


def test_clearing_one_action_puts_its_shipped_keys_back() -> None:
    bindings = SHIPPED.with_keyboard(Action.FIRE, 120).cleared(Action.FIRE)
    assert bindings.keys_for(Action.FIRE) == (32, 106)


def test_a_pad_control_is_bound_and_conflicts_the_same_way() -> None:
    bindings = SHIPPED.with_gamepad(Action.MOVE_UP, axis(1, -1))
    assert bindings.controls_for(Action.MOVE_UP) == (axis(1, -1),)
    with pytest.raises(BindingConflict, match="ALREADY"):
        bindings.with_gamepad(Action.MOVE_DOWN, axis(1, -1))


def test_an_overlay_has_one_spelling_whatever_order_it_was_built_in() -> None:
    """Determinism: two equal sets of bindings must compare and hash equal."""
    first = SHIPPED.with_keyboard(Action.FIRE, 120).with_keyboard(Action.MOVE_UP, 121)
    second = SHIPPED.with_keyboard(Action.MOVE_UP, 121).with_keyboard(Action.FIRE, 120)
    assert first == second
    assert hash(first) == hash(second)


def test_a_binding_set_that_names_one_key_twice_is_refused() -> None:
    with pytest.raises(BindingConflict, match="bound twice"):
        BindingSet(keyboard=((Action.FIRE, 120), (Action.MOVE_UP, 120)))


# -- resolution ---------------------------------------------------------------


def test_resolution_with_no_overlay_is_the_shipped_table() -> None:
    defaults = {119: Action.MOVE_UP, 115: Action.MOVE_DOWN}
    assert resolved_table(defaults, {}) == defaults


def test_resolution_drops_the_shadowed_default_and_adds_the_overlay() -> None:
    defaults = {119: Action.MOVE_UP, 115: Action.MOVE_DOWN}
    resolved = resolved_table(defaults, {Action.MOVE_UP: 115})
    assert resolved == {115: Action.MOVE_UP}


def test_a_pad_control_resolves_to_the_action_the_build_ships() -> None:
    assert observed(held_control_action(axis(1, -1))) is Action.MOVE_UP
    assert observed(held_control_action(hat(0, 1, 1))) is Action.MOVE_DOWN
    assert observed(held_control_action(button(0))) is Action.FIRE
    assert observed(edge_control_action(button(0))) is Action.UI_CONFIRM
    assert observed(edge_control_action(button(1))) is Action.UI_CANCEL
    assert observed(edge_control_action(button(7))) is Action.TOGGLE_PAUSE


def test_a_rebound_pad_control_takes_over_from_the_shipped_one() -> None:
    bindings = BindingSet(default_controls=((Action.FIRE, (button(0),)),)).with_gamepad(
        Action.FIRE, button(5)
    )
    assert observed(held_control_action(button(5), bindings)) is Action.FIRE
    assert held_control_action(button(0), bindings) is None


def test_the_pad_back_button_cannot_be_taken_by_anything() -> None:
    """Cancel is reserved, so a pad user can always abandon a capture with the pad."""
    with pytest.raises(BindingConflict, match="ALREADY BACK"):
        BindingSet(default_controls=((Action.UI_CANCEL, (button(1),)),)).with_gamepad(
            Action.FIRE, button(1)
        )


# -- held input, per device ---------------------------------------------------


def test_the_keyboard_can_never_collide_with_a_pad_instance_identifier() -> None:
    """SDL counts instance identifiers up from zero, so the keyboard's must be negative.

    Stated in ``intents`` and re-exported by ``accessibility`` to avoid an import cycle;
    this pins both the value's sign and the fact that the two names are one value.
    """
    assert KEYBOARD_DEVICE == INTENTS_KEYBOARD_DEVICE
    assert KEYBOARD_DEVICE < 0


def test_losing_one_device_releases_only_what_that_device_held() -> None:
    held = HeldActions()
    held.press(Action.MOVE_LEFT, KEYBOARD_DEVICE)
    held.press(Action.MOVE_DOWN, 3)
    held.clear_device(3)
    assert held.ordered() == (Action.MOVE_LEFT,)
    assert held.intent().direction is Direction.LEFT


def test_one_action_held_on_two_devices_survives_losing_one_of_them() -> None:
    held = HeldActions()
    held.press(Action.FIRE, KEYBOARD_DEVICE)
    held.press(Action.FIRE, 3)
    held.clear_device(3)
    assert Action.FIRE in held
    assert held.intent().fire


def test_recency_still_decides_a_facing_across_two_devices() -> None:
    held = HeldActions()
    held.press(Action.MOVE_LEFT, KEYBOARD_DEVICE)
    held.press(Action.MOVE_UP, 3)
    assert held.intent().direction is Direction.UP
    held.press(Action.MOVE_RIGHT, KEYBOARD_DEVICE)
    assert held.intent().direction is Direction.RIGHT


def test_a_release_from_one_device_leaves_the_other_alone() -> None:
    held = HeldActions()
    held.press(Action.MOVE_UP, KEYBOARD_DEVICE)
    held.press(Action.MOVE_UP, 3)
    held.release(Action.MOVE_UP, 3)
    assert held.ordered() == (Action.MOVE_UP,)
    assert held.devices() == (KEYBOARD_DEVICE,)


# -- the palette is still the shipped one -------------------------------------


def test_every_palette_default_is_the_module_constant_of_the_same_name() -> None:
    """The guarantee that a client on the default contrast draws what it always drew."""
    for field in theme.DEFAULT_PALETTE.__dataclass_fields__:
        assert getattr(theme.DEFAULT_PALETTE, field) == getattr(theme, field.upper()), field


def test_the_two_palettes_differ_everywhere_a_contrast_option_should_matter() -> None:
    default = theme.DEFAULT_PALETTE
    high = theme.HIGH_CONTRAST_PALETTE
    assert default != high
    for field in ("background", "text", "ground", "player_tank", "enemy_tank"):
        assert getattr(default, field) != getattr(high, field), field


def test_a_contrast_mode_maps_to_exactly_one_palette() -> None:
    for mode in theme.ContrastMode:
        assert isinstance(theme.palette_for(mode), theme.Palette)
    assert theme.palette_for(theme.ContrastMode.DEFAULT) is theme.DEFAULT_PALETTE
