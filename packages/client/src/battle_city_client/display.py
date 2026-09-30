"""The window, and the whole-number scale between it and the logical frame.

The client draws one fixed-size logical frame and the window shows an integer multiple of
it, centred, on a letterbox. A whole-number scale is not a stylistic preference: at a
fractional scale a 16-pixel tile lands on 43.5 window pixels, so tile edges alternate
between thick and thin and the brick texture shimmers as the window moves. Choosing the
largest whole multiple that fits keeps every pixel the same size.

:func:`integer_scale` and :func:`present_rect` are pure arithmetic and are tested as
such. :class:`Presenter` is the part that owns pygame surfaces: it creates the resizable
window, hands out the logical surface to draw on, and blits the scaled result. Nothing
else in the client knows the window's size, which is why a resize needs no cooperation
from the renderer.
"""

from __future__ import annotations

import pygame

from . import theme


def integer_scale(
    window_size: tuple[int, int],
    logical_size: tuple[int, int],
    *,
    min_scale: int = theme.MIN_SCALE,
    max_scale: int = theme.MAX_SCALE,
) -> int:
    """The largest whole multiple of ``logical_size`` that fits in ``window_size``.

    Never returns less than ``min_scale``: a window smaller than the frame is shown
    cropped rather than blank, because a blank window looks like a crash.
    """
    if logical_size[0] <= 0 or logical_size[1] <= 0:
        raise ValueError(f"logical_size must be positive, found {logical_size}")
    fit = min(window_size[0] // logical_size[0], window_size[1] // logical_size[1])
    return max(min_scale, min(max_scale, fit))


def present_rect(
    window_size: tuple[int, int], logical_size: tuple[int, int], scale: int
) -> pygame.Rect:
    """Where the scaled frame sits in the window: centred, with integer offsets."""
    width = logical_size[0] * scale
    height = logical_size[1] * scale
    return pygame.Rect(
        (window_size[0] - width) // 2,
        (window_size[1] - height) // 2,
        width,
        height,
    )


def window_size_for(
    scale: int, logical_size: tuple[int, int] = theme.LOGICAL_SIZE
) -> tuple[int, int]:
    """The window size that shows ``logical_size`` at exactly ``scale`` with no letterbox."""
    return logical_size[0] * scale, logical_size[1] * scale


def preferred_scale(logical_size: tuple[int, int] = theme.LOGICAL_SIZE) -> int:
    """A starting scale that leaves room for window decoration on this desktop.

    A desktop size is an optional convenience: some drivers, the dummy driver included,
    report nothing useful. The default scale is used whenever the query fails or returns
    something implausible, so the client opens either way.
    """
    try:
        sizes = pygame.display.get_desktop_sizes()
    except pygame.error:
        return theme.DEFAULT_SCALE
    if not sizes:
        return theme.DEFAULT_SCALE
    width, height = sizes[0]
    if width <= 0 or height <= 0:
        return theme.DEFAULT_SCALE
    usable = (int(width * 0.8), int(height * 0.8))
    return integer_scale(usable, logical_size, max_scale=theme.DEFAULT_SCALE)


class Presenter:
    """Owns the window surface and the logical surface drawn into it."""

    def __init__(
        self,
        *,
        scale: int = theme.DEFAULT_SCALE,
        logical_size: tuple[int, int] = theme.LOGICAL_SIZE,
        caption: str = "Battle City Reimagined",
    ) -> None:
        self.logical_size = logical_size
        self.requested_scale = max(theme.MIN_SCALE, min(theme.MAX_SCALE, scale))
        self.window = pygame.display.set_mode(
            window_size_for(self.requested_scale, logical_size), pygame.RESIZABLE
        )
        pygame.display.set_caption(caption)
        # Built after the mode is set so the logical surface adopts the window's pixel
        # format; converting before there is a display raises.
        self.surface = pygame.Surface(logical_size).convert()

    @property
    def scale(self) -> int:
        """The whole-number scale the current window can show."""
        return integer_scale(self.window.get_size(), self.logical_size)

    def resize(self, size: tuple[int, int]) -> None:
        """Adopt a window size the window system reported."""
        self.window = pygame.display.set_mode(size, pygame.RESIZABLE)
        self.requested_scale = self.scale

    def set_scale(self, scale: int) -> None:
        """Resize the window so the frame is shown at exactly ``scale``."""
        clamped = max(theme.MIN_SCALE, min(theme.MAX_SCALE, scale))
        if clamped == self.requested_scale and self.scale == clamped:
            return
        self.requested_scale = clamped
        self.window = pygame.display.set_mode(
            window_size_for(clamped, self.logical_size), pygame.RESIZABLE
        )

    def step_scale(self, delta: int) -> None:
        """Move the window one whole step larger or smaller."""
        self.set_scale(self.requested_scale + delta)

    def present(self) -> None:
        """Scale the logical surface into the window and show it."""
        window_size = self.window.get_size()
        scale = integer_scale(window_size, self.logical_size)
        target = present_rect(window_size, self.logical_size, scale)
        self.window.fill(theme.LETTERBOX)
        self.window.blit(pygame.transform.scale(self.surface, target.size), target.topleft)
        pygame.display.flip()
