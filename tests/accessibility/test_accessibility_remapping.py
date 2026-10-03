"""The options screen, and what a remapping does once it reaches the key tables.

Nothing here opens a window. The shell, the options model and the key tables are all
driveable without one -- ``keymap`` imports pygame for its keycodes and nothing else --
so the whole remapping flow is asserted from the actions a player produces.

The screen that must not change
-------------------------------
``CONTROLS`` is reached with one ``DOWN`` from the main menu, and up and down on it cycle
badges; ``tests/persistence`` asserts both and this issue may not edit that file. The
first two tests here hold the same ground from the inside, so a later change to the menu
cannot quietly break a test in a directory nobody is looking at.
"""

from __future__ import annotations

import pygame
import pytest
from accessibility_helpers import (
    axis,
    button,
    make_shell,
    observed,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)
from battle_city_client.accessibility import (
    ACTION_LABELS,
    DEFAULT_GAMEPAD_DEFAULTS,
    DEFAULT_GAMEPAD_EDGE,
    DEFAULT_GAMEPAD_HELD,
    REMAPPABLE_ACTIONS,
    RESERVED_ACTIONS,
)
from battle_city_client.intents import Action
from battle_city_client.keymap import (
    DEFAULT_BINDINGS,
    DEFAULT_KEYS_BY_ACTION,
    MAX_BINDING_SUMMARY,
    RESERVED_KEYS,
    SHIPPED_TABLES,
    binding_summary,
    edge_action_for,
    held_action_for,
    key_label,
)
from battle_city_client.options import (
    OPTIONS_FOOTNOTES,
    OptionId,
    OptionsState,
    rows,
)
from battle_city_client.persistence import CampaignProgress, LocalProfile
from battle_city_client.shell import ClientShell, MainMenuItem, Screen
from battle_city_client.theme import ContrastMode


def _open_options() -> ClientShell:
    shell = make_shell()
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    return shell


# -- the menu -----------------------------------------------------------------


def test_one_down_from_the_main_menu_still_reaches_the_controls_card() -> None:
    """The path ``tests/persistence`` depends on, held from inside this directory."""
    shell = make_shell()
    shell.handle(Action.UI_DOWN)
    assert observed(shell.main_item) is MainMenuItem.CONTROLS
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.CONTROLS


def test_the_controls_card_still_cycles_badges_with_up_and_down() -> None:
    shell = make_shell()
    shell.profile = LocalProfile(progress=CampaignProgress(stages_cleared=1))
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.CONTROLS
    before = shell.profile.badge.badge_id
    shell.handle(Action.UI_DOWN)
    assert shell.profile.badge.badge_id != before


def test_options_is_its_own_entry_below_controls_and_its_own_screen() -> None:
    shell = make_shell()
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_DOWN)
    assert observed(shell.main_item) is MainMenuItem.OPTIONS
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.OPTIONS
    shell.handle(Action.UI_CANCEL)
    assert observed(shell.screen) is Screen.MAIN_MENU


def test_quit_is_still_the_last_entry_and_is_one_up_from_play() -> None:
    """A new entry must not move the way out of the program."""
    shell = make_shell()
    shell.handle(Action.UI_UP)
    assert observed(shell.main_item) is MainMenuItem.QUIT
    shell.handle(Action.UI_CONFIRM)
    assert not shell.running


# -- moving around the screen --------------------------------------------------


def test_the_cursor_reaches_every_row_and_wraps() -> None:
    shell = _open_options()
    table = rows(shell.accessibility)
    seen = set()
    for _ in range(len(table)):
        seen.add(shell.options.row(shell.accessibility).id)
        shell.handle(Action.UI_DOWN)
    assert seen == {row.id for row in table}
    assert shell.options.index == 0
    shell.handle(Action.UI_UP)
    assert shell.options.index == len(table) - 1


def test_left_and_right_change_the_highlighted_setting() -> None:
    shell = _open_options()
    assert observed(shell.options.row(shell.accessibility).id) is OptionId.CONTRAST
    shell.handle(Action.UI_RIGHT)
    assert observed(shell.accessibility.contrast) is ContrastMode.HIGH
    shell.handle(Action.UI_LEFT)
    assert observed(shell.accessibility.contrast) is ContrastMode.DEFAULT


def test_a_volume_is_stepped_and_clamped_rather_than_wrapped() -> None:
    """A held key must reach the top and stay there, never fall back to the bottom."""
    shell = _open_options()
    while shell.options.row(shell.accessibility).id is not OptionId.SFX_LEVEL:
        shell.handle(Action.UI_DOWN)
    for _ in range(20):
        shell.handle(Action.UI_RIGHT)
    assert shell.accessibility.audio.sfx_volume == 100
    for _ in range(20):
        shell.handle(Action.UI_LEFT)
    assert shell.accessibility.audio.sfx_volume == 0


def test_confirm_works_a_switch_for_a_player_whose_stick_is_unusable() -> None:
    shell = _open_options()
    while shell.options.row(shell.accessibility).id is not OptionId.REDUCED_MOTION:
        shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert shell.accessibility.reduced_motion


def test_every_row_says_what_it_is_set_to() -> None:
    shell = _open_options()
    for row in rows(shell.accessibility):
        if row.id is OptionId.BINDING:
            assert row.action is not None
            assert row.label == ACTION_LABELS[row.action]
            assert len(binding_summary(shell.accessibility.bindings, row.action)) <= (
                MAX_BINDING_SUMMARY
            )
        else:
            assert row.value


def test_the_screen_says_what_its_settings_do_not_do() -> None:
    """The three honest qualifications, and the one about the screen itself."""
    joined = " ".join(OPTIONS_FOOTNOTES).upper()
    assert "NO AUDIO" in joined
    assert "NO TIME-VARYING EFFECTS" in joined
    assert "SESSION" in joined
    assert "WINDOW SCALE" in joined


# -- capture --------------------------------------------------------------------


def _arm(shell: ClientShell, action: Action) -> None:
    """Put the cursor on ``action``'s row and confirm it."""
    while True:
        row = shell.options.row(shell.accessibility)
        if row.id is OptionId.BINDING and row.action is action:
            break
        shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)


def test_confirming_a_binding_row_arms_it_and_says_so() -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert shell.capturing
    assert "PRESS" in shell.options.notice


def test_an_armed_row_takes_one_key_and_then_stops_waiting() -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert shell.capture_key(pygame.K_k)
    assert not shell.capturing
    assert shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_k,)


def test_a_rebound_key_drives_the_action_and_the_old_one_stops() -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert shell.capture_key(pygame.K_k)
    bindings = shell.accessibility.bindings
    assert observed(held_action_for(pygame.K_k, bindings)) is Action.FIRE
    assert held_action_for(pygame.K_SPACE, bindings) is None
    assert held_action_for(pygame.K_j, bindings) is None


def test_a_rebound_edge_action_only_moves_on_the_screens_that_had_it() -> None:
    """Rebinding pause must not make the new key mean pause on the stage list."""
    shell = _open_options()
    _arm(shell, Action.TOGGLE_PAUSE)
    assert shell.capture_key(pygame.K_q)
    bindings = shell.accessibility.bindings
    assert observed(edge_action_for(pygame.K_q, Screen.PLAYING, bindings)) is Action.TOGGLE_PAUSE
    assert edge_action_for(pygame.K_q, Screen.STAGE_SELECT, bindings) is None
    assert edge_action_for(pygame.K_p, Screen.PLAYING, bindings) is None


def test_a_conflicting_key_is_refused_and_the_row_stays_armed() -> None:
    shell = _open_options()
    _arm(shell, Action.MOVE_UP)
    assert not shell.capture_key(pygame.K_SPACE)
    assert shell.capturing
    assert "ALREADY" in shell.options.notice
    assert shell.accessibility.bindings.keys_for(Action.MOVE_UP) != (pygame.K_SPACE,)


@pytest.mark.parametrize("key", sorted(RESERVED_KEYS))
def test_a_reserved_key_is_refused_with_a_reason(key: int) -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert not shell.capture_key(key)
    assert "RESERVED" in shell.options.notice


def test_cancelling_a_capture_leaves_the_binding_exactly_as_it_was() -> None:
    shell = _open_options()
    before = shell.accessibility.bindings
    _arm(shell, Action.FIRE)
    shell.handle(Action.UI_CANCEL)
    assert not shell.capturing
    assert observed(shell.screen) is Screen.OPTIONS
    assert shell.accessibility.bindings == before


def test_an_armed_row_takes_a_pad_control_too() -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert shell.capture_control(button(5))
    assert shell.accessibility.bindings.controls_for(Action.FIRE) == (button(5),)


def test_a_conflicting_pad_control_is_refused() -> None:
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert not shell.capture_control(axis(1, -1))
    assert "ALREADY" in shell.options.notice


def test_capture_outside_the_options_screen_does_nothing() -> None:
    shell = make_shell()
    assert not shell.capturing
    assert not shell.capture_key(pygame.K_k)
    assert not shell.capture_control(button(3))


# -- the way out ----------------------------------------------------------------


@pytest.mark.parametrize("action", RESERVED_ACTIONS)
def test_the_way_out_is_not_on_the_options_screen_at_all(action: Action) -> None:
    """A row that could rebind back or quit is a row that can lock a player out."""
    shell = _open_options()
    assert action not in REMAPPABLE_ACTIONS
    assert all(row.action is not action for row in rows(shell.accessibility))


UNREMAPPABLE_SHIPPED_KEYS: tuple[tuple[int, Action, str], ...] = (
    (pygame.K_r, Action.RESUME_SAVE, "RESUME SAVE"),
    (pygame.K_m, Action.ONLINE_MODE, "ONLINE MODE"),
    (pygame.K_l, Action.ONLINE_STAGE, "ONLINE STAGE"),
)
"""Keys that drive an action the options screen does not offer a row for.

``R`` resumes the saved stage and is the lobby's ready key, ``M`` and ``L`` are the
lobby's mode and stage. None of them is rebindable, which is exactly why they used to be
takeable: the conflict table listed only the remappable actions, so nothing knew these
keys were spoken for.
"""


@pytest.mark.parametrize(("key", "action", "label"), UNREMAPPABLE_SHIPPED_KEYS)
def test_a_key_that_drives_an_unremappable_action_cannot_be_taken(
    key: int, action: Action, label: str
) -> None:
    """Regression: binding *confirm* to ``R`` was accepted, and ``R`` then stopped
    resuming the saved stage with nothing said about it."""
    shell = _open_options()
    _arm(shell, Action.UI_CONFIRM)
    assert not shell.capture_key(key)
    assert label in shell.options.notice
    assert shell.capturing


@pytest.mark.parametrize(("key", "action", "label"), UNREMAPPABLE_SHIPPED_KEYS)
def test_an_unremappable_action_keeps_its_key_on_the_screen_that_uses_it(
    key: int, action: Action, label: str
) -> None:
    """The other half: the refusal is what keeps these working, so check they do."""
    shell = _open_options()
    screen = Screen.STAGE_SELECT if action is Action.RESUME_SAVE else Screen.ONLINE_LOBBY
    bindings = shell.accessibility.bindings
    assert observed(edge_action_for(key, screen, bindings)) is action


def test_the_lobby_ready_key_is_spoken_for_too() -> None:
    """``R`` is two actions on two screens; naming either one is enough to refuse it."""
    shell = _open_options()
    _arm(shell, Action.UI_UP)
    assert not shell.capture_key(pygame.K_r)
    assert (
        observed(edge_action_for(pygame.K_r, Screen.ONLINE_LOBBY, shell.accessibility.bindings))
        is Action.ONLINE_READY
    )


def test_the_conflict_table_covers_every_key_the_build_ships() -> None:
    """The rule that stops this class of bug coming back, asserted as a rule.

    Every key in every shipped table must be findable in the conflict table, whether or
    not its action has a row on the options screen. An action missing from it is an
    action whose keys can be taken without anyone being told.
    """
    covered = {key for _, keys in DEFAULT_KEYS_BY_ACTION for key in keys}
    shipped = {key for table in SHIPPED_TABLES for key in table}
    assert shipped - covered == set()


def test_every_shipped_pad_control_is_spoken_for() -> None:
    """The same rule on the pad side, where button 1 is the reserved way out."""
    covered = {control for _, controls in DEFAULT_GAMEPAD_DEFAULTS for control in controls}
    shipped = set(DEFAULT_GAMEPAD_HELD) | set(DEFAULT_GAMEPAD_EDGE)
    assert shipped - covered == set()


def test_the_pads_back_button_cannot_be_taken_from_the_real_tables() -> None:
    """Asserted against ``DEFAULT_BINDINGS`` rather than a table built for the test.

    The hand-built version of this passed while the shipped one was wrong, which is how
    the gap survived: button 1 is ``UI_CANCEL``, ``UI_CANCEL`` is not remappable, and a
    remappable-only table therefore left it out.
    """
    shell = _open_options()
    _arm(shell, Action.FIRE)
    assert not shell.capture_control(button(1))
    assert "ALREADY BACK" in shell.options.notice


def test_cancel_keeps_its_keys_whatever_else_is_rebound() -> None:
    shell = _open_options()
    for action in (Action.UI_CONFIRM, Action.UI_UP, Action.UI_DOWN):
        _arm(shell, action)
        assert shell.capture_key(pygame.K_F1 + REMAPPABLE_ACTIONS.index(action))
    bindings = shell.accessibility.bindings
    for key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
        assert observed(edge_action_for(key, Screen.MAIN_MENU, bindings)) is Action.UI_CANCEL


def test_the_reset_key_is_reserved_and_puts_everything_back() -> None:
    shell = _open_options()
    shell.handle(Action.UI_RIGHT)
    _arm(shell, Action.FIRE)
    assert shell.capture_key(pygame.K_k)
    assert shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_k,)

    shell.handle(Action.OPTION_RESET)
    assert observed(shell.accessibility.contrast) is ContrastMode.DEFAULT
    assert shell.accessibility.bindings.keys_for(Action.FIRE) == (pygame.K_SPACE, pygame.K_j)
    assert "RESET" in shell.options.notice
    assert edge_action_for(pygame.K_F5, Screen.OPTIONS, DEFAULT_BINDINGS) is Action.OPTION_RESET
    assert pygame.K_F5 in RESERVED_KEYS


def test_the_reset_row_does_the_same_thing_as_the_reset_key() -> None:
    shell = _open_options()
    shell.handle(Action.UI_RIGHT)
    while shell.options.row(shell.accessibility).id is not OptionId.RESET:
        shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.accessibility.contrast) is ContrastMode.DEFAULT


def test_leaving_for_the_main_menu_abandons_an_armed_row() -> None:
    """A capture must not survive a screen the player left, waiting to eat a keystroke."""
    shell = _open_options()
    _arm(shell, Action.FIRE)
    shell.handle(Action.UI_CANCEL)
    shell.handle(Action.UI_CANCEL)
    assert observed(shell.screen) is Screen.MAIN_MENU
    assert not shell.capturing


# -- the shipped tables are unchanged where nothing was rebound ----------------


def test_a_lookup_with_no_remapping_is_the_lookup_it_always_was() -> None:
    for key, action in (
        (pygame.K_UP, Action.MOVE_UP),
        (pygame.K_w, Action.MOVE_UP),
        (pygame.K_SPACE, Action.FIRE),
        (pygame.K_j, Action.FIRE),
    ):
        assert observed(held_action_for(key)) is action
        assert observed(held_action_for(key, DEFAULT_BINDINGS)) is action
    assert observed(edge_action_for(pygame.K_ESCAPE, Screen.PLAYING)) is Action.TOGGLE_PAUSE
    assert observed(edge_action_for(pygame.K_ESCAPE, Screen.MAIN_MENU)) is Action.UI_CANCEL


def test_window_scaling_still_works_on_every_screen_including_the_new_one() -> None:
    for screen in Screen:
        assert observed(edge_action_for(pygame.K_EQUALS, screen)) is Action.SCALE_UP
        assert observed(edge_action_for(pygame.K_MINUS, screen)) is Action.SCALE_DOWN


def test_a_key_name_is_always_something_a_row_can_show() -> None:
    assert key_label(pygame.K_SPACE) == "SPACE"
    assert key_label(pygame.K_RETURN) == "RETURN"
    assert " " not in key_label(pygame.K_LSHIFT)
    assert key_label(-12345)


def test_an_options_state_starts_at_the_top_with_nothing_armed() -> None:
    state = OptionsState()
    assert state.index == 0
    assert not state.capturing
    assert state.notice == ""
