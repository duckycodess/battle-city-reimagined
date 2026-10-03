"""Badges: how one is earned, and the three places one must never reach.

The unlock half is ordinary: a badge states a tally, and whether it is unlocked is
recomputed from the campaign record every time it is read.

The other half is the product specification's rule -- cosmetics must not alter competitive
balance, and competitive cosmetics must not affect simulation state or visibility -- and it
is asserted rather than described. Two profiles that differ only in the badge they have
selected are driven through the same campaign and the same online session, and three
things have to hold:

* every tick's canonical state encoding is byte-identical,
* every tick's state hash is equal, and the sequence is *not* a constant, so the
  comparison is of a run that actually happened rather than of two idle boards, and
* every message either client would send is byte-identical, and no badge identifier
  appears anywhere in those bytes.

Nothing here imports pygame.
"""

from __future__ import annotations

from battle_city_client.campaign import CampaignRules
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.online import OnlineConfig
from battle_city_client.persistence import (
    BADGES,
    CampaignProgress,
    LocalProfile,
    cycled_badge,
    selected_badge,
    unlocked_badges,
)
from battle_city_client.shell import ClientShell, Screen
from battle_city_client.stage_adapter import StageEntry
from battle_city_protocol import (
    BaseSnapshot,
    ClientMessage,
    ContentRef,
    JoinAccepted,
    LobbyInfo,
    LobbyMember,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchSettings,
    MatchStarting,
    PlayerSnapshot,
    ServerMessage,
    SessionInfo,
    StateSnapshot,
    TankSnapshot,
    encode_message,
)
from battle_city_sim import (
    GridPos,
    PlayerSpawn,
    Stage,
    TankVariant,
    Tile,
    encode_state,
    state_hash,
)

VETERAN = "veteran"
ACE = "ace"

FIRING = PlayerIntent(fire=True)
RANGE_RULES = CampaignRules(spawn_interval_ticks=5, spawn_variants=(TankVariant.ENEMY_NORMAL,))
RUN_TICKS = 24
"""Short of the 28 ticks this stage clears in, so the comparison is of a live run."""


# -- unlocking ----------------------------------------------------------------


def test_a_fresh_profile_has_exactly_the_badge_that_needs_nothing() -> None:
    unlocked = unlocked_badges(CampaignProgress())
    assert len(unlocked) == 1
    assert unlocked[0] is BADGES[0]


def test_clearing_a_stage_and_completing_a_campaign_each_reveal_one() -> None:
    after_stage = CampaignProgress(stages_cleared=1)
    assert [badge.badge_id for badge in unlocked_badges(after_stage)][1:] == [VETERAN]

    after_campaign = CampaignProgress(stages_cleared=3, campaigns_completed=1)
    assert [badge.badge_id for badge in unlocked_badges(after_campaign)][1:] == [VETERAN, ACE]


def test_a_selection_the_tally_does_not_support_falls_back_rather_than_counting() -> None:
    """A hand-edited file can name a badge. Naming one is not earning one."""
    claimed = CampaignProgress(selected_badge=ACE)
    assert selected_badge(claimed) is BADGES[0]

    earned = CampaignProgress(campaigns_completed=1, selected_badge=ACE)
    assert selected_badge(earned).badge_id == ACE


def test_a_badge_this_build_does_not_have_falls_back() -> None:
    assert selected_badge(CampaignProgress(selected_badge="no-such-badge")) is BADGES[0]


def test_cycling_only_ever_reaches_an_earned_badge() -> None:
    progress = CampaignProgress(stages_cleared=1)
    assert cycled_badge(progress, 1).badge_id == VETERAN
    assert cycled_badge(progress, -1).badge_id == VETERAN
    assert cycled_badge(CampaignProgress(), 1) is BADGES[0]


def test_the_options_screen_cannot_select_a_locked_badge() -> None:
    profile = LocalProfile()
    profile.select_badge(ACE)
    assert profile.badge is BADGES[0]

    profile.record_stage_cleared(100)
    profile.select_badge(VETERAN)
    assert profile.badge.badge_id == VETERAN


def test_choosing_a_badge_is_a_key_on_the_controls_screen() -> None:
    shell = ClientShell(
        catalog=(_entry("range-01"),),
        profile=LocalProfile(progress=CampaignProgress(stages_cleared=1)),
    )
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    assert shell.screen is Screen.CONTROLS

    shell.handle(Action.UI_DOWN)
    assert shell.profile.badge.badge_id == VETERAN
    shell.handle(Action.UI_DOWN)
    assert shell.profile.badge is BADGES[0]


# -- the stages the comparison is run on --------------------------------------


def _rows() -> tuple[str, ...]:
    grid = [[Tile.EMPTY for _ in range(16)] for _ in range(16)]
    grid[15][7] = Tile.HOME
    return tuple("".join(str(tile.value) for tile in row) for row in grid)


def _entry(level_id: str) -> StageEntry:
    stage = Stage.create(
        stage_id=level_id,
        name=level_id.upper(),
        rows=_rows(),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(7, 12)),),
        enemy_spawns=(GridPos(7, 6),),
    )
    return StageEntry(level_id=level_id, name=stage.name, stage=stage, waves=(1,))


CATALOG = (_entry("range-01"),)


def _profile(badge_id: str, *, stages_cleared: int = 0, campaigns: int = 0) -> LocalProfile:
    return LocalProfile(
        progress=CampaignProgress(
            stages_cleared=stages_cleared,
            campaigns_completed=campaigns,
            selected_badge=badge_id,
        )
    )


def _run(profile: LocalProfile) -> tuple[list[str], list[bytes]]:
    """Play the same stage with ``profile``, collecting every tick's canonical state."""
    shell = ClientShell(catalog=CATALOG, campaign_rules=RANGE_RULES, profile=profile)
    assert shell.start_selected_stage()
    hashes: list[str] = []
    encodings: list[bytes] = []
    for _ in range(RUN_TICKS):
        shell.advance(1, FIRING)
        assert shell.session is not None
        hashes.append(state_hash(shell.session.state))
        encodings.append(encode_state(shell.session.state))
    return hashes, encodings


def test_a_selected_badge_changes_no_tick_of_the_simulation() -> None:
    plain = _run(_profile(BADGES[0].badge_id))
    decorated = _run(_profile(ACE, stages_cleared=9, campaigns=3))

    assert decorated[0] == plain[0]
    assert decorated[1] == plain[1]


def test_the_comparison_is_of_a_run_that_actually_happened() -> None:
    """Without this, two identical *empty* histories would pass the test above."""
    hashes, encodings = _run(_profile(BADGES[0].badge_id))
    assert len(hashes) == RUN_TICKS
    assert len(set(hashes)) > 1
    assert len(set(encodings)) > 1
    assert all(len(digest) == 64 for digest in hashes)


def test_no_badge_identifier_appears_in_an_encoded_state() -> None:
    _, encodings = _run(_profile(ACE, stages_cleared=9, campaigns=3))
    joined = b"".join(encodings)
    for badge in BADGES:
        assert badge.badge_id.encode() not in joined
        assert badge.label.encode() not in joined


# -- online -------------------------------------------------------------------

SESSION_ID = "session-1"
CONTENT = ContentRef(
    pack_id="duo-pack", pack_version="1.0.0", level_id="duo-arena", content_schema_version=1
)
GRID: tuple[str, ...] = ("0" * 16,) * 15 + ("0" * 8 + "8" + "0" * 7,)
LOBBY_SETTLED = 1
"""Index in the server script after which this client is seated and may say it is ready."""


def _settings() -> MatchSettings:
    return MatchSettings(
        mode=MatchMode.COOP, level_id="duo-arena", content=CONTENT, tick_rate=60, max_players=2
    )


def _session_info() -> SessionInfo:
    return SessionInfo(
        tick_rate=60,
        keyframe_interval=30,
        max_players=2,
        content=CONTENT,
        rules_digest="a" * 64,
        state_version=1,
    )


def _server_script() -> tuple[ServerMessage, ...]:
    """Exactly what the server says, from the welcome to the first keyframe."""
    return (
        LobbyWelcome(
            session_id=SESSION_ID,
            slot=1,
            host=True,
            lobby=LobbyInfo(
                capacity=2,
                offered_modes=(MatchMode.COOP,),
                playable_modes=(MatchMode.COOP,),
                offered_levels=("duo-arena",),
            ),
        ),
        LobbyState(
            session_id=SESSION_ID,
            revision=1,
            settings=_settings(),
            members=(
                LobbyMember(slot=1, display_name="ducky", ready=False, host=True, connected=True),
            ),
            host_slot=1,
            startable=False,
            blocked=None,
        ),
        MatchStarting(
            session_id=SESSION_ID,
            slot=1,
            token="token-slot-1-abcdef",
            session=_session_info(),
            settings=_settings(),
        ),
        JoinAccepted(session_id=SESSION_ID, slot=1, tick=0, session=_session_info()),
        StateSnapshot(
            session_id=SESSION_ID,
            tick=0,
            tick_rate=60,
            state_version=1,
            keyframe=True,
            grid=GRID,
            tanks=(
                TankSnapshot(
                    entity_id=1,
                    variant=0,
                    x=64,
                    y=160,
                    facing=0,
                    slot=1,
                    gatling_ticks=0,
                    invincible_ticks=0,
                ),
            ),
            projectiles=(),
            powerups=(),
            players=(PlayerSnapshot(slot=1, lives=3, tank_id=1, spawn_x=4, spawn_y=10),),
            base=BaseSnapshot(cell_x=8, cell_y=15, destroyed=False),
            state_hash="b" * 64,
        ),
    )


def _online_traffic(profile: LocalProfile) -> list[bytes]:
    """Everything a client with ``profile`` would send across one whole session."""
    shell = ClientShell(
        catalog=CATALOG,
        profile=profile,
        online_config=OnlineConfig(
            endpoint="loopback:0",
            session_id=SESSION_ID,
            ticket="ticket-host-aaaaaaaa",
            display_name="ducky",
            content=CONTENT,
        ),
    )
    sent: list[ClientMessage] = []
    assert shell.open_online()
    sent.extend(shell.take_outbox())
    for index, message in enumerate(_server_script()):
        shell.receive(message)
        sent.extend(shell.take_outbox())
        if index == LOBBY_SETTLED:
            # Readiness is a lobby message, so it is offered while there is still a
            # lobby: the traffic being compared has to include more than a join.
            shell.handle(Action.ONLINE_READY)
            sent.extend(shell.take_outbox())
    shell.pump_online(FIRING)
    sent.extend(shell.take_outbox())
    return [encode_message(message) for message in sent]


def test_two_clients_differing_only_in_a_badge_send_identical_bytes() -> None:
    plain = _online_traffic(_profile(BADGES[0].badge_id))
    decorated = _online_traffic(_profile(ACE, stages_cleared=9, campaigns=3))

    assert plain == decorated
    assert len(plain) == 4


def test_no_badge_identifier_appears_in_anything_sent() -> None:
    traffic = b"".join(_online_traffic(_profile(ACE, stages_cleared=9, campaigns=3)))
    for badge in BADGES:
        assert badge.badge_id.encode() not in traffic
        assert badge.label.encode() not in traffic
