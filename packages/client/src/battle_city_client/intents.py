"""Device-independent actions and the player intent one tick is built from.

This module sits between the keyboard and the simulation and knows about neither. It
names the actions the client understands, records which of them are held and in what
order, and reduces that to a :class:`PlayerIntent`: at most one facing and whether the
fire control is down. The keycode tables live in :mod:`battle_city_client.keymap`, the
translation from intent to simulation commands lives in
:mod:`battle_city_client.session`, and nothing here imports pygame, so the reduction is
testable without a display and stays the same when a gamepad or a remapping screen is
added later.

Two keys held at once needs an answer, and "whichever the set iterates first" is not one:
iteration order of an unordered collection must never decide gameplay. The tracker keeps
press order instead and the most recent movement press wins, which is also how the
control feels right -- rolling from ``left`` onto ``up`` turns immediately rather than
waiting for ``left`` to be released.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from battle_city_sim import Direction


class Action(Enum):
    """Something the player asked for, named without reference to the device."""

    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"
    FIRE = "fire"
    UI_UP = "ui_up"
    UI_DOWN = "ui_down"
    UI_CONFIRM = "ui_confirm"
    UI_CANCEL = "ui_cancel"
    TOGGLE_PAUSE = "toggle_pause"
    ONLINE_READY = "online_ready"
    ONLINE_MODE = "online_mode"
    ONLINE_STAGE = "online_stage"
    QUIT = "quit"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"


MOVEMENT_ACTIONS: Final[tuple[Action, ...]] = (
    Action.MOVE_UP,
    Action.MOVE_DOWN,
    Action.MOVE_LEFT,
    Action.MOVE_RIGHT,
)
"""Movement actions in a fixed order, so no code has to iterate a set of them."""

ACTION_DIRECTIONS: Final[dict[Action, Direction]] = {
    Action.MOVE_UP: Direction.UP,
    Action.MOVE_DOWN: Direction.DOWN,
    Action.MOVE_LEFT: Direction.LEFT,
    Action.MOVE_RIGHT: Direction.RIGHT,
}
"""The facing each movement action asks for."""


@dataclass(frozen=True, slots=True)
class PlayerIntent:
    """What one player wants during one tick: a facing to drive, and whether to fire."""

    direction: Direction | None = None
    fire: bool = False

    @property
    def idle(self) -> bool:
        """Whether this intent asks for nothing at all."""
        return self.direction is None and not self.fire


IDLE_INTENT: Final[PlayerIntent] = PlayerIntent()
"""The intent used whenever no run is being driven -- menus, pause, a lost window focus."""


def intent_from_held(held: Sequence[Action]) -> PlayerIntent:
    """Reduce held actions, oldest press first, to one intent.

    The most recent movement press wins. ``held`` is a sequence rather than a set
    precisely so that this answer is stated by the caller's press order instead of being
    left to hash ordering.
    """
    direction: Direction | None = None
    for action in held:
        mapped = ACTION_DIRECTIONS.get(action)
        if mapped is not None:
            direction = mapped
    return PlayerIntent(direction=direction, fire=Action.FIRE in held)


@dataclass(slots=True)
class HeldActions:
    """Which continuous actions are down, in the order they were pressed.

    A key repeat, a duplicated press event or a release for a key that was never seen are
    all ordinary things a window system delivers; each is idempotent here rather than an
    error, because dropping a frame of input is a better failure than crashing the loop.
    """

    _order: list[Action] = field(default_factory=list, init=False, repr=False)

    def press(self, action: Action) -> None:
        """Record ``action`` as held, moving it to the front of recency."""
        if action in self._order:
            self._order.remove(action)
        self._order.append(action)

    def release(self, action: Action) -> None:
        """Record ``action`` as no longer held. Releasing an unheld action does nothing."""
        if action in self._order:
            self._order.remove(action)

    def clear(self) -> None:
        """Drop every held action.

        Called when the window loses focus: the operating system stops delivering key
        releases to an unfocused window, so a key held at the moment focus was lost would
        otherwise stay held forever.
        """
        self._order.clear()

    def ordered(self) -> tuple[Action, ...]:
        """Held actions, oldest press first."""
        return tuple(self._order)

    def intent(self) -> PlayerIntent:
        """The intent these held actions reduce to."""
        return intent_from_held(self._order)

    def __contains__(self, action: object) -> bool:
        return action in self._order
