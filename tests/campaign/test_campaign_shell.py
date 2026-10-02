"""The client shell driving a campaign, with no window and no pygame.

The shell is the only place the campaign meets a player, so what is asserted here is the
join: that choosing a stage starts the campaign there, that the interstitial says what the
phase means and offers what it says, and that the shell's ``session`` and its ``campaign``
can never describe different runs.
"""

from __future__ import annotations

from battle_city_client.campaign import CampaignPhase, CampaignRules, IdleEnemyDriver
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.shell import ClientShell, PauseItem, RunOverItem, Screen
from battle_city_sim import RunOutcome, TankVariant
from campaign_helpers import (
    FIRING,
    QUICK_INTERVAL,
    TEST_SEED,
    ScriptedEnemyDriver,
    entry_for,
    firing_range_stage,
    observed,
    session_with_outcome,
)

RULES = CampaignRules(
    spawn_interval_ticks=QUICK_INTERVAL, spawn_variants=(TankVariant.ENEMY_NORMAL,)
)
IDLE = PlayerIntent()


def shell(*, stages: int = 2, quota: int = 1, driver: object | None = None) -> ClientShell:
    """A shell over ``stages`` firing-range stages, each declaring ``quota`` enemies."""
    catalog = tuple(
        entry_for(firing_range_stage(f"range-{index}"), waves=(quota,)) for index in range(stages)
    )
    return ClientShell(
        catalog=catalog,
        seed=TEST_SEED,
        campaign_rules=RULES,
        driver=IdleEnemyDriver() if driver is None else driver,  # type: ignore[arg-type]
    )


def play(instance: ClientShell, ticks: int = 400, intent: PlayerIntent = FIRING) -> ClientShell:
    """Advance one tick at a time until the shell stops consuming them."""
    for _ in range(ticks):
        if not instance.consumes_ticks:
            break
        instance.advance(1, intent)
    return instance


def start(instance: ClientShell) -> ClientShell:
    """Walk the menu the way a player does: PLAY, then confirm the highlighted stage."""
    instance.handle(Action.UI_CONFIRM)
    instance.handle(Action.UI_CONFIRM)
    return instance


# -- starting -----------------------------------------------------------------


def test_choosing_a_stage_starts_the_campaign_there() -> None:
    instance = shell(stages=3)
    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.STAGE_SELECT
    instance.handle(Action.UI_DOWN)
    instance.handle(Action.UI_DOWN)
    instance.handle(Action.UI_CONFIRM)

    assert observed(instance.screen) is Screen.PLAYING
    assert instance.campaign is not None
    assert instance.campaign.stage_index == 2
    assert instance.campaign.stage_number == 3
    assert instance.campaign.stage_count == 3
    assert instance.campaign.score == 0
    assert instance.session is not None
    assert instance.session.state.stage_id == "range-2"


def test_the_shell_session_is_the_campaign_session() -> None:
    """Two fields, one run. Everything that reads a session is unaffected by which
    started it, which is why the renderer and the existing client tests need no change."""
    instance = play(start(shell()), ticks=40)
    assert instance.campaign is not None
    assert instance.session is instance.campaign.session


def test_the_declared_wave_count_reaches_the_campaign() -> None:
    assert start(shell(quota=4)).campaign is not None
    assert start(shell(quota=4)).campaign.pending_enemies == 4  # type: ignore[union-attr]


def test_an_empty_catalog_starts_nothing() -> None:
    instance = ClientShell(catalog=())
    instance.handle(Action.UI_CONFIRM)
    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.STAGE_SELECT
    assert instance.campaign is None
    assert instance.notice


# -- the interstitial ---------------------------------------------------------


def test_clearing_a_stage_shows_what_it_was_and_offers_the_next_one() -> None:
    instance = play(start(shell(stages=2)))
    assert observed(instance.screen) is Screen.RUN_OVER
    assert instance.phase is CampaignPhase.STAGE_CLEARED
    assert instance.interstitial_title == "STAGE CLEAR"
    assert instance.interstitial_headline == "STAGE 1 OF 2"
    assert instance.interstitial_labels[0] == "NEXT STAGE"
    assert not instance.interstitial_is_failure


def test_confirming_the_next_stage_starts_it_and_keeps_the_score() -> None:
    instance = play(start(shell(stages=2)))
    scored = instance.campaign.score if instance.campaign else -1
    assert scored == 100

    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.PLAYING
    assert instance.campaign is not None
    assert instance.campaign.stage_index == 1
    assert instance.campaign.score == scored
    assert instance.session is not None
    assert instance.session.state.tick == 0


def test_completing_the_campaign_says_so_and_offers_another_run() -> None:
    instance = play(start(shell(stages=1)))
    assert instance.phase is CampaignPhase.COMPLETED
    assert instance.interstitial_title == "CAMPAIGN COMPLETE"
    assert instance.interstitial_headline == "STAGE CLEARED"
    assert instance.interstitial_labels[0] == "PLAY AGAIN"
    assert not instance.interstitial_is_failure
    assert instance.outcome is None

    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.PLAYING
    assert instance.campaign is not None
    assert instance.campaign.score == 0
    assert instance.campaign.stage_index == 0


def test_a_failed_campaign_reports_the_simulations_own_reason() -> None:
    instance = play(start(shell(quota=3, driver=ScriptedEnemyDriver())), intent=IDLE, ticks=4000)
    assert observed(instance.screen) is Screen.RUN_OVER
    assert instance.phase is CampaignPhase.FAILED
    assert instance.outcome is RunOutcome.PLAYERS_ELIMINATED
    assert instance.interstitial_title == "GAME OVER"
    assert instance.interstitial_headline == "ALL LIVES LOST"
    assert instance.interstitial_labels[0] == "RESTART CAMPAIGN"
    assert instance.interstitial_is_failure


def test_restarting_after_a_loss_begins_the_campaign_again() -> None:
    instance = play(start(shell(quota=3, driver=ScriptedEnemyDriver())), intent=IDLE, ticks=4000)
    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.PLAYING
    assert instance.campaign is not None
    assert instance.campaign.stage_index == 0
    assert instance.campaign.score == 0
    assert instance.campaign.lives == RULES.starting_lives


def test_a_longer_campaign_counts_its_stages_when_it_completes() -> None:
    """One stage is not "all 1 stages"; the wording has to survive both."""
    instance = play(start(shell(stages=2)))
    instance.handle(Action.UI_CONFIRM)
    instance = play(instance)
    assert instance.phase is CampaignPhase.COMPLETED
    assert instance.interstitial_headline == "ALL 2 STAGES CLEARED"


def test_the_interstitial_can_always_be_left_for_the_menu() -> None:
    instance = play(start(shell(stages=2)))
    instance.handle(Action.UI_DOWN)
    assert observed(instance.run_over_item) is RunOverItem.QUIT_TO_MENU
    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.MAIN_MENU
    assert instance.campaign is None
    assert instance.session is None


def test_a_campaign_less_session_keeps_the_free_standing_wording() -> None:
    """``open_session`` is still how a presentation test drives a terminal screen."""
    from battle_city_client.session import StageSession

    instance = shell()
    instance.open_session(StageSession.start(firing_range_stage()))
    assert instance.campaign is None
    assert instance.interstitial_title == "RUN OVER"
    assert instance.interstitial_labels[0] == "RETRY STAGE"


# -- a session written from outside -------------------------------------------
#
# ``ClientShell.session`` is read as well as written, and the write direction is part of
# the contract rather than an accident of the field being public: a caller that assembles
# a state assigns it there, which is how ``tests/client`` drives a terminal screen in a
# build where no live run reaches an outcome on its own. A campaign that republished its
# own session on every ``advance`` would silently discard that assignment. These four
# tests are the regression, and they live here because ``tests/client`` is outside this
# issue's allowed files.


def test_a_session_written_from_outside_is_adopted_by_the_campaign() -> None:
    instance = play(start(shell()), ticks=20)
    assert instance.session is not None
    before = instance.session.state.tick
    instance.session = session_with_outcome(instance.session, RunOutcome.BASE_DESTROYED)

    instance.advance(1, IDLE)

    assert observed(instance.screen) is Screen.RUN_OVER
    assert instance.phase is CampaignPhase.FAILED
    assert instance.outcome is RunOutcome.BASE_DESTROYED
    assert instance.campaign is not None
    assert instance.session is instance.campaign.session
    assert instance.session.state.tick == before, "nothing is simulated past an ending"


def test_a_session_written_from_outside_is_adopted_on_a_zero_tick_frame() -> None:
    """A frame shorter than a tick buys no ticks, and still has to see the assignment.

    At any display faster than the tick rate most frames are this one, so a write that
    only took effect on a frame worth a whole tick would take effect at a time the caller
    cannot predict. ``advance(0)`` is the smallest statement of that case.
    """
    instance = play(start(shell()), ticks=20)
    assert instance.session is not None
    instance.session = session_with_outcome(instance.session, RunOutcome.PLAYERS_ELIMINATED)

    instance.advance(0, IDLE)

    assert observed(instance.screen) is Screen.RUN_OVER
    assert instance.phase is CampaignPhase.FAILED
    assert instance.interstitial_headline == "ALL LIVES LOST"


def test_adopting_an_outside_session_leaves_the_campaigns_own_bookkeeping_alone() -> None:
    """Replacing the state is not a claim to have spawned, scored or cleared anything."""
    instance = play(start(shell(quota=2)), ticks=20)
    assert instance.campaign is not None
    pending = instance.campaign.pending_enemies
    score = instance.campaign.score
    assert instance.session is not None
    instance.session = session_with_outcome(instance.session, RunOutcome.BASE_DESTROYED)

    instance.advance(1, IDLE)

    assert instance.campaign is not None
    assert instance.campaign.pending_enemies == pending
    assert instance.campaign.score == score
    assert instance.campaign.stage_index == 0


def test_the_ordinary_frame_does_not_disturb_the_run() -> None:
    """Adoption is identity-tested, so the session the campaign just produced is kept."""
    instance = play(start(shell()), ticks=20)
    assert instance.campaign is not None
    held = instance.campaign

    instance.advance(0, IDLE)

    assert instance.campaign is not None
    assert instance.campaign.session is held.session, "the live session is not rebuilt"
    assert instance.campaign.phase is held.phase
    assert instance.campaign.pending_enemies == held.pending_enemies
    assert observed(instance.screen) is Screen.PLAYING


# -- pause and restart --------------------------------------------------------


def test_pausing_a_campaign_and_restarting_the_stage_rewinds_its_score() -> None:
    instance = play(start(shell(stages=2, quota=2)))
    assert instance.phase is CampaignPhase.STAGE_CLEARED
    instance.handle(Action.UI_CONFIRM)  # on to stage two
    instance.advance(QUICK_INTERVAL, FIRING)  # one kill in, and not yet cleared
    assert instance.campaign is not None
    assert instance.campaign.stage_index == 1
    carried = instance.campaign.stage_start_score
    assert carried == 200
    assert instance.campaign.score > carried

    instance.handle(Action.TOGGLE_PAUSE)
    assert observed(instance.screen) is Screen.PAUSED
    instance.handle(Action.UI_DOWN)
    assert observed(instance.pause_item) is PauseItem.RESTART
    instance.handle(Action.UI_CONFIRM)

    assert observed(instance.screen) is Screen.PLAYING
    assert instance.campaign is not None
    assert instance.campaign.stage_index == 1, "a stage restart stays on its stage"
    assert instance.campaign.score == carried, "the abandoned attempt leaves nothing behind"
    assert instance.session is not None
    assert instance.session.state.tick == 0


def test_quitting_to_the_menu_from_a_pause_drops_the_campaign() -> None:
    instance = play(start(shell()), ticks=20)
    instance.handle(Action.TOGGLE_PAUSE)
    instance.handle(Action.UI_DOWN)
    instance.handle(Action.UI_DOWN)
    instance.handle(Action.UI_CONFIRM)
    assert observed(instance.screen) is Screen.MAIN_MENU
    assert instance.campaign is None
    assert instance.session is None


def test_a_paused_campaign_consumes_no_ticks() -> None:
    instance = play(start(shell()), ticks=20)
    instance.handle(Action.TOGGLE_PAUSE)
    assert not instance.consumes_ticks
    before = instance.session.state.tick if instance.session else -1
    instance.advance(50, FIRING)
    assert instance.session is not None
    assert instance.session.state.tick == before


def test_losing_window_focus_pauses_a_campaign() -> None:
    instance = play(start(shell()), ticks=20)
    instance.set_focused(False)
    assert observed(instance.screen) is Screen.PAUSED
    assert instance.campaign is not None
