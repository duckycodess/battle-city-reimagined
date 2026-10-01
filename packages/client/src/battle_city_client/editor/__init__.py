"""The 16x16 level editor: paint terrain, place spawns, validate, save.

Launch it
---------
From the repository root::

    uv run --locked --package battle-city-client python -m battle_city_client.editor \
        --level packages/content/src/battle_city_content/levels/classic-01.json \
        --output /tmp/my-level.json

``--output`` is what makes a save possible, and it is never inferred. An editor that
wrote back over whatever it opened would be one keystroke from destroying a bundled
stage, so the save target is named on the command line, an existing target also needs
``--overwrite``, and the packaged content root is refused either way.

With no ``--level`` the editor opens a blank, already-valid 16x16 field: ground
everywhere, a home base at the bottom centre, one player slot and one enemy spawn, which
is the smallest document the content schema accepts.

Controls
--------
========================  ====================================================
Left click / drag         apply the active tool to a cell
Left click on a swatch    select that tile
``0``-``8``               select the tile with that code
``B`` ``P`` ``E`` ``X``   paint, place player spawn, place enemy spawn, delete
``TAB``                   next player slot
``V``                     validate through the content loader
``S``                     save to ``--output``
``R``                     re-read the opened file; unsaved edits ask twice
``-`` ``+``               window scale
``ESC``                   quit; an unsaved document asks twice
========================  ====================================================

Why it does not use ``battle_city_tools``
-----------------------------------------
The architecture specification allows ``client -> sim, content, protocol`` and does not
allow ``client -> tools``; the client manifest declares no such dependency. The editor
therefore keeps its own small document model in :mod:`~battle_city_client.editor.document`
rather than importing ``battle_city_tools.LevelDraft``, and that duplication is written
down there instead of being hidden. No *validation* is duplicated: both models answer
"is this valid?" by handing the serialised bytes to
:func:`battle_city_content.load_level`, so the editor, the headless tools and the game
cannot disagree about what a level is.

Layout
------
``document``  the editable level, its dirty flag, and validated atomic saving
``layout``    where everything sits, and the mouse arithmetic, as pure functions
``state``     the selected tile, the active tool, the slot, and the last verdict
``render``    draws a state into the editor's logical frame
``app``       the pygame loop, the window, and the only module that touches a device

Importing this package does not import pygame: the two modules that need it are resolved
on first access, exactly as the client package does it and for the same reason.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any, Final

from .document import EditorDocument, EditorRefusal, FieldDiagnostic
from .layout import LOGICAL_SIZE, PALETTE_TILES, cell_at, logical_point, swatch_at
from .state import TOOL_LABELS, EditorState, Tool

if TYPE_CHECKING:
    from .app import EditorApp, build_app, main
    from .render import EditorRenderer

_LAZY_EXPORTS: Final[dict[str, str]] = {
    "EditorApp": ".app",
    "EditorRenderer": ".render",
    "build_app": ".app",
    "main": ".app",
}
"""Names whose module imports pygame, resolved on first use rather than on import."""


def __getattr__(name: str) -> Any:
    """Resolve a pygame-backed export on first access. See :data:`_LAZY_EXPORTS`."""
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "LOGICAL_SIZE",
    "PALETTE_TILES",
    "TOOL_LABELS",
    "EditorApp",
    "EditorDocument",
    "EditorRefusal",
    "EditorRenderer",
    "EditorState",
    "FieldDiagnostic",
    "Tool",
    "build_app",
    "cell_at",
    "logical_point",
    "main",
    "swatch_at",
]
