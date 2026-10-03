"""Contrast, scale, reduced motion, and the cues that survive losing colour.

Four claims, and each one is checked against pixels rather than against the preference
that asked for it.

**Contrast reaches the art, not just the chrome.** Colour is baked into the surfaces the
asset library caches, so a palette that reached the renderer alone would leave the board
in the old one. The library is asked directly, and the renderer is asked to refuse a
library that disagrees with it.

**The default is unmoved.** Every field of the default palette is the module constant it
replaced -- ``tests/accessibility/test_accessibility_preferences`` pins that -- and here
a frame drawn with the palette given explicitly is compared byte for byte with one drawn
without it.

**Enlargement is the window scale.** The logical frame and the HUD geometry are the same
at every scale; the window shows a whole multiple of one fixed frame.

**Nothing critical is a colour.** The states the board distinguishes differ in shape, and
the two cues this phase adds -- whose tank this is, and which team it plays for -- are a
bracket and a count of pips.
"""

from __future__ import annotations

from collections.abc import Callable

import pygame
import pytest
from accessibility_helpers import (
    CAPTURE_SCALE,
    ENLARGED_CAPTURE_SCALE,
    luminance_pattern,
    make_shell,
    observed,
    online_shell,
    opaque_pixels,
    pygame_module_boundary,  # noqa: F401  -- autouse: releases pygame when this module ends
)
from battle_city_client import theme
from battle_city_client.assets import ProceduralAssetLibrary
from battle_city_client.display import integer_scale, window_size_for
from battle_city_client.intents import Action, PlayerIntent
from battle_city_client.rendering import Renderer
from battle_city_client.shell import ClientShell, Screen
from battle_city_protocol import (
    ContentRef,
)
from battle_city_sim import DEFAULT_RULES, Direction, TankVariant, Tile

SESSION_ID = "session-accessibility"
CONTENT = ContentRef(
    pack_id="duo-pack", pack_version="1.0.0", level_id="duo-arena", content_schema_version=1
)
GRID: tuple[str, ...] = ("0" * 16,) * 15 + ("0" * 8 + "8" + "0" * 7,)


def _surface() -> pygame.Surface:
    return pygame.Surface(theme.LOGICAL_SIZE)


def _renderer(palette: theme.Palette = theme.DEFAULT_PALETTE, *, reduced: bool = False) -> Renderer:
    return Renderer(ProceduralAssetLibrary(DEFAULT_RULES, palette), reduced_motion=reduced)


def _rendered(shell: ClientShell, renderer: Renderer | None = None) -> bytes:
    surface = _surface()
    (renderer or _renderer()).render(surface, shell)
    return pygame.image.tobytes(surface, "RGB")


def _playing_shell() -> ClientShell:
    shell = make_shell()
    shell.handle(Action.UI_CONFIRM)
    shell.handle(Action.UI_CONFIRM)
    shell.advance(45, PlayerIntent(direction=Direction.LEFT, fire=True))
    return shell


# -- contrast -------------------------------------------------------------------


def test_the_asset_cache_is_rebuilt_in_the_new_palette() -> None:
    """The half a tint would have missed: the terrain and the tanks themselves."""
    default = ProceduralAssetLibrary(DEFAULT_RULES, theme.DEFAULT_PALETTE)
    high = default.with_palette(theme.HIGH_CONTRAST_PALETTE)
    assert high is not default
    assert high.palette == theme.HIGH_CONTRAST_PALETTE
    builders: tuple[Callable[[ProceduralAssetLibrary], pygame.Surface], ...] = (
        lambda library: library.tile(Tile.BRICK),
        lambda library: library.tank(TankVariant.PLAYER, Direction.UP),
        lambda library: library.base(destroyed=False),
    )
    for build in builders:
        assert pygame.image.tobytes(build(default), "RGBA") != pygame.image.tobytes(
            build(high), "RGBA"
        )


def test_asking_for_the_same_palette_keeps_the_warm_cache() -> None:
    library = ProceduralAssetLibrary(DEFAULT_RULES)
    assert library.with_palette(theme.DEFAULT_PALETTE) is library


def test_a_renderer_refuses_a_library_that_is_in_another_palette() -> None:
    """A frame drawn half in each palette looks like a rendering bug, not a setting."""
    with pytest.raises(ValueError, match="does not match the asset library"):
        Renderer(
            ProceduralAssetLibrary(DEFAULT_RULES, theme.DEFAULT_PALETTE),
            palette=theme.HIGH_CONTRAST_PALETTE,
        )


def test_a_renderer_switches_palette_with_its_art_together() -> None:
    renderer = _renderer()
    switched = renderer.with_palette(theme.HIGH_CONTRAST_PALETTE)
    assert switched.palette == theme.HIGH_CONTRAST_PALETTE
    assert switched.assets.palette == theme.HIGH_CONTRAST_PALETTE
    assert renderer.with_palette(theme.DEFAULT_PALETTE) is renderer


def test_high_contrast_changes_the_frame_a_player_is_looking_at() -> None:
    shell = _playing_shell()
    assert _rendered(shell) != _rendered(shell, _renderer(theme.HIGH_CONTRAST_PALETTE))


def test_the_default_palette_draws_exactly_what_it_always_drew() -> None:
    """Passing the palette explicitly and not passing one must be the same pixels."""
    shell = _playing_shell()
    explicit = Renderer(
        ProceduralAssetLibrary(DEFAULT_RULES, theme.DEFAULT_PALETTE),
        palette=theme.DEFAULT_PALETTE,
    )
    assert _rendered(shell) == _rendered(shell, explicit)


# -- scale ----------------------------------------------------------------------


def test_enlarging_the_window_changes_no_geometry_in_the_frame() -> None:
    """Enlargement is the whole-number window scale; the frame it shows is one size."""
    for scale in (CAPTURE_SCALE, ENLARGED_CAPTURE_SCALE):
        size = window_size_for(scale)
        assert integer_scale(size, theme.LOGICAL_SIZE) == scale
        assert size == (theme.LOGICAL_SIZE[0] * scale, theme.LOGICAL_SIZE[1] * scale)
    assert theme.HUD_SIZE == (theme.HUD_WIDTH, theme.PLAYFIELD_SIZE)


def test_a_scaled_frame_is_the_same_pixels_repeated() -> None:
    """A whole-number scale is what keeps text and tile edges exact when enlarged."""
    shell = _playing_shell()
    surface = _surface()
    _renderer().render(surface, shell)
    scaled = pygame.transform.scale(surface, (theme.LOGICAL_SIZE[0] * 2, theme.LOGICAL_SIZE[1] * 2))
    for x, y in ((40, 40), (120, 90), (300, 200)):
        source = surface.get_at((x, y))
        for offset_x, offset_y in ((0, 0), (1, 0), (0, 1), (1, 1)):
            assert scaled.get_at((x * 2 + offset_x, y * 2 + offset_y)) == source


# -- reduced motion ---------------------------------------------------------------


def test_the_reduced_motion_preference_reaches_the_renderer() -> None:
    assert not _renderer().reduced_motion
    assert _renderer(reduced=True).reduced_motion
    assert _renderer().with_reduced_motion(True).reduced_motion
    renderer = _renderer()
    assert renderer.with_reduced_motion(False) is renderer


def test_reduced_motion_changes_no_pixel_in_this_build() -> None:
    """Honest, and asserted rather than claimed.

    There is no animation, no flash, no shake and no transition to hold still: every
    frame is a function of the state it is drawn from. If this test ever fails, an
    effect has arrived and this is the test that should grow into checking the effect
    holds still -- not the test that should be deleted.
    """
    shell = _playing_shell()
    assert _rendered(shell, _renderer()) == _rendered(shell, _renderer(reduced=True))


def test_two_renders_of_one_state_are_identical() -> None:
    """Nothing in presentation reads a clock, which is what makes a capture meaningful."""
    shell = _playing_shell()
    assert _rendered(shell) == _rendered(shell)


# -- cues that survive losing colour ------------------------------------------------


def test_the_local_tank_is_marked_by_shape() -> None:
    library = ProceduralAssetLibrary(DEFAULT_RULES)
    mine = library.slot_marker(local=True, team=None)
    theirs = library.slot_marker(local=False, team=None)
    assert opaque_pixels(mine)
    assert opaque_pixels(theirs) == frozenset()


def test_a_team_is_a_count_rather_than_a_colour() -> None:
    library = ProceduralAssetLibrary(DEFAULT_RULES)
    marks = {team: opaque_pixels(library.slot_marker(local=False, team=team)) for team in (1, 2, 3)}
    assert len(set(marks.values())) == 3
    assert len(marks[1]) < len(marks[2]) < len(marks[3])


def test_the_local_marker_is_not_the_invincibility_outline() -> None:
    """Two shapes that meant the same thing would be worse than one of them."""
    library = ProceduralAssetLibrary(DEFAULT_RULES)
    marker = opaque_pixels(library.slot_marker(local=True, team=None))
    plain = opaque_pixels(library.tank(TankVariant.PLAYER, Direction.UP))
    shielded = opaque_pixels(library.tank(TankVariant.PLAYER, Direction.UP, invincible=True))
    assert marker != shielded - plain
    assert marker != shielded


def test_the_marker_is_cached_like_every_other_surface() -> None:
    library = ProceduralAssetLibrary(DEFAULT_RULES)
    assert library.slot_marker(local=True, team=1) is library.slot_marker(local=True, team=1)


def test_the_marker_follows_the_palette() -> None:
    default = ProceduralAssetLibrary(DEFAULT_RULES, theme.DEFAULT_PALETTE)
    high = default.with_palette(theme.HIGH_CONTRAST_PALETTE)
    assert pygame.image.tobytes(
        default.slot_marker(local=True, team=1), "RGBA"
    ) != pygame.image.tobytes(high.slot_marker(local=True, team=1), "RGBA")


def test_the_board_still_tells_its_states_apart_without_hue() -> None:
    """The existing shape cues, re-asserted in the high-contrast palette as well."""
    for palette in (theme.DEFAULT_PALETTE, theme.HIGH_CONTRAST_PALETTE):
        library = ProceduralAssetLibrary(DEFAULT_RULES, palette)
        variants = {
            variant: luminance_pattern(library.tank(variant, Direction.UP))
            for variant in TankVariant
        }
        assert len(set(variants.values())) == len(TankVariant)
        assert luminance_pattern(library.base(destroyed=False)) != luminance_pattern(
            library.base(destroyed=True)
        )


# -- the online cues, from authoritative data ---------------------------------------


def test_the_online_board_marks_this_clients_seat() -> None:
    """Both tanks are the same variant, so only the marker tells them apart."""
    shell = online_shell(teams=False)
    assert observed(shell.screen) is Screen.ONLINE_PLAY
    surface = _surface()
    _renderer().render(surface, shell)
    origin_x, origin_y = theme.PLAYFIELD_ORIGIN
    mine = surface.subsurface(pygame.Rect(origin_x + 64, origin_y + 160, 16, 16)).copy()
    theirs = surface.subsurface(pygame.Rect(origin_x + 160, origin_y + 160, 16, 16)).copy()
    assert luminance_pattern(mine) != luminance_pattern(theirs)


def test_teams_change_the_board_without_changing_the_rules() -> None:
    coop = _rendered(online_shell(teams=False))
    teamed = _rendered(online_shell(teams=True))
    assert coop != teamed


def test_the_online_hud_names_the_seats_in_words() -> None:
    """A second reading of the same facts, for a viewer who cannot read the shapes.

    The HUD panel alone is compared, so this is about the seat list and not about the
    markers on the board: the same two players, with and without the teams the server
    assigned, must read differently in the panel.
    """
    panel = pygame.Rect(*theme.HUD_ORIGIN, *theme.HUD_SIZE)
    frames = []
    for teams in (False, True):
        surface = _surface()
        _renderer().render(surface, online_shell(teams=teams))
        frames.append(pygame.image.tobytes(surface.subsurface(panel).copy(), "RGB"))
    assert frames[0] != frames[1]


def test_a_marker_is_drawn_under_the_forest_overlay() -> None:
    """Concealment is the content specification's, and a cue must not lift it.

    Forest is drawn after the actors because it is an overlay that may conceal. The
    marker is blitted with the actors, so a tank standing in forest is covered exactly
    as it was before the marker existed: the top row of that cell is the forest's, not
    the marker's.
    """
    covered = tuple("0" * 4 + "7" + "0" * 11 if y == 10 else row for y, row in enumerate(GRID))
    shell = online_shell(teams=True, grid=covered)
    surface = _surface()
    _renderer().render(surface, shell)
    origin_x, origin_y = theme.PLAYFIELD_ORIGIN
    top_left = surface.get_at((origin_x + 64, origin_y + 160))
    assert tuple(top_left)[:3] == theme.DEFAULT_PALETTE.forest_leaf
    assert tuple(top_left)[:3] != theme.DEFAULT_PALETTE.text


ONLINE_SCREENS: frozenset[Screen] = frozenset({Screen.ONLINE_LOBBY, Screen.ONLINE_PLAY})
"""Screens that need a seated session behind them; see :func:`_online_shell`."""


@pytest.mark.parametrize(
    "palette", [theme.DEFAULT_PALETTE, theme.HIGH_CONTRAST_PALETTE], ids=["default", "high"]
)
def test_every_screen_draws_in_either_palette(palette: theme.Palette) -> None:
    """A palette that broke one screen would be found on that screen and nowhere else."""
    renderer = _renderer(palette)
    for screen in Screen:
        shell = online_shell(teams=True) if screen in ONLINE_SCREENS else _playing_shell()
        shell.screen = screen
        surface = _surface()
        renderer.render(surface, shell)
        colours = {
            tuple(surface.get_at((x, y)))
            for y in range(0, theme.LOGICAL_SIZE[1], 3)
            for x in range(0, theme.LOGICAL_SIZE[0], 3)
        }
        assert len(colours) > 2, f"{screen} rendered almost nothing in {palette.background}"


def test_the_controls_card_admits_when_its_table_is_out_of_date() -> None:
    """The card is a fixed list of shipped keys, and a remapping makes some of it untrue.

    It is not redrawn from live bindings -- the options screen already shows every
    binding as it stands -- but it must stop presenting a stale table as the truth.
    """
    from battle_city_client.keymap import DEFAULT_BINDINGS

    shell = make_shell()
    shell.screen = Screen.CONTROLS
    before = _rendered(shell)

    shell.accessibility = shell.accessibility.with_bindings(
        DEFAULT_BINDINGS.with_keyboard(Action.FIRE, 107)
    )
    assert shell.accessibility.bindings.remapped
    assert _rendered(shell) != before


def test_the_controls_card_is_unchanged_until_something_is_rebound() -> None:
    """The committed capture of this screen is taken with nothing rebound, and stays valid."""
    shell = make_shell()
    shell.screen = Screen.CONTROLS
    untouched = make_shell(bindings=False)
    untouched.screen = Screen.CONTROLS
    assert _rendered(shell) == _rendered(untouched)
