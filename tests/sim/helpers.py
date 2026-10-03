"""Shared builders for the simulation regression tests.

Tests state their own terrain instead of leaning on a bundled level, so a failure names
the rule under test rather than a stage edit somewhere else. Stage fixtures loaded from
the content package are exercised separately in ``test_stage_fixtures``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from battle_city_sim import (
    DEFAULT_RULES,
    Command,
    Event,
    GridPos,
    PlayerSpawn,
    Rules,
    SimulationState,
    Stage,
    TickInput,
    Tile,
    new_game,
    step,
    tile_code_char,
)

GRID_SIZE = 16
BASE_CELL = GridPos(8, 15)
PLAYER_CELL = GridPos(8, 10)
ENEMY_SPAWN_CELL = GridPos(2, 1)


def build_rows(
    overrides: Mapping[GridPos, Tile] | None = None,
    base: GridPos = BASE_CELL,
) -> tuple[str, ...]:
    """Return 16 tile-code rows: empty ground, one home base, plus ``overrides``."""
    cells = [[Tile.EMPTY for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[base.y][base.x] = Tile.HOME
    for cell, tile in (overrides or {}).items():
        cells[cell.y][cell.x] = tile
    return tuple("".join(tile_code_char(tile) for tile in row) for row in cells)


def make_stage(
    *,
    overrides: Mapping[GridPos, Tile] | None = None,
    player_cell: GridPos = PLAYER_CELL,
    player_cells: Sequence[PlayerSpawn] | None = None,
    enemy_cells: Sequence[GridPos] = (ENEMY_SPAWN_CELL,),
    base: GridPos = BASE_CELL,
    stage_id: str = "test-stage",
) -> Stage:
    spawns = (
        list(player_cells) if player_cells is not None else [PlayerSpawn(slot=1, cell=player_cell)]
    )
    return Stage.create(
        stage_id=stage_id,
        name="Test Stage",
        rows=build_rows(overrides, base),
        player_spawns=spawns,
        enemy_spawns=list(enemy_cells),
    )


def make_state(stage: Stage | None = None, *, seed: int = 1) -> SimulationState:
    return new_game(stage if stage is not None else make_stage(), seed=seed)


def run(
    state: SimulationState,
    ticks: int,
    *,
    commands: Mapping[int, Sequence[Command]] | None = None,
    rules: Rules = DEFAULT_RULES,
) -> tuple[SimulationState, tuple[Event, ...]]:
    """Step ``ticks`` times, issuing ``commands[tick]`` on the ticks that have entries."""
    collected: list[Event] = []
    current = state
    for _ in range(ticks):
        planned = () if commands is None else tuple(commands.get(current.tick, ()))
        result = step(current, TickInput(tick=current.tick, commands=planned), rules)
        current = result.state
        collected.extend(result.events)
    return current, tuple(collected)


def events_of[T](events: Sequence[Event], kind: type[T]) -> tuple[T, ...]:
    """Return every event of ``kind``, preserving emission order."""
    return tuple(event for event in events if isinstance(event, kind))


def only[T](events: Sequence[Event], kind: type[T]) -> T:
    """Return the single event of ``kind``; fails loudly when there is not exactly one."""
    matches = events_of(events, kind)
    assert len(matches) == 1, f"expected exactly one {kind.__name__}, got {len(matches)}"
    return matches[0]
