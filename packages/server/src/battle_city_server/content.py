"""Mapping loaded content onto a simulation stage and a protocol content reference.

The content package deliberately does not build a :class:`~battle_city_sim.Stage`: it
may not depend on the simulation. The protocol package deliberately does not know what
a level is: it may not depend on content. The server depends on both, so the two
mappings live here, and they are the only place the three vocabularies meet.

The mapping is mechanical because the content loader already enforces the same stage
contract the simulation does — a 16x16 grid, exactly one home base, spawns on empty
ground, unique slots — so a level that loaded will build a stage. A stage that is
refused anyway is a genuine disagreement between the two contracts and is reported as
such rather than repaired here.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Final

from battle_city_content import Level, Pack
from battle_city_protocol import ContentRef
from battle_city_sim import DEFAULT_RULES, GridPos, PlayerSpawn, Rules, Stage

RULES_DIGEST_LABEL: Final[bytes] = b"battle-city-rules-v1"
"""Domain separator for the rules digest, so the digest names what it fingerprints."""


def stage_from_level(level: Level) -> Stage:
    """Build the simulation stage a :class:`~battle_city_content.Level` describes."""
    return Stage.create(
        stage_id=level.level_id,
        name=level.name,
        rows=level.grid.rows,
        player_spawns=[
            PlayerSpawn(slot=spawn.slot, cell=GridPos(spawn.cell.x, spawn.cell.y))
            for spawn in level.player_spawns
        ],
        enemy_spawns=[GridPos(cell.x, cell.y) for cell in level.enemy_spawns],
    )


def content_ref_for(pack: Pack, level: Level) -> ContentRef:
    """Describe ``level`` inside ``pack`` as the reference peers compare on.

    Compatibility is an equality check on this record, so it names the pack, the pack's
    version, the level and the level schema version rather than a hash of the bytes: a
    mismatch tells a player which of those to fix.
    """
    return ContentRef(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        level_id=level.level_id,
        content_schema_version=level.schema_version,
    )


def rules_digest(rules: Rules = DEFAULT_RULES) -> str:
    """Return a stable digest of the rule constants a session runs.

    Two peers that step with different rules diverge on the first tick that touches the
    difference, which is a miserable bug to find from a desynchronised snapshot. The
    digest puts the disagreement on the wire at join time instead.

    It is computed from the field names and values of :class:`~battle_city_sim.Rules`,
    in declaration order, so adding a rule changes the digest and renaming one does too.
    """
    parts = [RULES_DIGEST_LABEL]
    for field in dataclasses.fields(rules):
        value = getattr(rules, field.name)
        parts.append(f"{field.name}={value}".encode())
    return hashlib.sha256(b"\x00".join(parts)).hexdigest()
