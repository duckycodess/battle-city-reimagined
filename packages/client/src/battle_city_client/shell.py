"""The client's screen state machine, with no pygame in it.

:class:`ClientShell` owns everything the window is *about*: which screen is showing,
where the menu cursor sits, which stage was chosen, the live :class:`StageSession`,
whether the run is paused and why, and whether the program should keep running. The
pygame layer in :mod:`battle_city_client.app` does three things to it -- feed it edge
actions, feed it ticks, and draw it -- and the renderer only reads it. Keeping the
machine here is what lets the whole menu-to-stage flow, the pause behaviour and the
focus-loss behaviour be tested without opening a display.

Two behaviours are choices rather than consequences, and are recorded as such:

* **Losing window focus pauses a run, and regaining it does not resume.** A window that
  resumes the instant it is clicked resumes while the click is still landing, and the
  player is back in the run before they are looking at it. The pause screen says which
  of the two causes it is showing, so the state is never ambiguous.
* **Terminal screens are shown for outcomes the simulation recorded, and for nothing
  else.** The shell reads :attr:`SimulationState.outcome`; it never sets one. This build
  ships no enemy behaviour and no wave scheduler, so no live run can reach an outcome,
  and that is the honest state of the game rather than a gap to paper over with a
  client-side rule. Stage victory and enemy waves arrive with the campaign and AI phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from battle_city_sim import DEFAULT_RULES, Rules, RunOutcome

from .intents import IDLE_INTENT, Action, PlayerIntent
from .session import DEFAULT_SEED, StageSession
from .stage_adapter import StageEntry


class Screen(Enum):
    """Which screen the window is showing."""

    MAIN_MENU = "main_menu"
    STAGE_SELECT = "stage_select"
    CONTROLS = "controls"
    PLAYING = "playing"
    PAUSED = "paused"
    RUN_OVER = "run_over"


class PauseCause(Enum):
    """Why a run is paused. Shown on the pause screen so the two never look alike."""

    PLAYER = "player"
    FOCUS_LOSS = "focus_loss"


class MainMenuItem(Enum):
    """Main menu entries, in display order."""

    PLAY = 0
    CONTROLS = 1
    QUIT = 2


class PauseItem(Enum):
    """Pause menu entries, in display order."""

    RESUME = 0
    RESTART = 1
    QUIT_TO_MENU = 2


class RunOverItem(Enum):
    """Terminal screen entries, in display order."""

    RETRY = 0
    QUIT_TO_MENU = 1


MAIN_MENU_LABELS: Final[dict[MainMenuItem, str]] = {
    MainMenuItem.PLAY: "PLAY",
    MainMenuItem.CONTROLS: "CONTROLS",
    MainMenuItem.QUIT: "QUIT",
}

PAUSE_LABELS: Final[dict[PauseItem, str]] = {
    PauseItem.RESUME: "RESUME",
    PauseItem.RESTART: "RESTART STAGE",
    PauseItem.QUIT_TO_MENU: "QUIT TO MENU",
}

RUN_OVER_LABELS: Final[dict[RunOverItem, str]] = {
    RunOverItem.RETRY: "RETRY STAGE",
    RunOverItem.QUIT_TO_MENU: "QUIT TO MENU",
}

OUTCOME_HEADLINES: Final[dict[RunOutcome, str]] = {
    RunOutcome.BASE_DESTROYED: "BASE DESTROYED",
    RunOutcome.PLAYERS_ELIMINATED: "ALL LIVES LOST",
}
"""Wording for each outcome the simulation can record. There is no win outcome yet."""

EMPTY_CATALOG_NOTICE: Final[str] = "NO STAGES AVAILABLE"

PAUSE_CAUSE_NOTICES: Final[dict[PauseCause, str]] = {
    PauseCause.PLAYER: "PAUSED BY PLAYER",
    PauseCause.FOCUS_LOSS: "WINDOW LOST FOCUS",
}
"""Sub-headings for the pause screen. The two causes never read alike, so a run that
paused itself while the player was elsewhere is never mistaken for one they paused."""


def _wrapped(index: int, delta: int, length: int) -> int:
    """Move a menu cursor by ``delta``, wrapping. An empty menu keeps index zero."""
    if length <= 0:
        return 0
    return (index + delta) % length


@dataclass(slots=True)
class ClientShell:
    """The whole client, minus the window."""

    catalog: tuple[StageEntry, ...]
    seed: int = DEFAULT_SEED
    rules: Rules = DEFAULT_RULES
    screen: Screen = Screen.MAIN_MENU
    main_index: int = 0
    stage_index: int = 0
    pause_index: int = 0
    run_over_index: int = 0
    session: StageSession | None = None
    focused: bool = True
    pause_cause: PauseCause | None = None
    running: bool = True
    notice: str = field(default="", init=False)

    # -- queries ---------------------------------------------------------------

    @property
    def main_item(self) -> MainMenuItem:
        """The highlighted main menu entry."""
        return MainMenuItem(self.main_index)

    @property
    def pause_item(self) -> PauseItem:
        """The highlighted pause menu entry."""
        return PauseItem(self.pause_index)

    @property
    def run_over_item(self) -> RunOverItem:
        """The highlighted terminal screen entry."""
        return RunOverItem(self.run_over_index)

    @property
    def selected_entry(self) -> StageEntry | None:
        """The highlighted stage, or ``None`` when the catalog is empty."""
        if not self.catalog:
            return None
        return self.catalog[self.stage_index]

    @property
    def consumes_ticks(self) -> bool:
        """Whether the shell is in a state that advances the simulation."""
        return self.screen is Screen.PLAYING and self.session is not None

    @property
    def outcome(self) -> RunOutcome | None:
        """The outcome the simulation recorded for the current run, if any."""
        return None if self.session is None else self.session.state.outcome

    # -- transitions -----------------------------------------------------------

    def quit(self) -> None:
        """Ask the loop to stop after the current frame."""
        self.running = False

    def open_session(self, session: StageSession) -> None:
        """Adopt ``session`` as the current run and show the screen it belongs on.

        A session that already carries an outcome opens on the terminal screen. That is
        how a presentation test drives a terminal screen: it assembles the finished state
        itself. Nothing in the client sets an outcome.
        """
        self.session = session
        self.pause_cause = None
        self.notice = ""
        if session.finished:
            self.run_over_index = 0
            self.screen = Screen.RUN_OVER
        else:
            self.screen = Screen.PLAYING

    def start_selected_stage(self) -> bool:
        """Start the highlighted stage. Returns whether a run began."""
        entry = self.selected_entry
        if entry is None:
            self.notice = EMPTY_CATALOG_NOTICE
            return False
        self.open_session(StageSession.start(entry.stage, seed=self.seed, rules=self.rules))
        return True

    def set_focused(self, focused: bool) -> None:
        """Record window focus, pausing a live run when focus is lost."""
        self.focused = focused
        if not focused and self.screen is Screen.PLAYING:
            self._pause(PauseCause.FOCUS_LOSS)

    def advance(self, ticks: int, intent: PlayerIntent = IDLE_INTENT) -> None:
        """Advance the run by ``ticks``, then show a terminal screen if it ended."""
        if not self.consumes_ticks or self.session is None:
            return
        self.session = self.session.advance(ticks, intent)
        if self.session.finished:
            self.run_over_index = 0
            self.screen = Screen.RUN_OVER

    def handle(self, action: Action) -> None:
        """Apply one edge-triggered action to the current screen."""
        if action is Action.QUIT:
            self.quit()
            return
        match self.screen:
            case Screen.MAIN_MENU:
                self._handle_main_menu(action)
            case Screen.STAGE_SELECT:
                self._handle_stage_select(action)
            case Screen.CONTROLS:
                self._handle_controls(action)
            case Screen.PLAYING:
                self._handle_playing(action)
            case Screen.PAUSED:
                self._handle_paused(action)
            case Screen.RUN_OVER:
                self._handle_run_over(action)

    # -- per-screen handlers ---------------------------------------------------

    def _handle_main_menu(self, action: Action) -> None:
        match action:
            case Action.UI_UP:
                self.main_index = _wrapped(self.main_index, -1, len(MainMenuItem))
            case Action.UI_DOWN:
                self.main_index = _wrapped(self.main_index, 1, len(MainMenuItem))
            case Action.UI_CONFIRM:
                self._confirm_main_menu()
            case _:
                return

    def _confirm_main_menu(self) -> None:
        match self.main_item:
            case MainMenuItem.PLAY:
                self.notice = "" if self.catalog else EMPTY_CATALOG_NOTICE
                self.screen = Screen.STAGE_SELECT
            case MainMenuItem.CONTROLS:
                self.screen = Screen.CONTROLS
            case MainMenuItem.QUIT:
                self.quit()

    def _handle_stage_select(self, action: Action) -> None:
        match action:
            case Action.UI_UP:
                self.stage_index = _wrapped(self.stage_index, -1, len(self.catalog))
            case Action.UI_DOWN:
                self.stage_index = _wrapped(self.stage_index, 1, len(self.catalog))
            case Action.UI_CONFIRM:
                self.start_selected_stage()
            case Action.UI_CANCEL:
                self._return_to_main_menu()
            case _:
                return

    def _handle_controls(self, action: Action) -> None:
        if action in (Action.UI_CONFIRM, Action.UI_CANCEL):
            self._return_to_main_menu()

    def _handle_playing(self, action: Action) -> None:
        if action in (Action.TOGGLE_PAUSE, Action.UI_CANCEL):
            self._pause(PauseCause.PLAYER)

    def _handle_paused(self, action: Action) -> None:
        match action:
            case Action.UI_UP:
                self.pause_index = _wrapped(self.pause_index, -1, len(PauseItem))
            case Action.UI_DOWN:
                self.pause_index = _wrapped(self.pause_index, 1, len(PauseItem))
            case Action.TOGGLE_PAUSE | Action.UI_CANCEL:
                self.resume()
            case Action.UI_CONFIRM:
                self._confirm_pause()
            case _:
                return

    def _confirm_pause(self) -> None:
        match self.pause_item:
            case PauseItem.RESUME:
                self.resume()
            case PauseItem.RESTART:
                self._restart_session()
            case PauseItem.QUIT_TO_MENU:
                self._return_to_main_menu()

    def _handle_run_over(self, action: Action) -> None:
        match action:
            case Action.UI_UP:
                self.run_over_index = _wrapped(self.run_over_index, -1, len(RunOverItem))
            case Action.UI_DOWN:
                self.run_over_index = _wrapped(self.run_over_index, 1, len(RunOverItem))
            case Action.UI_CANCEL:
                self._return_to_main_menu()
            case Action.UI_CONFIRM:
                if self.run_over_item is RunOverItem.RETRY:
                    self._restart_session()
                else:
                    self._return_to_main_menu()
            case _:
                return

    # -- shared transitions ----------------------------------------------------

    def resume(self) -> None:
        """Leave the pause screen and continue the run."""
        if self.screen is not Screen.PAUSED or self.session is None:
            return
        self.pause_cause = None
        self.screen = Screen.PLAYING

    def _pause(self, cause: PauseCause) -> None:
        if self.session is None:
            return
        self.pause_cause = cause
        self.pause_index = 0
        self.screen = Screen.PAUSED

    def _restart_session(self) -> None:
        if self.session is None:
            self._return_to_main_menu()
            return
        self.open_session(self.session.restarted())

    def _return_to_main_menu(self) -> None:
        self.session = None
        self.pause_cause = None
        self.screen = Screen.MAIN_MENU
