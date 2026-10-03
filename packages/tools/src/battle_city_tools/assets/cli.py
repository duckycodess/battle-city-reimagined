"""``python -m battle_city_tools.assets``: build, validate and describe a sprite pack.

A separate entry point from ``python -m battle_city_tools`` on purpose. That command owns
level content and its exit statuses mean things about level documents; this one owns
pixels. Keeping them apart means neither grows a subcommand that only makes sense for the
other, and it leaves the content tool's surface untouched by this change.

The statuses match the content tool so a script can treat them the same way: ``0``
success, ``1`` an artifact is invalid and the message names the file and the field, ``3``
a refusal, and argparse's own ``2`` for a usage error.

Nothing here opens a window, calls Blender, or touches the network. ``build`` reads a
directory of renders that Blender already produced; ``validate`` reads only the checked-in
files, which is what lets continuous integration check the art without a GPU.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from .build import COMPOSITE_LEVEL, build_pack
from .errors import AssetInvalid, AssetRefusal
from .metadata import METADATA_FILENAME, load
from .validation import validate_directory

PROGRAM: Final[str] = "battle_city_tools.assets"
DEFAULT_PACK: Final[Path] = Path("assets/sprites/starter")

EXIT_OK: Final[int] = 0
EXIT_INVALID: Final[int] = 1
EXIT_REFUSED: Final[int] = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command. Returns a process exit status."""
    options = parse_args(argv)
    try:
        return _dispatch(options)
    except AssetInvalid as error:
        print(f"{PROGRAM}: {error.diagnostic}", file=sys.stderr)
        return EXIT_INVALID
    except AssetRefusal as error:
        print(f"{PROGRAM}: refused: {error}", file=sys.stderr)
        return EXIT_REFUSED


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse a command line."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Pack, validate and describe Battle City Reimagined sprite atlases.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="pack rendered frames into the tracked artifacts")
    build.add_argument(
        "--frames", type=Path, required=True, help="directory of renders and render.json"
    )
    build.add_argument("--output", type=Path, required=True, help="directory to write into")
    build.add_argument(
        "--level",
        default=COMPOSITE_LEVEL,
        help=f"bundled level the stage composite is drawn from (default {COMPOSITE_LEVEL})",
    )

    validate = commands.add_parser("validate", help="check a packed atlas against its sidecar")
    _add_pack(validate)
    validate.add_argument(
        "--ignore-catalog",
        action="store_true",
        help="skip the check that the sidecar still agrees with the shipped catalogue",
    )

    describe = commands.add_parser("describe", help="print a deterministic report of a pack")
    _add_pack(describe)

    return parser.parse_args(argv)


def _add_pack(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pack",
        type=Path,
        default=DEFAULT_PACK,
        help=f"directory holding {METADATA_FILENAME} (default {DEFAULT_PACK})",
    )


def _dispatch(options: argparse.Namespace) -> int:
    if options.command == "build":
        report = build_pack(options.frames, options.output, level_id=options.level)
        print(report.summary())
        return EXIT_OK
    if options.command == "validate":
        outcome = validate_directory(options.pack, require_catalog=not options.ignore_catalog)
        print(outcome.summary(), file=sys.stdout if outcome.ok else sys.stderr)
        return EXIT_OK if outcome.ok else EXIT_INVALID
    return _describe(options.pack)


def _describe(pack: Path) -> int:
    sidecar = pack / METADATA_FILENAME
    metadata = load(sidecar.read_bytes(), artifact=str(sidecar))
    width, height = metadata.frame_size
    lines = [
        f"pack {metadata.pack_id} schema {metadata.schema_version}",
        f"atlas {metadata.atlas.path} {metadata.atlas.width}x{metadata.atlas.height}",
        f"frames {len(metadata.frames)} of {width}x{height}",
        f"palette {len(metadata.palette)} colours",
        f"blender {metadata.render['blender.version']} "
        f"build {metadata.render['blender.build_hash']}",
        f"engine {metadata.render['engine']} on {metadata.render['cycles.device']} "
        f"at {metadata.render['cycles.samples']} samples, seed {metadata.render['cycles.seed']}",
        "",
        "frames:",
    ]
    lines.extend(
        f"  {frame.name:<30} {frame.group:<10} rect {list(frame.rect)} "
        f"pivot {list(frame.pivot)} hitbox {frame.hitbox}"
        for frame in metadata.frames
    )
    lines.append("")
    lines.append("animations:")
    lines.extend(
        f"  {animation.name:<12} {'loop ' if animation.loop else 'once '}"
        f"{', '.join(animation.frames)}"
        for animation in metadata.animations
    )
    lines.append("")
    lines.append("states:")
    lines.extend(f"  {state:<18} {frame}" for state, frame in sorted(metadata.states.items()))
    print("\n".join(lines))
    return EXIT_OK
