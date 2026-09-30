"""One local run: the simulation state, and the translation of intent into commands.

:class:`StageSession` is the only place the client turns a player's wishes into
simulation input. It holds a state, a stage and the rules the run was started with, and
it advances by calling :func:`battle_city_sim.step` once per tick. It is frozen for the
same reason the simulation state is: advancing returns a new session, so the renderer can
keep the session it drew and nothing can be half-advanced.

The class adds no game rules. It decides *which legal command to send*, never what a
command means:

* A facing plus a live tank becomes one :class:`~battle_city_sim.MoveCommand`; the
  simulation decides whether the tank actually moves, what blocks it and what its shot
  does to the terrain.
* Fire becomes one :class:`~battle_city_sim.FireCommand`. The per-tank projectile limit
  and the gatling cadence are simulation gates, and firing into them is a legal no-op, so
  the client does not pre-filter and cannot drift from the rule.
* A slot waiting to respawn with lives left becomes one
  :class:`~battle_city_sim.RespawnCommand`, as soon as the simulation would accept it.

That last one is a policy choice and is recorded as one. The simulation refuses a respawn
onto an occupied spawn cell by raising, and refusing is correct there -- it keeps the slot
explicitly deferred instead of stacking two bodies -- so the client checks the same
condition through the simulation's own geometry before asking. Respawn *pacing*, an
invulnerability window, a stage restart policy: those are campaign rules and belong to the
campaign phase, not to this adapter. Asking at the first legal tick is the smallest
policy that keeps a run playable without inventing one.

There is no score here. The product specification defers scoring to an accepted gameplay
proposal and the simulation reports points as events rather than holding a total, so the
client reports what it is given and accumulates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from battle_city_sim import (
    DEFAULT_RULES,
    Command,
    Event,
    FireCommand,
    MoveCommand,
    PlayerState,
    Rect,
    RespawnCommand,
    Rules,
    SimulationState,
    Stage,
    Tank,
    TickInput,
    new_game,
    step,
)

from .intents import IDLE_INTENT, PlayerIntent

DEFAULT_SEED: int = 1
"""Seed used when a caller does not choose one.

The simulation carries a seeded generator even though nothing in this phase draws from
it, so a run still needs a seed. A fixed default keeps a locally started stage
reproducible and keeps screenshots stable; a campaign or a match supplies its own.
"""


@dataclass(frozen=True, slots=True)
class StageSession:
    """A run of one stage, advanced tick by tick.

    Build one with :meth:`start`. The plain constructor takes a state directly, which is
    what lets a test drive presentation from a state it assembled itself.
    """

    stage: Stage
    state: SimulationState
    rules: Rules = DEFAULT_RULES
    player_slot: int = 1
    seed: int = DEFAULT_SEED
    last_events: tuple[Event, ...] = ()

    @classmethod
    def start(
        cls,
        stage: Stage,
        *,
        seed: int = DEFAULT_SEED,
        rules: Rules = DEFAULT_RULES,
        player_slot: int | None = None,
    ) -> StageSession:
        """Begin ``stage`` at tick zero with one local player.

        ``player_slot`` defaults to the lowest slot the stage declares. Only that slot is
        placed: a second local player would need a second binding set and a second HUD
        column, and this phase is single-seat.
        """
        slot = stage.player_spawns[0].slot if player_slot is None else player_slot
        stage.player_spawn_for(slot)
        return cls(
            stage=stage,
            state=new_game(stage, seed=seed, rules=rules, player_slots=(slot,)),
            rules=rules,
            player_slot=slot,
            seed=seed,
            last_events=(),
        )

    @property
    def finished(self) -> bool:
        """Whether the run reached a terminal outcome recorded by the simulation."""
        return self.state.finished

    @property
    def player(self) -> PlayerState:
        """The local player's record."""
        return self.state.player(self.player_slot)

    @property
    def player_tank(self) -> Tank | None:
        """The local player's tank, or ``None`` while the slot waits to respawn."""
        tank_id = self.player.tank_id
        return None if tank_id is None else self.state.find_tank(tank_id)

    def restarted(self) -> StageSession:
        """A fresh run of the same stage with the same seed, rules and slot."""
        return StageSession.start(
            self.stage, seed=self.seed, rules=self.rules, player_slot=self.player_slot
        )

    def commands_for(self, intent: PlayerIntent) -> tuple[Command, ...]:
        """The simulation commands ``intent`` produces against the current state."""
        tank = self.player_tank
        if tank is None:
            return self._respawn_commands()
        commands: list[Command] = []
        if intent.direction is not None:
            commands.append(MoveCommand(tank_id=tank.entity_id, direction=intent.direction))
        if intent.fire:
            commands.append(FireCommand(tank_id=tank.entity_id))
        return tuple(commands)

    def advance(self, ticks: int, intent: PlayerIntent = IDLE_INTENT) -> StageSession:
        """Advance ``ticks`` ticks, holding ``intent`` for each of them.

        Commands are rebuilt every tick because the state they address changes: a respawn
        hands the slot a new tank identifier, and a destroyed tank must stop receiving
        move commands within the same call.
        """
        if ticks < 0:
            raise ValueError(f"ticks must not be negative, found {ticks}")
        session = self
        events: list[Event] = []
        for _ in range(ticks):
            if session.state.finished:
                break
            result = step(
                session.state,
                TickInput.from_iterable(session.state.tick, session.commands_for(intent)),
                session.rules,
            )
            events.extend(result.events)
            session = replace(session, state=result.state)
        return replace(session, last_events=tuple(events))

    def _respawn_commands(self) -> tuple[Command, ...]:
        """Ask for a respawn only on a tick the simulation would accept it."""
        player = self.player
        if not player.awaiting_respawn or player.eliminated:
            return ()
        body = Rect(
            x=player.spawn.x * self.rules.tile_size,
            y=player.spawn.y * self.rules.tile_size,
            width=self.rules.tank_size,
            height=self.rules.tank_size,
        )
        for tank in self.state.tanks:
            if body.overlaps(tank.body(self.rules)):
                return ()
        return (RespawnCommand(slot=player.slot),)
