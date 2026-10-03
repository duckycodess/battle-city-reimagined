"""Rendering and the asset seam: every screen draws, and nothing is a colour alone."""

from __future__ import annotations

import pygame
import pytest
from battle_city_client import (
    Action,
    PlayerIntent,
    ProceduralAssetLibrary,
    Renderer,
    theme,
)
from battle_city_client.glyphs import GLYPHS, MISSING_GLYPH, glyph_rows, text_width
from battle_city_client.online import OnlineConfig
from battle_city_client.shell import ClientShell, Screen
from battle_city_protocol import (
    BaseSnapshot,
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
    SessionInfo,
    StateSnapshot,
    TankSnapshot,
)
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    Faction,
    PowerupKind,
    Rules,
    RunOutcome,
    TankVariant,
    Tile,
)
from client_helpers import (
    distinct_colors,
    finished_session,
    logical_surface,
    make_shell,
    opaque_pixels,
    renderer,
)


def _rendered(shell: ClientShell) -> pygame.Surface:
    surface = logical_surface()
    renderer().render(surface, shell)
    return surface


def _playing_shell() -> ClientShell:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(45, PlayerIntent(direction=Direction.LEFT, fire=True))
    return shell


# -- online fixtures ----------------------------------------------------------
#
# The online screens are reached by handing the shell the server messages that put it
# there. No server runs here: `tests/multiplayer` drives the real one, and this file
# only needs each screen to be *reachable* so the coverage assertion below stays total.

_ONLINE_SESSION_ID = "session-1"
_ONLINE_CONTENT = ContentRef(
    pack_id="duo-pack", pack_version="1.0.0", level_id="duo-arena", content_schema_version=1
)
_ONLINE_GRID: tuple[str, ...] = ("0" * 16,) * 15 + ("0" * 8 + "8" + "0" * 7,)


def _online_settings() -> MatchSettings:
    return MatchSettings(
        mode=MatchMode.COOP,
        level_id="duo-arena",
        content=_ONLINE_CONTENT,
        tick_rate=60,
        max_players=2,
    )


def _online_shell() -> ClientShell:
    """A shell seated in a lobby with a two-member roster, as the server broadcast it."""
    shell = make_shell()
    shell.online_config = OnlineConfig(
        endpoint="loopback:0",
        session_id=_ONLINE_SESSION_ID,
        ticket="ticket-host-aaaaaaaa",
        display_name="ducky",
        content=_ONLINE_CONTENT,
    )
    assert shell.open_online()
    shell.receive(
        LobbyWelcome(
            session_id=_ONLINE_SESSION_ID,
            slot=1,
            host=True,
            lobby=LobbyInfo(
                capacity=2,
                offered_modes=(MatchMode.COOP, MatchMode.FREE_FOR_ALL, MatchMode.TEAM_BATTLE),
                playable_modes=(MatchMode.COOP,),
                offered_levels=("duo-arena",),
            ),
        )
    )
    shell.receive(
        LobbyState(
            session_id=_ONLINE_SESSION_ID,
            revision=1,
            settings=_online_settings(),
            members=(
                LobbyMember(slot=1, display_name="ducky", ready=True, host=True, connected=True),
                LobbyMember(slot=2, display_name="tj", ready=False, host=False, connected=True),
            ),
            host_slot=1,
            startable=False,
            blocked=None,
        )
    )
    return shell


def _online_playing_shell() -> ClientShell:
    """The same shell after the server started the match and sent the first keyframe."""
    shell = _online_shell()
    session_info = SessionInfo(
        tick_rate=60,
        keyframe_interval=30,
        max_players=2,
        content=_ONLINE_CONTENT,
        rules_digest="a" * 64,
        state_version=1,
    )
    shell.receive(
        MatchStarting(
            session_id=_ONLINE_SESSION_ID,
            slot=1,
            token="token-slot-1-abcdef",
            session=session_info,
            settings=_online_settings(),
        )
    )
    shell.receive(JoinAccepted(session_id=_ONLINE_SESSION_ID, slot=1, tick=0, session=session_info))
    shell.receive(
        StateSnapshot(
            session_id=_ONLINE_SESSION_ID,
            tick=0,
            tick_rate=60,
            state_version=1,
            keyframe=True,
            grid=_ONLINE_GRID,
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
            players=(
                PlayerSnapshot(slot=1, lives=3, tank_id=1, spawn_x=4, spawn_y=10),
                PlayerSnapshot(slot=2, lives=3, tank_id=None, spawn_x=12, spawn_y=10),
            ),
            base=BaseSnapshot(cell_x=8, cell_y=15, destroyed=False),
            state_hash="b" * 64,
        )
    )
    return shell


# -- screens ------------------------------------------------------------------


def test_every_screen_draws_something() -> None:
    shell = make_shell()
    frames: dict[Screen, pygame.Surface] = {}

    frames[Screen.MAIN_MENU] = _rendered(shell)
    shell.handle(Action.UI_CONFIRM)
    frames[Screen.STAGE_SELECT] = _rendered(shell)
    shell.handle(Action.UI_CANCEL)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    frames[Screen.CONTROLS] = _rendered(shell)
    shell.handle(Action.UI_CANCEL)
    shell.handle(Action.UI_DOWN)
    shell.handle(Action.UI_CONFIRM)
    frames[Screen.OPTIONS] = _rendered(shell)

    playing = _playing_shell()
    frames[Screen.PLAYING] = _rendered(playing)
    playing.handle(Action.TOGGLE_PAUSE)
    frames[Screen.PAUSED] = _rendered(playing)

    over = make_shell()
    over.open_session(finished_session(RunOutcome.BASE_DESTROYED))
    frames[Screen.RUN_OVER] = _rendered(over)

    lobby = _online_shell()
    frames[Screen.ONLINE_LOBBY] = _rendered(lobby)
    frames[Screen.ONLINE_PLAY] = _rendered(_online_playing_shell())

    assert set(frames) == set(Screen)
    for screen, surface in frames.items():
        assert distinct_colors(surface) > 2, f"{screen} rendered almost nothing"


def test_the_pause_overlay_changes_the_frame_it_covers() -> None:
    shell = _playing_shell()
    playing = pygame.image.tobytes(_rendered(shell), "RGB")
    shell.handle(Action.TOGGLE_PAUSE)
    paused = pygame.image.tobytes(_rendered(shell), "RGB")
    assert playing != paused


def test_the_terminal_overlay_differs_from_an_ordinary_pause() -> None:
    """Both dim the board; a player must still be able to tell which one they are in."""
    over = make_shell()
    over.open_session(finished_session(RunOutcome.PLAYERS_ELIMINATED))
    terminal = pygame.image.tobytes(_rendered(over), "RGB")

    paused = make_shell()
    paused.handle(Action.UI_CONFIRM)
    paused.handle(Action.UI_CONFIRM)
    paused.handle(Action.TOGGLE_PAUSE)
    assert terminal != pygame.image.tobytes(_rendered(paused), "RGB")


def test_the_two_outcomes_are_worded_differently() -> None:
    frames = []
    for outcome in RunOutcome:
        shell = make_shell()
        shell.open_session(finished_session(outcome))
        frames.append(pygame.image.tobytes(_rendered(shell), "RGB"))
    assert frames[0] != frames[1]


def test_a_shell_with_no_session_still_renders() -> None:
    """Drawing must never depend on a run existing; a menu is drawn before one does."""
    shell = make_shell()
    shell.screen = Screen.PLAYING
    assert distinct_colors(_rendered(shell)) >= 1


# -- the seam -----------------------------------------------------------------


def test_the_renderer_refuses_art_sized_for_different_rules() -> None:
    """Art cannot change collision geometry, so a mismatch is an error, not a stretch."""
    with pytest.raises(ValueError, match="does not match"):
        Renderer(ProceduralAssetLibrary(Rules(tile_size=8, tank_size=8)))


def test_every_tile_is_exactly_one_cell() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    for tile in Tile:
        surface = assets.tile(tile)
        assert surface.get_size() == (DEFAULT_RULES.tile_size, DEFAULT_RULES.tile_size)


def test_every_tank_is_exactly_its_collision_body() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    for variant in TankVariant:
        for facing in Direction:
            surface = assets.tank(variant, facing)
            assert surface.get_size() == (DEFAULT_RULES.tank_size, DEFAULT_RULES.tank_size)


def test_terrain_kinds_differ_by_more_than_hue() -> None:
    """Distinct textures, so the board reads without separating two colours."""
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    shapes = {tile: _luminance_pattern(assets.tile(tile)) for tile in Tile}
    assert len(set(shapes.values())) == len(Tile)


def test_brick_and_cracked_brick_are_told_apart_by_shape() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    brick = _luminance_pattern(assets.tile(Tile.BRICK))
    cracked = _luminance_pattern(assets.tile(Tile.CRACKED_BRICK))
    assert brick != cracked


def test_tank_variants_carry_different_markings() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    marks = {
        variant: _luminance_pattern(assets.tank(variant, Direction.UP)) for variant in TankVariant
    }
    assert len(set(marks.values())) == len(TankVariant)


def test_an_invincible_tank_is_outlined_not_merely_tinted() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    plain = assets.tank(TankVariant.PLAYER, Direction.UP)
    shielded = assets.tank(TankVariant.PLAYER, Direction.UP, invincible=True)
    assert opaque_pixels(plain) != opaque_pixels(shielded)


def test_a_destroyed_base_is_a_different_shape() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    assert _luminance_pattern(assets.base(destroyed=False)) != _luminance_pattern(
        assets.base(destroyed=True)
    )


def test_each_facing_points_somewhere_different() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    shapes = {
        facing: opaque_pixels(assets.tank(TankVariant.PLAYER, facing)) for facing in Direction
    }
    assert len(set(shapes.values())) == len(Direction)


def test_powerups_carry_a_letter_each() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    shapes = {kind: _luminance_pattern(assets.powerup(kind)) for kind in PowerupKind}
    assert len(set(shapes.values())) == len(PowerupKind)


def test_projectiles_are_sized_from_the_collision_square() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    span = 2 * DEFAULT_RULES.projectile_radius + 1
    for faction in Faction:
        assert assets.projectile(faction).get_size() == (span + 2, span + 2)


def test_surfaces_are_cached_rather_than_redrawn() -> None:
    assets = ProceduralAssetLibrary(DEFAULT_RULES)
    assert assets.tile(Tile.BRICK) is assets.tile(Tile.BRICK)
    assert assets.glyph("A", theme.TEXT) is assets.glyph("a", theme.TEXT)


# -- text ---------------------------------------------------------------------


def test_every_glyph_is_five_by_seven() -> None:
    for character, rows in GLYPHS.items():
        assert len(rows) == 7, character
        assert {len(row) for row in rows} == {5}, character
        assert set("".join(rows)) <= {"#", "."}, character


def test_an_unknown_character_renders_as_a_visible_box() -> None:
    assert glyph_rows("¥") is MISSING_GLYPH


def test_glyph_lookup_ignores_case() -> None:
    assert glyph_rows("q") == glyph_rows("Q")


def test_text_width_accounts_for_spacing_but_not_a_trailing_gap() -> None:
    assert text_width("") == 0
    assert text_width("A") == 5
    assert text_width("AB") == 11
    assert text_width("AB", 2) == 22


def test_hud_labels_fit_the_panel() -> None:
    """A label wider than the panel would be clipped by the border, silently."""
    inner = theme.HUD_WIDTH - 8
    for label in ("STAGE", "TICK", "LIVES", "TANK", "GATLING", "SHIELD", "SHOTS", "TANKS", "BASE"):
        assert text_width(label) <= inner, label


def _luminance_pattern(surface: pygame.Surface) -> tuple[int, ...]:
    """A coarse brightness fingerprint, which is what survives losing hue."""
    return tuple(
        (surface.get_at((x, y)).r + surface.get_at((x, y)).g + surface.get_at((x, y)).b) // 96
        for y in range(surface.get_height())
        for x in range(surface.get_width())
    )
