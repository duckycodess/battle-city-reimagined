"""The immutable simulation state and its constructor.

:class:`SimulationState` is a value: every field is frozen and every collection is a
tuple held in ascending entity-identifier order. ``step`` never mutates a state it is
given; it builds a new one. Holding an old state is therefore a valid way to rewind, and
comparing two states is comparing two values.

The state deliberately has no score field. The product spec defers exact scoring to a
gameplay change proposal, so points are reported as events and accumulated by whichever
layer owns campaign policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from .entities import (
    BaseState,
    Faction,
    PlayerState,
    PowerupPickup,
    Projectile,
    RunOutcome,
    Tank,
    TankVariant,
)
from .geometry import Direction, GridPos, Vec2
from .rng import Rng
from .rules import DEFAULT_RULES, Rules
from .stage import Stage
from .tiles import TileGrid

FIRST_ENTITY_ID: Final[int] = 1
"""Identifier allocation starts at 1 so ``0`` stays available as a null sentinel."""

INITIAL_PLAYER_FACING: Final[Direction] = Direction.UP
"""Players enter facing up.

The historical tank chose its facing with an unseeded ``random.choice``, which cannot
appear in a deterministic simulation. Fixing the facing is the smallest replacement that
keeps a run reproducible.
"""


@dataclass(frozen=True, slots=True)
class SimulationState:
    """A complete, self-contained snapshot of one run at one tick."""

    tick: int
    stage_id: str
    grid: TileGrid
    tanks: tuple[Tank, ...]
    projectiles: tuple[Projectile, ...]
    powerups: tuple[PowerupPickup, ...]
    players: tuple[PlayerState, ...]
    base: BaseState
    rng: Rng
    next_entity_id: int
    outcome: RunOutcome | None = None

    @property
    def finished(self) -> bool:
        """Whether the run reached a terminal outcome. Terminal states absorb steps."""
        return self.outcome is not None

    def world_size(self, rules: Rules = DEFAULT_RULES) -> tuple[int, int]:
        """Pixel width and height of the playfield."""
        return self.grid.width * rules.tile_size, self.grid.height * rules.tile_size

    def tank(self, tank_id: int) -> Tank:
        for tank in self.tanks:
            if tank.entity_id == tank_id:
                return tank
        raise KeyError(f"unknown tank: {tank_id}")

    def find_tank(self, tank_id: int) -> Tank | None:
        for tank in self.tanks:
            if tank.entity_id == tank_id:
                return tank
        return None

    def projectile(self, projectile_id: int) -> Projectile:
        for projectile in self.projectiles:
            if projectile.entity_id == projectile_id:
                return projectile
        raise KeyError(f"unknown projectile: {projectile_id}")

    def powerup(self, powerup_id: int) -> PowerupPickup:
        for powerup in self.powerups:
            if powerup.entity_id == powerup_id:
                return powerup
        raise KeyError(f"unknown powerup: {powerup_id}")

    def player(self, slot: int) -> PlayerState:
        for player in self.players:
            if player.slot == slot:
                return player
        raise KeyError(f"unknown player slot: {slot}")

    def find_player(self, slot: int) -> PlayerState | None:
        for player in self.players:
            if player.slot == slot:
                return player
        return None

    def tanks_of(self, faction: Faction) -> tuple[Tank, ...]:
        return tuple(tank for tank in self.tanks if tank.faction is faction)

    def projectiles_owned_by(self, tank_id: int) -> tuple[Projectile, ...]:
        return tuple(p for p in self.projectiles if p.owner_id == tank_id)


def new_game(
    stage: Stage,
    *,
    seed: int,
    rules: Rules = DEFAULT_RULES,
    player_slots: tuple[int, ...] | None = None,
) -> SimulationState:
    """Build the tick-zero state for ``stage``.

    ``player_slots`` selects which of the stage's declared slots take part; the default
    is every declared slot. No enemies and no powerups exist at tick zero: both arrive
    through explicit tick commands, because wave cadence and powerup pacing are campaign
    policy rather than rules-engine behaviour.
    """
    slots = (
        tuple(spawn.slot for spawn in stage.player_spawns)
        if player_slots is None
        else tuple(sorted(set(player_slots)))
    )
    if not slots:
        raise ValueError("new_game needs at least one player slot")

    tanks: list[Tank] = []
    players: list[PlayerState] = []
    next_id = FIRST_ENTITY_ID
    for slot in slots:
        spawn = stage.player_spawn_for(slot)
        tank = Tank(
            entity_id=next_id,
            variant=TankVariant.PLAYER,
            position=_cell_to_pixel(spawn.cell, rules),
            facing=INITIAL_PLAYER_FACING,
            player_slot=slot,
        )
        tanks.append(tank)
        players.append(
            PlayerState(
                slot=slot,
                lives=rules.starting_lives,
                spawn=spawn.cell,
                tank_id=tank.entity_id,
            )
        )
        next_id += 1

    return SimulationState(
        tick=0,
        stage_id=stage.stage_id,
        grid=stage.grid,
        tanks=tuple(tanks),
        projectiles=(),
        powerups=(),
        players=tuple(players),
        base=BaseState(cell=stage.base_cell, destroyed=False),
        rng=Rng.from_seed(seed),
        next_entity_id=next_id,
        outcome=None,
    )


def _cell_to_pixel(cell: GridPos, rules: Rules) -> Vec2:
    return Vec2(cell.x * rules.tile_size, cell.y * rules.tile_size)


def with_outcome(state: SimulationState, outcome: RunOutcome) -> SimulationState:
    """Return ``state`` with a terminal outcome recorded, keeping everything else."""
    return replace(state, outcome=outcome)
