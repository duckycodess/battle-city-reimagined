"""Session-only accessibility preferences, and the pure logic that acts on them.

Nothing in this module imports pygame, reads a clock, touches a file or knows what a
keycode means. It names the preferences the accessibility specification asks for --
remappable keyboard and gamepad controls, configurable input repeat and stick
sensitivity, contrast, reduced motion and independent sound controls -- as validated
immutable values, and it holds the arithmetic that turns a device reading into an
:class:`~battle_city_client.intents.Action`. The layers that do know about devices are
:mod:`battle_city_client.keymap` (keycodes), :mod:`battle_city_client.gamepad` (SDL
joysticks) and :mod:`battle_city_client.app` (the loop). That split is what lets every
rule here be tested with no display, no pad and no window.

Session-only, and said plainly
------------------------------
**None of this is saved.** The local settings document holds three fields -- window
scale, frame cap and roster name -- and gaining a field means a schema bump and a
migration, which belongs to the issue that owns the save format. A preference changed
here lasts for the launch that changed it, the options screen says so, and the window
scale, which *is* saved, stays the one setting that survives because it was already
saved before this module existed.

Nothing here reaches the simulation
-----------------------------------
A binding decides which :class:`Action` a device input names. The actions themselves are
unchanged, the reduction from held actions to a
:class:`~battle_city_client.intents.PlayerIntent` is unchanged, and a tick still carries
exactly a facing and a fire flag. Repeat is the one piece that could leak -- a repeat is
a synthetic press nobody made -- so :class:`RepeatTimer` is restricted by construction to
the edge actions a menu cursor uses: it is driven only from the loop's menu path, it
never touches held state, and no repeated action is ever reduced into an intent. Dead
zone and axis bounding sit on the other side of the same line: they decide whether a
stick reading counts as held at all, before anything is held.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Final

from .intents import KEYBOARD_DEVICE, Action
from .theme import ContrastMode

__all__ = [
    "ACTION_LABELS",
    "AUDIO_INACTIVE_NOTICE",
    "DEAD_ZONE_STEP_PERCENT",
    "DEFAULT_ACCESSIBILITY",
    "DEFAULT_DEAD_ZONE_PERCENT",
    "DEFAULT_GAMEPAD_DEFAULTS",
    "DEFAULT_GAMEPAD_EDGE",
    "DEFAULT_GAMEPAD_HELD",
    "EMPTY_BINDINGS",
    "KEYBOARD_DEVICE",
    "MAX_DEAD_ZONE_PERCENT",
    "MAX_REPEAT_DELAY_MS",
    "MAX_REPEAT_INTERVAL_MS",
    "MAX_VOLUME",
    "MIN_DEAD_ZONE_PERCENT",
    "MIN_REPEAT_DELAY_MS",
    "MIN_REPEAT_INTERVAL_MS",
    "MIN_VOLUME",
    "REMAPPABLE_ACTIONS",
    "REMAPPABLE_EDGE_ACTIONS",
    "REMAPPABLE_HELD_ACTIONS",
    "REPEATABLE_ACTIONS",
    "REPEAT_STEP_MS",
    "RESERVED_ACTIONS",
    "VOLUME_STEP",
    "AccessibilityPreferences",
    "AudioPreferences",
    "BindingConflict",
    "BindingSet",
    "GamepadControl",
    "GamepadControlKind",
    "RepeatOptions",
    "RepeatTimer",
    "axis_direction",
    "edge_control_action",
    "held_control_action",
    "resolved_table",
]


class GamepadControlKind(Enum):
    """The three kinds of thing an SDL joystick reports."""

    BUTTON = "button"
    AXIS = "axis"
    HAT = "hat"


@dataclass(frozen=True, slots=True)
class GamepadControl:
    """One physical control on a pad, named without reference to a particular pad.

        A button is an index. An axis is an index and a sign, because a stick pushed left and
        the same stick pushed right are two controls as far as a binding is concerned. A hat
        is an index, an axis within that hat (``0`` horizontal, ``1`` vertical) and a sign.

    Sorted through :attr:`sort_key` wherever more than one is listed, so a binding table has
        one spelling. Iterating a set of these to decide what a player asked for would be a
        gameplay decision taken by hash ordering, which is exactly what the repository's
        determinism rule forbids.
    """

    kind: GamepadControlKind
    index: int
    direction: int = 0
    axis: int = 0

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"control index must not be negative, found {self.index}")
        if self.kind is GamepadControlKind.BUTTON:
            if self.direction != 0 or self.axis != 0:
                raise ValueError("a button has no direction and no axis")
            return
        if self.direction not in (-1, 1):
            raise ValueError(f"direction must be -1 or 1, found {self.direction}")
        if self.kind is GamepadControlKind.AXIS and self.axis != 0:
            raise ValueError("an axis control is named by its index, not by a sub-axis")
        if self.kind is GamepadControlKind.HAT and self.axis not in (0, 1):
            raise ValueError(f"hat axis must be 0 or 1, found {self.axis}")

    @property
    def sort_key(self) -> tuple[str, int, int, int]:
        """A total order over controls, for listing them the same way every time."""
        return (self.kind.value, self.index, self.axis, self.direction)

    @property
    def label(self) -> str:
        """A short name for the options screen, in the font's own alphabet."""
        match self.kind:
            case GamepadControlKind.BUTTON:
                return f"PAD B{self.index}"
            case GamepadControlKind.AXIS:
                sign = "+" if self.direction > 0 else "-"
                return f"PAD AX{self.index}{sign}"
            case GamepadControlKind.HAT:
                return f"PAD HAT{_HAT_NAMES[(self.axis, self.direction)]}"


_HAT_NAMES: Final[dict[tuple[int, int], str]] = {
    (0, -1): " L",
    (0, 1): " R",
    (1, -1): " U",
    (1, 1): " D",
}


class BindingConflict(ValueError):
    """A requested binding was refused, with a sentence saying why.

    Raised rather than applied-and-hoped-for: a remapping screen that silently accepts a
    binding it cannot honour leaves a player pressing a key that does nothing and no way
    to find out which key took it.
    """


RESERVED_ACTIONS: Final[tuple[Action, ...]] = (Action.UI_CANCEL, Action.QUIT)
"""The way out, which this build will not let a player rebind away.

A remapping screen can leave a player unable to operate the game -- that is its whole
risk -- so going back and quitting are held out of it. ``UI_CANCEL`` keeps its shipped
keys on every screen, and ``QUIT`` is the window's own close button and is never a key at
all. Between them there is always a keystroke that leaves a screen and always a way to
end the program, whatever else has been bound to what.
"""

REMAPPABLE_HELD_ACTIONS: Final[tuple[Action, ...]] = (
    Action.MOVE_UP,
    Action.MOVE_DOWN,
    Action.MOVE_LEFT,
    Action.MOVE_RIGHT,
    Action.FIRE,
)
"""Continuous controls a player may move. Sampled every frame while a run is driven."""

REMAPPABLE_EDGE_ACTIONS: Final[tuple[Action, ...]] = (
    Action.UI_UP,
    Action.UI_DOWN,
    Action.UI_CONFIRM,
    Action.TOGGLE_PAUSE,
)
"""Edge controls a player may move. ``UI_CANCEL`` is deliberately absent; see
:data:`RESERVED_ACTIONS`."""

REPEATABLE_ACTIONS: Final[tuple[Action, ...]] = (
    Action.UI_UP,
    Action.UI_DOWN,
    Action.UI_LEFT,
    Action.UI_RIGHT,
)
"""The only actions a repeat may synthesise: the four a menu cursor moves with.

Confirm, cancel, pause and every gameplay control are absent by construction, so no
repeat can start a run, leave one, fire a shot or move a tank. This is the structural
half of "repeat never reaches the simulation"; :class:`RepeatTimer` refuses anything
else outright.
"""

REMAPPABLE_ACTIONS: Final[tuple[Action, ...]] = (
    *REMAPPABLE_HELD_ACTIONS,
    *REMAPPABLE_EDGE_ACTIONS,
)
"""Every action the options screen offers a row for, in display order."""

ACTION_LABELS: Final[dict[Action, str]] = {
    Action.MOVE_UP: "UP",
    Action.MOVE_DOWN: "DOWN",
    Action.MOVE_LEFT: "LEFT",
    Action.MOVE_RIGHT: "RIGHT",
    Action.FIRE: "FIRE",
    Action.UI_UP: "MENU UP",
    Action.UI_DOWN: "MENU DOWN",
    Action.UI_CONFIRM: "CONFIRM",
    Action.UI_LEFT: "MENU LEFT",
    Action.UI_RIGHT: "MENU RIGHT",
    Action.UI_CANCEL: "BACK",
    Action.TOGGLE_PAUSE: "PAUSE",
}
"""What each remappable action is called on screen."""


def _require_remappable(action: Action) -> None:
    if action in RESERVED_ACTIONS:
        raise BindingConflict(f"{_name(action)} IS RESERVED AND CANNOT MOVE")
    if action not in REMAPPABLE_ACTIONS:
        raise BindingConflict(f"{_name(action)} CANNOT BE REBOUND")


def _require_unique(values: tuple[object, ...], what: str) -> None:
    if len(set(values)) != len(values):
        raise BindingConflict(f"one {what} is bound twice")


def _name(action: Action) -> str:
    return ACTION_LABELS.get(action, action.value.replace("_", " ").upper())


def _ordered_keyboard(
    pairs: Iterable[tuple[Action, int]],
) -> tuple[tuple[Action, int], ...]:
    """Overlay entries in :data:`REMAPPABLE_ACTIONS` order, so a set has one spelling."""
    order = {action: index for index, action in enumerate(REMAPPABLE_ACTIONS)}
    return tuple(sorted(pairs, key=lambda pair: order[pair[0]]))


def _ordered_gamepad(
    pairs: Iterable[tuple[Action, GamepadControl]],
) -> tuple[tuple[Action, GamepadControl], ...]:
    order = {action: index for index, action in enumerate(REMAPPABLE_ACTIONS)}
    return tuple(sorted(pairs, key=lambda pair: order[pair[0]]))


@dataclass(frozen=True, slots=True, kw_only=True)
class BindingSet:
    """What the player changed, over what the build ships, plus what it will not change.

    Four parts, and each is a different kind of fact:

    ``keyboard`` and ``gamepad``
        the overlay -- one input per action, and only for an action the player moved.
    ``default_keys`` and ``default_controls``
        what the build ships, by action, so the options screen can say what a row is
        bound to and so a conflict can name the action it collides with. Supplied by the
        layer that knows the device's own codes, which keeps this module free of them.
    ``reserved_keys``
        inputs this build refuses to capture at all, so the way out of a screen survives
        every remapping.

    Everything is a tuple rather than a mapping so the value is hashable, comparable and
    has one spelling; the ``*_map`` properties hand out dictionaries for lookup.
    """

    keyboard: tuple[tuple[Action, int], ...] = ()
    gamepad: tuple[tuple[Action, GamepadControl], ...] = ()
    default_keys: tuple[tuple[Action, tuple[int, ...]], ...] = ()
    default_controls: tuple[tuple[Action, tuple[GamepadControl, ...]], ...] = ()
    reserved_keys: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        for action, _ in self.keyboard:
            _require_remappable(action)
        for action, _ in self.gamepad:
            _require_remappable(action)
        _require_unique(tuple(action for action, _ in self.keyboard), "keyboard")
        _require_unique(tuple(action for action, _ in self.gamepad), "gamepad")
        _require_unique(tuple(key for _, key in self.keyboard), "keyboard key")
        _require_unique(tuple(control for _, control in self.gamepad), "pad control")
        for _, key in self.keyboard:
            if key in self.reserved_keys:
                raise BindingConflict(f"key {key} is reserved and cannot be rebound")

    # -- reading ---------------------------------------------------------------

    @property
    def keyboard_map(self) -> dict[Action, int]:
        """The keyboard overlay, by action."""
        return dict(self.keyboard)

    @property
    def gamepad_map(self) -> dict[Action, GamepadControl]:
        """The pad overlay, by action."""
        return dict(self.gamepad)

    def key_of(self, action: Action) -> int | None:
        """The key the player bound ``action`` to, or ``None`` if they did not."""
        return self.keyboard_map.get(action)

    def control_of(self, action: Action) -> GamepadControl | None:
        """The pad control the player bound ``action`` to, or ``None``."""
        return self.gamepad_map.get(action)

    def default_keys_of(self, action: Action) -> tuple[int, ...]:
        """The keys the build ships for ``action``."""
        return dict(self.default_keys).get(action, ())

    def default_controls_of(self, action: Action) -> tuple[GamepadControl, ...]:
        """The pad controls the build ships for ``action``."""
        return dict(self.default_controls).get(action, ())

    def keys_for(self, action: Action) -> tuple[int, ...]:
        """Every key that drives ``action`` now: the overlay's, or the shipped ones.

        A remapped action keeps exactly the one key it was given. Replacing the shipped
        keys rather than adding to them is what makes the row on the options screen the
        whole truth about that action.
        """
        bound = self.key_of(action)
        if bound is not None:
            return (bound,)
        taken = frozenset(key for _, key in self.keyboard)
        return tuple(key for key in self.default_keys_of(action) if key not in taken)

    def controls_for(self, action: Action) -> tuple[GamepadControl, ...]:
        """Every pad control that drives ``action`` now."""
        bound = self.control_of(action)
        if bound is not None:
            return (bound,)
        taken = frozenset(control for _, control in self.gamepad)
        return tuple(
            control for control in self.default_controls_of(action) if control not in taken
        )

    def action_using_key(self, key: int, *, besides: Action | None = None) -> Action | None:
        """Which action ``key`` already drives, ignoring ``besides``. ``None`` when free."""
        for action, bound in self.keyboard:
            if bound == key and action is not besides:
                return action
        for action, keys in self.default_keys:
            if action is besides or action in self.keyboard_map:
                continue
            if key in keys:
                return action
        return None

    def action_using_control(
        self, control: GamepadControl, *, besides: Action | None = None
    ) -> Action | None:
        """Which action ``control`` already drives, ignoring ``besides``."""
        for action, bound in self.gamepad:
            if bound == control and action is not besides:
                return action
        for action, controls in self.default_controls:
            if action is besides or action in self.gamepad_map:
                continue
            if control in controls:
                return action
        return None

    # -- changing --------------------------------------------------------------

    def with_keyboard(self, action: Action, key: int) -> BindingSet:
        """Bind ``action`` to ``key``, refusing a reserved or already-taken input."""
        _require_remappable(action)
        if key in self.reserved_keys:
            raise BindingConflict(f"{_name(action)} CANNOT TAKE A RESERVED KEY")
        clash = self.action_using_key(key, besides=action)
        if clash is not None:
            raise BindingConflict(f"THAT KEY IS ALREADY {_name(clash)}")
        kept = tuple((bound, value) for bound, value in self.keyboard if bound is not action)
        return replace(self, keyboard=_ordered_keyboard((*kept, (action, key))))

    def with_gamepad(self, action: Action, control: GamepadControl) -> BindingSet:
        """Bind ``action`` to ``control``, refusing an already-taken one."""
        _require_remappable(action)
        clash = self.action_using_control(control, besides=action)
        if clash is not None:
            raise BindingConflict(f"THAT CONTROL IS ALREADY {_name(clash)}")
        kept = tuple((bound, value) for bound, value in self.gamepad if bound is not action)
        return replace(self, gamepad=_ordered_gamepad((*kept, (action, control))))

    def cleared(self, action: Action) -> BindingSet:
        """Put ``action`` back on the inputs the build ships for it."""
        return replace(
            self,
            keyboard=tuple((bound, key) for bound, key in self.keyboard if bound is not action),
            gamepad=tuple(
                (bound, control) for bound, control in self.gamepad if bound is not action
            ),
        )

    def reset(self) -> BindingSet:
        """Drop every remapping. The shipped tables, the reserved keys and the defaults
        are kept, because none of them is a thing the player changed."""
        return replace(self, keyboard=(), gamepad=())

    @property
    def remapped(self) -> bool:
        """Whether anything has been rebound this session."""
        return bool(self.keyboard or self.gamepad)


EMPTY_BINDINGS: Final[BindingSet] = BindingSet()
"""No remapping, no shipped tables and nothing reserved.

The default a shell is built with, and what the pure tests use. A real launch replaces it
with :data:`battle_city_client.keymap.DEFAULT_BINDINGS`, which carries the keycodes.
"""


MIN_REPEAT_DELAY_MS: Final[int] = 100
MAX_REPEAT_DELAY_MS: Final[int] = 1500
MIN_REPEAT_INTERVAL_MS: Final[int] = 40
MAX_REPEAT_INTERVAL_MS: Final[int] = 600
REPEAT_STEP_MS: Final[int] = 20
"""Bounds and the step the options screen moves in. A repeat faster than the bound would
outrun a frame at any sane cap; one slower would be indistinguishable from off."""


@dataclass(frozen=True, slots=True)
class RepeatOptions:
    """How a held menu direction repeats.

    Off is a real setting and not a zero: somebody who overshoots every list wants one
    press to mean one step, and expressing that as an unreachably long delay would be a
    lie about what the number does.
    """

    enabled: bool = True
    delay_ms: int = 400
    interval_ms: int = 140

    def __post_init__(self) -> None:
        if not MIN_REPEAT_DELAY_MS <= self.delay_ms <= MAX_REPEAT_DELAY_MS:
            raise ValueError(
                f"repeat delay must be {MIN_REPEAT_DELAY_MS}-{MAX_REPEAT_DELAY_MS} ms, "
                f"found {self.delay_ms}"
            )
        if not MIN_REPEAT_INTERVAL_MS <= self.interval_ms <= MAX_REPEAT_INTERVAL_MS:
            raise ValueError(
                f"repeat interval must be {MIN_REPEAT_INTERVAL_MS}-{MAX_REPEAT_INTERVAL_MS} ms, "
                f"found {self.interval_ms}"
            )


@dataclass(slots=True)
class RepeatTimer:
    """Turns a held menu direction into further edge actions, and nothing else.

    Driven by elapsed milliseconds handed in by the caller, never by a clock it reads
    itself, so a test states the timing instead of waiting for it. Only an action in
    :data:`REPEATABLE_ACTIONS` may be held here, which is the structural half of the
    promise that a repeat cannot reach the simulation: there is no way to express
    "repeat the fire control" with this class.
    """

    options: RepeatOptions = RepeatOptions()
    _held: dict[Action, int] = field(default_factory=dict, init=False, repr=False)
    """Action to milliseconds remaining before its next emission."""

    _order: list[Action] = field(default_factory=list, init=False, repr=False)
    """Press order, so two directions held at once resolve by recency, not by hashing."""

    def press(self, action: Action) -> None:
        """Start repeating ``action``. The first emission is the caller's own press."""
        if action not in REPEATABLE_ACTIONS:
            raise ValueError(f"{action.value} is not a cursor action and must not repeat")
        if action in self._order:
            self._order.remove(action)
        self._order.append(action)
        self._held[action] = self.options.delay_ms

    def release(self, action: Action) -> None:
        """Stop repeating ``action``. Releasing one that is not held does nothing."""
        self._held.pop(action, None)
        if action in self._order:
            self._order.remove(action)

    def clear(self) -> None:
        """Forget everything. Used on focus loss, a device loss and a screen change."""
        self._held.clear()
        self._order.clear()

    @property
    def active(self) -> tuple[Action, ...]:
        """What is being repeated, oldest press first."""
        return tuple(self._order)

    def advance(self, elapsed_ms: int) -> tuple[Action, ...]:
        """Bank ``elapsed_ms`` and return the repeats that fell due, in press order.

        At most one repeat per action per call: a frame that took a second is a stall,
        and releasing eight menu steps out of it would move a cursor somewhere the player
        never asked for. The remainder is dropped rather than banked, for the same reason
        the tick accumulator bounds its catch-up.
        """
        if elapsed_ms < 0:
            raise ValueError(f"elapsed_ms must not be negative, found {elapsed_ms}")
        if not self.options.enabled:
            return ()
        due: list[Action] = []
        for action in self._order:
            remaining = self._held[action] - elapsed_ms
            if remaining <= 0:
                due.append(action)
                remaining = self.options.interval_ms
            self._held[action] = remaining
        return tuple(due)


MIN_DEAD_ZONE_PERCENT: Final[int] = 0
MAX_DEAD_ZONE_PERCENT: Final[int] = 90
DEAD_ZONE_STEP_PERCENT: Final[int] = 5
DEFAULT_DEAD_ZONE_PERCENT: Final[int] = 25
"""Stick dead zone, as whole percent of full deflection.

Whole percent rather than a float because it is a setting a player reads and steps, and
because an integer comparison against a bounded reading has one answer on every machine.
"""


def axis_direction(value: float, dead_zone_percent: int) -> int:
    """Which way a stick is pushed: ``-1``, ``0`` or ``1``.

    The reading is bounded before it is judged. SDL promises ``-1.0`` to ``1.0`` and a
    miscalibrated or hot-plugged pad does not always deliver it, and a reading that is
    not a number at all compares false against everything -- so both are turned into
    "centred" here rather than becoming a direction nobody pushed.
    """
    if value != value:  # NaN, which no comparison below would catch.
        return 0
    bounded = max(-1.0, min(1.0, value))
    if abs(bounded) * 100.0 <= dead_zone_percent:
        return 0
    return 1 if bounded > 0 else -1


MIN_VOLUME: Final[int] = 0
MAX_VOLUME: Final[int] = 100
VOLUME_STEP: Final[int] = 10

AUDIO_INACTIVE_NOTICE: Final[str] = "NO AUDIO IN THIS BUILD"
"""Said on the options screen beside the sound rows, because they do nothing yet.

This build initialises no mixer, ships no sound and plays nothing; the loop's module
docstring records why the mixer is left alone. The controls are here because the
preference model is real and independent -- effects and music are two numbers and two
mutes, not one slider -- and because a control that appears when audio lands would be a
control nobody had set. Labelling them inactive is the honest half of offering them.
"""


@dataclass(frozen=True, slots=True)
class AudioPreferences:
    """Independent effect and music levels, each with its own mute.

    Mute is kept beside the volume rather than folded into it, so muting and unmuting
    returns to the level that was set instead of to whatever zero was last typed.
    """

    sfx_volume: int = 70
    sfx_muted: bool = False
    music_volume: int = 50
    music_muted: bool = False

    def __post_init__(self) -> None:
        for name, value in (("sfx_volume", self.sfx_volume), ("music_volume", self.music_volume)):
            if not MIN_VOLUME <= value <= MAX_VOLUME:
                raise ValueError(f"{name} must be {MIN_VOLUME}-{MAX_VOLUME}, found {value}")

    @property
    def effective_sfx(self) -> int:
        """The level effects would actually play at. Zero while muted."""
        return 0 if self.sfx_muted else self.sfx_volume

    @property
    def effective_music(self) -> int:
        """The level music would actually play at. Zero while muted."""
        return 0 if self.music_muted else self.music_volume


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessibilityPreferences:
    """Every accessibility preference this session is running with.

    Immutable, validated on construction, and replaced wholesale by the ``with_`` helpers
    rather than mutated, so a preference that reached a renderer or a key table is a
    preference that was checked. Nothing here is written to disk; see the module
    docstring.
    """

    bindings: BindingSet = EMPTY_BINDINGS
    repeat: RepeatOptions = RepeatOptions()
    dead_zone_percent: int = DEFAULT_DEAD_ZONE_PERCENT
    contrast: ContrastMode = ContrastMode.DEFAULT
    reduced_motion: bool = False
    audio: AudioPreferences = AudioPreferences()

    def __post_init__(self) -> None:
        if not MIN_DEAD_ZONE_PERCENT <= self.dead_zone_percent <= MAX_DEAD_ZONE_PERCENT:
            raise ValueError(
                f"dead zone must be {MIN_DEAD_ZONE_PERCENT}-{MAX_DEAD_ZONE_PERCENT} percent, "
                f"found {self.dead_zone_percent}"
            )

    def with_bindings(self, bindings: BindingSet) -> AccessibilityPreferences:
        return replace(self, bindings=bindings)

    def with_repeat(self, repeat: RepeatOptions) -> AccessibilityPreferences:
        return replace(self, repeat=repeat)

    def with_dead_zone(self, percent: int) -> AccessibilityPreferences:
        """Move the dead zone, clamped to the bounds rather than refused.

        A stepped setting is clamped where a parsed one is refused: the player is holding
        a key down, and the honest answer to "quieter than the quietest" is the quietest.
        """
        clamped = max(MIN_DEAD_ZONE_PERCENT, min(MAX_DEAD_ZONE_PERCENT, percent))
        return replace(self, dead_zone_percent=clamped)

    def with_contrast(self, contrast: ContrastMode) -> AccessibilityPreferences:
        return replace(self, contrast=contrast)

    def with_reduced_motion(self, reduced: bool) -> AccessibilityPreferences:
        return replace(self, reduced_motion=reduced)

    def with_audio(self, audio: AudioPreferences) -> AccessibilityPreferences:
        return replace(self, audio=audio)

    def reset(self) -> AccessibilityPreferences:
        """Everything back to the shipped defaults, keeping the device tables.

        The recovery path for a remapping that went wrong: one row on the options screen,
        reachable with the keys that are reserved from remapping, so it can be used from
        whatever state the controls have been left in.
        """
        return AccessibilityPreferences(bindings=self.bindings.reset())


DEFAULT_ACCESSIBILITY: Final[AccessibilityPreferences] = AccessibilityPreferences()
"""What a launch starts from. Contrast default, motion unrestricted, nothing rebound."""


def held_control_action(
    control: GamepadControl, bindings: BindingSet = EMPTY_BINDINGS
) -> Action | None:
    """The continuous action a pad control drives, under ``bindings``."""
    overlay = {
        action: bound for action, bound in bindings.gamepad if action in REMAPPABLE_HELD_ACTIONS
    }
    if not overlay:
        return DEFAULT_GAMEPAD_HELD.get(control)
    return resolved_table(DEFAULT_GAMEPAD_HELD, overlay).get(control)


def edge_control_action(
    control: GamepadControl, bindings: BindingSet = EMPTY_BINDINGS
) -> Action | None:
    """The edge action a pad control triggers, under ``bindings``."""
    overlay = {
        action: bound
        for action, bound in bindings.gamepad
        if action in REMAPPABLE_EDGE_ACTIONS and action in DEFAULT_GAMEPAD_EDGE.values()
    }
    if not overlay:
        return DEFAULT_GAMEPAD_EDGE.get(control)
    return resolved_table(DEFAULT_GAMEPAD_EDGE, overlay).get(control)


def resolved_table[T](defaults: Mapping[T, Action], overlay: Mapping[Action, T]) -> dict[T, Action]:
    """One lookup table from the shipped one and the player's overlay.

    Two rules, and they are the same rule from both ends. An action the player rebound
    loses the inputs the build gave it, so a row on the options screen is the whole truth
    about that action. An input the player took is taken, so it cannot still mean the
    thing it used to mean as well.
    """
    taken = frozenset(overlay.values())
    table = {
        key: action
        for key, action in defaults.items()
        if action not in overlay and key not in taken
    }
    for action, key in overlay.items():
        table[key] = action
    return table


DEFAULT_GAMEPAD_HELD: Final[dict[GamepadControl, Action]] = {
    GamepadControl(GamepadControlKind.AXIS, 1, -1): Action.MOVE_UP,
    GamepadControl(GamepadControlKind.AXIS, 1, 1): Action.MOVE_DOWN,
    GamepadControl(GamepadControlKind.AXIS, 0, -1): Action.MOVE_LEFT,
    GamepadControl(GamepadControlKind.AXIS, 0, 1): Action.MOVE_RIGHT,
    GamepadControl(GamepadControlKind.HAT, 0, -1, 1): Action.MOVE_UP,
    GamepadControl(GamepadControlKind.HAT, 0, 1, 1): Action.MOVE_DOWN,
    GamepadControl(GamepadControlKind.HAT, 0, -1, 0): Action.MOVE_LEFT,
    GamepadControl(GamepadControlKind.HAT, 0, 1, 0): Action.MOVE_RIGHT,
    GamepadControl(GamepadControlKind.BUTTON, 0): Action.FIRE,
}
"""Continuous pad controls, sampled while a run is being driven.

The left stick and the hat both drive, because a pad whose hat is its only usable control
and a pad whose stick is are both ordinary. SDL's axis 0 is horizontal and axis 1 is
vertical with *down* positive, which is why ``AXIS 1 -`` is up.
"""

DEFAULT_GAMEPAD_EDGE: Final[dict[GamepadControl, Action]] = {
    GamepadControl(GamepadControlKind.AXIS, 1, -1): Action.UI_UP,
    GamepadControl(GamepadControlKind.AXIS, 1, 1): Action.UI_DOWN,
    GamepadControl(GamepadControlKind.AXIS, 0, -1): Action.UI_LEFT,
    GamepadControl(GamepadControlKind.AXIS, 0, 1): Action.UI_RIGHT,
    GamepadControl(GamepadControlKind.HAT, 0, -1, 1): Action.UI_UP,
    GamepadControl(GamepadControlKind.HAT, 0, 1, 1): Action.UI_DOWN,
    GamepadControl(GamepadControlKind.HAT, 0, -1, 0): Action.UI_LEFT,
    GamepadControl(GamepadControlKind.HAT, 0, 1, 0): Action.UI_RIGHT,
    GamepadControl(GamepadControlKind.BUTTON, 0): Action.UI_CONFIRM,
    GamepadControl(GamepadControlKind.BUTTON, 1): Action.UI_CANCEL,
    GamepadControl(GamepadControlKind.BUTTON, 7): Action.TOGGLE_PAUSE,
}
"""Edge pad controls, for every screen that shows a cursor.

Button 1 is cancel on every pad layout this build has seen, which makes it the pad's half
of the reserved way out: it is in :data:`RESERVED_ACTIONS`, so nothing can take it.
"""

DEFAULT_GAMEPAD_DEFAULTS: Final[tuple[tuple[Action, tuple[GamepadControl, ...]], ...]] = tuple(
    (
        action,
        tuple(
            sorted(
                {
                    control
                    for table in (DEFAULT_GAMEPAD_HELD, DEFAULT_GAMEPAD_EDGE)
                    for control, bound in table.items()
                    if bound is action
                },
                key=lambda control: control.sort_key,
            )
        ),
    )
    for action in REMAPPABLE_ACTIONS
)
"""Shipped pad controls by action, for the options screen and conflict reporting."""
