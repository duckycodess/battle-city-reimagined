"""Write a level the way the persistence specification asks: validated, then atomic.

Three rules, and each one exists because of a way an authoring tool can destroy work.

* **The bytes that passed are the bytes that land.** A document is serialised once; that
  payload is validated and that same payload is written. Re-encoding between the two
  steps would put a file on disk that nothing ever checked.
* **The write is atomic.** The payload goes to a temporary file *in the destination
  directory* -- not in the system temporary directory, which is often another filesystem
  where :func:`os.replace` cannot rename -- and replaces the target in one step. A
  crash leaves either the old file or the new one, never half of either.
* **The target is always explicit.** Nothing is written over unless the caller named
  that file and said ``overwrite``, and nothing at all is written inside the bundled
  content root, which is the game's own regression fixtures and belongs to the content
  package rather than to an author.
* **A replaced file keeps its permissions.** Staging through a temporary file means the
  bytes land in a file this write *created*, so its mode is this write's responsibility.
  Overwriting restores the mode the target already had; a new file gets the mode any
  other tool would have given it, ``0o666`` as narrowed by the process umask. Neither
  path reads or changes the umask, which is process-wide state another thread may be
  relying on.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from battle_city_content import ContentError, Level, bundled_content_root, load_level

from .draft import LevelDraft
from .errors import Diagnostic, DocumentInvalid, ToolRefusal
from .serialization import serialize_document
from .validation import validate_level_bytes

BUNDLED_ROOT: Final[Path] = bundled_content_root().resolve()
"""The packaged levels, packs and schemas. These tools never write anything inside it."""


@dataclass(frozen=True, slots=True)
class SaveReport:
    """What a completed save did, for a caller that wants to say so."""

    path: Path
    level_id: str
    bytes_written: int


def read_level_draft(path: str | os.PathLike[str]) -> LevelDraft:
    """Load a level file and open it for editing.

    Raises :class:`DocumentInvalid` rather than the loader's own error, so a tool has one
    failure type to report whether the document came off disk or out of an editor.
    """
    source = Path(os.fspath(path))
    try:
        level = load_level(source)
    except ContentError as error:
        raise DocumentInvalid(Diagnostic(str(source), "", str(error))) from error
    return LevelDraft.from_level(level)


def save_level(
    draft: LevelDraft,
    target: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> SaveReport:
    """Validate ``draft`` and write it to ``target``, or refuse and write nothing."""
    destination = Path(os.fspath(target))
    require_writable_file(destination, overwrite=overwrite)
    payload = serialize_document(draft.to_document())
    level: Level = validate_level_bytes(payload, document=str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(destination, payload)
    return SaveReport(path=destination, level_id=level.level_id, bytes_written=len(payload))


def require_outside_bundled_content(target: Path) -> None:
    """Refuse a path inside the packaged content, whatever flags were passed.

    ``overwrite`` does not unlock this. The bundled pack is the regression fixture the
    simulation tests compare against; a tool that can rewrite it can make the suite agree
    with a mistake. Resolving first is what makes the check hold for a symlink that
    points into the package from somewhere else.
    """
    resolved = target.resolve()
    if resolved == BUNDLED_ROOT or resolved.is_relative_to(BUNDLED_ROOT):
        raise ToolRefusal(
            f"{target} resolves to {resolved}, inside the bundled content root "
            f"{BUNDLED_ROOT}; the packaged pack is a regression fixture and these tools "
            "never write into it. Export to a directory of your own instead."
        )


def require_writable_file(target: Path, *, overwrite: bool) -> None:
    """Refuse a target these tools must not write, before anything is encoded."""
    require_outside_bundled_content(target)
    if target.is_dir():
        raise ToolRefusal(f"{target} is a directory, not a level file")
    if target.exists() and not overwrite:
        raise ToolRefusal(
            f"{target} already exists; pass --overwrite to replace it, or name another file"
        )


DEFAULT_FILE_MODE: Final[int] = 0o666
"""The mode a new level file is created with, before the process umask narrows it."""

_TEMPORARY_ATTEMPTS: Final[int] = 8
"""How many names to try before giving up; a collision is already astronomically rare."""


def replaced_file_mode(path: Path) -> int | None:
    """The mode to restore when ``path`` is overwritten, or ``None`` for a fresh file.

    Only a regular file has a mode worth keeping. A symbolic link does not: its own bits
    are meaningless on the platforms these tools run on, and :func:`os.replace` replaces
    the *link* rather than following it, so the write produces a new regular file whose
    permissions are no more inherited than any other new file's.
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError, NotADirectoryError:
        return None
    if not stat.S_ISREG(status.st_mode):
        return None
    return stat.S_IMODE(status.st_mode)


def open_exclusive_sibling(path: Path, mode: int) -> tuple[int, Path]:
    """Create a new hidden file beside ``path`` and return its descriptor and name.

    ``O_EXCL`` is what makes this safe without a lock: the file is created by this call
    or not at all, so a name another writer happens to hold is a retry rather than a
    file two writers share.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(_TEMPORARY_ATTEMPTS):
        candidate = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
        try:
            return os.open(candidate, flags, mode), candidate
        except FileExistsError:
            continue
    raise OSError(f"could not create a temporary file beside {path}")


def write_atomic(path: Path, payload: bytes) -> None:
    """Replace ``path`` with ``payload`` in one step, leaving no partial file behind.

    The temporary file is a sibling of the destination -- not in the system temporary
    directory, which is often another filesystem where :func:`os.replace` cannot rename
    -- and it carries the mode the destination will end up with before it carries any
    bytes. A file being replaced is staged ``0o600`` and widened to the mode it is
    taking over, so the window before :func:`os.fchmod` is narrower than the finished
    file rather than wider; a new file is created ``0o666`` and the kernel applies the
    umask, which is the mode an ordinary :func:`open` would have produced.
    """
    preserved = replaced_file_mode(path)
    handle, temporary = open_exclusive_sibling(
        path, 0o600 if preserved is not None else DEFAULT_FILE_MODE
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            if preserved is not None:
                os.fchmod(stream.fileno(), preserved)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


__all__ = [
    "BUNDLED_ROOT",
    "DEFAULT_FILE_MODE",
    "Diagnostic",
    "DocumentInvalid",
    "SaveReport",
    "ToolRefusal",
    "open_exclusive_sibling",
    "read_level_draft",
    "require_outside_bundled_content",
    "replaced_file_mode",
    "require_writable_file",
    "save_level",
    "write_atomic",
]
