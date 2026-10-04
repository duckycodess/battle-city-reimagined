"""Mapping loaded content onto a simulation stage and a protocol content reference.

The content package deliberately does not build a :class:`~battle_city_sim.Stage`: it
may not depend on the simulation. The protocol package deliberately does not know what
a level is: it may not depend on content. The server depends on both, so the two
mappings live here, and they are the only place the three vocabularies meet.

The mapping is mechanical because the content loader already enforces the same stage
contract the simulation does — a 16x16 grid, exactly one home base, spawns on empty
ground, unique slots, zero or exactly two teleport pads — so a level that loaded will
build a stage. A stage that is refused anyway is a genuine disagreement between the two
contracts and is reported as such rather than repaired here.

One thing this module does decide: whether a stage's terrain may be *spoken* in the
content version a session negotiated. A keyframe carries tile codes and
``ContentRef.content_schema_version`` is what tells a client how to read them, so a
session that ran gimmick terrain while claiming version 1 would be sending a client
characters its own build is right to refuse. :func:`require_speakable_terrain` refuses
that session before anyone connects; see :func:`battle_city_server.config.SessionConfig`.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Sequence
from typing import Final

from battle_city_content import (
    SUPPORTED_LEVEL_SCHEMA_VERSIONS,
    Level,
    Pack,
    tile_codes_for,
)
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


class UnspeakableTerrainError(ValueError):
    """A stage carries tile codes the session's content version does not define.

    Not a client error and not a content error: the level loaded, the stage built, and
    the two were then paired with a content reference that disagrees with them. It is
    raised while a session is being described rather than while it is running, because a
    keyframe is not the place to discover that a client cannot read the board.
    """


def require_speakable_terrain(rows: Sequence[str], content_schema_version: int) -> None:
    """Refuse terrain a peer agreeing to ``content_schema_version`` could not read.

    The agreed content version is the sole compatibility signal for the opt-in gimmick
    terrain -- the wire version, the snapshot version and the canonical state version are
    all unchanged -- so it is also the only thing that says which row characters a
    keyframe may carry. Checking once, here, means the per-tick snapshot path stays a
    translation and nothing re-validates terrain that cannot change.
    """
    if content_schema_version not in SUPPORTED_LEVEL_SCHEMA_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_LEVEL_SCHEMA_VERSIONS))
        raise UnspeakableTerrainError(
            f"content schema version {content_schema_version} is not supported; "
            f"this build speaks {supported}"
        )
    allowed = tile_codes_for(content_schema_version)
    for y, row in enumerate(rows):
        for x, code in enumerate(row):
            if code not in allowed:
                raise UnspeakableTerrainError(
                    f"grid.rows[{y}][{x}] is tile code {code!r}, which content schema "
                    f"version {content_schema_version} does not define"
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
