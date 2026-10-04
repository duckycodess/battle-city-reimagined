"""Builders for the bot regression tests.

The name is package-qualified on purpose. ``tests/`` has no package markers, so pytest
imports every module under its bare basename and mypy names it the same way; two
same-named files anywhere in the tree then collide, at import time for pytest and as a
duplicate module for ``mypy packages tests``. ``conftest.py`` would have been exempt from
the first collision but not the second, and the sibling suites already carry their own
``conftest.py``. So this file takes a prefix nothing else can claim, matching
``client_helpers`` and ``content_helpers``. For the same reason every module in this
directory is named ``test_ai_*``.

Stages are built here instead of loaded from the content package: a bot test should fail
because a decision changed, not because somebody edited a shipped level.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from battle_city_ai import Bot, DifficultyProfile, plan_tick, seed_from_state
from battle_city_sim import (
    DEFAULT_RULES,
    Direction,
    Event,
    GridPos,
    PlayerSpawn,
    Rules,
    SimulationState,
    SpawnEnemyCommand,
    Stage,
    TankVariant,
    TickInput,
    Tile,
    new_game,
    step,
    tile_code_char,
)

GRID_SIZE = 16
BASE_CELL = GridPos(8, 15)
PLAYER_CELL = GridPos(2, 2)
ENEMY_CELL = GridPos(12, 2)

PLAYER_TANK_ID = 1
"""``new_game`` allocates identifiers from 1, so the single player slot owns tank 1."""


def rows(
    overrides: Mapping[GridPos, Tile] | None = None,
    base: GridPos = BASE_CELL,
) -> tuple[str, ...]:
    """Return 16 tile-code rows of empty ground with one home base plus ``overrides``."""
    cells = [[Tile.EMPTY for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[base.y][base.x] = Tile.HOME
    for cell, tile in (overrides or {}).items():
        cells[cell.y][cell.x] = tile
    return tuple("".join(tile_code_char(tile) for tile in row) for row in cells)


def arena(
    *,
    overrides: Mapping[GridPos, Tile] | None = None,
    player_cell: GridPos = PLAYER_CELL,
    enemy_cells: Sequence[GridPos] = (ENEMY_CELL,),
    base: GridPos = BASE_CELL,
) -> Stage:
    return Stage.create(
        stage_id="ai-test-stage",
        name="AI Test Stage",
        rows=rows(overrides, base),
        player_spawns=[PlayerSpawn(slot=1, cell=player_cell)],
        enemy_spawns=list(enemy_cells),
    )


def game(stage: Stage | None = None, *, seed: int = 7) -> SimulationState:
    return new_game(stage if stage is not None else arena(), seed=seed)


def with_enemy(
    state: SimulationState,
    cell: GridPos,
    *,
    variant: TankVariant = TankVariant.ENEMY_NORMAL,
    facing: Direction = Direction.DOWN,
    rules: Rules = DEFAULT_RULES,
) -> SimulationState:
    """Spawn one enemy and advance a tick, returning the state that now holds it."""
    command = SpawnEnemyCommand(cell=cell, variant=variant, facing=facing)
    return step(state, TickInput.of(state.tick, command), rules).state


def bots_for(
    state: SimulationState,
    assignments: Mapping[int, DifficultyProfile],
) -> tuple[Bot, ...]:
    """Build bots for ``{tank_id: profile}``, seeded from the session seed."""
    seed = seed_from_state(state)
    return tuple(
        Bot.create(tank_id=tank_id, profile=profile, seed=seed)
        for tank_id, profile in sorted(assignments.items())
    )


@dataclass(frozen=True, slots=True)
class Session:
    """Everything a driven run produced, so a test can assert on any layer of it."""

    state: SimulationState
    bots: tuple[Bot, ...]
    inputs: tuple[TickInput, ...]
    events: tuple[Event, ...]


def drive(
    state: SimulationState,
    bots: Sequence[Bot],
    ticks: int,
    *,
    rules: Rules = DEFAULT_RULES,
) -> Session:
    """Run ``ticks`` ticks with ``bots`` supplying every command."""
    current = state
    current_bots = tuple(bots)
    collected_inputs: list[TickInput] = []
    collected_events: list[Event] = []
    for _ in range(ticks):
        plan = plan_tick(current_bots, current, rules)
        collected_inputs.append(plan.tick_input)
        result = step(current, plan.tick_input, rules)
        collected_events.extend(result.events)
        current = result.state
        current_bots = plan.bots
    return Session(
        state=current,
        bots=current_bots,
        inputs=tuple(collected_inputs),
        events=tuple(collected_events),
    )
