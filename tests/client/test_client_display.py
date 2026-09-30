"""Whole-number scaling and window resize."""

from __future__ import annotations

import pygame
import pytest
from battle_city_client import theme
from battle_city_client.display import (
    Presenter,
    integer_scale,
    preferred_scale,
    present_rect,
    window_size_for,
)
from client_helpers import ensure_display

LOGICAL = theme.LOGICAL_SIZE


def test_the_scale_is_the_largest_whole_multiple_that_fits() -> None:
    assert integer_scale((LOGICAL[0] * 3, LOGICAL[1] * 3), LOGICAL) == 3
    assert integer_scale((LOGICAL[0] * 3 - 1, LOGICAL[1] * 3), LOGICAL) == 2
    assert integer_scale((LOGICAL[0] * 4, LOGICAL[1] * 2), LOGICAL) == 2


def test_a_window_smaller_than_the_frame_is_cropped_not_blanked() -> None:
    """Scale never drops below one: a blank window reads as a crash."""
    assert integer_scale((10, 10), LOGICAL) == 1


def test_the_scale_is_bounded() -> None:
    assert integer_scale((100_000, 100_000), LOGICAL) == theme.MAX_SCALE


def test_a_degenerate_logical_size_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        integer_scale((100, 100), (0, 240))


def test_the_frame_is_centred_with_integer_offsets() -> None:
    rect = present_rect((LOGICAL[0] * 2 + 40, LOGICAL[1] * 2 + 20), LOGICAL, 2)
    assert rect.size == (LOGICAL[0] * 2, LOGICAL[1] * 2)
    assert rect.topleft == (20, 10)


def test_window_size_for_leaves_no_letterbox() -> None:
    size = window_size_for(3)
    assert integer_scale(size, LOGICAL) == 3
    assert present_rect(size, LOGICAL, 3).topleft == (0, 0)


def test_a_preferred_scale_is_always_usable() -> None:
    """The dummy driver reports no useful desktop; the client must still open."""
    ensure_display()
    assert theme.MIN_SCALE <= preferred_scale() <= theme.MAX_SCALE


def test_the_presenter_opens_at_the_requested_scale() -> None:
    ensure_display()
    presenter = Presenter(scale=2)
    assert presenter.surface.get_size() == LOGICAL
    assert presenter.window.get_size() == window_size_for(2)
    assert presenter.scale == 2


def test_resizing_adopts_the_new_whole_number_scale() -> None:
    ensure_display()
    presenter = Presenter(scale=1)
    presenter.resize((LOGICAL[0] * 3 + 25, LOGICAL[1] * 3 + 25))
    assert presenter.scale == 3
    presenter.resize((LOGICAL[0], LOGICAL[1]))
    assert presenter.scale == 1


def test_stepping_the_scale_stays_inside_the_bounds() -> None:
    ensure_display()
    presenter = Presenter(scale=theme.MIN_SCALE)
    presenter.step_scale(-1)
    assert presenter.requested_scale == theme.MIN_SCALE
    presenter.set_scale(theme.MAX_SCALE + 5)
    assert presenter.requested_scale == theme.MAX_SCALE


def test_presenting_fills_the_letterbox_and_never_raises() -> None:
    ensure_display()
    presenter = Presenter(scale=1)
    presenter.surface.fill(theme.ACCENT)
    presenter.resize((LOGICAL[0] * 2 + 60, LOGICAL[1] * 2 + 60))
    presenter.present()
    assert presenter.window.get_at((2, 2))[:3] == theme.LETTERBOX
    assert (
        presenter.window.get_at(present_rect(presenter.window.get_size(), LOGICAL, 2).center)[:3]
        == theme.ACCENT
    )


def test_the_logical_surface_size_never_changes_with_the_window() -> None:
    """Rendering is written against one frame size, whatever the window does."""
    ensure_display()
    presenter = Presenter(scale=1)
    presenter.resize((LOGICAL[0] * 4, LOGICAL[1] * 4))
    assert presenter.surface.get_size() == LOGICAL
    pygame.display.quit()
