"""The level being edited: a small mutable model, validated by the content loader.

This is the editor's own working copy, and it is deliberately not
``battle_city_tools.LevelDraft``. The architecture specification allows ``client -> sim,
content, protocol``; it does not allow ``client -> tools``, and the client manifest does
not depend on the tools package. Sharing the draft would mean either a dependency the
dependency rules forbid or a shared package that nothing has proposed, so the editor
keeps a thin model of its own and the duplication is accepted and written down here.

The duplication is bounded on purpose. Both models hold the same five things -- rows,
spawns, identity, provenance and schema version -- and both answer the question "is this
valid?" by handing the serialised bytes to :func:`battle_city_content.load_level`. No
validation rule is copied, so the editor, the headless tools and the game cannot disagree
about what a level is; what is copied is the shape of an editable grid, which is small
and visibly the same in both files.

The declared schema version is the one thing this model computes rather than stores.
Painting a conveyor or a teleport pad makes a document a version 2 document, because
those codes do not exist in version 1 and a file claiming version 1 while carrying one
would be refused by every reader including this editor; removing them again does not drag
a document that was *opened* as version 2 back down, because its author chose that
version. See :attr:`EditorDocument.effective_schema_version`.

Beyond that, this model owns what the headless draft has no use for: whether the document
has unsaved changes, which file it was opened from, and which file a save is allowed to
write. The save target is never inferred. An editor that silently wrote back over the
level it opened would be one keystroke away from destroying a bundled stage, so the
target comes from ``--output`` and from nowhere else, an existing file needs ``overwrite``
as well, and the packaged content root is refused either way.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from battle_city_content import (
    CLASSIC_GRID_SIZE,
    GIMMICK_LEVEL_SCHEMA_VERSION,
    LEVEL_SCHEMA_VERSION,
    TILE_BY_CHAR,
    ContentError,
    ContentValidationError,
    GridCell,
    Level,
    LevelSource,
    PlayerSpawn,
    TileCode,
    Wave,
    bundled_content_root,
    load_level,
    uses_gimmick_tiles,
)

BLANK_BASE_CELL: Final[GridCell] = GridCell(x=7, y=15)
BLANK_PLAYER_CELL: Final[GridCell] = GridCell(x=4, y=14)
BLANK_ENEMY_CELL: Final[GridCell] = GridCell(x=0, y=0)
"""A new level opens valid: one base, one player slot, one enemy spawn."""

BUNDLED_ROOT: Final[Path] = bundled_content_root().resolve()
"""Packaged content. The editor never writes inside it, with or without ``overwrite``."""

SCRATCH_PREFIX: Final[str] = "battle-city-editor-"


class EditorRefusal(Exception):
    """The editor declined to write. Not a content problem; a target problem."""


@dataclass(frozen=True, slots=True)
class FieldDiagnostic:
    """One validation failure, located in the document the author is editing."""

    document: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}" if self.field else self.message

    @property
    def headline(self) -> str:
        """A short upper-case summary for the editor's status bar."""
        return (self.field or self.document).upper()


@dataclass(slots=True)
class EditorDocument:
    """A level open in the editor."""

    level_id: str
    name: str
    rows: list[list[str]]
    player_spawns: list[PlayerSpawn] = field(default_factory=list)
    enemy_spawns: list[GridCell] = field(default_factory=list)
    source: LevelSource | None = None
    waves: tuple[Wave, ...] = ()
    schema_version: int = LEVEL_SCHEMA_VERSION
    path: Path | None = None
    opened_from: Path | None = None
    dirty: bool = False

    # -- construction ----------------------------------------------------------

    @classmethod
    def blank(
        cls,
        *,
        level_id: str = "new-level",
        name: str = "New Level",
        size: int = CLASSIC_GRID_SIZE,
    ) -> EditorDocument:
        """A valid empty level: ground everywhere, a base, one player and one enemy."""
        document = cls(
            level_id=level_id,
            name=name,
            rows=[[TileCode.EMPTY.value] * size for _ in range(size)],
            player_spawns=[PlayerSpawn(slot=1, cell=BLANK_PLAYER_CELL)],
            enemy_spawns=[BLANK_ENEMY_CELL],
        )
        document.rows[BLANK_BASE_CELL.y][BLANK_BASE_CELL.x] = TileCode.HOME.value
        return document

    @classmethod
    def from_level(cls, level: Level, *, opened_from: Path | None = None) -> EditorDocument:
        """Open a validated level, keeping its identity, provenance and waves."""
        return cls(
            level_id=level.level_id,
            name=level.name,
            rows=[list(row) for row in level.grid.rows],
            player_spawns=list(level.player_spawns),
            enemy_spawns=list(level.enemy_spawns),
            source=level.source,
            waves=level.waves,
            schema_version=level.schema_version,
            opened_from=opened_from if opened_from is not None else level.origin,
        )

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> EditorDocument:
        """Load ``path`` for editing. Raises :class:`EditorRefusal` when it will not load."""
        source = Path(os.fspath(path))
        try:
            level = load_level(source)
        except ContentError as error:
            raise EditorRefusal(f"{source} cannot be opened: {error}") from error
        return cls.from_level(level, opened_from=source)

    def reopen(self) -> EditorDocument:
        """Re-read the file this document was opened from, discarding unsaved edits."""
        origin = self.opened_from
        if origin is None:
            raise EditorRefusal("this document was not opened from a file")
        reloaded = EditorDocument.open(origin)
        reloaded.path = self.path
        return reloaded

    def rename(self, *, level_id: str | None = None, name: str | None = None) -> bool:
        """Restate the identity. Returns whether anything changed.

        The content specification calls a rename a new identifier rather than an edit, so
        this is as much an unsaved change as painting a cell is: a document renamed and
        then closed has lost work exactly the way a document painted and then closed has.
        Restating the value a document already carries changes nothing and marks nothing.
        """
        changed = False
        if level_id is not None and level_id != self.level_id:
            self.level_id = level_id
            changed = True
        if name is not None and name != self.name:
            self.name = name
            changed = True
        if changed:
            self.dirty = True
        return changed

    # -- grid ------------------------------------------------------------------

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def contains(self, cell: GridCell) -> bool:
        return 0 <= cell.y < self.height and 0 <= cell.x < self.width

    def tile_at(self, cell: GridCell) -> TileCode:
        return TILE_BY_CHAR[self.rows[cell.y][cell.x]]

    def paint(self, cell: GridCell, tile: TileCode) -> bool:
        """Set ``cell`` to ``tile``. Returns whether anything changed."""
        if not self.contains(cell) or self.rows[cell.y][cell.x] == tile.value:
            return False
        self.rows[cell.y][cell.x] = tile.value
        self.dirty = True
        return True

    def grid_rows(self) -> tuple[str, ...]:
        return tuple("".join(row) for row in self.rows)

    @property
    def uses_gimmick_tiles(self) -> bool:
        """Whether any cell holds a code only level schema version 2 defines."""
        return uses_gimmick_tiles(self.grid_rows())

    @property
    def teleport_pads(self) -> tuple[GridCell, ...]:
        """Every teleport pad, in row-major order. A valid level declares zero or two."""
        return tuple(
            GridCell(x, y)
            for y, row in enumerate(self.rows)
            for x, code in enumerate(row)
            if code == TileCode.TELEPORT_PAD.value
        )

    @property
    def effective_schema_version(self) -> int:
        """The version a save would declare: version 2 once gimmick terrain is painted.

        Raised by the content, never lowered by it. A document opened as version 2 stays
        version 2 after its last conveyor is erased, because the version is something its
        author declared and erasing a tile is not a decision to downgrade the file. A
        version 1 document that gains a conveyor has to move, because there is no such
        thing as a valid version 1 level carrying one.
        """
        if self.uses_gimmick_tiles:
            return max(self.schema_version, GIMMICK_LEVEL_SCHEMA_VERSION)
        return self.schema_version

    # -- spawns ----------------------------------------------------------------

    @property
    def player_slots(self) -> tuple[int, ...]:
        return tuple(sorted(spawn.slot for spawn in self.player_spawns))

    def next_free_slot(self) -> int:
        used = set(self.player_slots)
        slot = 1
        while slot in used:
            slot += 1
        return slot

    def player_spawn_at(self, cell: GridCell) -> PlayerSpawn | None:
        for spawn in self.player_spawns:
            if spawn.cell == cell:
                return spawn
        return None

    def place_player_spawn(self, slot: int, cell: GridCell) -> bool:
        """Put ``slot`` on ``cell``, moving it off wherever it was."""
        if not self.contains(cell):
            return False
        if self.player_spawn_at(cell) is not None or cell in self.enemy_spawns:
            return False
        self.player_spawns = sorted(
            [spawn for spawn in self.player_spawns if spawn.slot != slot]
            + [PlayerSpawn(slot=slot, cell=cell)]
        )
        self.dirty = True
        return True

    def place_enemy_spawn(self, cell: GridCell) -> bool:
        """Declare an enemy spawn at ``cell`` if nothing already stands there."""
        if not self.contains(cell):
            return False
        if cell in self.enemy_spawns or self.player_spawn_at(cell) is not None:
            return False
        self.enemy_spawns.append(cell)
        self.dirty = True
        return True

    def remove_spawn_at(self, cell: GridCell) -> bool:
        """Delete whichever spawn stands on ``cell``."""
        spawn = self.player_spawn_at(cell)
        if spawn is not None:
            self.player_spawns = [other for other in self.player_spawns if other.slot != spawn.slot]
            self.dirty = True
            return True
        if cell in self.enemy_spawns:
            self.enemy_spawns.remove(cell)
            self.dirty = True
            return True
        return False

    def spawn_label_at(self, cell: GridCell) -> str | None:
        """What stands on ``cell``: ``P<slot>``, ``E``, or nothing."""
        spawn = self.player_spawn_at(cell)
        if spawn is not None:
            return f"P{spawn.slot}"
        return "E" if cell in self.enemy_spawns else None

    # -- documents -------------------------------------------------------------

    def to_document(self) -> dict[str, object]:
        """The level document, keyed in the order the bundled levels use."""
        document: dict[str, object] = {
            "schema_version": self.effective_schema_version,
            "id": self.level_id,
            "name": self.name,
        }
        if self.source is not None:
            document["source"] = {
                "repository": self.source.repository,
                "revision": self.source.revision,
                "path": self.source.path,
            }
        document["grid"] = {
            "width": self.width,
            "height": self.height,
            "rows": list(self.grid_rows()),
        }
        document["spawns"] = {
            "players": [
                {"slot": spawn.slot, "x": spawn.cell.x, "y": spawn.cell.y}
                for spawn in self.player_spawns
            ],
            "enemies": [{"x": cell.x, "y": cell.y} for cell in self.enemy_spawns],
        }
        if self.waves:
            document["waves"] = [{"enemies": wave.enemies} for wave in self.waves]
        return document

    def serialized(self) -> bytes:
        """The exact bytes a save would write: encoded once, validated and stored as one."""
        text = json.dumps(
            self.to_document(), indent=2, ensure_ascii=False, allow_nan=False, sort_keys=False
        )
        return f"{text}\n".encode()

    # -- validation and saving -------------------------------------------------

    def validate(self) -> FieldDiagnostic | None:
        """Run the content loader over the current document. ``None`` means valid.

        The loader reads a scratch copy and reports the scratch path; every diagnostic is
        remapped to the name the author knows this document by before it is returned, and
        the scratch directory is removed whichever way the check went.
        """
        return self._validate(self.serialized())

    def save(self, *, overwrite: bool = False) -> Path:
        """Validate and write to :attr:`path`, atomically, or refuse and write nothing.

        A save that succeeds records the version it declared, so the document carries the
        same version the file on disk now does. Without that, a version 1 document that
        gained a conveyor would write version 2 and still believe it was version 1:
        erasing the conveyor and saving again would silently write version 1 back, and
        the same edits would produce different files depending on whether the author had
        reopened the file in between. Promoting here is the half of
        :attr:`effective_schema_version` that outlives one edit -- a version is raised by
        the content and then kept, never lowered by erasing what raised it.
        """
        target = self.path
        if target is None:
            raise EditorRefusal("no save target; launch with --output to name one")
        self._require_writable(target, overwrite=overwrite)
        declared = self.effective_schema_version
        payload = self.serialized()
        invalid = self._validate(payload, label=str(target))
        if invalid is not None:
            raise EditorRefusal(f"{target} would be invalid -- {invalid}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, payload)
        self.schema_version = declared
        self.dirty = False
        return target

    @property
    def label(self) -> str:
        """What the header calls this document."""
        if self.path is not None:
            return str(self.path)
        if self.opened_from is not None:
            return f"{self.opened_from} (no --output)"
        return "(unsaved)"

    def _validate(self, payload: bytes, *, label: str | None = None) -> FieldDiagnostic | None:
        document = label if label is not None else self.label
        with tempfile.TemporaryDirectory(prefix=SCRATCH_PREFIX) as scratch:
            staged = Path(scratch) / "level.json"
            staged.write_bytes(payload)
            try:
                load_level(staged)
            except ContentValidationError as error:
                return FieldDiagnostic(document, error.field, error.message)
            except ContentError as error:
                return FieldDiagnostic(document, "", str(error))
        return None

    def _require_writable(self, target: Path, *, overwrite: bool) -> None:
        resolved = target.resolve()
        if resolved == BUNDLED_ROOT or resolved.is_relative_to(BUNDLED_ROOT):
            raise EditorRefusal(
                f"{target} is inside the bundled content root; those levels are the "
                "game's regression fixtures and the editor never writes into them"
            )
        if target.is_dir():
            raise EditorRefusal(f"{target} is a directory, not a level file")
        if target.exists() and not overwrite:
            raise EditorRefusal(f"{target} already exists; relaunch with --overwrite to replace it")


DEFAULT_FILE_MODE: Final[int] = 0o666
"""The mode a new level file is created with, before the process umask narrows it."""

_TEMPORARY_ATTEMPTS: Final[int] = 8
"""How many names to try before giving up; a collision is already astronomically rare."""


def _replaced_file_mode(path: Path) -> int | None:
    """The mode to restore when ``path`` is overwritten, or ``None`` for a fresh file.

    Only a regular file has a mode worth keeping. A symbolic link does not: its own bits
    are meaningless on the platforms the editor runs on, and :func:`os.replace` replaces
    the *link* rather than following it, so the save produces a new regular file whose
    permissions are no more inherited than any other new file's.
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError, NotADirectoryError:
        return None
    if not stat.S_ISREG(status.st_mode):
        return None
    return stat.S_IMODE(status.st_mode)


def _open_exclusive_sibling(path: Path, mode: int) -> tuple[int, Path]:
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
    raise EditorRefusal(f"could not create a temporary file beside {path}")


def _write_atomic(path: Path, payload: bytes) -> None:
    """Replace ``path`` in one step, through a temporary file in the same directory.

    The temporary file must share the destination's directory: :func:`os.replace` cannot
    rename across filesystems, and the system temporary directory very often is one.

    It also has to carry the mode the saved file will end up with, because staging means
    the bytes land in a file this save created rather than in the file the author opened.
    Saving over an existing level restores that level's mode -- a stage file is created
    ``0o600`` and widened once, so the window before :func:`os.fchmod` is narrower than
    the finished file rather than wider -- and a new level is created ``0o666`` for the
    kernel to narrow by the process umask, which is the mode an ordinary :func:`open`
    would have produced. The umask itself is never read or changed: it is process-wide
    state, and the editor shares the process with everything else the client is doing.
    """
    preserved = _replaced_file_mode(path)
    handle, temporary = _open_exclusive_sibling(
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
