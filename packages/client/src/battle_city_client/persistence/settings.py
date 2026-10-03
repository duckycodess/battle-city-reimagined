"""The local settings document: the three preferences this build actually has.

A settings record holds exactly what the player can already change and what the launch
already accepts -- the window scale, the frame cap the loop aims for, and the roster name
other players see. It holds nothing else on purpose. A settings file is the place a
secret ends up by accident, so the rule here is structural rather than a reminder: the
record has three fields, the reader refuses any key it does not know, and the lobby
ticket, the session identifier and the server address are not among them. They stay where
they are, on the command line, for the life of one launch.

Everything is validated on construction rather than at the point of use, so a settings
value that reached this record is a value the client can act on. The scale is bounded by
:mod:`battle_city_client.theme`, which is the same bound the window honours. The frame cap
is bounded here and re-exported by :mod:`battle_city_client.app`, so the launch flag and
the saved value cannot drift apart. The display name is held to the protocol's own roster
rule: a name that would be refused by a server is refused here, which is what stops a
saved file from producing a join request the lobby throws away.

Accessibility settings -- contrast, reduced motion, remapped keys, sound -- are not here
because this build has none of them to save. They are named in the persistence and
accessibility specification and they arrive as further fields with a schema bump, which is
precisely what :mod:`~battle_city_client.persistence.migrations` exists to carry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from battle_city_protocol import MAX_DISPLAY_NAME_LENGTH, JsonValue

from .. import theme
from .documents import CorruptSave, read_int, read_text, require_exact_keys

SETTINGS_KIND: Final[str] = "settings"
SETTINGS_SCHEMA_VERSION: Final[int] = 1
"""Bump when a field is added, removed or reinterpreted, and add the migration with it."""

DEFAULT_FRAME_CAP: Final[int] = 120
"""Frames per second the loop aims for.

A cap above the tick rate keeps input latency low without spinning a core; the tick
accumulator makes the exact number irrelevant to how a run plays.
"""

MIN_FRAME_CAP: Final[int] = 1
MAX_FRAME_CAP: Final[int] = 1000
"""Bounds for the frame cap. Zero means "never wait" to pygame, which is a busy spin."""

DEFAULT_DISPLAY_NAME: Final[str] = "player"
"""Roster name used when neither a launch option nor a saved file supplied one."""

SCALE_FIELD: Final[str] = "scale"
FRAME_CAP_FIELD: Final[str] = "frame_cap"
DISPLAY_NAME_FIELD: Final[str] = "display_name"

SETTINGS_FIELDS: Final[frozenset[str]] = frozenset(
    {SCALE_FIELD, FRAME_CAP_FIELD, DISPLAY_NAME_FIELD}
)
"""Every key a settings document may carry. A fourth key means the file is not ours."""

_ROSTER_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
"""Mirrors ``battle_city_protocol.validation.require_name``.

The rule is restated rather than imported because the protocol publishes the bound and
the message types but not the predicate. ``tests/persistence`` closes the gap by putting a
saved name through a real :class:`~battle_city_protocol.LobbyJoin`, so the two cannot
drift without a test saying so.
"""


@dataclass(frozen=True, slots=True)
class LocalSettings:
    """Validated local preferences. Immutable; change one with :func:`dataclasses.replace`."""

    scale: int = theme.DEFAULT_SCALE
    frame_cap: int = DEFAULT_FRAME_CAP
    display_name: str = DEFAULT_DISPLAY_NAME

    def __post_init__(self) -> None:
        if not theme.MIN_SCALE <= self.scale <= theme.MAX_SCALE:
            raise ValueError(
                f"scale must be between {theme.MIN_SCALE} and {theme.MAX_SCALE}, found {self.scale}"
            )
        if not MIN_FRAME_CAP <= self.frame_cap <= MAX_FRAME_CAP:
            raise ValueError(
                f"frame cap must be between {MIN_FRAME_CAP} and {MAX_FRAME_CAP}, "
                f"found {self.frame_cap}"
            )
        if not self.display_name or len(self.display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise ValueError(
                f"display name must be 1 to {MAX_DISPLAY_NAME_LENGTH} characters, "
                f"found {len(self.display_name)}"
            )
        if _ROSTER_NAME.fullmatch(self.display_name) is None:
            raise ValueError(
                "display name must use letters, digits, '.', '_' or '-' and start and end "
                f"alphanumeric, found {self.display_name!r}"
            )

    def with_scale(self, scale: int) -> LocalSettings:
        """The same settings at ``scale``, clamped to what the window can show."""
        clamped = max(theme.MIN_SCALE, min(theme.MAX_SCALE, scale))
        return replace(self, scale=clamped)

    def to_data(self) -> dict[str, JsonValue]:
        """The payload a settings document carries."""
        return {
            DISPLAY_NAME_FIELD: self.display_name,
            FRAME_CAP_FIELD: self.frame_cap,
            SCALE_FIELD: self.scale,
        }


def settings_from_data(data: Mapping[str, JsonValue]) -> LocalSettings:
    """Build settings from a decoded payload, refusing anything that is not one.

    A value that is present and out of range is a :class:`CorruptSave`, not a clamp. The
    file is being read because it claims to say what the player chose, and a reader that
    quietly substitutes its own number has stopped reporting what the file says.
    """
    require_exact_keys(data, SETTINGS_FIELDS, "settings")
    try:
        return LocalSettings(
            scale=read_int(data, SCALE_FIELD, minimum=theme.MIN_SCALE, maximum=theme.MAX_SCALE),
            frame_cap=read_int(data, FRAME_CAP_FIELD, minimum=MIN_FRAME_CAP, maximum=MAX_FRAME_CAP),
            display_name=read_text(data, DISPLAY_NAME_FIELD, max_length=MAX_DISPLAY_NAME_LENGTH),
        )
    except ValueError as error:
        raise CorruptSave(f"settings are not usable: {error}") from error
