"""Tick inputs: the only way state advances.

A :class:`TickInput` is a whole tick's worth of intents. The rules engine validates all
of them before applying any of them, so a rejected tick leaves the caller holding the
exact pre-tick state.

Intent ordering inside a tick is *not* significant for the entity commands: the engine
resolves each phase by walking entities in ascending identifier order and looking up
that entity's command. Two callers that submit the same intents in different orders
therefore reach the same state. The spawn and despawn commands are the exception; they
are applied in the order they appear, because that order is itself the caller's
decision.

Enemy behaviour, wave cadence and powerup pacing are *not* in this module. Enemy actors
exist and accept the same movement and fire commands a player does, but choosing those
commands belongs to the AI package, and spawning an enemy or a powerup is an explicit
command a campaign or a test issues. Automatic waves, random powerup pacing, stage-win
timing and legacy cheats are deferred to a gameplay change proposal.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .entities import PowerupKind, TankVariant
from .geometry import Direction, GridPos


@dataclass(frozen=True, slots=True)
class MoveCommand:
    """Face ``direction`` and try to advance one ``rules.tank_speed`` step.

    Facing changes even when the step is blocked, matching the historical tank, which
    turned on key press regardless of whether the move succeeded.
    """

    tank_id: int
    direction: Direction


@dataclass(frozen=True, slots=True)
class FireCommand:
    """Fire along the tank's current facing.

    The shot is refused when the tank already owns ``rules.max_projectiles_per_tank``
    live projectiles, unless gatling mode is active for that tank.
    """

    tank_id: int


@dataclass(frozen=True, slots=True)
class RespawnCommand:
    """Return a slot's tank to its spawn after a life was lost.

    Rejected when the slot is not waiting to respawn, or when its spawn cell is occupied
    by a live tank, including one spawned earlier in the same tick. The slot then stays in
    ``awaiting_respawn`` and the caller may retry on a later tick; placing a tank inside
    another one would lock both in place permanently.
    """

    slot: int


@dataclass(frozen=True, slots=True)
class SpawnEnemyCommand:
    """Place an enemy tank on ``cell``.

    Wave composition and cadence are a campaign concern, so the caller states which
    variant appears where and when.
    """

    cell: GridPos
    variant: TankVariant
    facing: Direction = Direction.DOWN


@dataclass(frozen=True, slots=True)
class SpawnPowerupCommand:
    """Place an uncollected powerup on ``cell``."""

    cell: GridPos
    kind: PowerupKind


@dataclass(frozen=True, slots=True)
class DespawnPowerupCommand:
    """Remove an uncollected powerup. Despawn pacing is a campaign concern."""

    powerup_id: int


type Command = (
    MoveCommand
    | FireCommand
    | RespawnCommand
    | SpawnEnemyCommand
    | SpawnPowerupCommand
    | DespawnPowerupCommand
)


@dataclass(frozen=True, slots=True)
class TickInput:
    """Every intent submitted for one tick.

    ``tick`` must equal the state's current tick. Carrying it makes a replay stream
    self-describing and makes a dropped or duplicated tick a validation failure rather
    than a silent divergence.
    """

    tick: int
    commands: tuple[Command, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, tick: int, *commands: Command) -> TickInput:
        """Convenience constructor: ``TickInput.of(0, MoveCommand(1, Direction.UP))``."""
        return cls(tick=tick, commands=tuple(commands))

    @classmethod
    def from_iterable(cls, tick: int, commands: Iterable[Command]) -> TickInput:
        return cls(tick=tick, commands=tuple(commands))
