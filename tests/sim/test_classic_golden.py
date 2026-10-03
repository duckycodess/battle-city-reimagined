"""Golden canonical hashes for the three classic stages, as literals.

The rest of the suite proves the simulation is *self*-consistent: the same state and the
same inputs produce the same bytes twice in one process. That is the wrong shape of claim
for a compatibility promise, because it stays true however far the rules drift. What the
accepted ``gimmicks-v1`` change promises is something stronger -- that a classic state and
a classic input sequence produce the bytes they produced *before* that change -- and only
a literal recorded outside the code can say so.

The digests below were computed on commit ``fd8355b``, the revision this change starts
from, with the simulation exactly as it was before any gimmick terrain existed, and are
reproduced unchanged by the implementation. A failure here means a classic replay no
longer replays, which needs a proposal and a replay-compatibility policy rather than a
new literal; see :mod:`battle_city_sim.codec`.

The script is deliberately broad rather than minimal. It turns through all four facings,
fires on a seventh of the ticks, spawns a shielded enemy on tick zero and runs for two
hundred ticks, so it exercises movement, blocking, the muzzle, projectile travel, mirror
reflection, brick damage and the shield ladder -- every phase whose ordering the movement
restructure could have disturbed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from battle_city_sim import (
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PlayerSpawn,
    SimulationState,
    SpawnEnemyCommand,
    Stage,
    TankVariant,
    TickInput,
    new_game,
    run_ticks,
    state_hash,
)

LEVELS_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "content"
    / "src"
    / "battle_city_content"
    / "levels"
)

SEED: Final[int] = 1
TICKS: Final[int] = 200
FIRE_EVERY: Final[int] = 7
TURN_ORDER: Final[tuple[Direction, ...]] = (
    Direction.UP,
    Direction.RIGHT,
    Direction.DOWN,
    Direction.LEFT,
)

GOLDEN: Final[dict[str, tuple[str, str, int]]] = {
    "classic-01": (
        "d27f09f7ef98b53c5ac5ff991ba099f758b148cbf6d9f96b29790670ab6aa30c",
        "83a74d4c0513f80a3100c455f15e31b1d28f9cbcb5bee688eed4aeeb33a27d11",
        238,
    ),
    "classic-02": (
        "c5c8231ec4b95f9f85da45d418fa99cb3d4b3c2e4dca8dce108df14080a7ace7",
        "01042249997c5c54887450fa391f067867eefcea2fa9933f0f5bb6fcb349ac19",
        238,
    ),
    "classic-03": (
        "e6221a0c57aebf045c16dafdc149475ecf17f303aae38233fb17d1784d0f803a",
        "502c6f4f172befbce38d0e4ffa906090c9340715918003368fec1672c696fd81",
        230,
    ),
}
"""``{stage: (tick-zero hash, tick-200 hash, events emitted)}``, recorded at ``fd8355b``."""


def classic_stage(level_id: str) -> Stage:
    """Build a stage straight from the shipped level file, without the content loader.

    Reading the JSON here rather than importing ``battle_city_content`` keeps this a
    simulation test: the claim is about the engine's bytes, and routing it through a
    second package would let a loader change move the goalposts.
    """
    raw: Any = json.loads((LEVELS_DIR / f"{level_id}.json").read_text(encoding="utf-8"))
    grid: Any = raw["grid"]
    spawns: Any = raw["spawns"]
    return Stage.create(
        stage_id=str(raw["id"]),
        name=str(raw["name"]),
        rows=[str(row) for row in grid["rows"]],
        player_spawns=[
            PlayerSpawn(slot=int(item["slot"]), cell=GridPos(int(item["x"]), int(item["y"])))
            for item in spawns["players"]
        ],
        enemy_spawns=[GridPos(int(item["x"]), int(item["y"])) for item in spawns["enemies"]],
    )


def script_for(stage: Stage) -> tuple[TickInput, ...]:
    """The recorded input sequence. Changing it invalidates every literal above."""
    inputs: list[TickInput] = []
    for tick in range(TICKS):
        commands: list[MoveCommand | FireCommand | SpawnEnemyCommand] = [
            MoveCommand(tank_id=1, direction=TURN_ORDER[tick % len(TURN_ORDER)])
        ]
        if tick % FIRE_EVERY == 0:
            commands.append(FireCommand(tank_id=1))
        if tick == 0:
            commands.append(
                SpawnEnemyCommand(cell=stage.enemy_spawns[0], variant=TankVariant.ENEMY_SHIELDED)
            )
        inputs.append(TickInput(tick=tick, commands=tuple(commands)))
    return tuple(inputs)


def start(level_id: str) -> tuple[Stage, SimulationState]:
    stage = classic_stage(level_id)
    return stage, new_game(stage, seed=SEED)


@pytest.mark.parametrize("level_id", sorted(GOLDEN))
def test_a_classic_stage_still_hashes_to_its_recorded_tick_zero(level_id: str) -> None:
    _, state = start(level_id)
    assert state_hash(state) == GOLDEN[level_id][0]


@pytest.mark.parametrize("level_id", sorted(GOLDEN))
def test_a_classic_replay_still_ends_on_its_recorded_hash(level_id: str) -> None:
    """The whole point: a recorded classic run replays to the bytes it always did."""
    stage, state = start(level_id)
    final, events = run_ticks(state, script_for(stage))
    expected_zero, expected_final, expected_events = GOLDEN[level_id]
    assert state_hash(state) == expected_zero
    assert state_hash(final) == expected_final
    assert len(events) == expected_events
    assert final.tick == TICKS


@pytest.mark.parametrize("level_id", sorted(GOLDEN))
def test_a_classic_grid_round_trips_through_the_row_alphabet(level_id: str) -> None:
    """Appending tile codes must not have moved a single classic character."""
    stage = classic_stage(level_id)
    rows = stage.grid.to_rows()
    raw: Any = json.loads((LEVELS_DIR / f"{level_id}.json").read_text(encoding="utf-8"))
    assert list(rows) == [str(row) for row in raw["grid"]["rows"]]
    assert set("".join(rows)) <= set("012345678")


def test_the_three_classic_stages_are_the_whole_recorded_set() -> None:
    """A fourth bundled classic stage would need its own literal before it could ship."""
    assert sorted(path.stem for path in LEVELS_DIR.glob("classic-*.json")) == sorted(GOLDEN)
