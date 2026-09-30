"""The three bundled classic layouts are regression fixtures for the rules engine.

The content package owns decoding and schema validation. This test only proves the
simulation accepts the shipped layouts verbatim and can run a tick on each of them, so a
future stage edit that the rules engine cannot host fails here rather than in play.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from battle_city_sim import (
    CLASSIC_GRID_SIZE,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PlayerSpawn,
    SpawnEnemyCommand,
    Stage,
    TankVariant,
    TickInput,
    Tile,
    new_game,
    state_hash,
    step,
)

LEVELS_DIR = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "content"
    / "src"
    / "battle_city_content"
    / "levels"
)


def _level_paths() -> list[Path]:
    paths = sorted(LEVELS_DIR.glob("classic-*.json"))
    assert paths, f"no bundled classic levels under {LEVELS_DIR}"
    return paths


def _load(path: Path) -> Stage:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
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


@pytest.mark.parametrize("path", _level_paths(), ids=lambda path: path.stem)
def test_a_bundled_classic_layout_validates(path: Path) -> None:
    stage = _load(path)
    assert stage.grid.width == CLASSIC_GRID_SIZE
    assert stage.grid.height == CLASSIC_GRID_SIZE
    assert stage.grid.at(stage.base_cell) is Tile.HOME
    assert stage.player_spawns
    assert stage.enemy_spawns


@pytest.mark.parametrize("path", _level_paths(), ids=lambda path: path.stem)
def test_a_bundled_classic_layout_runs_deterministically(path: Path) -> None:
    stage = _load(path)
    first = new_game(stage, seed=9)
    second = new_game(stage, seed=9)
    assert state_hash(first) == state_hash(second)

    player_id = first.tanks[0].entity_id
    enemy_cell = stage.enemy_spawns[0]
    script = (
        TickInput.of(0, SpawnEnemyCommand(enemy_cell, TankVariant.ENEMY_SHIELDED)),
        TickInput.of(1, MoveCommand(player_id, Direction.UP), FireCommand(player_id)),
        TickInput.of(2, MoveCommand(player_id, Direction.LEFT)),
    )
    left = first
    right = second
    for tick_input in script:
        left = step(left, tick_input).state
        right = step(right, tick_input).state
    assert state_hash(left) == state_hash(right)


def test_the_bundled_set_still_holds_the_three_historical_layouts() -> None:
    stages = [_load(path) for path in _level_paths()]
    assert len(stages) == 3
    assert [stage.stage_id for stage in stages] == [
        "classic-01",
        "classic-02",
        "classic-03",
    ]
    assert len({tuple(stage.grid.to_rows()) for stage in stages}) == 3
