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
* **Failure is still only ever the simulation's.** The shell reads
  :attr:`SimulationState.outcome` and never sets one, and so does the campaign: its
  ``FAILED`` phase is derived from that field rather than decided beside it. What the
  campaign adds is the other direction -- a stage cleared and a campaign completed -- which
  the simulation has no value for and should not have one for.

Campaign and free play
----------------------
Choosing a stage starts the campaign *at that stage*. There is only one mode: the product
specification asks that a mode supply rules and content to the shared simulation rather
than fork it, and with no saved progress the stage list is also the whole checkpoint
story. A run that reaches a stage clear, a campaign completion or a failure shows the
interstitial on :attr:`Screen.RUN_OVER`, whose wording and menu come from the campaign
phase.

The shell still supports a campaign-less session, because :meth:`open_session` takes one
directly and the presentation tests drive terminal screens that way. When a campaign is
running, :attr:`session` holds that campaign's session, so everything that reads a session
-- the renderer, the HUD, a test -- is unaffected by which of the two started it.

:attr:`session` is *written* as well as read, and that direction is part of the contract
rather than an accident of it being a public field: a caller assembles a state and assigns
it, which is how a test reaches a terminal screen without enemy behaviour to reach it
with. A running campaign therefore adopts whatever :attr:`session` holds at the top of
:meth:`advance` and re-reads its phase from it, instead of overwriting the assignment with
the session it last produced.

:attr:`Screen` deliberately gains no members. A stage clear and a campaign completion each
deserve their own screen, and
``tests/client/test_client_presentation.py::test_every_screen_draws_something`` asserts
every member is drawn by a test this issue may not edit. Issue #36 owns that split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from battle_city_sim import DEFAULT_RULES, Rules, RunOutcome

from .campaign import (
    DEFAULT_CAMPAIGN_RULES,
    CampaignPhase,
    CampaignRules,
    CampaignRun,
    EnemyCommandDriver,
    IdleEnemyDriver,
    campaign_plan,
)
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
"""Wording for each failure the simulation can record. Victory is a campaign phase."""

INTERSTITIAL_TITLES: Final[dict[CampaignPhase, str]] = {
    CampaignPhase.PLAYING: "PAUSED",
    CampaignPhase.STAGE_CLEARED: "STAGE CLEAR",
    CampaignPhase.COMPLETED: "CAMPAIGN COMPLETE",
    CampaignPhase.FAILED: "GAME OVER",
}
"""Headline for each campaign phase. ``PLAYING`` never reaches the interstitial."""

INTERSTITIAL_PRIMARY_LABELS: Final[dict[CampaignPhase, str]] = {
    CampaignPhase.PLAYING: "RESUME",
    CampaignPhase.STAGE_CLEARED: "NEXT STAGE",
    CampaignPhase.COMPLETED: "PLAY AGAIN",
    CampaignPhase.FAILED: "RESTART CAMPAIGN",
}
"""What the first interstitial entry does, said in the words of what it does.

A cleared stage continues; a completed or failed campaign starts over. The free-play
wording stays :data:`RUN_OVER_LABELS`, because a campaign-less session has one stage and
retrying it is all there is.
"""

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
    campaign_rules: CampaignRules = DEFAULT_CAMPAIGN_RULES
    driver: EnemyCommandDriver = field(default_factory=IdleEnemyDriver)
    """Who commands the enemy tanks. The default commands nobody; see ``campaign/driver``.

    A field rather than a constant so a test -- or a later build with a declared dependency
    on the AI package -- injects a real one without the shell learning what a bot is.
    """
    campaign: CampaignRun | None = None
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
        """The failure the simulation recorded for the current run, if any."""
        return None if self.session is None else self.session.state.outcome

    @property
    def phase(self) -> CampaignPhase | None:
        """The campaign's phase, or ``None`` when no campaign is running."""
        return None if self.campaign is None else self.campaign.phase

    @property
    def interstitial_title(self) -> str:
        """The headline on the screen shown when a run stops."""
        if self.campaign is None:
            return "RUN OVER"
        return INTERSTITIAL_TITLES[self.campaign.phase]

    @property
    def interstitial_headline(self) -> str:
        """The line under the headline: why the run stopped, in one phrase."""
        outcome = self.outcome
        if outcome is not None:
            return OUTCOME_HEADLINES[outcome]
        campaign = self.campaign
        if campaign is None:
            return "RUN ENDED"
        if campaign.phase is CampaignPhase.COMPLETED:
            if campaign.stage_count == 1:
                return "STAGE CLEARED"
            return f"ALL {campaign.stage_count} STAGES CLEARED"
        return f"STAGE {campaign.stage_number} OF {campaign.stage_count}"

    @property
    def interstitial_labels(self) -> tuple[str, ...]:
        """The interstitial menu, in display order and in the words of what it does."""
        tail = RUN_OVER_LABELS[RunOverItem.QUIT_TO_MENU]
        if self.campaign is None:
            return (RUN_OVER_LABELS[RunOverItem.RETRY], tail)
        return (INTERSTITIAL_PRIMARY_LABELS[self.campaign.phase], tail)

    @property
    def interstitial_is_failure(self) -> bool:
        """Whether the interstitial reports a loss, so colour is not the only signal."""
        if self.outcome is not None:
            return True
        return self.campaign is not None and self.campaign.phase is CampaignPhase.FAILED

    # -- transitions -----------------------------------------------------------

    def quit(self) -> None:
        """Ask the loop to stop after the current frame."""
        self.running = False

    def open_session(self, session: StageSession) -> None:
        """Adopt ``session`` as a campaign-less run and show the screen it belongs on.

        A session that already carries an outcome opens on the terminal screen. That is
        how a presentation test drives a terminal screen: it assembles the finished state
        itself. Nothing in the client sets an outcome.
        """
        self.campaign = None
        self.session = session
        self.pause_cause = None
        self.notice = ""
        if session.finished:
            self.run_over_index = 0
            self.screen = Screen.RUN_OVER
        else:
            self.screen = Screen.PLAYING

    def open_campaign(self, campaign: CampaignRun) -> None:
        """Adopt ``campaign`` and show the screen its phase belongs on.

        :attr:`session` is set from the campaign here and in :meth:`advance`, and nowhere
        else, so the two can never describe different runs.
        """
        self.campaign = campaign
        self.session = campaign.session
        self.pause_cause = None
        self.notice = ""
        if campaign.phase is CampaignPhase.PLAYING:
            self.screen = Screen.PLAYING
        else:
            self.run_over_index = 0
            self.screen = Screen.RUN_OVER

    def start_selected_stage(self) -> bool:
        """Start the campaign at the highlighted stage. Returns whether a run began.

        Starting part-way through is the checkpoint policy, not a debug affordance: there
        is no saved progress to resume from, so a player picks up where they choose, with
        the campaign's starting lives and a score of zero.
        """
        if self.selected_entry is None:
            self.notice = EMPTY_CATALOG_NOTICE
            return False
        self.open_campaign(
            CampaignRun.start(
                campaign_plan(self.catalog, self.campaign_rules),
                seed=self.seed,
                rules=self.campaign_rules,
                sim_rules=self.rules,
                driver=self.driver,
                stage_index=self.stage_index,
            )
        )
        return True

    def set_focused(self, focused: bool) -> None:
        """Record window focus, pausing a live run when focus is lost."""
        self.focused = focused
        if not focused and self.screen is Screen.PLAYING:
            self._pause(PauseCause.FOCUS_LOSS)

    def advance(self, ticks: int, intent: PlayerIntent = IDLE_INTENT) -> None:
        """Advance the run by ``ticks``, then show the interstitial if it stopped.

        A campaign stops for three reasons and a campaign-less session for one. Both leave
        the playing screen the same way: from inside this method, with no key press behind
        the transition, which is why :meth:`ClientApp.advance_frame` tidies up held input
        afterwards.

        :attr:`session` is writable and a caller may have replaced it since the last tick,
        so a campaign adopts whatever is there before it runs any -- including when
        ``ticks`` is zero, which is an ordinary frame at a display faster than the tick
        rate. A state written from outside therefore reaches the screen on the next frame
        whether or not that frame bought a tick, and the campaign and the shell can never
        describe different runs.
        """
        if not self.consumes_ticks or self.session is None:
            return
        campaign = self.campaign
        if campaign is not None:
            campaign = campaign.with_session(self.session)
            self.campaign = campaign.advance(ticks, intent)
            self.session = self.campaign.session
            if self.campaign.phase is not CampaignPhase.PLAYING:
                self.run_over_index = 0
                self.screen = Screen.RUN_OVER
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
                    self._continue_from_interstitial()
                else:
                    self._return_to_main_menu()
            case _:
                return

    def _continue_from_interstitial(self) -> None:
        """What the first interstitial entry does, which depends on why the run stopped."""
        campaign = self.campaign
        if campaign is None:
            self._restart_session()
            return
        match campaign.phase:
            case CampaignPhase.STAGE_CLEARED:
                self.open_campaign(campaign.advanced_stage())
            case CampaignPhase.COMPLETED | CampaignPhase.FAILED:
                self.open_campaign(campaign.restarted())
            case CampaignPhase.PLAYING:
                # Not reachable through the loop: the interstitial is only shown for a
                # phase that stopped. Resuming is still the honest answer to "continue".
                self.open_campaign(campaign)

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
        """Replay the current stage. In a campaign that rewinds the score with it."""
        campaign = self.campaign
        if campaign is not None:
            self.open_campaign(campaign.restarted_stage())
            return
        if self.session is None:
            self._return_to_main_menu()
            return
        self.open_session(self.session.restarted())

    def _return_to_main_menu(self) -> None:
        self.session = None
        self.campaign = None
        self.pause_cause = None
        self.screen = Screen.MAIN_MENU
