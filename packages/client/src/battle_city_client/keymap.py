"""Keycode tables: the one place a physical key becomes an :class:`Action`.

Keyboard only, this phase. A gamepad and a remapping screen are accessibility
requirements and they arrive as extra tables feeding the same :class:`Action` vocabulary;
nothing downstream of here learns about a device, so adding one does not reach the shell,
the session or the renderer.

The tables are split by screen because the same key means different things in a menu and
in a run. ``UP`` moves a tank while playing and a cursor while choosing, and ``ESCAPE``
opens the pause menu rather than leaving it. Resolving that with one table and a pile of
conditions at the call site is how a key ends up doing two things at once; resolving it
with :func:`edge_bindings_for` keeps the answer in one readable place.

Held keys and pressed keys are also separate. Movement and fire are held -- the loop asks
what is down at the start of each frame -- while everything else is edge-triggered, so a
key repeat cannot advance a menu three entries or toggle pause twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import pygame

from .intents import Action
from .shell import Screen

HELD_BINDINGS: Final[Mapping[int, Action]] = {
    pygame.K_UP: Action.MOVE_UP,
    pygame.K_w: Action.MOVE_UP,
    pygame.K_DOWN: Action.MOVE_DOWN,
    pygame.K_s: Action.MOVE_DOWN,
    pygame.K_LEFT: Action.MOVE_LEFT,
    pygame.K_a: Action.MOVE_LEFT,
    pygame.K_RIGHT: Action.MOVE_RIGHT,
    pygame.K_d: Action.MOVE_RIGHT,
    pygame.K_SPACE: Action.FIRE,
    pygame.K_j: Action.FIRE,
}
"""Continuous controls, sampled every frame while a run is being driven."""

PLAY_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    pygame.K_ESCAPE: Action.TOGGLE_PAUSE,
    pygame.K_p: Action.TOGGLE_PAUSE,
}
"""Edge controls available during a run. Quitting is deliberately not one of them: a run
is left through the pause menu, so no single keystroke can discard it."""

MENU_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    pygame.K_UP: Action.UI_UP,
    pygame.K_w: Action.UI_UP,
    pygame.K_DOWN: Action.UI_DOWN,
    pygame.K_s: Action.UI_DOWN,
    pygame.K_RETURN: Action.UI_CONFIRM,
    pygame.K_KP_ENTER: Action.UI_CONFIRM,
    pygame.K_SPACE: Action.UI_CONFIRM,
    pygame.K_ESCAPE: Action.UI_CANCEL,
    pygame.K_BACKSPACE: Action.UI_CANCEL,
}
"""Edge controls for every screen that shows a cursor."""

PAUSE_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    **MENU_EDGE_BINDINGS,
    pygame.K_ESCAPE: Action.TOGGLE_PAUSE,
    pygame.K_p: Action.TOGGLE_PAUSE,
}
"""The pause menu, where ``ESCAPE`` resumes instead of abandoning the run."""

STAGE_SELECT_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    **MENU_EDGE_BINDINGS,
    pygame.K_r: Action.RESUME_SAVE,
}
"""The stage list, where one key picks the saved stage up again.

Resuming is a key of its own rather than a menu entry because the list is the pack and
every row in it has to keep meaning "start this stage". ``R`` is free on this screen --
it is a lobby key, and there is no lobby here -- and it is the same letter the lobby uses
for an action the player asks for rather than navigates to.
"""

LOBBY_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    **MENU_EDGE_BINDINGS,
    pygame.K_r: Action.ONLINE_READY,
    pygame.K_m: Action.ONLINE_MODE,
    pygame.K_l: Action.ONLINE_STAGE,
}
"""The lobby, where readiness, the mode and the stage are each one key.

``SPACE`` is deliberately left as confirm-and-therefore-start: it is the key a player
holds to fire, and a lobby is the one screen where that has no meaning yet.
"""

WINDOW_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    pygame.K_MINUS: Action.SCALE_DOWN,
    pygame.K_KP_MINUS: Action.SCALE_DOWN,
    pygame.K_EQUALS: Action.SCALE_UP,
    pygame.K_PLUS: Action.SCALE_UP,
    pygame.K_KP_PLUS: Action.SCALE_UP,
}
"""Window scaling, available on every screen and handled by the loop, not the shell."""

CONTROL_HELP: Final[tuple[tuple[str, str], ...]] = (
    ("MOVE", "ARROWS / WASD"),
    ("FIRE", "SPACE / J"),
    ("PAUSE", "ESC / P"),
    ("SELECT", "ENTER"),
    ("BACK", "ESC"),
    ("RESUME SAVE", "R"),
    ("CHOOSE BADGE", "UP / DOWN"),
    ("WINDOW SCALE", "- / +"),
    ("LOBBY READY", "R"),
    ("LOBBY MODE", "M"),
    ("LOBBY STAGE", "L"),
    ("QUIT", "WINDOW CLOSE"),
)
"""What the controls screen lists. Kept beside the tables so the two cannot drift."""


def edge_bindings_for(screen: Screen) -> Mapping[int, Action]:
    """The edge-triggered table that applies on ``screen``."""
    match screen:
        case Screen.PLAYING | Screen.ONLINE_PLAY:
            return PLAY_EDGE_BINDINGS
        case Screen.STAGE_SELECT:
            return STAGE_SELECT_EDGE_BINDINGS
        case Screen.PAUSED:
            return PAUSE_EDGE_BINDINGS
        case Screen.ONLINE_LOBBY:
            return LOBBY_EDGE_BINDINGS
        case _:
            return MENU_EDGE_BINDINGS


def held_action_for(key: int) -> Action | None:
    """The continuous action ``key`` drives, if any."""
    return HELD_BINDINGS.get(key)


def edge_action_for(key: int, screen: Screen) -> Action | None:
    """The edge action ``key`` triggers on ``screen``, if any.

    Window scaling is checked first so that it works identically everywhere and cannot be
    shadowed by a screen-specific binding.
    """
    window_action = WINDOW_EDGE_BINDINGS.get(key)
    if window_action is not None:
        return window_action
    return edge_bindings_for(screen).get(key)
