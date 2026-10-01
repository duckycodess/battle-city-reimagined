"""Headless content tools: validate, inspect, export and author level packs.

These tools are the content pipeline's command line and the authoring model behind it.
They never execute level data, never draw anything and never open a window:
``python -m battle_city_tools`` runs the same checks in a terminal, in CI and inside an
editor, and the package imports nothing but the standard library and
:mod:`battle_city_content`.

Validation is not re-implemented here. A document is serialised once, written to a
scratch file, and handed to the content loader; whatever the loader says is what these
tools report, remapped to the file the author is editing rather than the scratch copy.
That is the whole reason the editor and the game cannot disagree about what a valid level
is.

Using it
--------

Run a command with ``uv run --locked --package battle-city-tools python -m
battle_city_tools``, then one of::

    validate --bundled
    inspect --level levels/my-level.json
    export --bundled --output out/
    create --id my-level --name "My Level" --output levels/my-level.json

Exit status is ``0`` for success, ``1`` for an invalid document, ``3`` for a refusal and
``2`` for a usage error.

In Python::

    from battle_city_tools import LevelDraft, read_level_draft, save_level

    draft = read_level_draft("levels/classic-01.json")
    draft.paint(GridCell(3, 3), TileCode.BRICK)
    save_level(draft, "levels/mine.json")

Safety rules these tools keep
-----------------------------

* The bytes that are validated are the bytes that are written.
* Nothing is ever written inside the bundled content root, with or without ``overwrite``.
* Nothing is overwritten unless the caller named the target *and* passed ``overwrite``.
* A pack export is staged and loaded in full before anything is moved into place, and it
  refuses a destination that holds the pack's own source files.

Layout
------
``serialization``  the document-to-bytes encoding, done exactly once
``draft``          :class:`~battle_city_tools.draft.LevelDraft`, the editable level
``errors``         :class:`Diagnostic`, and the invalid-versus-refused distinction
``validation``     the scratch-file round trip through the content loader
``storage``        validated, atomic single-level writes
``export``         staged, validated, all-or-nothing pack export
``inspection``     deterministic text reports
``cli``            the argument parser and the commands
"""

from .draft import LevelDraft
from .errors import Diagnostic, DocumentInvalid, ToolRefusal, ToolsError
from .export import ExportReport, export_pack, level_filename, pack_document
from .inspection import level_report, pack_report, tile_counts
from .serialization import JsonValue, serialize_document
from .storage import BUNDLED_ROOT, SaveReport, read_level_draft, save_level, write_atomic
from .validation import validate_level_bytes

__all__ = [
    "BUNDLED_ROOT",
    "Diagnostic",
    "DocumentInvalid",
    "ExportReport",
    "JsonValue",
    "LevelDraft",
    "SaveReport",
    "ToolRefusal",
    "ToolsError",
    "export_pack",
    "level_filename",
    "level_report",
    "pack_document",
    "pack_report",
    "read_level_draft",
    "save_level",
    "serialize_document",
    "tile_counts",
    "validate_level_bytes",
    "write_atomic",
]
