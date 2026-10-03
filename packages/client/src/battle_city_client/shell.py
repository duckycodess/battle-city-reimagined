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

from battle_city_protocol import ClientMessage, MatchMode, ServerMessage
from battle_city_sim import DEFAULT_RULES, Rules, RunOutcome

from .intents import IDLE_INTENT, Action, PlayerIntent
from .online import OnlineConfig, OnlinePhase, OnlineSession
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
    ONLINE_LOBBY = "online_lobby"
    """A server-owned lobby: roster, settings, readiness, and the host's start."""

    ONLINE_PLAY = "online_play"
    """A run the server is running. Everything on this screen came off the wire."""


class PauseCause(Enum):
    """Why a run is paused. Shown on the pause screen so the two never look alike."""

    PLAYER = "player"
    FOCUS_LOSS = "focus_loss"


class MainMenuItem(Enum):
    """Main menu entries, in display order."""

    PLAY = 0
    CONTROLS = 1
    ONLINE = 2
    QUIT = 3


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
    MainMenuItem.ONLINE: "ONLINE",
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

NO_SERVER_NOTICE: Final[str] = "ONLINE NEEDS --SERVER --SESSION --TICKET"
"""Shown when online play was chosen without the details needed to reach a lobby."""

ONLINE_HELP: Final[tuple[tuple[str, str], ...]] = (
    ("READY", "R"),
    ("MODE (HOST)", "M"),
    ("STAGE (HOST)", "L"),
    ("START (HOST)", "ENTER"),
    ("LEAVE", "ESC"),
)
"""What the lobby screen lists. Kept beside the shell so the two cannot drift."""

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
    online_config: OnlineConfig | None = None
    online: OnlineSession | None = field(default=None, init=False)
    outbox: list[ClientMessage] = field(default_factory=list, init=False, repr=False)
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
        """Whether the shell is in a state that advances a *local* simulation.

        Never true online. An online run is advanced by the server and arrives as
        snapshots; a client that also stepped a local copy would be showing a second
        game that happens to look like the first one until it does not.
        """
        return self.screen is Screen.PLAYING and self.session is not None

    @property
    def drives_tank(self) -> bool:
        """Whether held keys are being sampled for a tank, locally or over the wire."""
        return self.consumes_ticks or (
            self.screen is Screen.ONLINE_PLAY and self.online is not None
        )

    @property
    def online_phase(self) -> OnlinePhase | None:
        """Where the online session has got to, or ``None`` when there is not one."""
        return None if self.online is None else self.online.phase

    @property
    def wants_link(self) -> bool:
        """Whether the shell is on an online screen and needs a transport open."""
        return self.screen in (Screen.ONLINE_LOBBY, Screen.ONLINE_PLAY)

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

    def open_online(self) -> bool:
        """Begin an online session and queue the seat request. Returns whether it began.

        Nothing is sent from here. The join message goes into :attr:`outbox`, which the
        loop drains into whatever transport it opened; the shell never holds a socket.
        """
        if self.online_config is None:
            self.notice = NO_SERVER_NOTICE
            return False
        self.session = None
        self.pause_cause = None
        self.notice = ""
        session = self.online_config.session()
        self.online = session
        self.outbox.append(session.join_message())
        self.screen = Screen.ONLINE_LOBBY
        return True

    def take_outbox(self) -> tuple[ClientMessage, ...]:
        """Hand over everything waiting to be sent, and forget it."""
        pending = tuple(self.outbox)
        self.outbox.clear()
        return pending

    def receive(self, message: ServerMessage) -> None:
        """Apply one authoritative message and follow wherever it leads.

        The two transitions that happen here are the server's, not the shell's: a match
        that started makes this client prove its membership, and a membership that was
        accepted puts it on the online screen. Neither is anticipated.
        """
        session = self.online
        if session is None:
            return
        session.apply(message)
        if session.phase is OnlinePhase.STARTING:
            request = session.join_request()
            if request is not None:
                self.outbox.append(request)
        if session.phase is OnlinePhase.PLAYING and self.screen is Screen.ONLINE_LOBBY:
            self.screen = Screen.ONLINE_PLAY

    def pump_online(self, intent: PlayerIntent = IDLE_INTENT) -> None:
        """Offer this client's intent for the tick the server last reported."""
        session = self.online
        if session is None or self.screen is not Screen.ONLINE_PLAY:
            return
        batch = session.input_batch(intent)
        if batch is not None:
            self.outbox.append(batch)

    def link_lost(self, detail: str = "") -> None:
        """Record that the transport ended. The session is over for this client."""
        if self.online is not None:
            self.online.link_lost(detail)

    def leave_online(self) -> None:
        """Give up the seat, say so if the session is still live, and go back."""
        session = self.online
        if session is not None:
            leaving = session.leave_message()
            if leaving is not None:
                self.outbox.append(leaving)
        self.online = None
        self.screen = Screen.MAIN_MENU
        self.notice = ""

    def set_focused(self, focused: bool) -> None:
        """Record window focus, pausing a live *local* run when focus is lost.

        An online run is not paused, because pausing it would be a fiction: the server
        keeps running the match whatever this window is doing. The held keys are still
        dropped by the loop, so an unfocused client stops driving its tank rather than
        driving it blind.
        """
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
            case Screen.ONLINE_LOBBY:
                self._handle_online_lobby(action)
            case Screen.ONLINE_PLAY:
                self._handle_online_play(action)

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
            case MainMenuItem.ONLINE:
                self.open_online()
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

    def _handle_online_lobby(self, action: Action) -> None:
        session = self.online
        if session is None or action is Action.UI_CANCEL:
            self.leave_online()
            return
        if session.phase is OnlinePhase.ENDED:
            if action in (Action.UI_CONFIRM, Action.ONLINE_READY):
                self.leave_online()
            return
        match action:
            case Action.ONLINE_READY:
                self._offer(session.ready_message(not session.ready))
            case Action.ONLINE_MODE:
                self._offer(session.configure_message(mode=self._next_mode(session)))
            case Action.ONLINE_STAGE:
                self._offer(session.configure_message(level_id=self._next_level(session)))
            case Action.UI_CONFIRM:
                self._offer(session.start_message())
            case _:
                return

    def _handle_online_play(self, action: Action) -> None:
        """Leaving is the only thing a key does here.

        There is no pause: the match belongs to the server and this window cannot stop
        it. Offering a pause that did nothing would be worse than not offering one.
        """
        if action in (Action.UI_CANCEL, Action.TOGGLE_PAUSE):
            self.leave_online()

    def _offer(self, message: ClientMessage | None) -> None:
        """Queue a message the session was willing to produce, and drop a ``None``."""
        if message is not None:
            self.outbox.append(message)

    @staticmethod
    def _next_mode(session: OnlineSession) -> MatchMode:
        """The next mode the server offers, including the ones it will not start.

        A competitive mode is selectable because the setting is real and is recorded in
        the match settings. The lobby says plainly that this build will not start one,
        and the server refuses if anybody tries.
        """
        offered = tuple(MatchMode) if session.info is None else tuple(session.info.offered_modes)
        current = MatchMode.COOP if session.lobby is None else session.lobby.settings.mode
        index = offered.index(current) if current in offered else -1
        return offered[(index + 1) % len(offered)]

    @staticmethod
    def _next_level(session: OnlineSession) -> str | None:
        offered = () if session.info is None else tuple(session.info.offered_levels)
        if not offered or session.lobby is None:
            return None
        current = session.lobby.settings.level_id
        index = offered.index(current) if current in offered else -1
        return offered[(index + 1) % len(offered)]

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
        self.online = None
        self.pause_cause = None
        self.screen = Screen.MAIN_MENU
