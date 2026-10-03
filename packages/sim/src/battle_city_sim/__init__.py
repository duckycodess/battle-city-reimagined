"""Headless deterministic game rules. Keep presentation and I/O outside this package.

``battle_city_sim`` is the shared rules engine for the client, the authoritative server,
bots, and replay tooling. It is standard library only and depends on no other project
package.

Contract
--------
The simulation is a pure state transition::

    result = step(state, tick_input, rules)
    result.state, result.events

* State is immutable. Every collection is a tuple in ascending entity-identifier order.
* Time is an integer tick counter. Nothing reads a clock, a frame rate or a duration.
* Randomness is :class:`~battle_city_sim.rng.Rng`, a seeded, versioned, project-owned
  generator carried inside the state. :mod:`random` is never imported.
* Identical initial state, rules and ordered tick inputs produce identical canonical
  bytes and identical :func:`~battle_city_sim.codec.state_hash` digests.
* Nothing here opens a file, a socket, a display or an audio device, and nothing reads
  the environment.

Start at :func:`new_game` to build a tick-zero state, :func:`step` to advance it, and
:func:`run_ticks` to replay a recorded sequence.

Historical source and the ambiguities it left
---------------------------------------------
The gameplay brief comes from ``duckycodess/Battle-City`` at revision
``5c9d81cd0de89a05f5946448d19c40fb343b0a2d``. No Pyxel runtime code is reused. That
project is a source-material baseline; specifications and deterministic tests, not
incidental legacy bugs, govern the rebuild. Where the original was ambiguous or
self-contradictory the rebuild records a choice rather than inheriting an accident:

1. **Grid dimensions.** The original held an 18x18 array and wrote the 16x16 stage into
   its interior, using the outer ring as an indestructible border. Stage data never
   contained that ring. The rebuild keeps 16x16 as both the external and the internal
   convention and replaces the ring with an explicit world bound. See
   :mod:`battle_city_sim.stage`.
2. **Projectile speed.** Bullets were advanced twice per frame by two different
   functions. The rebuild advances once per tick at ``rules.projectile_speed``. See
   :attr:`~battle_city_sim.rules.Rules.projectile_speed`.
3. **Mirror naming.** Tile 3 is named "north-east" but deflects a rightward shot
   downward, and tile 4 is named "south-east" but deflects it upward, so the names read
   backwards against the geometry. The rebuild keeps the historical mapping and the
   historical names, published as explicit direction tables, so converted stages still
   play. See :data:`~battle_city_sim.tiles.MIRROR_REFLECTIONS`.
4. **Repeated reflection.** A mirror re-deflected a shot on every frame it spent inside
   the cell. The rebuild deflects once per entry. See
   :class:`~battle_city_sim.entities.Projectile`.
5. **Tile lookup.** Player shots looked up their tile with ``floor((x + r) / 16)`` while
   enemy shots used ``x // 16``. The rebuild uses the projectile's own pixel for every
   owner.
6. **Base destruction.** Any bullet reaching the home tile ended the run, including the
   defender's own. The content specification says the base "is destroyed by a hostile
   projectile", so a friendly shot is now absorbed like stone. See
   :data:`~battle_city_sim.step.BASE_FACTION`.
7. **Powerup timers.** Gatling and invincibility shared one countdown, so the second
   pickup silently reset the first. Each effect now has its own timer.
8. **Muzzle offsets.** Three facings used an offset of 7 and one used 8. The rebuild uses
   7 everywhere. See :attr:`~battle_city_sim.rules.Rules.muzzle_offset`.
9. **Forest concealment.** The original had no rule; forest sprites were simply drawn
   last. The rebuild states an explicit rule in :mod:`battle_city_sim.visibility` and
   gives it no gameplay effect until a proposal defines one.
10. **Initial facing.** Tanks picked a facing with an unseeded ``random.choice``. Players
    now enter facing up.

Opt-in gimmick terrain
----------------------
Content schema version 2 adds four conveyor tiles and a teleport pad to the vocabulary;
see :mod:`battle_city_sim.tiles` for the codes and :mod:`battle_city_sim.gimmicks` for
the arithmetic. They displace tanks inside the existing movement phase and are ordinary
traversable ground to everything else: a projectile, a powerup, the score, the wave
count, the win condition and forest concealment are all untouched. A stage that declares
none of them steps exactly as it did before, byte for byte, which is what keeps the
classic layouts and their recorded hashes stable. The canonical state version is
unchanged: the new tiles are new byte *values* in a field that was already one byte per
cell, not a new field.

Deliberately not implemented here
---------------------------------
Enemy steering, enemy wave cadence, random powerup spawn and despawn pacing, stage-win
timing, score accumulation, and the legacy ``hesoyam``/``pewpews``/``juancho`` cheats are
absent. The product specification defers exact scoring, wave pacing and win-state timing
to an accepted gameplay change proposal, and bot behaviour belongs to the AI package.
Enemy tanks are first-class actors that accept the same commands a player does, and
enemies and powerups enter play through explicit tick commands, so a campaign, a test, or
a bot supplies the policy without the rules engine guessing one.
"""

from .codec import CANONICAL_STATE_VERSION, encode_state, state_hash
from .entities import (
    ENEMY_VARIANTS,
    BaseState,
    Faction,
    PlayerState,
    PowerupKind,
    PowerupPickup,
    Projectile,
    RunOutcome,
    Tank,
    TankVariant,
)
from .errors import InvalidInputError, SimulationError, StageValidationError
from .events import (
    BaseDestroyed,
    EnemySpawned,
    Event,
    ExtraLifeGranted,
    PlayerLifeLost,
    PlayerRespawned,
    PowerupCollected,
    PowerupDespawned,
    PowerupExpired,
    PowerupSpawned,
    ProjectileEnded,
    ProjectileEndReason,
    ProjectileFired,
    ProjectileReflected,
    RunEnded,
    ScoreAwarded,
    ScoreReason,
    ShieldBroken,
    TankDestroyed,
    TankMoveBlocked,
    TankMoved,
    TileDamaged,
)
from .geometry import DIRECTION_ORDER, Direction, GridPos, Rect, Vec2
from .gimmicks import (
    TELEPORT_PAIR_SIZE,
    centre_cell,
    conveyor_target,
    teleport_pads,
    teleport_target,
)
from .inputs import (
    Command,
    DespawnPowerupCommand,
    FireCommand,
    MoveCommand,
    RespawnCommand,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TickInput,
)
from .rng import RNG_ALGORITHM, RNG_VERSION, Rng
from .rules import DEFAULT_RULES, Rules
from .stage import CLASSIC_GRID_SIZE, PlayerSpawn, Stage
from .state import INITIAL_PLAYER_FACING, SimulationState, new_game
from .step import BASE_FACTION, StepResult, run_ticks, step
from .tiles import (
    CLASSIC_TILES,
    CONVEYOR_DIRECTIONS,
    GIMMICK_TILES,
    MIRROR_REFLECTIONS,
    PROJECTILE_TILE_DAMAGE,
    TILE_BY_CHAR,
    TILE_CODE_CHARS,
    TILES_BLOCKING_TANKS,
    TILES_PASSING_PROJECTILES,
    Tile,
    TileGrid,
    blocks_tank,
    conveyor_direction,
    is_teleport_pad,
    passes_projectile,
    tile_code_char,
)
from .visibility import concealed_tank_ids, is_tank_concealed

__all__ = [
    "BASE_FACTION",
    "BaseDestroyed",
    "BaseState",
    "CANONICAL_STATE_VERSION",
    "CLASSIC_GRID_SIZE",
    "CLASSIC_TILES",
    "CONVEYOR_DIRECTIONS",
    "Command",
    "DEFAULT_RULES",
    "DIRECTION_ORDER",
    "DespawnPowerupCommand",
    "Direction",
    "ENEMY_VARIANTS",
    "EnemySpawned",
    "Event",
    "ExtraLifeGranted",
    "Faction",
    "FireCommand",
    "GIMMICK_TILES",
    "GridPos",
    "INITIAL_PLAYER_FACING",
    "InvalidInputError",
    "MIRROR_REFLECTIONS",
    "MoveCommand",
    "PROJECTILE_TILE_DAMAGE",
    "PlayerLifeLost",
    "PlayerRespawned",
    "PlayerSpawn",
    "PlayerState",
    "PowerupCollected",
    "PowerupDespawned",
    "PowerupExpired",
    "PowerupKind",
    "PowerupPickup",
    "PowerupSpawned",
    "Projectile",
    "ProjectileEndReason",
    "ProjectileEnded",
    "ProjectileFired",
    "ProjectileReflected",
    "RNG_ALGORITHM",
    "RNG_VERSION",
    "Rect",
    "RespawnCommand",
    "Rng",
    "Rules",
    "RunEnded",
    "RunOutcome",
    "ScoreAwarded",
    "ScoreReason",
    "ShieldBroken",
    "SimulationError",
    "SimulationState",
    "SpawnEnemyCommand",
    "SpawnPowerupCommand",
    "Stage",
    "StageValidationError",
    "StepResult",
    "TELEPORT_PAIR_SIZE",
    "TILES_BLOCKING_TANKS",
    "TILES_PASSING_PROJECTILES",
    "TILE_BY_CHAR",
    "TILE_CODE_CHARS",
    "Tank",
    "TankDestroyed",
    "TankMoveBlocked",
    "TankMoved",
    "TankVariant",
    "TickInput",
    "Tile",
    "TileDamaged",
    "TileGrid",
    "Vec2",
    "blocks_tank",
    "centre_cell",
    "concealed_tank_ids",
    "conveyor_direction",
    "conveyor_target",
    "encode_state",
    "is_tank_concealed",
    "is_teleport_pad",
    "new_game",
    "passes_projectile",
    "run_ticks",
    "state_hash",
    "step",
    "teleport_pads",
    "teleport_target",
    "tile_code_char",
]
