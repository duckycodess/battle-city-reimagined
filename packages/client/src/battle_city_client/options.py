"""The options screen as a list of rows and the edits a cursor makes to them.

No pygame, no keycodes, no surface. This module turns an
:class:`~battle_city_client.accessibility.AccessibilityPreferences` into the rows a
player moves a cursor through, and turns a cursor movement back into a new preferences
value. :mod:`battle_city_client.shell` owns the cursor and the capture state,
:mod:`battle_city_client.rendering` draws the rows, and
:mod:`battle_city_client.keymap` spells the device-specific half of a binding row --
because a keycode is the one thing on this screen that only the device layer can name.

Why a separate screen
---------------------
``CONTROLS`` is a reference card: it lists what the keys do and lets a player choose a
badge, and ``tests/persistence`` asserts that up and down on it cycle badges. Hanging
editable options off the same two keys would have meant one screen where up and down
sometimes moved a cursor and sometimes changed a cosmetic, and would have changed
behaviour that another issue's tests pin down. So the options are their own screen, their
own main-menu entry, and the reference card is left exactly as it was.

Operating it without being able to operate it
---------------------------------------------
A remapping screen is the one screen that can lock a player out of the game, so three
things are held back from it. ``UI_CANCEL`` and the window's close button are reserved
and can never be rebound, so there is always a keystroke that leaves and always a way to
quit. :data:`OptionId.RESET` puts every preference and every binding back to the shipped
ones, and it is reachable with the keys that cannot be taken. And capture is explicit --
a row is armed with confirm, says so while it is armed, takes exactly one input, and is
abandoned by the reserved cancel.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Final

from .accessibility import (
    ACTION_LABELS,
    AUDIO_INACTIVE_NOTICE,
    DEAD_ZONE_STEP_PERCENT,
    MAX_REPEAT_DELAY_MS,
    MAX_REPEAT_INTERVAL_MS,
    MAX_VOLUME,
    MIN_REPEAT_DELAY_MS,
    MIN_REPEAT_INTERVAL_MS,
    MIN_VOLUME,
    REMAPPABLE_ACTIONS,
    REPEAT_STEP_MS,
    VOLUME_STEP,
    AccessibilityPreferences,
    BindingConflict,
    GamepadControl,
    RepeatOptions,
)
from .intents import Action
from .theme import ContrastMode


class OptionId(Enum):
    """Which row of the options screen this is."""

    CONTRAST = "contrast"
    REDUCED_MOTION = "reduced_motion"
    SFX_LEVEL = "sfx_level"
    SFX_MUTE = "sfx_mute"
    MUSIC_LEVEL = "music_level"
    MUSIC_MUTE = "music_mute"
    DEAD_ZONE = "dead_zone"
    REPEAT = "repeat"
    REPEAT_DELAY = "repeat_delay"
    REPEAT_RATE = "repeat_rate"
    RESET = "reset"
    BINDING = "binding"


AUDIO_ROW_NOTE: Final[str] = "INACTIVE"
"""Marked on every sound row, not only in the footnote under them."""

LEFT_COLUMN: Final[int] = 0
RIGHT_COLUMN: Final[int] = 1


@dataclass(frozen=True, slots=True)
class OptionRow:
    """One line of the options screen.

    ``value`` is already spelled for every row the preferences can spell on their own. A
    binding row leaves it empty and carries ``action`` instead, because naming the key
    behind a keycode needs the device layer; see
    :func:`battle_city_client.keymap.binding_summary`.
    """

    id: OptionId
    label: str
    value: str = ""
    column: int = LEFT_COLUMN
    action: Action | None = None
    note: str = ""
    """A short qualifier shown beside the row -- what a setting does *not* do yet."""


REDUCED_MOTION_NOTE: Final[str] = "NO TIME-VARYING EFFECTS YET"
"""Said on the screen, because the preference is real and has nothing to hold still.

This build draws no animation, no flash, no shake and no transition: every frame is a
function of the state it is drawn from. The preference is carried to the renderer so that
the first effect to arrive has something to ask, and saying that plainly is better than
a switch that looks like it is doing something.
"""

SCALE_NOTE: Final[str] = "WINDOW SCALE IS - AND +"
"""UI enlargement is the window scale, which already works on every screen.

The client draws one fixed logical frame and the window shows a whole multiple of it, so
making the interface bigger *is* raising that multiple -- and because it is whole-number
the text stays pixel-exact at every size. Nothing here changes the logical frame or the
HUD geometry; a bigger frame would be a different layout, not a larger one.
"""


def contrast_label(contrast: ContrastMode) -> str:
    """How a contrast mode is named on screen."""
    return "HIGH" if contrast is ContrastMode.HIGH else "DEFAULT"


def _switch(value: bool) -> str:
    return "ON" if value else "OFF"


def rows(preferences: AccessibilityPreferences) -> tuple[OptionRow, ...]:
    """Every row of the options screen, in cursor order.

    Cursor order is reading order: the left column top to bottom, then the right. The
    cursor is one list across two panels rather than a grid, so up and down reach every
    row and no row needs a key that no other screen uses.
    """
    audio = preferences.audio
    repeat = preferences.repeat
    fixed = (
        OptionRow(OptionId.CONTRAST, "CONTRAST", contrast_label(preferences.contrast)),
        OptionRow(
            OptionId.REDUCED_MOTION,
            "REDUCED MOTION",
            _switch(preferences.reduced_motion),
        ),
        OptionRow(OptionId.SFX_LEVEL, "SFX LEVEL", str(audio.sfx_volume), note=AUDIO_ROW_NOTE),
        OptionRow(OptionId.SFX_MUTE, "SFX MUTE", _switch(audio.sfx_muted), note=AUDIO_ROW_NOTE),
        OptionRow(
            OptionId.MUSIC_LEVEL, "MUSIC LEVEL", str(audio.music_volume), note=AUDIO_ROW_NOTE
        ),
        OptionRow(
            OptionId.MUSIC_MUTE, "MUSIC MUTE", _switch(audio.music_muted), note=AUDIO_ROW_NOTE
        ),
        OptionRow(OptionId.DEAD_ZONE, "PAD DEAD ZONE", f"{preferences.dead_zone_percent}%"),
        OptionRow(OptionId.REPEAT, "MENU REPEAT", _switch(repeat.enabled)),
        OptionRow(OptionId.REPEAT_DELAY, "REPEAT DELAY", f"{repeat.delay_ms}MS"),
        OptionRow(OptionId.REPEAT_RATE, "REPEAT RATE", f"{repeat.interval_ms}MS"),
        OptionRow(OptionId.RESET, "RESET ALL", "ENTER"),
    )
    bindings = tuple(
        OptionRow(OptionId.BINDING, ACTION_LABELS[action], column=RIGHT_COLUMN, action=action)
        for action in REMAPPABLE_ACTIONS
    )
    return fixed + bindings


ADJUSTABLE: Final[frozenset[OptionId]] = frozenset(
    {
        OptionId.CONTRAST,
        OptionId.REDUCED_MOTION,
        OptionId.SFX_LEVEL,
        OptionId.SFX_MUTE,
        OptionId.MUSIC_LEVEL,
        OptionId.MUSIC_MUTE,
        OptionId.DEAD_ZONE,
        OptionId.REPEAT,
        OptionId.REPEAT_DELAY,
        OptionId.REPEAT_RATE,
    }
)
"""Rows that left and right change. Everything else is confirm, or is read-only."""


def adjust(
    preferences: AccessibilityPreferences, row: OptionRow, delta: int
) -> AccessibilityPreferences:
    """``preferences`` with ``row`` moved by ``delta``. Unknown rows change nothing.

    Every number is stepped and clamped rather than wrapped. A player holding right on a
    volume expects to reach the top and stay there, and a value that silently wrapped to
    the bottom would be the one way a held key can undo itself.
    """
    audio = preferences.audio
    repeat = preferences.repeat
    match row.id:
        case OptionId.CONTRAST:
            modes = tuple(ContrastMode)
            index = modes.index(preferences.contrast)
            return preferences.with_contrast(modes[(index + delta) % len(modes)])
        case OptionId.REDUCED_MOTION:
            return preferences.with_reduced_motion(not preferences.reduced_motion)
        case OptionId.SFX_LEVEL:
            return preferences.with_audio(
                replace(audio, sfx_volume=_volume(audio.sfx_volume, delta))
            )
        case OptionId.SFX_MUTE:
            return preferences.with_audio(replace(audio, sfx_muted=not audio.sfx_muted))
        case OptionId.MUSIC_LEVEL:
            return preferences.with_audio(
                replace(audio, music_volume=_volume(audio.music_volume, delta))
            )
        case OptionId.MUSIC_MUTE:
            return preferences.with_audio(replace(audio, music_muted=not audio.music_muted))
        case OptionId.DEAD_ZONE:
            return preferences.with_dead_zone(
                preferences.dead_zone_percent + delta * DEAD_ZONE_STEP_PERCENT
            )
        case OptionId.REPEAT:
            return preferences.with_repeat(replace(repeat, enabled=not repeat.enabled))
        case OptionId.REPEAT_DELAY:
            return preferences.with_repeat(
                replace(
                    repeat,
                    delay_ms=_bounded(
                        repeat.delay_ms + delta * REPEAT_STEP_MS,
                        MIN_REPEAT_DELAY_MS,
                        MAX_REPEAT_DELAY_MS,
                    ),
                )
            )
        case OptionId.REPEAT_RATE:
            return preferences.with_repeat(
                replace(
                    repeat,
                    interval_ms=_bounded(
                        repeat.interval_ms + delta * REPEAT_STEP_MS,
                        MIN_REPEAT_INTERVAL_MS,
                        MAX_REPEAT_INTERVAL_MS,
                    ),
                )
            )
        case OptionId.RESET | OptionId.BINDING:
            return preferences


def _volume(value: int, delta: int) -> int:
    return _bounded(value + delta * VOLUME_STEP, MIN_VOLUME, MAX_VOLUME)


def _bounded(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


CAPTURE_PROMPT: Final[str] = "PRESS A KEY OR PAD CONTROL"
CAPTURE_CANCEL_HINT: Final[str] = "ESC CANCELS - PAD B1 CANCELS"
CAPTURE_RESERVED_HINT: Final[str] = "BACK AND QUIT CANNOT BE REBOUND"
RESET_NOTICE: Final[str] = "OPTIONS AND BINDINGS RESET"
SESSION_ONLY_NOTICE: Final[str] = "OPTIONS LAST FOR THIS SESSION"
"""Said on the screen, once. These preferences are not written to the save file; the
settings document holds the window scale, the frame cap and the roster name, and adding
a field to it is a schema change that belongs to the issue that owns the format."""


OPTIONS_FOOTNOTES: Final[tuple[str, ...]] = (
    SESSION_ONLY_NOTICE,
    AUDIO_INACTIVE_NOTICE,
    REDUCED_MOTION_NOTE,
    SCALE_NOTE,
)
"""The four things this screen has to say about what its settings do and do not do.

Kept beside the rows rather than in the renderer, so the screen cannot come to promise
something the preferences do not deliver.
"""


@dataclass(slots=True)
class OptionsState:
    """Where the cursor is on the options screen, and whether a row is armed.

    ``capture`` holds the action a row is waiting to bind. While it is set the screen is
    taking one raw input and nothing else: the loop stops translating keys into actions,
    so the key that is about to become *fire* cannot fire on its way in.
    """

    index: int = 0
    capture: Action | None = None
    notice: str = ""

    @property
    def capturing(self) -> bool:
        """Whether a row is waiting for one input."""
        return self.capture is not None

    def row(self, preferences: AccessibilityPreferences) -> OptionRow:
        """The highlighted row."""
        table = rows(preferences)
        return table[self.index % len(table)]

    def move(self, delta: int, preferences: AccessibilityPreferences) -> None:
        """Move the cursor, wrapping, and clear any notice the last row left."""
        table = rows(preferences)
        self.index = (self.index + delta) % len(table)
        self.notice = ""

    def arm(self, action: Action) -> None:
        """Wait for one input to bind to ``action``."""
        self.capture = action
        self.notice = CAPTURE_PROMPT

    def cancel(self) -> None:
        """Stop waiting. The binding is left exactly as it was."""
        self.capture = None
        self.notice = ""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """What a capture did: the preferences to adopt, and the line to show."""

    preferences: AccessibilityPreferences
    notice: str
    accepted: bool = field(default=True)


def capture_key(preferences: AccessibilityPreferences, action: Action, key: int) -> CaptureResult:
    """Bind ``action`` to ``key``, or say why not.

    A refusal keeps the preferences untouched and returns the sentence to show, so a
    conflict is a thing the player reads rather than a binding that quietly did not
    happen.
    """
    try:
        bindings = preferences.bindings.with_keyboard(action, key)
    except BindingConflict as refusal:
        return CaptureResult(preferences, str(refusal), accepted=False)
    return CaptureResult(preferences.with_bindings(bindings), f"{ACTION_LABELS[action]} BOUND")


def capture_control(
    preferences: AccessibilityPreferences, action: Action, control: GamepadControl
) -> CaptureResult:
    """Bind ``action`` to a pad control, or say why not."""
    try:
        bindings = preferences.bindings.with_gamepad(action, control)
    except BindingConflict as refusal:
        return CaptureResult(preferences, str(refusal), accepted=False)
    return CaptureResult(preferences.with_bindings(bindings), f"{ACTION_LABELS[action]} BOUND")


def default_repeat() -> RepeatOptions:
    """The shipped repeat settings. Named so a test does not restate the numbers."""
    return RepeatOptions()
