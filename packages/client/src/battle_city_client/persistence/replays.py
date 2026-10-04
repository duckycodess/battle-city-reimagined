"""Keeping recorded replays on disk, bounded in size and in number.

A replay is a much larger document than a settings or campaign save and it is versioned
by the protocol package rather than by this one, so it does not travel inside the local
save envelope :mod:`battle_city_client.persistence.documents` defines. It is written as
the protocol encoded it, through the same atomic replace every other local document uses:
a temporary beside the target, flushed and ``fsync``-ed, then :func:`os.replace`. A reader
sees the whole old file or the whole new one.

Two bounds, for the two ways a replay directory grows
-----------------------------------------------------
:data:`~battle_city_protocol.MAX_REPLAY_BYTES` bounds one document and
:data:`MAX_STORED_REPLAYS` bounds how many this library will add. Reaching the second
refuses the save rather than deleting somebody's recording to make room: which replay is
worth keeping is the player's decision and there is no interface for it yet. Both bounds
are the honest answer to the storage-growth risk — the directory cannot grow without
limit, and it does not quietly prune itself either.

Reading one back is as untrusting as reading a frame
----------------------------------------------------
At most one byte past the size limit is read, so an oversized file is refused on the
strength of that byte. What is read then goes through :func:`~battle_city_protocol.decode_replay`,
which refuses malformed JSON, unknown fields, an unknown document, a replay version this
build does not write, and any key that looks like a credential. Nothing is migrated and
nothing is repaired: a replay this build cannot read is left exactly where it is.

Names are not paths
-------------------
A replay name is held to the identifier charset, so it cannot contain a separator, a
``..`` or a leading dot, and the file it maps to is always directly inside the replay
directory.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

from battle_city_protocol import (
    MAX_REPLAY_BYTES,
    ReplayDocument,
    ReplayError,
    decode_replay,
    encode_replay,
)

# ``_read_bounded`` and ``_write_atomically`` are package-internal on purpose: the
# bounded read and the atomic replace are one implementation shared by every local
# document, and they are deliberately not part of this package's published surface.
from .store import Loaded, _read_bounded, _write_atomically, default_save_dir

REPLAY_DIRECTORY: Final[str] = "replays"
"""Subdirectory of the profile that holds recordings."""

REPLAY_SUFFIX: Final[str] = ".replay.json"

MAX_STORED_REPLAYS: Final[int] = 32
"""Recordings this library will add to one directory. See the module docstring."""

_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
"""The identifier charset, matched with :func:`re.fullmatch`. No separators, ever."""

MAX_NAME_LENGTH: Final[int] = 64


class ReplayNameError(ValueError):
    """A replay name is not one this library will turn into a filename."""


class ReplayLibrary:
    """Reads and replaces the replay documents of one local profile."""

    __slots__ = ("_directory", "_locate")

    def __init__(self, locate: Callable[[], Path] = default_save_dir) -> None:
        self._locate = locate
        self._directory: Path | None = None

    @property
    def directory(self) -> Path:
        """Where recordings live, resolved once on first use and remembered."""
        if self._directory is None:
            self._directory = self._locate() / REPLAY_DIRECTORY
        return self._directory

    def path_for(self, name: str) -> Path:
        """Where the ``name`` recording lives. Raises on a name that is not one."""
        return self.directory / f"{require_replay_name(name)}{REPLAY_SUFFIX}"

    def names(self) -> tuple[str, ...]:
        """Every recording in the directory, sorted. Empty when there is no directory."""
        try:
            entries = sorted(path.name for path in self.directory.iterdir())
        except OSError:
            return ()
        return tuple(
            entry[: -len(REPLAY_SUFFIX)] for entry in entries if entry.endswith(REPLAY_SUFFIX)
        )

    def save(self, name: str, document: ReplayDocument) -> str:
        """Replace the ``name`` recording atomically. Returns a notice, or ``""``."""
        path = self.path_for(name)
        try:
            payload = encode_replay(document)
        except ReplayError as error:
            return f"REPLAY COULD NOT BE SAVED: {error.code.value.upper()}"
        stored = self.names()
        if name not in stored and len(stored) >= MAX_STORED_REPLAYS:
            return f"REPLAY FOLDER IS FULL ({MAX_STORED_REPLAYS} RECORDINGS)"
        try:
            _write_atomically(path, payload)
        except OSError:
            return "REPLAY COULD NOT BE WRITTEN"
        return ""

    def load(self, name: str) -> Loaded[ReplayDocument]:
        """Read the ``name`` recording. Never raises for a file it cannot use."""
        path = self.path_for(name)
        try:
            payload = _read_bounded(path, MAX_REPLAY_BYTES)
        except FileNotFoundError:
            return Loaded(value=None)
        except OSError:
            return Loaded(value=None, notice="REPLAY COULD NOT BE READ", writable=False)
        if len(payload) > MAX_REPLAY_BYTES:
            return Loaded(value=None, notice="REPLAY IS TOO LARGE", writable=False)
        try:
            return Loaded(value=decode_replay(payload))
        except ReplayError as error:
            return Loaded(
                value=None,
                notice=f"REPLAY IS UNUSABLE: {error.code.value.upper()}",
                writable=False,
            )

    def delete(self, name: str) -> str:
        """Remove the ``name`` recording. Returns a notice, or ``""``.

        Removing one that is not there is not an error: the directory ends up the way
        the caller asked for either way.
        """
        try:
            os.remove(self.path_for(name))
        except FileNotFoundError:
            return ""
        except OSError:
            return "REPLAY COULD NOT BE REMOVED"
        return ""


def require_replay_name(name: str) -> str:
    """Return ``name`` when it is a bounded identifier, and raise when it is not."""
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ReplayNameError(f"a replay name must be 1 to {MAX_NAME_LENGTH} characters")
    if _NAME.fullmatch(name) is None:
        raise ReplayNameError(
            "a replay name must use letters, digits, '.', '_' or '-' and start and end alphanumeric"
        )
    return name
