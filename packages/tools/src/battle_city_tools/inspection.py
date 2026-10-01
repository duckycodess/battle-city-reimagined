"""Describe a validated pack or level as text, for a terminal and for a review.

Inspection reports only what the loader already decided. It counts tiles, lists spawns
and names the base cell; it does not re-derive a rule, and it never reports a document it
could not load, because an unloadable document is a validation failure rather than a
thing with properties worth listing.

The output is ordered deterministically -- tiles in vocabulary order, levels in manifest
order, spawns as the loader sorted them -- so two runs on the same pack produce the same
bytes and a report can be diffed.
"""

from __future__ import annotations

from collections import Counter

from battle_city_content import TILE_BY_CHAR, Level, Pack, TileCode


def level_report(level: Level) -> str:
    """A multi-line description of one level."""
    lines = [
        f"level {level.level_id}",
        f"  name            {level.name}",
        f"  schema_version  {level.schema_version}",
        f"  origin          {level.origin}",
        f"  grid            {level.grid.width}x{level.grid.height}",
        f"  base            ({level.base_cell.x}, {level.base_cell.y})",
        "  player spawns   "
        + (
            ", ".join(f"slot {s.slot} ({s.cell.x}, {s.cell.y})" for s in level.player_spawns)
            or "none"
        ),
        "  enemy spawns    "
        + (", ".join(f"({cell.x}, {cell.y})" for cell in level.enemy_spawns) or "none"),
        f"  waves           {_waves(level)}",
        f"  source          {_source(level)}",
        "  tiles",
    ]
    counts = tile_counts(level)
    lines.extend(f"    {tile.name.lower():<14}{counts[tile]}" for tile in TileCode)
    return "\n".join(lines)


def pack_report(pack: Pack) -> str:
    """A multi-line description of a pack and every level in it."""
    lines = [
        f"pack {pack.pack_id} {pack.version}",
        f"  name                    {pack.name}",
        f"  schema_version          {pack.schema_version}",
        f"  content_schema_version  {pack.content_schema_version}",
        f"  origin                  {pack.origin}",
        f"  authors                 {', '.join(pack.authors)}",
        f"  license                 {pack.license.spdx_id}",
        f"  levels                  {len(pack.levels)}",
    ]
    lines.extend(level_report(level) for level in pack.levels)
    return "\n".join(lines)


def tile_counts(level: Level) -> Counter[TileCode]:
    """How many cells hold each tile. Every tile appears, including the absent ones."""
    counts: Counter[TileCode] = Counter(dict.fromkeys(TileCode, 0))
    counts.update(TILE_BY_CHAR[code] for row in level.grid.rows for code in row)
    return counts


def _waves(level: Level) -> str:
    if not level.waves:
        return "none"
    return ", ".join(f"{wave.enemies} enemies" for wave in level.waves)


def _source(level: Level) -> str:
    if level.source is None:
        return "none (authored here)"
    return f"{level.source.repository}@{level.source.revision} {level.source.path}"
