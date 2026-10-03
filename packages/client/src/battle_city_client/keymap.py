"""Keycode tables: the one place a physical key becomes an :class:`Action`.

This module is the keyboard's half of the input layer, and the only place in the client
that knows what an SDL keycode is. The pad's half is
:mod:`battle_city_client.gamepad`, and both feed the same :class:`Action` vocabulary, so
nothing downstream of here -- the shell, the session, the renderer -- learns that a
device exists.

Remapping is an overlay, not a replacement. The tables below are what the build ships;
:class:`~battle_city_client.accessibility.BindingSet` holds what the player changed, and
:func:`~battle_city_client.accessibility.resolved_table` combines the two at lookup time
under two rules that are the same rule from both ends: an action that was rebound loses
the keys the build gave it, and a key that was taken stops meaning what it used to. A
lookup with no overlay is byte-for-byte the lookup this module always did.

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

from .accessibility import (
    DEFAULT_GAMEPAD_DEFAULTS,
    EMPTY_BINDINGS,
    REMAPPABLE_ACTIONS,
    REMAPPABLE_EDGE_ACTIONS,
    REMAPPABLE_HELD_ACTIONS,
    BindingSet,
    resolved_table,
)
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
    pygame.K_LEFT: Action.UI_LEFT,
    pygame.K_a: Action.UI_LEFT,
    pygame.K_RIGHT: Action.UI_RIGHT,
    pygame.K_d: Action.UI_RIGHT,
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

OPTIONS_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    **MENU_EDGE_BINDINGS,
    pygame.K_F5: Action.OPTION_RESET,
    pygame.K_DELETE: Action.OPTION_RESET,
}
"""The options screen, where one key puts every preference and binding back.

A key of its own, and a reserved one, because the row that resets is reached with
``UI_CONFIRM`` and ``UI_CONFIRM`` is a binding a player may move. Without a key that
cannot be taken, a confirm bound to a control that does not work would leave the screen
that fixes it unusable from inside itself.
"""

WINDOW_EDGE_BINDINGS: Final[Mapping[int, Action]] = {
    pygame.K_MINUS: Action.SCALE_DOWN,
    pygame.K_KP_MINUS: Action.SCALE_DOWN,
    pygame.K_EQUALS: Action.SCALE_UP,
    pygame.K_PLUS: Action.SCALE_UP,
    pygame.K_KP_PLUS: Action.SCALE_UP,
}
"""Window scaling, available on every screen and handled by the loop, not the shell."""

RESERVED_KEYS: Final[frozenset[int]] = frozenset(
    {
        pygame.K_ESCAPE,
        pygame.K_BACKSPACE,
        pygame.K_F5,
        pygame.K_DELETE,
        pygame.K_MINUS,
        pygame.K_KP_MINUS,
        pygame.K_EQUALS,
        pygame.K_PLUS,
        pygame.K_KP_PLUS,
    }
)
"""Keys the remapping screen refuses to capture, and why each one is here.

``ESCAPE`` and ``BACKSPACE`` are ``UI_CANCEL``: the keystroke that leaves a screen has
to work from every screen, including the one that rebinds keys. ``F5`` and ``DELETE``
reset every preference, which is the recovery path and must survive a confirm that was
bound somewhere unusable. The scale keys are the interface's own enlargement control and
are handled by the loop on every screen, so a key that was both would resize the window
while it drove a tank.
"""

DEFAULT_KEYS_BY_ACTION: Final[tuple[tuple[Action, tuple[int, ...]], ...]] = tuple(
    (
        action,
        tuple(
            sorted(
                {
                    key
                    for table in (
                        HELD_BINDINGS,
                        PLAY_EDGE_BINDINGS,
                        MENU_EDGE_BINDINGS,
                        PAUSE_EDGE_BINDINGS,
                        STAGE_SELECT_EDGE_BINDINGS,
                        LOBBY_EDGE_BINDINGS,
                        OPTIONS_EDGE_BINDINGS,
                    )
                    for key, bound in table.items()
                    if bound is action
                }
            )
        ),
    )
    for action in REMAPPABLE_ACTIONS
)
"""Every shipped key for every remappable action, gathered from all the tables.

Gathered rather than written out, so a key added to a table above is a key the options
screen reports and a key a conflict can name, with nobody having to remember this list.
"""

DEFAULT_BINDINGS: Final[BindingSet] = BindingSet(
    default_keys=DEFAULT_KEYS_BY_ACTION,
    default_controls=DEFAULT_GAMEPAD_DEFAULTS,
    reserved_keys=RESERVED_KEYS,
)
"""The shipped tables as a value the shell and the options screen can read.

Carries no remapping of its own: it is the *starting point*, which is why a client built
with it behaves exactly as one built without it until somebody rebinds something.
"""


def key_label(key: int) -> str:
    """A short, upper-case name for a keycode, in the bitmap font's own alphabet.

    SDL's own name is used wherever it is usable, because it is the name printed on the
    key the player just pressed. A name with a space in it is squeezed, and an empty one
    -- which SDL returns for a code it does not know -- becomes the numeric code, so a
    row always says something rather than going blank.
    """
    try:
        name = pygame.key.name(key)
    except ValueError, pygame.error:
        name = ""
    cleaned = str(name).strip().upper().replace(" ", "")
    return cleaned or f"#{key}"


MAX_BINDING_SUMMARY: Final[int] = 13
"""Characters a binding row has room for, at the panel width the options screen uses."""


def binding_summary(bindings: BindingSet, action: Action) -> str:
    """What ``action`` is bound to now, keyboard first, pad second.

    Deliberately compact, and compact by dropping rather than by cutting: the options
    screen has one line per action, a row that overflowed its panel would be clipped by
    the border without saying so, and half a key name is worse than one key name. Two
    keys are listed when both fit, otherwise the first alone, and the pad control is
    kept either way because it is the only thing on the row a pad user can read.
    """
    labels = [key_label(key) for key in bindings.keys_for(action)]
    controls = bindings.controls_for(action)
    pad = controls[0].label if controls else ""
    summary = "UNBOUND"
    for count in (2, 1):
        keys = "+".join(labels[:count])
        summary = f"{keys}/{pad}" if keys and pad else (keys or pad or "UNBOUND")
        if len(summary) <= MAX_BINDING_SUMMARY:
            return summary
    return summary[:MAX_BINDING_SUMMARY]


OPTIONS_HELP: Final[tuple[tuple[str, str], ...]] = (
    ("MOVE CURSOR", "UP / DOWN"),
    ("CHANGE VALUE", "LEFT / RIGHT"),
    ("REBIND", "ENTER"),
    ("RESET ALL", "F5"),
    ("BACK", "ESC"),
)
"""What the options screen lists. Kept beside the tables so the two cannot drift."""

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
        case Screen.OPTIONS:
            return OPTIONS_EDGE_BINDINGS
        case _:
            return MENU_EDGE_BINDINGS


def held_action_for(key: int, bindings: BindingSet = EMPTY_BINDINGS) -> Action | None:
    """The continuous action ``key`` drives, if any, under ``bindings``."""
    overlay = {
        action: bound for action, bound in bindings.keyboard if action in REMAPPABLE_HELD_ACTIONS
    }
    if not overlay:
        return HELD_BINDINGS.get(key)
    return resolved_table(HELD_BINDINGS, overlay).get(key)


def edge_action_for(
    key: int, screen: Screen, bindings: BindingSet = EMPTY_BINDINGS
) -> Action | None:
    """The edge action ``key`` triggers on ``screen``, if any, under ``bindings``.

    Window scaling is checked first so that it works identically everywhere and cannot be
    shadowed by a screen-specific binding -- and its keys are reserved, so a remapping
    cannot shadow it either.
    """
    window_action = WINDOW_EDGE_BINDINGS.get(key)
    if window_action is not None:
        return window_action
    table = edge_bindings_for(screen)
    overlay = {
        action: bound
        for action, bound in bindings.keyboard
        if action in REMAPPABLE_EDGE_ACTIONS and action in table.values()
    }
    if not overlay:
        return table.get(key)
    return resolved_table(table, overlay).get(key)
