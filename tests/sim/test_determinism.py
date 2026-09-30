"""Determinism and replay: the acceptance signal for the whole package.

A recorded input sequence is replayed from the same initial state and must land on the
same canonical bytes, and the same intents submitted in a different order inside a tick
must land on the same state.
"""

from __future__ import annotations

from collections.abc import Sequence

from battle_city_sim import (
    DEFAULT_RULES,
    Command,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PowerupKind,
    Rect,
    RespawnCommand,
    Rng,
    SimulationState,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
    TickInput,
    Tile,
    encode_state,
    run_ticks,
    state_hash,
    step,
)
from helpers import make_stage, make_state, run

PLAYER_ID = 1

TERRAIN = {
    GridPos(8, 8): Tile.BRICK,
    GridPos(7, 8): Tile.BRICK,
    GridPos(9, 8): Tile.CRACKED_BRICK,
    GridPos(4, 6): Tile.MIRROR_NE,
    GridPos(11, 6): Tile.MIRROR_SE,
    GridPos(7, 9): Tile.MIRROR_NE,
    GridPos(9, 11): Tile.MIRROR_SE,
    GridPos(6, 10): Tile.MIRROR_NE,
    GridPos(4, 10): Tile.WATER,
    GridPos(12, 10): Tile.FOREST,
    GridPos(2, 12): Tile.STONE,
}
ENEMY_CELLS = (GridPos(2, 2), GridPos(13, 2), GridPos(2, 13), GridPos(13, 13))
DIRECTIONS = (Direction.UP, Direction.DOWN, Direction.LEFT, Direction.RIGHT)


def _scenario_state(seed: int = 5) -> SimulationState:
    return make_state(make_stage(overrides=TERRAIN, base=GridPos(7, 15)), seed=seed)


def _record(state: SimulationState, ticks: int, script_seed: int) -> tuple[TickInput, ...]:
    """Play a scripted run, returning the tick inputs it actually submitted.

    The script is driven by its own generator so the sequence is reproducible, and it only
    emits commands that are legal for the state it sees, which is what a client or a bot
    would have to do.
    """
    rng = Rng.from_seed(script_seed)
    recorded: list[TickInput] = []
    current = state
    for index in range(ticks):
        commands: list[Command] = []
        if index % 37 == 5:
            cell = ENEMY_CELLS[(index // 37) % len(ENEMY_CELLS)]
            if _cell_is_free(current, cell):
                variant, rng = rng.choice((TankVariant.ENEMY_NORMAL, TankVariant.ENEMY_SHIELDED))
                facing, rng = rng.choice(DIRECTIONS)
                commands.append(SpawnEnemyCommand(cell, variant, facing))
        if index % 53 == 11:
            kind, rng = rng.choice(tuple(PowerupKind))
            cell = GridPos(6, 12)
            if not any(pickup.cell == cell for pickup in current.powerups):
                commands.append(SpawnPowerupCommand(cell, kind))
        for tank in current.tanks:
            roll, rng = rng.below(8)
            if roll < 5:
                direction, rng = rng.choice(DIRECTIONS)
                commands.append(MoveCommand(tank.entity_id, direction))
            if roll in (1, 6):
                commands.append(FireCommand(tank.entity_id))
        for player in current.players:
            if player.awaiting_respawn:
                commands.append(RespawnCommand(player.slot))
        tick_input = TickInput(tick=current.tick, commands=tuple(commands))
        recorded.append(tick_input)
        current = step(current, tick_input).state
        if current.finished:
            break
    return tuple(recorded)


def _cell_is_free(state: SimulationState, cell: GridPos) -> bool:
    size = DEFAULT_RULES.tank_size
    body = Rect(cell.x * DEFAULT_RULES.tile_size, cell.y * DEFAULT_RULES.tile_size, size, size)
    return not any(body.overlaps(tank.body(DEFAULT_RULES)) for tank in state.tanks)


def test_a_recorded_sequence_replays_to_the_same_bytes() -> None:
    start = _scenario_state()
    script = _record(start, 400, script_seed=17)
    assert len(script) > 50, "the scripted run should exercise a long sequence"

    first, first_events = run_ticks(start, script)
    second, second_events = run_ticks(start, script)
    assert encode_state(first) == encode_state(second)
    assert state_hash(first) == state_hash(second)
    assert first_events == second_events

    kinds = {type(event).__name__ for event in first_events}
    assert {
        "EnemySpawned",
        "ProjectileFired",
        "ProjectileReflected",
        "TileDamaged",
        "TankMoved",
        "TankMoveBlocked",
        "ProjectileEnded",
    } <= kinds, f"the scripted run should exercise the rules, saw {sorted(kinds)}"


def test_replaying_from_a_fresh_initial_state_matches() -> None:
    script = _record(_scenario_state(), 250, script_seed=23)
    one, _ = run_ticks(_scenario_state(), script)
    two, _ = run_ticks(_scenario_state(), script)
    assert state_hash(one) == state_hash(two)


def test_every_intermediate_tick_hash_matches() -> None:
    start = _scenario_state()
    script = _record(start, 200, script_seed=31)
    first = _hash_trace(start, script)
    second = _hash_trace(start, script)
    assert first == second
    assert len(set(first)) > 10, "the run should actually change state"


def _hash_trace(state: SimulationState, script: Sequence[TickInput]) -> tuple[str, ...]:
    digests: list[str] = []
    current = state
    for tick_input in script:
        current = step(current, tick_input).state
        digests.append(state_hash(current))
    return tuple(digests)


def test_command_order_inside_a_tick_does_not_matter() -> None:
    state, _ = run(
        _scenario_state(),
        1,
        commands={
            0: [
                SpawnEnemyCommand(GridPos(2, 2), TankVariant.ENEMY_NORMAL),
                SpawnEnemyCommand(GridPos(13, 2), TankVariant.ENEMY_SHIELDED),
            ]
        },
    )
    forward: tuple[Command, ...] = (
        MoveCommand(1, Direction.LEFT),
        FireCommand(1),
        MoveCommand(2, Direction.DOWN),
        FireCommand(2),
        MoveCommand(3, Direction.RIGHT),
        FireCommand(3),
    )
    one = step(state, TickInput(tick=state.tick, commands=forward))
    two = step(state, TickInput(tick=state.tick, commands=tuple(reversed(forward))))
    assert encode_state(one.state) == encode_state(two.state)


def test_entities_are_always_held_in_ascending_identifier_order() -> None:
    start = _scenario_state()
    script = _record(start, 200, script_seed=41)
    current = start
    for tick_input in script:
        current = step(current, tick_input).state
        for group in (current.tanks, current.projectiles, current.powerups):
            ids = [entity.entity_id for entity in group]
            assert ids == sorted(ids)
        assert all(
            entity.entity_id < current.next_entity_id
            for group in (current.tanks, current.projectiles, current.powerups)
            for entity in group
        )


def test_identifiers_are_never_reused() -> None:
    """An identifier that has left play never comes back, so replays stay comparable."""
    start = _scenario_state()
    script = _record(start, 300, script_seed=53)
    retired: set[int] = set()
    previous: set[int] = set()
    current = start
    for tick_input in script:
        current = step(current, tick_input).state
        live = {
            entity.entity_id
            for group in (current.tanks, current.projectiles, current.powerups)
            for entity in group
        }
        assert not (live & retired), "a retired identifier came back"
        retired |= previous - live
        previous = live
    assert retired, "the scripted run should retire some entities"
    assert max(retired | previous) < current.next_entity_id


def test_stepping_never_mutates_the_state_it_was_given() -> None:
    start = _scenario_state()
    before = encode_state(start)
    script = _record(start, 120, script_seed=61)
    run_ticks(start, script)
    assert encode_state(start) == before
    assert start.tick == 0
