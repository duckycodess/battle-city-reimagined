"""The three converted classic layouts are pinned here, tile by tile.

Every row below is written out as a literal rather than derived from the checked-in
files or from a hash of them. A hash proves a file did not change; it does not prove the
file still holds the layout the conversion produced. These literals are the record of
that conversion, so an accidental edit to a bundled level fails with a readable diff on
the offending row.

The same layouts are exercised against the rules engine in ``tests/sim``. Here they are
checked as content: identifiers, names, provenance, spawn coordinates, pack order, and
that the record this package produces builds the simulation stage the engine expects.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from battle_city_content import (
    CLASSIC_GRID_SIZE,
    LEVEL_SCHEMA_VERSION,
    GridCell,
    Level,
    PlayerSpawn,
    TileCode,
    bundled_content_root,
    load_bundled_pack,
    load_level,
)

# The simulation package ships no py.typed marker, so mypy treats it as untyped when
# only these paths are checked (``mypy packages/content tests/content``) and as typed
# when the whole tree is (``mypy packages tests``). The second code keeps the suppression
# itself from being reported as unused in the second case.
from battle_city_sim import GridPos, Stage  # type: ignore[import-untyped, unused-ignore]
from battle_city_sim import PlayerSpawn as SimPlayerSpawn

HISTORICAL_REPOSITORY = "https://github.com/duckycodess/Battle-City"
HISTORICAL_REVISION = "5c9d81cd0de89a05f5946448d19c40fb343b0a2d"
HISTORICAL_PATH = "assets/stage.py"

CLASSIC_01_ROWS: tuple[str, ...] = (
    "2222222222222222",
    "2000000000000002",
    "2000000000000002",
    "2002220220222002",
    "2000000000000002",
    "2020002112002002",
    "2002002002000202",
    "2020002112002002",
    "2002000000000202",
    "2020002002002002",
    "2110000000000112",
    "2020202002020202",
    "2020202002020202",
    "2000000000000002",
    "2000002220000002",
    "2222222822222222",
)

CLASSIC_02_ROWS: tuple[str, ...] = (
    "0000000000000000",
    "0000000000000000",
    "0020000000000200",
    "0200255555520020",
    "0020212222120200",
    "0200255555520020",
    "0000000000000000",
    "0000022222200000",
    "0300000000000400",
    "0111000000022222",
    "0202001010077002",
    "0202000200077002",
    "0772001010020002",
    "2222000000011111",
    "0000002220000000",
    "7777772827777777",
)

CLASSIC_03_ROWS: tuple[str, ...] = (
    "0077770000006000",
    "0077771110000000",
    "0002225552222770",
    "6002220002222770",
    "0004000660030770",
    "0000000660000770",
    "5566222000151002",
    "2266222004151002",
    "0000077702203550",
    "0300077702200110",
    "0005550022040022",
    "0005556611000022",
    "0022000006615006",
    "0012000006615000",
    "1100002220000220",
    "1103002820004220",
)

CLASSIC_LAYOUTS: dict[
    str, tuple[str, tuple[str, ...], tuple[PlayerSpawn, ...], tuple[GridCell, ...]]
] = {
    "classic-01": (
        "Classic Stage 1",
        CLASSIC_01_ROWS,
        (PlayerSpawn(slot=1, cell=GridCell(14, 11)),),
        (GridCell(1, 1), GridCell(14, 1)),
    ),
    "classic-02": (
        "Classic Stage 2",
        CLASSIC_02_ROWS,
        (PlayerSpawn(slot=1, cell=GridCell(13, 12)),),
        (GridCell(0, 0), GridCell(15, 0)),
    ),
    "classic-03": (
        "Classic Stage 3",
        CLASSIC_03_ROWS,
        (PlayerSpawn(slot=1, cell=GridCell(10, 15)),),
        (GridCell(0, 0), GridCell(15, 0)),
    ),
}

CLASSIC_BASE_CELLS: dict[str, GridCell] = {
    "classic-01": GridCell(7, 15),
    "classic-02": GridCell(7, 15),
    "classic-03": GridCell(7, 15),
}

LEVEL_IDS = tuple(CLASSIC_LAYOUTS)


def bundled_level(level_id: str) -> Level:
    return load_level(bundled_content_root() / "levels" / f"{level_id}.json")


@pytest.mark.parametrize("level_id", LEVEL_IDS)
def test_a_bundled_layout_matches_its_pinned_rows(level_id: str) -> None:
    level = bundled_level(level_id)
    name, rows, _players, _enemies = CLASSIC_LAYOUTS[level_id]
    assert level.level_id == level_id
    assert level.name == name
    assert level.grid.rows == rows
    assert level.grid.width == CLASSIC_GRID_SIZE
    assert level.grid.height == CLASSIC_GRID_SIZE
    assert level.schema_version == LEVEL_SCHEMA_VERSION


@pytest.mark.parametrize("level_id", LEVEL_IDS)
def test_a_bundled_layout_keeps_its_spawns_and_base(level_id: str) -> None:
    level = bundled_level(level_id)
    _name, _rows, players, enemies = CLASSIC_LAYOUTS[level_id]
    assert level.player_spawns == players
    assert level.enemy_spawns == enemies
    assert level.base_cell == CLASSIC_BASE_CELLS[level_id]
    assert level.grid.tile_at(level.base_cell) is TileCode.HOME
    for spawn in level.player_spawns:
        assert level.grid.tile_at(spawn.cell) is TileCode.EMPTY
    for cell in level.enemy_spawns:
        assert level.grid.tile_at(cell) is TileCode.EMPTY


@pytest.mark.parametrize("level_id", LEVEL_IDS)
def test_a_bundled_layout_records_its_historical_provenance(level_id: str) -> None:
    level = bundled_level(level_id)
    assert level.source is not None
    assert level.source.repository == HISTORICAL_REPOSITORY
    assert level.source.revision == HISTORICAL_REVISION
    assert level.source.path == HISTORICAL_PATH


@pytest.mark.parametrize("level_id", LEVEL_IDS)
def test_a_bundled_layout_declares_no_waves(level_id: str) -> None:
    # The historical stage data carries no wave information, so none is invented here.
    # Authored wave behaviour belongs to the campaign phase that owns it.
    assert bundled_level(level_id).waves == ()


def test_the_three_layouts_stay_distinct() -> None:
    rows = {level_id: layout[1] for level_id, layout in CLASSIC_LAYOUTS.items()}
    assert len(set(rows.values())) == 3
    assert all(len(row) == CLASSIC_GRID_SIZE for layout in rows.values() for row in layout)
    assert all(len(layout) == CLASSIC_GRID_SIZE for layout in rows.values())


def test_the_bundled_pack_lists_the_classic_stages_in_order() -> None:
    pack = load_bundled_pack()
    assert pack.pack_id == "classic"
    assert pack.schema_version == 1
    assert pack.version == "1.0.0"
    assert pack.content_schema_version == LEVEL_SCHEMA_VERSION
    assert pack.level_ids == LEVEL_IDS
    assert [level.level_id for level in pack.levels] == list(LEVEL_IDS)
    assert pack.level("classic-02").name == "Classic Stage 2"
    with pytest.raises(KeyError):
        pack.level("classic-99")


def test_the_bundled_pack_asserts_no_licence_over_converted_data() -> None:
    # The historical repository publishes no licence, so the pack claims none. SPDX
    # spells exactly that NOASSERTION; a real identifier here would be a false claim.
    license_ = load_bundled_pack().license
    assert license_.spdx_id == "NOASSERTION"
    assert HISTORICAL_REVISION in license_.notice


def test_the_bundled_pack_levels_match_the_standalone_loads() -> None:
    pack = load_bundled_pack()
    for level in pack.levels:
        assert level == bundled_level(level.level_id)


def test_manifests_stay_out_of_the_levels_directory() -> None:
    # The simulation's fixture test globs levels/classic-*.json and builds a stage from
    # every hit. A manifest filed there would be loaded as a level.
    levels_dir = bundled_content_root() / "levels"
    for path in sorted(levels_dir.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert "grid" in document, f"{path} is not a level document"
    assert sorted(p.name for p in levels_dir.glob("classic-*.json")) == [
        f"{level_id}.json" for level_id in LEVEL_IDS
    ]


@pytest.mark.parametrize("level_id", LEVEL_IDS)
def test_a_bundled_layout_builds_the_simulation_stage(level_id: str) -> None:
    """Content records map onto the simulation contract without translation loss.

    The content package may not import the simulation, so this parity check lives in the
    test tree, which may import both. It is what keeps the two copies of the stage
    contract honest.
    """
    level = bundled_level(level_id)
    stage = Stage.create(
        stage_id=level.level_id,
        name=level.name,
        rows=list(level.grid.rows),
        player_spawns=[
            SimPlayerSpawn(slot=spawn.slot, cell=GridPos(spawn.cell.x, spawn.cell.y))
            for spawn in level.player_spawns
        ],
        enemy_spawns=[GridPos(cell.x, cell.y) for cell in level.enemy_spawns],
    )
    assert stage.stage_id == level.level_id
    assert stage.name == level.name
    assert stage.grid.to_rows() == level.grid.rows
    assert (stage.base_cell.x, stage.base_cell.y) == (level.base_cell.x, level.base_cell.y)
    assert [(spawn.slot, spawn.cell.x, spawn.cell.y) for spawn in stage.player_spawns] == [
        (spawn.slot, spawn.cell.x, spawn.cell.y) for spawn in level.player_spawns
    ]
    assert [(cell.x, cell.y) for cell in stage.enemy_spawns] == [
        (cell.x, cell.y) for cell in level.enemy_spawns
    ]


def test_the_content_package_does_not_import_the_simulation() -> None:
    """The dependency direction is content -> nothing. Enforce it as a contract.

    A stray import would be invisible in a test run that imports both packages anyway,
    so this parses the shipped sources instead. Prose references to the simulation in a
    docstring are fine; an import statement is not.
    """
    package_root = bundled_content_root()
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.partition(".")[0].startswith("battle_city_") for name in names):
                offenders.append(f"{path.relative_to(package_root)}: {names}")
    assert offenders == []


def test_bundled_content_root_points_at_the_installed_package() -> None:
    root: Path = bundled_content_root()
    assert (root / "levels").is_dir()
    assert (root / "packs" / "classic.json").is_file()
    assert (root / "schemas" / "classic-level.schema.json").is_file()
    assert (root / "schemas" / "pack.schema.json").is_file()
