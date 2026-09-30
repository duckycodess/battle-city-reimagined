"""The fixed-tick rules engine.

``step(state, tick_input, rules)`` is the simulation's only entry point. It is a pure
function of its arguments: no clock, no file, no socket, no environment, no unseeded
randomness, and no mutation of the state it is handed. Given the same state, the same
rules and the same tick input it returns the same next state and the same event tuple on
any machine.

Tick phases
-----------
Every tick runs these phases in this fixed order. The order is part of the contract:
changing it changes replays, so it needs a proposal.

1. **Validate** every command against the pre-tick state. Any rejection aborts the whole
   tick, so a rejected input never lands half-applied.
2. **Spawn** enemies and powerups from explicit commands, in submitted order.
3. **Expire** gatling and invincibility timers, in ascending tank order.
4. **Respawn** player slots that asked for it.
5. **Move** tanks, in ascending tank order.
6. **Fire**, in ascending tank order: at most one projectile per tank per tick.
7. **Advance** every projectile exactly once by ``rules.projectile_speed``.
8. **Resolve terrain** for each projectile, in ascending projectile order.
9. **Resolve projectile against projectile**, over ascending identifier pairs.
10. **Resolve projectile against tank**, ascending projectile then ascending tank.
11. **Collect powerups**, ascending tank then ascending powerup.
12. **Advance the tick counter.**

Phases 5 and 6 walk entities rather than the submitted command list, so the order
commands appear inside a tick input does not change the result.

Choices that differ from the historical runtime
-----------------------------------------------
The historical project is a source-material baseline, not a specification. Where its
behaviour was an accident of its update loop, the rebuild states a rule instead. Each of
these is also noted at the definition it affects:

* **One projectile advance per tick.** ``Bullets.update`` moved each bullet, then
  ``check_bullet_block_collission`` re-appended it already moved again. The doubled
  6px-per-frame travel was never a designed speed.
* **One reflection per mirror entry.** The historical code re-applied the mirror swap on
  every frame the bullet spent inside the mirror cell, so a shot could oscillate. A
  projectile now records the mirror cell that deflected it and is deflected again only
  after leaving that cell.
* **The base is destroyed by a hostile projectile only.** The content specification says
  the home base "is destroyed by a hostile projectile"; the historical code ended the run
  on *any* bullet reaching the home tile, including the defender's own. A friendly shot
  is now absorbed like stone.
* **Separate powerup timers.** The historical tank shared one ``powerup_timer`` between
  gatling and invincibility, so collecting the second powerup silently reset the first.
  Each effect now has its own countdown.
* **Symmetric muzzle offsets.** See :attr:`Rules.muzzle_offset`.
* **Friendly fire passes through.** A projectile ignores tanks of its own faction rather
  than being absorbed by them, so a bot cannot use a team-mate as a shield by accident.
* **Fixed initial facing.** See :data:`battle_city_sim.state.INITIAL_PLAYER_FACING`.
* **No automatic behaviour.** Enemy steering, wave cadence, powerup spawn pacing,
  stage-win timing and the legacy cheat codes are absent. They were driven by
  ``random.random()`` and frame counters in the original and belong to the AI package and
  to an accepted campaign rules proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from .codec import state_hash
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
from .errors import InvalidInputError
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
from .geometry import Direction, GridPos, Rect, Vec2, cell_of, cells_overlapping, clamp
from .inputs import (
    DespawnPowerupCommand,
    FireCommand,
    MoveCommand,
    RespawnCommand,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TickInput,
)
from .rules import DEFAULT_RULES, Rules
from .state import SimulationState
from .tiles import (
    MIRROR_REFLECTIONS,
    PROJECTILE_TILE_DAMAGE,
    Tile,
    TileGrid,
    blocks_tank,
    passes_projectile,
)

BASE_FACTION: Final[Faction] = Faction.PLAYER
"""The faction the home base belongs to. A projectile of any other faction is hostile."""


@dataclass(frozen=True, slots=True)
class StepResult:
    """The state after a tick and everything that happened during it."""

    state: SimulationState
    events: tuple[Event, ...]

    @property
    def hash(self) -> str:
        """Canonical hash of the resulting state. See :mod:`battle_city_sim.codec`."""
        return state_hash(self.state)


def step(
    state: SimulationState,
    tick_input: TickInput,
    rules: Rules = DEFAULT_RULES,
) -> StepResult:
    """Advance ``state`` by exactly one tick.

    Raises :class:`InvalidInputError` when any command is illegal for ``state``; the
    caller's state is untouched in that case.

    A finished run is an absorbing state: the tick counter stops, the state is returned
    unchanged, and no events are produced. Callers check :attr:`SimulationState.finished`
    rather than relying on an exception.
    """
    if state.finished:
        return StepResult(state=state, events=())

    _validate(state, tick_input, rules)

    frame = _Frame(state, rules)
    _phase_spawn(frame, tick_input)
    _phase_expire_powerups(frame)
    _phase_respawn(frame, tick_input)
    _phase_move(frame, tick_input)
    _phase_fire(frame, tick_input)
    _phase_advance_projectiles(frame)
    _phase_terrain(frame)
    _phase_projectile_vs_projectile(frame)
    _phase_projectile_vs_tank(frame)
    _phase_collect_powerups(frame)
    return frame.finish()


class _Frame:
    """Mutable working copy of one tick, owned entirely by :func:`step`.

    Nothing outside this module ever sees a ``_Frame``: it is built from an immutable
    state and frozen back into one, which is what keeps the public contract immutable
    while the phases stay readable.
    """

    __slots__ = (
        "base",
        "events",
        "grid",
        "next_entity_id",
        "outcome",
        "players",
        "powerups",
        "projectiles",
        "rules",
        "source",
        "tanks",
        "tick",
        "world_height",
        "world_width",
    )

    def __init__(self, state: SimulationState, rules: Rules) -> None:
        self.source = state
        self.rules = rules
        self.tick = state.tick
        self.grid: TileGrid = state.grid
        self.tanks: dict[int, Tank] = {tank.entity_id: tank for tank in state.tanks}
        self.projectiles: dict[int, Projectile] = {
            shot.entity_id: shot for shot in state.projectiles
        }
        self.powerups: dict[int, PowerupPickup] = {
            pickup.entity_id: pickup for pickup in state.powerups
        }
        self.players: dict[int, PlayerState] = {player.slot: player for player in state.players}
        self.base: BaseState = state.base
        self.next_entity_id = state.next_entity_id
        self.outcome: RunOutcome | None = state.outcome
        self.events: list[Event] = []
        self.world_width = state.grid.width * rules.tile_size
        self.world_height = state.grid.height * rules.tile_size

    def allocate_id(self) -> int:
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        return entity_id

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def tank_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.tanks))

    def projectile_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.projectiles))

    def powerup_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.powerups))

    def player_slots(self) -> tuple[int, ...]:
        return tuple(sorted(self.players))

    def end_run(self, outcome: RunOutcome) -> None:
        if self.outcome is None:
            self.outcome = outcome
            self.emit(RunEnded(outcome=outcome))

    def finish(self) -> StepResult:
        state = SimulationState(
            tick=self.tick + 1,
            stage_id=self.source.stage_id,
            grid=self.grid,
            tanks=tuple(self.tanks[key] for key in sorted(self.tanks)),
            projectiles=tuple(self.projectiles[key] for key in sorted(self.projectiles)),
            powerups=tuple(self.powerups[key] for key in sorted(self.powerups)),
            players=tuple(self.players[key] for key in sorted(self.players)),
            base=self.base,
            rng=self.source.rng,
            next_entity_id=self.next_entity_id,
            outcome=self.outcome,
        )
        return StepResult(state=state, events=tuple(self.events))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(state: SimulationState, tick_input: TickInput, rules: Rules) -> None:
    """Reject an illegal tick input before any of it is applied."""
    if tick_input.tick != state.tick:
        raise InvalidInputError(
            f"tick mismatch: input targets tick {tick_input.tick}, state is at {state.tick}"
        )

    tank_ids = {tank.entity_id for tank in state.tanks}
    powerup_ids = {pickup.entity_id for pickup in state.powerups}
    occupied_cells = {(pickup.cell.x, pickup.cell.y) for pickup in state.powerups}
    occupied_bodies = [tank.body(rules) for tank in state.tanks]

    moved: set[int] = set()
    fired: set[int] = set()
    respawning: set[int] = set()
    despawning: set[int] = set()

    for index, command in enumerate(tick_input.commands):
        field = f"commands[{index}]"
        match command:
            case MoveCommand(tank_id=tank_id):
                _require_known_tank(field, tank_id, tank_ids)
                _require_unique(field, "move", tank_id, moved)
            case FireCommand(tank_id=tank_id):
                _require_known_tank(field, tank_id, tank_ids)
                _require_unique(field, "fire", tank_id, fired)
            case RespawnCommand(slot=slot):
                player = state.find_player(slot)
                if player is None:
                    raise InvalidInputError(f"{field}: unknown player slot {slot}")
                if not player.awaiting_respawn:
                    raise InvalidInputError(f"{field}: slot {slot} is not waiting to respawn")
                _require_unique(field, "respawn", slot, respawning)
            case SpawnEnemyCommand(cell=cell, variant=variant):
                if variant not in ENEMY_VARIANTS:
                    raise InvalidInputError(f"{field}: {variant.name} is not an enemy variant")
                body = _cell_body(cell, rules)
                _require_placeable(field, state.grid, cell, body, rules)
                for other in occupied_bodies:
                    if body.overlaps(other):
                        raise InvalidInputError(
                            f"{field}: cell ({cell.x}, {cell.y}) overlaps a tank"
                        )
                occupied_bodies.append(body)
            case SpawnPowerupCommand(cell=cell):
                body = _cell_body(cell, rules)
                _require_placeable(field, state.grid, cell, body, rules)
                key = (cell.x, cell.y)
                if key in occupied_cells:
                    raise InvalidInputError(
                        f"{field}: a powerup already occupies ({cell.x}, {cell.y})"
                    )
                occupied_cells.add(key)
            case DespawnPowerupCommand(powerup_id=powerup_id):
                if powerup_id not in powerup_ids:
                    raise InvalidInputError(f"{field}: unknown powerup {powerup_id}")
                _require_unique(field, "despawn", powerup_id, despawning)
            case _:
                raise InvalidInputError(f"{field}: unsupported command {command!r}")


def _require_known_tank(field: str, tank_id: int, known: set[int]) -> None:
    if tank_id not in known:
        raise InvalidInputError(f"{field}: unknown tank {tank_id}")


def _require_unique(field: str, kind: str, key: int, seen: set[int]) -> None:
    if key in seen:
        raise InvalidInputError(f"{field}: duplicate {kind} command for {key}")
    seen.add(key)


def _require_placeable(field: str, grid: TileGrid, cell: GridPos, body: Rect, rules: Rules) -> None:
    if not grid.contains(cell):
        raise InvalidInputError(
            f"{field}: cell ({cell.x}, {cell.y}) is outside the {grid.width}x{grid.height} grid"
        )
    for covered in cells_overlapping(body, rules.tile_size):
        if not grid.contains(covered):
            raise InvalidInputError(f"{field}: body at ({cell.x}, {cell.y}) leaves the playfield")
        if blocks_tank(grid.at(covered)):
            raise InvalidInputError(
                f"{field}: cell ({covered.x}, {covered.y}) is blocked by {grid.at(covered).name}"
            )


def _cell_body(cell: GridPos, rules: Rules) -> Rect:
    return Rect(
        cell.x * rules.tile_size, cell.y * rules.tile_size, rules.tank_size, rules.tank_size
    )


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def _phase_spawn(frame: _Frame, tick_input: TickInput) -> None:
    """Apply explicit spawn and despawn commands in submitted order."""
    for command in tick_input.commands:
        match command:
            case SpawnEnemyCommand(cell=cell, variant=variant, facing=facing):
                tank = Tank(
                    entity_id=frame.allocate_id(),
                    variant=variant,
                    position=_cell_pixel(cell, frame.rules),
                    facing=facing,
                )
                frame.tanks[tank.entity_id] = tank
                frame.emit(EnemySpawned(tank_id=tank.entity_id, variant=variant, cell=cell))
            case SpawnPowerupCommand(cell=cell, kind=kind):
                pickup = PowerupPickup(entity_id=frame.allocate_id(), kind=kind, cell=cell)
                frame.powerups[pickup.entity_id] = pickup
                frame.emit(PowerupSpawned(powerup_id=pickup.entity_id, kind=kind, cell=cell))
            case DespawnPowerupCommand(powerup_id=powerup_id):
                removed = frame.powerups.pop(powerup_id)
                frame.emit(
                    PowerupDespawned(
                        powerup_id=removed.entity_id,
                        kind=removed.kind,
                        cell=removed.cell,
                    )
                )
            case _:
                continue


def _phase_expire_powerups(frame: _Frame) -> None:
    """Count down each tank's effect timers, emitting one event per expiry."""
    for tank_id in frame.tank_ids():
        tank = frame.tanks[tank_id]
        gatling = tank.gatling_ticks
        invincible = tank.invincible_ticks
        if gatling > 0:
            gatling -= 1
            if gatling == 0:
                frame.emit(PowerupExpired(tank_id=tank_id, kind=PowerupKind.GATLING))
        if invincible > 0:
            invincible -= 1
            if invincible == 0:
                frame.emit(PowerupExpired(tank_id=tank_id, kind=PowerupKind.INVINCIBILITY))
        if gatling != tank.gatling_ticks or invincible != tank.invincible_ticks:
            frame.tanks[tank_id] = replace(tank, gatling_ticks=gatling, invincible_ticks=invincible)


def _phase_respawn(frame: _Frame, tick_input: TickInput) -> None:
    """Return requested slots to their spawn cell with effects cleared."""
    requested = {
        command.slot for command in tick_input.commands if isinstance(command, RespawnCommand)
    }
    for slot in frame.player_slots():
        if slot not in requested:
            continue
        player = frame.players[slot]
        tank = Tank(
            entity_id=frame.allocate_id(),
            variant=TankVariant.PLAYER,
            position=_cell_pixel(player.spawn, frame.rules),
            facing=Direction.UP,
            player_slot=slot,
        )
        frame.tanks[tank.entity_id] = tank
        frame.players[slot] = replace(player, tank_id=tank.entity_id)
        frame.emit(PlayerRespawned(slot=slot, tank_id=tank.entity_id, cell=player.spawn))


def _phase_move(frame: _Frame, tick_input: TickInput) -> None:
    """Turn and advance tanks in ascending identifier order."""
    directions = {
        command.tank_id: command.direction
        for command in tick_input.commands
        if isinstance(command, MoveCommand)
    }
    rules = frame.rules
    for tank_id in frame.tank_ids():
        direction = directions.get(tank_id)
        if direction is None:
            continue
        tank = frame.tanks[tank_id]
        tank = replace(tank, facing=direction)
        dx, dy = direction.scaled(rules.tank_speed)
        target = Vec2(
            clamp(tank.position.x + dx, 0, frame.world_width - rules.tank_size),
            clamp(tank.position.y + dy, 0, frame.world_height - rules.tank_size),
        )
        body = Rect(target.x, target.y, rules.tank_size, rules.tank_size)
        if target == tank.position or _blocked(frame, tank_id, body):
            frame.tanks[tank_id] = tank
            frame.emit(TankMoveBlocked(tank_id=tank_id, position=tank.position, facing=direction))
            continue
        moved = replace(tank, position=target)
        frame.tanks[tank_id] = moved
        frame.emit(
            TankMoved(
                tank_id=tank_id,
                origin=tank.position,
                position=target,
                facing=direction,
            )
        )


def _blocked(frame: _Frame, tank_id: int, body: Rect) -> bool:
    """Return whether ``body`` overlaps blocking terrain or another tank."""
    for cell in cells_overlapping(body, frame.rules.tile_size):
        if not frame.grid.contains(cell) or blocks_tank(frame.grid.at(cell)):
            return True
    for other_id in frame.tank_ids():
        if other_id == tank_id:
            continue
        if body.overlaps(frame.tanks[other_id].body(frame.rules)):
            return True
    return False


def _phase_fire(frame: _Frame, tick_input: TickInput) -> None:
    """Spawn at most one projectile per tank per tick.

    A projectile created here is advanced by phase 7 in the same tick, matching the
    historical order where firing preceded the bullet update. A tank that is both
    auto-firing under gatling and holding an explicit fire command still produces one
    projectile; the historical loop could produce two in a frame.
    """
    requested = {
        command.tank_id for command in tick_input.commands if isinstance(command, FireCommand)
    }
    rules = frame.rules
    for tank_id in frame.tank_ids():
        tank = frame.tanks[tank_id]
        gatling = tank.gatling_ticks > 0
        auto_fire = gatling and frame.tick % rules.gatling_interval_ticks == 0
        live = sum(1 for shot in frame.projectiles.values() if shot.owner_id == tank_id)
        gated_fire = tank_id in requested and (gatling or live < rules.max_projectiles_per_tank)
        if not (auto_fire or gated_fire):
            continue
        projectile = Projectile(
            entity_id=frame.allocate_id(),
            owner_id=tank_id,
            faction=tank.faction,
            position=_muzzle(tank, rules),
            direction=tank.facing,
        )
        frame.projectiles[projectile.entity_id] = projectile
        frame.emit(
            ProjectileFired(
                projectile_id=projectile.entity_id,
                owner_id=tank_id,
                faction=projectile.faction,
                position=projectile.position,
                direction=projectile.direction,
            )
        )


def _muzzle(tank: Tank, rules: Rules) -> Vec2:
    """Return the pixel a projectile leaving ``tank`` starts on."""
    x, y = tank.position.x, tank.position.y
    inset = rules.muzzle_inset
    offset = rules.muzzle_offset
    size = rules.tank_size
    match tank.facing:
        case Direction.UP:
            return Vec2(x + offset, y + inset)
        case Direction.DOWN:
            return Vec2(x + offset, y + size - inset)
        case Direction.LEFT:
            return Vec2(x + inset, y + offset)
        case Direction.RIGHT:
            return Vec2(x + size - inset, y + offset)


def _phase_advance_projectiles(frame: _Frame) -> None:
    """Move every projectile exactly once. See the module note on the historical drift."""
    speed = frame.rules.projectile_speed
    for projectile_id in frame.projectile_ids():
        projectile = frame.projectiles[projectile_id]
        dx, dy = projectile.direction.scaled(speed)
        frame.projectiles[projectile_id] = replace(
            projectile, position=projectile.position.translated(dx, dy)
        )


def _phase_terrain(frame: _Frame) -> None:
    """Resolve each projectile against the tile it now occupies."""
    for projectile_id in frame.projectile_ids():
        projectile = frame.projectiles[projectile_id]
        position = projectile.position
        if not (0 <= position.x < frame.world_width and 0 <= position.y < frame.world_height):
            _end_projectile(frame, projectile, ProjectileEndReason.OUT_OF_BOUNDS)
            continue

        cell = cell_of(position, frame.rules.tile_size)
        if projectile.reflected_cell is not None and projectile.reflected_cell != cell:
            projectile = replace(projectile, reflected_cell=None)
            frame.projectiles[projectile_id] = projectile

        tile = frame.grid.at(cell)
        if passes_projectile(tile):
            continue
        if tile in MIRROR_REFLECTIONS:
            if projectile.reflected_cell is None:
                outgoing = MIRROR_REFLECTIONS[tile][projectile.direction]
                frame.projectiles[projectile_id] = replace(
                    projectile, direction=outgoing, reflected_cell=cell
                )
                frame.emit(
                    ProjectileReflected(
                        projectile_id=projectile_id,
                        cell=cell,
                        tile=tile,
                        incoming=projectile.direction,
                        outgoing=outgoing,
                    )
                )
            continue
        if tile in PROJECTILE_TILE_DAMAGE:
            replacement = PROJECTILE_TILE_DAMAGE[tile]
            frame.grid = frame.grid.with_tile(cell, replacement)
            frame.emit(
                TileDamaged(
                    cell=cell,
                    previous=tile,
                    current=replacement,
                    projectile_id=projectile_id,
                )
            )
            _end_projectile(frame, projectile, ProjectileEndReason.TILE_DESTROYED)
            continue
        if tile is Tile.HOME:
            if projectile.faction is BASE_FACTION:
                _end_projectile(frame, projectile, ProjectileEndReason.TILE_BLOCKED)
                continue
            frame.base = replace(frame.base, destroyed=True)
            frame.emit(BaseDestroyed(cell=cell, projectile_id=projectile_id))
            _end_projectile(frame, projectile, ProjectileEndReason.HIT_BASE)
            frame.end_run(RunOutcome.BASE_DESTROYED)
            continue
        _end_projectile(frame, projectile, ProjectileEndReason.TILE_BLOCKED)


def _phase_projectile_vs_projectile(frame: _Frame) -> None:
    """Destroy mutually overlapping projectiles over ascending identifier pairs."""
    ids = frame.projectile_ids()
    consumed: set[int] = set()
    for index, first_id in enumerate(ids):
        if first_id in consumed:
            continue
        first = frame.projectiles[first_id]
        for second_id in ids[index + 1 :]:
            if second_id in consumed:
                continue
            second = frame.projectiles[second_id]
            if not first.body(frame.rules).overlaps(second.body(frame.rules)):
                continue
            consumed.add(first_id)
            consumed.add(second_id)
            _end_projectile(frame, first, ProjectileEndReason.HIT_PROJECTILE)
            _end_projectile(frame, second, ProjectileEndReason.HIT_PROJECTILE)
            break


def _phase_projectile_vs_tank(frame: _Frame) -> None:
    """Resolve projectile hits on tanks, ascending projectile then ascending tank."""
    for projectile_id in frame.projectile_ids():
        projectile = frame.projectiles[projectile_id]
        body = projectile.body(frame.rules)
        for tank_id in frame.tank_ids():
            tank = frame.tanks.get(tank_id)
            if tank is None or tank.faction is projectile.faction:
                continue
            if tank.invincible_ticks > 0:
                continue
            if not body.overlaps(tank.body(frame.rules)):
                continue
            _end_projectile(frame, projectile, ProjectileEndReason.HIT_TANK)
            if tank.variant is TankVariant.PLAYER:
                _destroy_player_tank(frame, tank, projectile_id)
            else:
                _damage_enemy_tank(frame, tank, projectile_id)
            break


def _damage_enemy_tank(frame: _Frame, tank: Tank, projectile_id: int) -> None:
    """Apply the shielded/normal/unshielded damage ladder and report its points."""
    if tank.variant is TankVariant.ENEMY_SHIELDED:
        frame.tanks[tank.entity_id] = replace(tank, variant=TankVariant.ENEMY_UNSHIELDED)
        frame.emit(ShieldBroken(tank_id=tank.entity_id, projectile_id=projectile_id))
        _award(frame, ScoreReason.SHIELD_BROKEN, tank.entity_id, projectile_id)
        return
    reason = (
        ScoreReason.NORMAL_KILL
        if tank.variant is TankVariant.ENEMY_NORMAL
        else ScoreReason.UNSHIELDED_KILL
    )
    del frame.tanks[tank.entity_id]
    frame.emit(
        TankDestroyed(
            tank_id=tank.entity_id,
            variant=tank.variant,
            projectile_id=projectile_id,
        )
    )
    _award(frame, reason, tank.entity_id, projectile_id)


def _points_for(rules: Rules, reason: ScoreReason) -> int:
    match reason:
        case ScoreReason.SHIELD_BROKEN:
            return rules.score_shield_break
        case ScoreReason.NORMAL_KILL:
            return rules.score_normal_kill
        case ScoreReason.UNSHIELDED_KILL:
            return rules.score_unshielded_kill


def _award(frame: _Frame, reason: ScoreReason, subject_tank_id: int, projectile_id: int) -> None:
    """Report points without accumulating them; campaign policy owns the total."""
    points = _points_for(frame.rules, reason)
    frame.emit(
        ScoreAwarded(
            points=points,
            reason=reason,
            subject_tank_id=subject_tank_id,
            projectile_id=projectile_id,
        )
    )


def _destroy_player_tank(frame: _Frame, tank: Tank, projectile_id: int) -> None:
    """Remove a player tank, spend a life, and end the run when every slot is out."""
    del frame.tanks[tank.entity_id]
    frame.emit(
        TankDestroyed(
            tank_id=tank.entity_id,
            variant=tank.variant,
            projectile_id=projectile_id,
        )
    )
    slot = tank.player_slot
    if slot is None:
        return
    player = frame.players[slot]
    remaining = max(player.lives - 1, 0)
    frame.players[slot] = replace(player, lives=remaining, tank_id=None)
    frame.emit(PlayerLifeLost(slot=slot, lives_remaining=remaining, tank_id=tank.entity_id))
    if all(frame.players[other].eliminated for other in frame.player_slots()):
        frame.end_run(RunOutcome.PLAYERS_ELIMINATED)


def _phase_collect_powerups(frame: _Frame) -> None:
    """Let player tanks collect overlapping powerups, ascending tank then powerup."""
    for tank_id in frame.tank_ids():
        slot = frame.tanks[tank_id].player_slot
        if frame.tanks[tank_id].faction is not Faction.PLAYER or slot is None:
            continue
        for powerup_id in frame.powerup_ids():
            pickup = frame.powerups.get(powerup_id)
            if pickup is None:
                continue
            body = frame.tanks[tank_id].body(frame.rules)
            if not body.overlaps(pickup.body(frame.rules)):
                continue
            del frame.powerups[powerup_id]
            frame.emit(
                PowerupCollected(
                    powerup_id=powerup_id,
                    kind=pickup.kind,
                    tank_id=tank_id,
                    slot=slot,
                )
            )
            _apply_powerup(frame, tank_id, slot, pickup.kind)


def _apply_powerup(frame: _Frame, tank_id: int, slot: int, kind: PowerupKind) -> None:
    rules = frame.rules
    tank = frame.tanks[tank_id]
    match kind:
        case PowerupKind.GATLING:
            frame.tanks[tank_id] = replace(tank, gatling_ticks=rules.powerup_duration_ticks)
        case PowerupKind.INVINCIBILITY:
            frame.tanks[tank_id] = replace(tank, invincible_ticks=rules.powerup_duration_ticks)
        case PowerupKind.EXTRA_LIFE:
            player = frame.players[slot]
            lives = player.lives + rules.extra_life_amount
            frame.players[slot] = replace(player, lives=lives)
            frame.emit(ExtraLifeGranted(slot=slot, lives=lives))


def _end_projectile(frame: _Frame, projectile: Projectile, reason: ProjectileEndReason) -> None:
    frame.projectiles.pop(projectile.entity_id, None)
    frame.emit(
        ProjectileEnded(
            projectile_id=projectile.entity_id,
            reason=reason,
            position=projectile.position,
        )
    )


def _cell_pixel(cell: GridPos, rules: Rules) -> Vec2:
    return Vec2(cell.x * rules.tile_size, cell.y * rules.tile_size)


def run_ticks(
    state: SimulationState,
    tick_inputs: tuple[TickInput, ...],
    rules: Rules = DEFAULT_RULES,
) -> tuple[SimulationState, tuple[Event, ...]]:
    """Step a whole recorded input sequence and return the final state and all events.

    This is the replay primitive: feeding the same initial state, rules and sequence must
    reproduce the same final canonical hash.
    """
    events: list[Event] = []
    current = state
    for tick_input in tick_inputs:
        result = step(current, tick_input, rules)
        current = result.state
        events.extend(result.events)
    return current, tuple(events)
