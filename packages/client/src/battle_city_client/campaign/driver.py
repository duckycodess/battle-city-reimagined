"""The seam between the campaign and whatever steers its enemies.

The campaign decides *which* enemies appear, *where* and *when*. It does not decide what
they then do, and it must not: bot behaviour belongs to ``battle_city_ai``, the
architecture specification allows ``ai -> sim`` and not ``client -> ai``, and the client
manifest declares no dependency on the AI package. Putting steering here would either
break that direction or fork the bot rules into the presentation layer.

So the campaign takes a driver. A driver is a value, like everything else in a run: each
call returns the successor driver alongside its commands, so a :class:`CampaignRun` stays
immutable and a replay re-derives the same decisions rather than replaying a mutated
object.

What a driver may do
--------------------
Nothing it returns is trusted. :func:`legal_enemy_commands` filters every driver's output
down to at most one :class:`~battle_city_sim.MoveCommand` and one
:class:`~battle_city_sim.FireCommand` per tank, for tanks that are alive and on the enemy
faction. A driver therefore cannot drive the player's tank, cannot respawn a slot, cannot
spawn an enemy or a powerup, and cannot make :func:`battle_city_sim.step` reject the
campaign's tick. The filter is what makes the seam safe to hand to code the campaign did
not write, rather than merely convenient.

What ships
----------
:class:`IdleEnemyDriver`. The client that ships with this build spawns enemies on the
historical cadence and gives them no commands at all, so they hold position and never
fire. That is a real limitation, not a placeholder dressed up as one: a stage is still
cleared by destroying its quota, the base can still be lost, but nothing shoots back.
Giving the shipped client a real driver means giving it a declared dependency on the AI
package, which is issue #35.

``tests/campaign`` injects a driver backed by ``battle_city_ai`` -- tests may import every
package -- which is how this seam is shown to carry a real bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from battle_city_sim import (
    Command,
    Faction,
    FireCommand,
    MoveCommand,
    Rules,
    SimulationState,
)


@runtime_checkable
class EnemyCommandDriver(Protocol):
    """Something that chooses what the enemy tanks do.

    Implementations are values. Both methods return the successor driver rather than
    mutating, because a campaign run is immutable and a stage restart must re-derive a
    driver rather than inherit one that has already seen the abandoned attempt.
    """

    def entering_stage(self, state: SimulationState, rules: Rules) -> EnemyCommandDriver:
        """Return the driver to use for a stage that is beginning at ``state``.

        Called once per stage, including after a restart, so per-stage memory starts
        empty. ``state`` is the stage's tick-zero state and holds no enemies yet.
        """
        ...

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[EnemyCommandDriver, tuple[Command, ...]]:
        """Return the successor driver and this tick's enemy commands.

        Called once per tick with the pre-tick state, after that tick's spawn has been
        decided but before it has been applied, so a tank spawned this tick is first seen
        on the following one. Whatever is returned is filtered by
        :func:`legal_enemy_commands` before it reaches the simulation.
        """
        ...


@dataclass(frozen=True, slots=True)
class IdleEnemyDriver:
    """A driver that issues nothing. Enemies hold position and never fire.

    The default, and what the shipped client uses. See the module docstring for why, and
    the client README for the same statement where a player would read it.
    """

    def entering_stage(self, state: SimulationState, rules: Rules) -> IdleEnemyDriver:
        return self

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[IdleEnemyDriver, tuple[Command, ...]]:
        return self, ()


def legal_enemy_commands(
    commands: tuple[Command, ...], state: SimulationState
) -> tuple[Command, ...]:
    """Keep only the commands a driver is allowed to issue, in submitted order.

    A command survives when it is a move or a fire naming a live tank of the enemy
    faction, and when that tank has not already been given a command of the same kind this
    tick. Everything else is dropped silently: a driver is not a caller to be reported to,
    it is a policy whose output is a suggestion, and raising would let a third-party
    driver take the run down with it.
    """
    enemy_ids = {tank.entity_id for tank in state.tanks if tank.faction is Faction.ENEMY}
    moved: set[int] = set()
    fired: set[int] = set()
    kept: list[Command] = []
    for command in commands:
        match command:
            case MoveCommand(tank_id=tank_id):
                if tank_id in enemy_ids and tank_id not in moved:
                    moved.add(tank_id)
                    kept.append(command)
            case FireCommand(tank_id=tank_id):
                if tank_id in enemy_ids and tank_id not in fired:
                    fired.add(tank_id)
                    kept.append(command)
            case _:
                continue
    return tuple(kept)
