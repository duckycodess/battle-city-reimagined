"""Where local documents live, how they are replaced, and what happens when one is bad.

This is the only module in the client that writes to a disk, and all of it is about the
two moments a save goes wrong: the moment it is replaced, and the moment it is read back.

Replacing one is atomic
-----------------------
Every write goes to a temporary file beside the target, is flushed and ``fsync``-ed, and
is then moved onto the target with :func:`os.replace`, which is atomic on every platform
this game runs on. A reader therefore sees the whole old file or the whole new one and
never a half-written document, whatever the process does in between. The directory is
``fsync``-ed afterwards so the rename itself survives a power loss, and a write that fails
part-way removes its temporary file and leaves the target exactly as it was. A leftover
temporary from a kill that beat the cleanup is inert: nothing reads it, and the next write
replaces it.

Reading one back can fail in three ways, and they are not the same
------------------------------------------------------------------
* **Corrupt.** The bytes are not a document this build can read. Defaults are used, in
  memory, and the file is marked unwritable: overwriting it would destroy the only copy of
  whatever it actually was, and a player who can still read their old file with a text
  editor has a recovery path that an overwrite takes away.
* **From a newer build.** The schema version is one this build does not know. Same
  answer, and for a stronger reason: the file is not damaged at all, it belongs to a
  newer installation, and writing this build's understanding over it would silently
  discard whatever that build recorded.
* **From an older build.** The document is migrated through
  :mod:`~battle_city_client.persistence.migrations`, the original bytes are written to a
  ``.v<version>.bak`` file beside it *before* the upgraded document replaces it, and the
  upgrade only proceeds if that backup was written. A migration that has no path forward
  is treated exactly like a corrupt file.

  **An existing backup is never written over.** A second file arriving at the same
  version -- one restored from elsewhere, or a save directory that two installations have
  taken turns with -- would otherwise have its predecessor's only copy replaced by its
  own, which is the one thing a backup exists to prevent. Each backup is created
  exclusively, under the next free ordinal, and when every ordinal is taken the upgrade
  is refused rather than performed over somebody's recovery copy.

In all three failing cases the game keeps running on defaults and says so once, on screen.
Nothing here raises into the caller, and nothing here can stop a launch: a save file is
not a reason to refuse to play.

Where the files go
------------------
``$BATTLE_CITY_SAVE_DIR`` when it is set, then ``$XDG_DATA_HOME``, then ``%APPDATA%``,
then ``~/.local/share``, each with ``battle-city-reimagined`` under it. The location is
resolved lazily, on the first read or write, and the directory is created only by a write
-- so a launch that changes nothing creates no files and a test can point the whole thing
at a temporary directory by handing the store a different locator.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from battle_city_protocol import JsonValue

from .documents import CorruptSave, Document, FutureSchema, SaveError, decode_document
from .documents import encode_document as encode_save_document
from .migrations import MigrationFailed, MigrationRegistry, MigrationTable, migrate
from .progress import PROGRESS_KIND
from .settings import SETTINGS_KIND

SAVE_DIR_ENV: Final[str] = "BATTLE_CITY_SAVE_DIR"
"""Set this to put the whole profile somewhere else. Honoured before anything else."""

APPLICATION_DIRECTORY: Final[str] = "battle-city-reimagined"
TEMP_SUFFIX: Final[str] = ".tmp"
BACKUP_TEMPLATE: Final[str] = "{name}.v{version}.bak"
MAX_BACKUPS: Final[int] = 9
"""How many backups one document may accumulate at a single schema version.

Finite because a directory that grows without bound is its own kind of failure, and
small because nine untouched copies of one version is already a sign that something
other than an ordinary upgrade is going on. Reaching the limit refuses the upgrade; it
never deletes one of the nine to make room.
"""

KIND_LABELS: Final[dict[str, str]] = {
    SETTINGS_KIND: "SETTINGS",
    PROGRESS_KIND: "CAMPAIGN SAVE",
}
"""How each document is named in a notice the player reads."""

MIGRATIONS: Final[MigrationTable] = {SETTINGS_KIND: {}, PROGRESS_KIND: {}}
"""The shipped migration table: nothing to upgrade from yet. See :mod:`.migrations`."""


def default_save_dir() -> Path:
    """Where this machine keeps the profile, decided from the environment alone.

    No directory is created and nothing is read here; this is arithmetic over strings and
    is safe to call from anywhere, including a test that only wants to know the answer.
    """
    override = os.environ.get(SAVE_DIR_ENV)
    if override:
        return Path(override)
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / APPLICATION_DIRECTORY
    app_data = os.environ.get("APPDATA")
    if app_data:
        return Path(app_data) / APPLICATION_DIRECTORY
    return _home() / ".local" / "share" / APPLICATION_DIRECTORY


@dataclass(frozen=True, slots=True)
class Loaded[T]:
    """The result of reading one document: what was found, what to say, and what may follow.

    ``value`` is ``None`` when nothing usable was read, which covers both "there is no
    file yet" and "the file could not be used" -- ``notice`` tells those apart, being
    empty in the first case. ``writable`` is ``False`` when the file on disk must not be
    replaced, which is how a corrupt or newer save survives the session that met it.
    """

    value: T | None
    notice: str = ""
    writable: bool = True


class ProfileStore:
    """Reads and replaces the documents of one local profile."""

    __slots__ = ("_directory", "_locate", "_migrations")

    def __init__(
        self,
        locate: Callable[[], Path] = default_save_dir,
        *,
        migrations: MigrationTable = MIGRATIONS,
    ) -> None:
        self._locate = locate
        self._directory: Path | None = None
        self._migrations = migrations

    @property
    def directory(self) -> Path:
        """The profile directory, resolved once on first use and remembered."""
        if self._directory is None:
            self._directory = self._locate()
        return self._directory

    def path_for(self, kind: str) -> Path:
        """Where the ``kind`` document lives."""
        return self.directory / f"{kind}.json"

    # -- reading ---------------------------------------------------------------

    def load[T](
        self,
        kind: str,
        *,
        current_version: int,
        parse: Callable[[Mapping[str, JsonValue]], T],
    ) -> Loaded[T]:
        """Read the ``kind`` document, migrating it if it is older. Never raises."""
        path = self.path_for(kind)
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return Loaded(value=None)
        except OSError:
            return Loaded(
                value=None, notice=self._notice(kind, "COULD NOT BE READ"), writable=False
            )

        try:
            document = decode_document(payload, kind=kind, current_version=current_version)
        except FutureSchema as error:
            return Loaded(
                value=None,
                notice=self._notice(kind, f"IS FROM A NEWER BUILD (V{error.found})"),
                writable=False,
            )
        except CorruptSave:
            return Loaded(value=None, notice=self._notice(kind, "IS UNREADABLE"), writable=False)

        if document.schema_version < current_version:
            return self._upgraded(document, path, payload, current_version, parse)
        return self._parsed(kind, document.data, parse)

    def _upgraded[T](
        self,
        document: Document,
        path: Path,
        original: bytes,
        current_version: int,
        parse: Callable[[Mapping[str, JsonValue]], T],
    ) -> Loaded[T]:
        """Migrate an older document, preserving the original before replacing it."""
        kind = document.kind
        steps: MigrationRegistry = self._migrations.get(kind, {})
        try:
            data = migrate(
                document.data,
                from_version=document.schema_version,
                to_version=current_version,
                steps=steps,
            )
        except MigrationFailed:
            return Loaded(
                value=None,
                notice=self._notice(kind, f"V{document.schema_version} CANNOT BE UPGRADED"),
                writable=False,
            )

        loaded = self._parsed(kind, data, parse)
        if loaded.value is None:
            return loaded

        # The upgrade is abandoned rather than performed without a way back, and rather
        # than performed over an existing backup. The migrated document is still
        # returned, so the session runs on it; the file on disk stays at the version its
        # backup would have preserved.
        try:
            backup = _preserve(path, original, document.schema_version)
        except OSError:
            return Loaded(
                value=loaded.value,
                notice=self._notice(kind, "COULD NOT BE BACKED UP BEFORE UPGRADE"),
                writable=False,
            )
        if backup is None:
            return Loaded(
                value=loaded.value,
                notice=self._notice(kind, "HAS TOO MANY BACKUPS TO UPGRADE"),
                writable=False,
            )

        notice = self.save(kind, version=current_version, data=data)
        if notice:
            return Loaded(value=loaded.value, notice=notice, writable=False)
        return Loaded(
            value=loaded.value,
            notice=self._notice(kind, f"UPGRADED FROM V{document.schema_version}"),
        )

    def _parsed[T](
        self,
        kind: str,
        data: Mapping[str, JsonValue],
        parse: Callable[[Mapping[str, JsonValue]], T],
    ) -> Loaded[T]:
        """Turn a decoded payload into a record, or report that it is not one."""
        try:
            return Loaded(value=parse(data))
        except SaveError:
            return Loaded(value=None, notice=self._notice(kind, "IS UNREADABLE"), writable=False)

    # -- writing ---------------------------------------------------------------

    def save(self, kind: str, *, version: int, data: Mapping[str, JsonValue]) -> str:
        """Replace the ``kind`` document atomically. Returns a notice, or ``""`` on success."""
        try:
            payload = encode_save_document(Document(kind=kind, schema_version=version, data=data))
        except SaveError:
            return self._notice(kind, "COULD NOT BE ENCODED")
        try:
            _write_atomically(self.path_for(kind), payload)
        except OSError:
            return self._notice(kind, "COULD NOT BE WRITTEN")
        return ""

    @staticmethod
    def _notice(kind: str, tail: str) -> str:
        return f"{KIND_LABELS.get(kind, kind.upper())} {tail}"


def _preserve(path: Path, original: bytes, version: int) -> Path | None:
    """Write ``original`` to a free backup slot beside ``path``. ``None`` when none is free.

    Each candidate is created exclusively, so the check for a free name and the claim on
    it are one operation and a backup can never land on top of another one -- not when
    two installations share a save directory, and not when a restored file arrives at a
    version that has been backed up before.

    A write that fails for any other reason removes its own partial file and raises, so a
    half-written backup is never mistaken for a copy of anything.
    """
    first = path.with_name(BACKUP_TEMPLATE.format(name=path.name, version=version))
    candidates = (first, *(first.with_name(f"{first.name}.{n}") for n in range(2, MAX_BACKUPS + 1)))
    for candidate in candidates:
        try:
            _write_new_file(candidate, original)
        except FileExistsError:
            continue
        return candidate
    return None


def _write_new_file(path: Path, payload: bytes) -> None:
    """Create ``path`` exclusively and write ``payload`` to it, or raise.

    :class:`FileExistsError` means the name was taken, and is the caller's signal to try
    another one. Any other failure leaves nothing behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except OSError:
        path.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def _write_atomically(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` through a temporary file and :func:`os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + TEMP_SUFFIX)
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    """Flush the directory entry so a completed rename survives a power loss.

    Best effort by design: not every platform or filesystem allows a directory to be
    opened and synced, and a refusal there says nothing about the file, which has already
    been written and replaced.
    """
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        return
    finally:
        os.close(handle)


def _home() -> Path:
    """The user's home directory, or the working directory when there is no telling."""
    expanded = os.path.expanduser("~")
    return Path.cwd() if expanded == "~" else Path(expanded)
