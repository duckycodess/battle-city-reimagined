"""Keyboard translation, held-key ordering, and what a lost window focus does."""

from __future__ import annotations

import pygame
import pytest
from battle_city_client import Action, HeldActions, PlayerIntent, intent_from_held
from battle_city_client.keymap import edge_action_for, held_action_for
from battle_city_client.shell import PauseCause, Screen
from battle_city_sim import Direction
from client_helpers import make_shell, observed

# -- held actions -------------------------------------------------------------


def test_nothing_held_is_an_idle_intent() -> None:
    assert HeldActions().intent() == PlayerIntent()
    assert HeldActions().intent().idle


def test_the_most_recent_movement_press_wins() -> None:
    """Two directions at once must not be settled by set iteration order."""
    held = HeldActions()
    held.press(Action.MOVE_LEFT)
    held.press(Action.MOVE_UP)
    assert held.intent().direction is Direction.UP
    held.press(Action.MOVE_RIGHT)
    assert held.intent().direction is Direction.RIGHT


def test_releasing_the_newest_direction_falls_back_to_the_one_still_held() -> None:
    held = HeldActions()
    held.press(Action.MOVE_LEFT)
    held.press(Action.MOVE_UP)
    held.release(Action.MOVE_UP)
    assert held.intent().direction is Direction.LEFT


def test_repeated_presses_and_unmatched_releases_are_harmless() -> None:
    """A window system repeats keys and can drop a release; neither may raise."""
    held = HeldActions()
    held.press(Action.FIRE)
    held.press(Action.FIRE)
    held.release(Action.MOVE_DOWN)
    assert held.intent() == PlayerIntent(fire=True)
    held.release(Action.FIRE)
    assert held.intent().idle


def test_fire_and_a_direction_coexist() -> None:
    held = HeldActions()
    held.press(Action.MOVE_DOWN)
    held.press(Action.FIRE)
    assert held.intent() == PlayerIntent(direction=Direction.DOWN, fire=True)


def test_intent_reduction_reads_press_order_not_membership() -> None:
    assert intent_from_held([Action.MOVE_UP, Action.MOVE_DOWN]).direction is Direction.DOWN
    assert intent_from_held([Action.MOVE_DOWN, Action.MOVE_UP]).direction is Direction.UP


# -- key tables ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "action"),
    [
        (pygame.K_UP, Action.MOVE_UP),
        (pygame.K_w, Action.MOVE_UP),
        (pygame.K_a, Action.MOVE_LEFT),
        (pygame.K_RIGHT, Action.MOVE_RIGHT),
        (pygame.K_SPACE, Action.FIRE),
    ],
)
def test_movement_and_fire_are_held_controls(key: int, action: Action) -> None:
    assert held_action_for(key) is action


def test_escape_means_pause_in_a_run_and_back_in_a_menu() -> None:
    assert edge_action_for(pygame.K_ESCAPE, Screen.PLAYING) is Action.TOGGLE_PAUSE
    assert edge_action_for(pygame.K_ESCAPE, Screen.PAUSED) is Action.TOGGLE_PAUSE
    assert edge_action_for(pygame.K_ESCAPE, Screen.MAIN_MENU) is Action.UI_CANCEL


def test_arrow_keys_drive_a_cursor_only_outside_a_run() -> None:
    assert edge_action_for(pygame.K_UP, Screen.STAGE_SELECT) is Action.UI_UP
    assert edge_action_for(pygame.K_UP, Screen.PLAYING) is None


def test_window_scaling_works_on_every_screen() -> None:
    for screen in Screen:
        assert edge_action_for(pygame.K_EQUALS, screen) is Action.SCALE_UP
        assert edge_action_for(pygame.K_MINUS, screen) is Action.SCALE_DOWN


def test_an_unbound_key_produces_nothing() -> None:
    assert held_action_for(pygame.K_F7) is None
    assert edge_action_for(pygame.K_F7, Screen.MAIN_MENU) is None


# -- focus --------------------------------------------------------------------


def test_losing_focus_pauses_a_run() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.set_focused(False)
    assert observed(shell.screen) is Screen.PAUSED
    assert observed(shell.pause_cause) is PauseCause.FOCUS_LOSS
    assert not shell.consumes_ticks


def test_regaining_focus_does_not_resume_on_its_own() -> None:
    """A deliberate choice: the run must not restart under a click that is still landing."""
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.set_focused(False)
    shell.set_focused(True)
    assert observed(shell.screen) is Screen.PAUSED
    assert observed(shell.pause_cause) is PauseCause.FOCUS_LOSS
    shell.handle(Action.TOGGLE_PAUSE)
    assert observed(shell.screen) is Screen.PLAYING


def test_losing_focus_in_a_menu_changes_nothing() -> None:
    shell = make_shell()
    shell.set_focused(False)
    assert observed(shell.screen) is Screen.MAIN_MENU
    assert observed(shell.pause_cause) is None


def test_clearing_held_actions_is_what_focus_loss_needs() -> None:
    """An unfocused window stops delivering releases, so a held key would stick."""
    held = HeldActions()
    held.press(Action.MOVE_LEFT)
    held.press(Action.FIRE)
    held.clear()
    assert held.ordered() == ()
    assert held.intent().idle
