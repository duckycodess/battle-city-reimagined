"""``python -m battle_city_tools``: validate, inspect, export, create.

Headless by construction. Nothing in this package imports pygame, reads a clock or opens
a socket; a command reads content files, writes content files, and prints text. That is
what lets the same checks run in CI and inside an editor.

The exit status distinguishes the two failures an author cares about. ``1`` means a
document is invalid and the message names the file and the field. ``3`` means the tool
*refused* -- an existing file nobody asked to replace, a destination inside the bundled
pack -- which is not a content problem and should not be answered by passing a flag
until it goes away. Usage errors keep argparse's own ``2``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from battle_city_content import (
    ContentError,
    Level,
    Pack,
    load_bundled_pack,
    load_level,
    load_pack,
)

from .draft import LevelDraft
from .errors import DocumentInvalid, ToolRefusal
from .export import export_pack
from .inspection import level_report, pack_report
from .storage import save_level

PROGRAM: Final[str] = "battle_city_tools"

EXIT_OK: Final[int] = 0
EXIT_INVALID: Final[int] = 1
EXIT_REFUSED: Final[int] = 3
"""Kept distinct from argparse's usage status of 2, and from a validation failure."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command. Returns a process exit status."""
    options = parse_args(argv)
    try:
        return _dispatch(options)
    except DocumentInvalid as error:
        print(f"{PROGRAM}: {error.diagnostic}", file=sys.stderr)
        return EXIT_INVALID
    except ToolRefusal as error:
        print(f"{PROGRAM}: refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except ContentError as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return EXIT_INVALID


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse a command line."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Validate, inspect, export and create Battle City Reimagined content.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="load a pack or level and report the result")
    _add_source(validate, allow_level=True)

    inspect = commands.add_parser("inspect", help="describe a validated pack or level")
    _add_source(inspect, allow_level=True)

    export = commands.add_parser("export", help="write a pack to a stand-alone directory")
    _add_source(export, allow_level=False)
    export.add_argument("--output", type=Path, required=True, help="destination directory")
    export.add_argument(
        "--overwrite", action="store_true", help="replace the destination if it exists"
    )

    create = commands.add_parser("create", help="write a new blank level")
    create.add_argument("--id", dest="level_id", required=True, help="stable level identifier")
    create.add_argument("--name", required=True, help="display name")
    create.add_argument("--output", type=Path, required=True, help="level file to write")
    create.add_argument("--overwrite", action="store_true", help="replace the file if it exists")

    return parser.parse_args(argv)


def _add_source(parser: argparse.ArgumentParser, *, allow_level: bool) -> None:
    """Add the mutually exclusive content source. The target is always explicit."""
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pack", type=Path, help="pack manifest to read")
    source.add_argument(
        "--bundled", action="store_true", help="the classic pack shipped with the game"
    )
    if allow_level:
        source.add_argument("--level", type=Path, help="single level file to read")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="pack root for --pack; defaults to the manifest's own directory",
    )


def _dispatch(options: argparse.Namespace) -> int:
    match options.command:
        case "validate":
            return _validate(options)
        case "inspect":
            return _inspect(options)
        case "export":
            return _export(options)
        case _:
            return _create(options)


def _validate(options: argparse.Namespace) -> int:
    loaded = _load(options)
    if isinstance(loaded, Pack):
        print(f"ok: pack {loaded.pack_id} {loaded.version}, {len(loaded.levels)} levels")
    else:
        print(f"ok: level {loaded.level_id}")
    return EXIT_OK


def _inspect(options: argparse.Namespace) -> int:
    loaded = _load(options)
    print(pack_report(loaded) if isinstance(loaded, Pack) else level_report(loaded))
    return EXIT_OK


def _export(options: argparse.Namespace) -> int:
    loaded = _load(options)
    if not isinstance(loaded, Pack):
        raise ToolRefusal("export needs a pack; --level exports nothing on its own")
    report = export_pack(loaded, options.output, overwrite=options.overwrite)
    print(
        f"exported {loaded.pack_id} to {report.destination}: "
        f"{len(report.levels)} levels, {report.bytes_written} bytes"
    )
    return EXIT_OK


def _create(options: argparse.Namespace) -> int:
    draft = LevelDraft.blank(level_id=options.level_id, name=options.name)
    report = save_level(draft, options.output, overwrite=options.overwrite)
    print(f"created {report.level_id} at {report.path} ({report.bytes_written} bytes)")
    return EXIT_OK


def _load(options: argparse.Namespace) -> Pack | Level:
    """Load whichever source the command named."""
    if options.bundled:
        return load_bundled_pack()
    if getattr(options, "level", None) is not None:
        return load_level(options.level)
    return load_pack(options.pack, root=options.root)
