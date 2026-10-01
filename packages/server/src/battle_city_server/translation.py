"""Turning simulation values into protocol messages and client actions into commands.

This is the only module that knows both vocabularies. Keeping it in one file means the
answer to "what does the client see?" is one readable mapping rather than a search, and
it keeps the protocol package free of simulation imports.

Three directions of travel:

* :func:`commands_for` turns a client's allowed actions into simulation commands for
  that client's own tank. It refuses anything the pre-tick state cannot support, which
  is how a malformed intent is caught before it can abort a whole tick.
* :func:`snapshot_of` turns the authoritative state into a snapshot. Terrain is only
  carried on a keyframe; everything else is listed every tick.
* :func:`protocol_events` turns the tick's events into the published event table.
"""

from __future__ import annotations

from collections.abc import Sequence

from battle_city_protocol import (
    MAX_EVENTS_PER_MESSAGE,
    ActionKind,
    BaseSnapshot,
    DirectionCode,
    EventKind,
    GameEvent,
    PlayerAction,
    PlayerSnapshot,
    PowerupSnapshot,
    ProjectileSnapshot,
    StateSnapshot,
    TankSnapshot,
)
from battle_city_sim import (
    BaseDestroyed,
    Command,
    Direction,
    EnemySpawned,
    Event,
    ExtraLifeGranted,
    FireCommand,
    MoveCommand,
    PlayerLifeLost,
    PlayerRespawned,
    PlayerState,
    PowerupCollected,
    PowerupDespawned,
    PowerupExpired,
    PowerupSpawned,
    ProjectileEnded,
    ProjectileFired,
    ProjectileReflected,
    RespawnCommand,
    RunEnded,
    ScoreAwarded,
    ShieldBroken,
    SimulationState,
    TankDestroyed,
    TankMoveBlocked,
    TankMoved,
    TileDamaged,
    state_hash,
)

DIRECTIONS: dict[DirectionCode, Direction] = {
    DirectionCode.UP: Direction.UP,
    DirectionCode.DOWN: Direction.DOWN,
    DirectionCode.LEFT: Direction.LEFT,
    DirectionCode.RIGHT: Direction.RIGHT,
}
"""The wire facings and the simulation facings, side by side."""


class UntranslatableEventError(Exception):
    """A simulation event has no entry in the published protocol event table.

    This is a programming error, not a client error: the two vocabularies have drifted.
    It is raised rather than skipped so the gap is reported instead of being broadcast
    as an authoritative tick that quietly lost an event.
    """


class IllegalActionError(Exception):
    """A client's actions cannot apply to the state the tick will run against.

    Raised while building commands, before the tick starts, so the offending batch can
    be dropped and answered while every other client's input for the tick survives.
    """


def commands_for(
    slot: int, player: PlayerState | None, actions: Sequence[PlayerAction]
) -> tuple[Command, ...]:
    """Return the simulation commands ``actions`` mean for ``slot``.

    Only the sending slot's own tank can be named: the tank identifier comes from the
    server's player record, never from the message, so a client cannot address another
    player's tank however it words its batch.

    Commands are emitted respawn, move, fire. The rules engine resolves entity commands
    by walking entities rather than the submitted list, so this order is for readability
    rather than for outcome; the spawn commands that *are* order-sensitive are not
    available to a client at all.
    """
    if player is None:
        raise IllegalActionError(f"slot {slot} is not in this run")
    by_kind = {action.kind: action for action in actions}

    commands: list[Command] = []
    if ActionKind.RESPAWN in by_kind:
        if not player.awaiting_respawn:
            raise IllegalActionError(f"slot {slot} is not waiting to respawn")
        commands.append(RespawnCommand(slot=slot))
    move = by_kind.get(ActionKind.MOVE)
    fire = by_kind.get(ActionKind.FIRE)
    if move is not None or fire is not None:
        if player.tank_id is None:
            raise IllegalActionError(f"slot {slot} has no live tank")
        if move is not None:
            if move.direction is None:
                raise IllegalActionError(f"slot {slot} sent a move with no direction")
            commands.append(
                MoveCommand(tank_id=player.tank_id, direction=DIRECTIONS[move.direction])
            )
        if fire is not None:
            commands.append(FireCommand(tank_id=player.tank_id))
    return tuple(commands)


def snapshot_of(
    state: SimulationState,
    *,
    session_id: str,
    tick_rate: int,
    state_version: int,
    keyframe: bool,
) -> StateSnapshot:
    """Describe ``state`` for every client in the session.

    The same snapshot goes to everyone: the server has no per-client view to keep in
    step, and one encoded frame serves the whole broadcast.
    """
    return StateSnapshot(
        session_id=session_id,
        tick=state.tick,
        tick_rate=tick_rate,
        state_version=state_version,
        keyframe=keyframe,
        grid=grid_rows(state) if keyframe else None,
        tanks=tuple(
            TankSnapshot(
                entity_id=tank.entity_id,
                variant=tank.variant.value,
                x=tank.position.x,
                y=tank.position.y,
                facing=tank.facing.value,
                slot=tank.player_slot,
                gatling_ticks=tank.gatling_ticks,
                invincible_ticks=tank.invincible_ticks,
            )
            for tank in state.tanks
        ),
        projectiles=tuple(
            ProjectileSnapshot(
                entity_id=projectile.entity_id,
                owner_id=projectile.owner_id,
                faction=projectile.faction.value,
                x=projectile.position.x,
                y=projectile.position.y,
                direction=projectile.direction.value,
            )
            for projectile in state.projectiles
        ),
        powerups=tuple(
            PowerupSnapshot(
                entity_id=powerup.entity_id,
                kind=powerup.kind.value,
                cell_x=powerup.cell.x,
                cell_y=powerup.cell.y,
            )
            for powerup in state.powerups
        ),
        players=tuple(
            PlayerSnapshot(
                slot=player.slot,
                lives=player.lives,
                tank_id=player.tank_id,
                spawn_x=player.spawn.x,
                spawn_y=player.spawn.y,
            )
            for player in state.players
        ),
        base=BaseSnapshot(
            cell_x=state.base.cell.x,
            cell_y=state.base.cell.y,
            destroyed=state.base.destroyed,
        ),
        outcome=None if state.outcome is None else state.outcome.value,
        state_hash=state_hash(state),
    )


def grid_rows(state: SimulationState) -> tuple[str, ...]:
    """Return the terrain as the content package's tile-code rows."""
    return tuple("".join(str(tile.value) for tile in row) for row in state.grid.rows)


def protocol_events(events: Sequence[Event]) -> tuple[GameEvent, ...]:
    """Translate a tick's events, truncating at the published per-message limit.

    Truncation keeps one pathological tick from producing an unsendable frame. It costs
    presentation detail only: the snapshot for the same tick is the authority, so a
    client that loses the tail of an event list still renders the right world.
    """
    return tuple(protocol_event(event) for event in events[:MAX_EVENTS_PER_MESSAGE])


def protocol_event(event: Event) -> GameEvent:
    """Translate one simulation event into its published record."""
    match event:
        case EnemySpawned():
            return GameEvent.of(
                EventKind.ENEMY_SPAWNED,
                event.tank_id,
                event.variant.value,
                event.cell.x,
                event.cell.y,
            )
        case PowerupSpawned():
            return GameEvent.of(
                EventKind.POWERUP_SPAWNED,
                event.powerup_id,
                event.kind.value,
                event.cell.x,
                event.cell.y,
            )
        case PowerupDespawned():
            return GameEvent.of(
                EventKind.POWERUP_DESPAWNED,
                event.powerup_id,
                event.kind.value,
                event.cell.x,
                event.cell.y,
            )
        case TankMoved():
            return GameEvent.of(
                EventKind.TANK_MOVED,
                event.tank_id,
                event.origin.x,
                event.origin.y,
                event.position.x,
                event.position.y,
                event.facing.value,
            )
        case TankMoveBlocked():
            return GameEvent.of(
                EventKind.TANK_MOVE_BLOCKED,
                event.tank_id,
                event.position.x,
                event.position.y,
                event.facing.value,
            )
        case ProjectileFired():
            return GameEvent.of(
                EventKind.PROJECTILE_FIRED,
                event.projectile_id,
                event.owner_id,
                event.faction.value,
                event.position.x,
                event.position.y,
                event.direction.value,
            )
        case ProjectileReflected():
            return GameEvent.of(
                EventKind.PROJECTILE_REFLECTED,
                event.projectile_id,
                event.cell.x,
                event.cell.y,
                event.tile.value,
                event.incoming.value,
                event.outgoing.value,
            )
        case ProjectileEnded():
            return GameEvent.of(
                EventKind.PROJECTILE_ENDED,
                event.projectile_id,
                event.reason.value,
                event.position.x,
                event.position.y,
            )
        case TileDamaged():
            return GameEvent.of(
                EventKind.TILE_DAMAGED,
                event.cell.x,
                event.cell.y,
                event.previous.value,
                event.current.value,
                event.projectile_id,
            )
        case ShieldBroken():
            return GameEvent.of(EventKind.SHIELD_BROKEN, event.tank_id, event.projectile_id)
        case TankDestroyed():
            return GameEvent.of(
                EventKind.TANK_DESTROYED,
                event.tank_id,
                event.variant.value,
                event.projectile_id,
            )
        case ScoreAwarded():
            return GameEvent.of(
                EventKind.SCORE_AWARDED,
                event.points,
                event.reason.value,
                event.subject_tank_id,
                event.projectile_id,
            )
        case PlayerLifeLost():
            return GameEvent.of(
                EventKind.PLAYER_LIFE_LOST,
                event.slot,
                event.lives_remaining,
                event.tank_id,
            )
        case PlayerRespawned():
            return GameEvent.of(
                EventKind.PLAYER_RESPAWNED,
                event.slot,
                event.tank_id,
                event.cell.x,
                event.cell.y,
            )
        case PowerupCollected():
            return GameEvent.of(
                EventKind.POWERUP_COLLECTED,
                event.powerup_id,
                event.kind.value,
                event.tank_id,
                event.slot,
            )
        case PowerupExpired():
            return GameEvent.of(EventKind.POWERUP_EXPIRED, event.tank_id, event.kind.value)
        case ExtraLifeGranted():
            return GameEvent.of(EventKind.EXTRA_LIFE_GRANTED, event.slot, event.lives)
        case BaseDestroyed():
            return GameEvent.of(
                EventKind.BASE_DESTROYED, event.cell.x, event.cell.y, event.projectile_id
            )
        case RunEnded():
            return GameEvent.of(EventKind.RUN_ENDED, event.outcome.value)
        case _:
            # The simulation's event union is exhausted above, so a new event kind is
            # a type error here before it is a runtime one. Refusing it loudly anyway
            # means an unmapped event can never leave the server as silence: a client
            # would otherwise be told a tick produced nothing when it produced
            # something nobody translated.
            raise UntranslatableEventError(
                f"no protocol record for simulation event {type(event).__name__}"
            )
