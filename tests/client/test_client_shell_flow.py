"""Menu to stage and back: the whole flow, with no window open."""

from __future__ import annotations

from battle_city_client import Action, PlayerIntent
from battle_city_client.shell import (
    ClientShell,
    MainMenuItem,
    PauseCause,
    PauseItem,
    RunOverItem,
    Screen,
)
from battle_city_sim import RunOutcome
from client_helpers import finished_session, make_entry, make_shell, make_stage, observed


def test_a_new_shell_opens_on_the_main_menu() -> None:
    shell = make_shell()
    assert observed(shell.screen) is Screen.MAIN_MENU
    assert observed(shell.main_item) is MainMenuItem.PLAY
    assert shell.session is None
    assert shell.running


def test_the_main_menu_cursor_wraps_in_both_directions() -> None:
    shell = make_shell()
    shell.handle(Action.UI_UP)
    assert observed(shell.main_item) is MainMenuItem.QUIT
    shell.handle(Action.UI_DOWN)
    assert observed(shell.main_item) is MainMenuItem.PLAY


def test_play_leads_to_stage_select_and_confirming_starts_a_run() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.STAGE_SELECT
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.PLAYING
    assert shell.session is not None
    assert shell.session.state.tick == 0
    assert shell.consumes_ticks


def test_the_started_stage_is_the_highlighted_one() -> None:
    entries = (make_entry(make_stage(stage_id="first")), make_entry(make_stage(stage_id="second")))
    shell = make_shell(entries)
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_DOWN)
    assert shell.selected_entry is not None
    assert shell.selected_entry.level_id == "second"
    shell.handle(Action.UI_CONFIRM)
    assert shell.session is not None
    assert shell.session.state.stage_id == "second"


def test_stage_select_cancels_back_to_the_main_menu() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CANCEL)
    assert observed(shell.screen) is Screen.MAIN_MENU


def test_the_controls_screen_is_reachable_and_returns() -> None:
    shell = make_shell()
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.CONTROLS
    shell.handle(Action.UI_CANCEL)
    assert observed(shell.screen) is Screen.MAIN_MENU


def test_quitting_from_the_main_menu_stops_the_loop() -> None:
    shell = make_shell()
    shell.handle(Action.UI_UP)
    shell.handle(Action.UI_CONFIRM)
    assert not shell.running


def test_an_empty_catalog_explains_itself_instead_of_starting_nothing() -> None:
    shell = ClientShell(catalog=())
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.STAGE_SELECT
    assert shell.notice
    shell.handle(Action.UI_CONFIRM)
    assert shell.session is None
    assert observed(shell.screen) is Screen.STAGE_SELECT


def test_pausing_stops_tick_consumption_and_resuming_restores_it() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(10)
    paused_tick = shell.session.state.tick if shell.session else -1

    shell.handle(Action.TOGGLE_PAUSE)
    assert observed(shell.screen) is Screen.PAUSED
    assert observed(shell.pause_cause) is PauseCause.PLAYER
    assert not shell.consumes_ticks

    shell.advance(30)
    assert shell.session is not None
    assert shell.session.state.tick == paused_tick

    shell.handle(Action.TOGGLE_PAUSE)
    assert observed(shell.screen) is Screen.PLAYING
    assert observed(shell.pause_cause) is None
    shell.advance(5)
    assert shell.session.state.tick == paused_tick + 5


def test_the_pause_menu_can_restart_the_stage() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(25, PlayerIntent(fire=True))
    shell.handle(Action.TOGGLE_PAUSE)
    shell.handle(Action.UI_DOWN)
    assert observed(shell.pause_item) is PauseItem.RESTART
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.PLAYING
    assert shell.session is not None
    assert shell.session.state.tick == 0
    assert shell.session.state.projectiles == ()


def test_the_pause_menu_can_abandon_the_run() -> None:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.TOGGLE_PAUSE)
    shell.handle(Action.UI_UP)
    assert observed(shell.pause_item) is PauseItem.QUIT_TO_MENU
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.MAIN_MENU
    assert shell.session is None


def test_no_single_keystroke_discards_a_run() -> None:
    """``UI_CANCEL`` during play opens the pause menu; it does not leave the stage."""
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CANCEL)
    assert observed(shell.screen) is Screen.PAUSED
    assert shell.session is not None


def test_a_recorded_outcome_opens_the_terminal_screen() -> None:
    """The one path to a terminal screen: a state that already carries an outcome.

    The session here was advanced for real and then stamped by the test, because this
    build has no enemy behaviour and no live run can reach an outcome.
    """
    shell = make_shell()
    shell.open_session(finished_session(RunOutcome.BASE_DESTROYED))
    assert observed(shell.screen) is Screen.RUN_OVER
    assert shell.outcome is RunOutcome.BASE_DESTROYED
    assert not shell.consumes_ticks


def test_the_terminal_screen_can_retry_or_leave() -> None:
    shell = make_shell()
    shell.open_session(finished_session(RunOutcome.PLAYERS_ELIMINATED))
    assert observed(shell.run_over_item) is RunOverItem.RETRY
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.PLAYING
    assert shell.session is not None
    assert shell.session.state.tick == 0
    assert shell.outcome is None

    shell.open_session(finished_session(RunOutcome.PLAYERS_ELIMINATED))
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert observed(shell.screen) is Screen.MAIN_MENU
    assert shell.session is None


def test_a_terminal_state_absorbs_further_ticks() -> None:
    shell = make_shell()
    shell.open_session(finished_session())
    before = shell.session.state.tick if shell.session else -1
    shell.advance(50)
    assert shell.session is not None
    assert shell.session.state.tick == before


def test_no_live_run_reaches_an_outcome_in_this_build() -> None:
    """Guards the boundary the phase was scoped around.

    The client adds no enemy behaviour and no wave scheduler, so a run driven with every
    control held cannot end. When enemy waves arrive this test should be replaced by one
    asserting the rule that ended the run -- not deleted quietly.
    """
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    for _ in range(40):
        shell.advance(30, PlayerIntent(fire=True))
    assert shell.session is not None
    assert shell.session.state.outcome is None
    assert observed(shell.screen) is Screen.PLAYING


def test_quit_stops_the_loop_from_any_screen() -> None:
    for screen_setup in (
        lambda shell: None,
        lambda shell: shell.handle(Action.UI_CONFIRM),
    ):
        shell = make_shell()
        screen_setup(shell)
        shell.handle(Action.QUIT)
        assert not shell.running
