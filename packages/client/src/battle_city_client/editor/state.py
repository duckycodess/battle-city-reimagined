"""What the editor is doing, with no pygame in it.

:class:`EditorState` is to the editor what ``ClientShell`` is to the game: everything the
window is *about* -- the open document, the selected tile, the active tool, which player
slot the next spawn belongs to, the last validation result and the line in the status bar
-- with none of the window itself. The pygame layer feeds it clicks and keys and draws
it; the renderer only reads it. Keeping it here is what lets paint, spawn editing,
validation and the dirty flag be tested without opening a display.

Validation is never implicit. The state records the result of the last explicit check and
says so, because an editor that silently revalidated on every keystroke would report an
error for a level the author is halfway through fixing, and one that never revalidated
would show a stale verdict after an edit. Editing therefore *clears* the verdict rather
than recomputing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from battle_city_content import GridCell, TileCode

from .document import EditorDocument, EditorRefusal, FieldDiagnostic
from .layout import PALETTE_TILES


class Tool(Enum):
    """What a click on the field does."""

    PAINT = "paint"
    PLAYER_SPAWN = "player_spawn"
    ENEMY_SPAWN = "enemy_spawn"
    DELETE_SPAWN = "delete_spawn"


TOOL_LABELS: Final[dict[Tool, str]] = {
    Tool.PAINT: "PAINT",
    Tool.PLAYER_SPAWN: "PLAYER",
    Tool.ENEMY_SPAWN: "ENEMY",
    Tool.DELETE_SPAWN: "DELETE",
}

_GRID_CELL_FIELD: Final[re.Pattern[str]] = re.compile(r"grid\.rows\[(?P<y>\d+)\]\[(?P<x>\d+)\]")
"""The loader's field path for one offending cell, which the editor can point at."""


def error_cell(diagnostic: FieldDiagnostic | None) -> GridCell | None:
    """The cell a diagnostic blames, when it blames exactly one."""
    if diagnostic is None:
        return None
    match = _GRID_CELL_FIELD.fullmatch(diagnostic.field)
    if match is None:
        return None
    return GridCell(x=int(match.group("x")), y=int(match.group("y")))


@dataclass(slots=True)
class EditorState:
    """The whole editor, minus the window."""

    document: EditorDocument
    tile_index: int = 0
    tool: Tool = Tool.PAINT
    active_slot: int = 1
    hover: GridCell | None = None
    diagnostic: FieldDiagnostic | None = None
    checked: bool = False
    status: str = "READY"
    status_is_error: bool = False
    overwrite: bool = False
    quit_armed: bool = False
    running: bool = True

    # -- queries ---------------------------------------------------------------

    @property
    def selected_tile(self) -> TileCode:
        """The tile the paint tool lays down."""
        return PALETTE_TILES[self.tile_index]

    @property
    def valid(self) -> bool:
        """Whether the last explicit check passed. Meaningless unless :attr:`checked`."""
        return self.checked and self.diagnostic is None

    # -- selection -------------------------------------------------------------

    def select_tile(self, index: int) -> None:
        """Choose a palette entry. An index outside the palette is ignored."""
        if 0 <= index < len(PALETTE_TILES):
            self.tile_index = index
            self.quit_armed = False

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        self.note(f"TOOL {TOOL_LABELS[tool]}")

    def cycle_slot(self) -> None:
        """Move to the next declared player slot, then to the next free one, and wrap."""
        slots = [*self.document.player_slots, self.document.next_free_slot()]
        ordered = sorted(set(slots))
        following = [slot for slot in ordered if slot > self.active_slot]
        self.active_slot = following[0] if following else ordered[0]
        self.note(f"SLOT {self.active_slot}")

    # -- editing ---------------------------------------------------------------

    def apply_at(self, cell: GridCell) -> bool:
        """Apply the active tool to ``cell``. Returns whether the document changed."""
        self.quit_armed = False
        changed = self._apply(cell)
        if changed:
            self.checked = False
            self.diagnostic = None
        return changed

    def _apply(self, cell: GridCell) -> bool:
        match self.tool:
            case Tool.PAINT:
                return self.document.paint(cell, self.selected_tile)
            case Tool.PLAYER_SPAWN:
                placed = self.document.place_player_spawn(self.active_slot, cell)
                if not placed:
                    self.note("CELL ALREADY HOLDS A SPAWN", error=True)
                return placed
            case Tool.ENEMY_SPAWN:
                placed = self.document.place_enemy_spawn(cell)
                if not placed:
                    self.note("CELL ALREADY HOLDS A SPAWN", error=True)
                return placed
            case Tool.DELETE_SPAWN:
                removed = self.document.remove_spawn_at(cell)
                if not removed:
                    self.note("NO SPAWN HERE", error=True)
                return removed

    # -- commands --------------------------------------------------------------

    def check(self) -> FieldDiagnostic | None:
        """Validate the document through the content loader and record the verdict."""
        self.diagnostic = self.document.validate()
        self.checked = True
        if self.diagnostic is None:
            self.note("CHECKED")
        else:
            self.note(str(self.diagnostic).upper(), error=True)
        return self.diagnostic

    def save(self) -> bool:
        """Validate and write to the explicit save target. Returns whether it was written."""
        try:
            path = self.document.save(overwrite=self.overwrite)
        except EditorRefusal as refusal:
            self.note(str(refusal).upper(), error=True)
            return False
        self.diagnostic = None
        self.checked = True
        self.note(f"SAVED {path}".upper())
        return True

    def reload(self) -> bool:
        """Re-read the opened file, discarding edits. Returns whether it was re-read."""
        try:
            self.document = self.document.reopen()
        except EditorRefusal as refusal:
            self.note(str(refusal).upper(), error=True)
            return False
        self.checked = False
        self.diagnostic = None
        self.note("RELOADED")
        return True

    def quit(self) -> None:
        """Stop after this frame, once unsaved work has been flagged exactly once.

        Discarding an unsaved level on a single keystroke is the one mistake an editor
        cannot apologise for, so the first request arms the second. Anything else the
        author does clears the arming, because a confirmation that outlives the question
        is not a confirmation.
        """
        if self.document.dirty and not self.quit_armed:
            self.note("UNSAVED CHANGES - PRESS ESC AGAIN TO DISCARD", error=True)
            self.quit_armed = True
            return
        self.running = False

    def note(self, message: str, *, error: bool = False) -> None:
        """Put one line in the status bar, and forget any pending quit confirmation."""
        self.status = message
        self.status_is_error = error
        self.quit_armed = False
