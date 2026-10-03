"""Level schema version 2: the opt-in conveyor and teleport-pad format.

Two things are being checked, and they pull in opposite directions on purpose.

The first is that version 2 accepts what it is meant to accept -- five new tile codes,
a pad pair, and everything version 1 already accepted -- so an author can write the
format at all.

The second is that version 1 did not move. A classic document carrying a conveyor is
refused, a version 2 document is refused by a build that is told to read version 1, and
the three bundled classic levels still load exactly as they did. That half is the
compatibility promise the accepted change makes, and it is the half a future edit is
most likely to break by accident.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from battle_city_content import (
    CLASSIC_TILES,
    GIMMICK_LEVEL_SCHEMA_VERSION,
    GIMMICK_TILES,
    LEVEL_SCHEMA_FILENAMES,
    LEVEL_SCHEMA_VERSION,
    SUPPORTED_LEVEL_SCHEMA_VERSIONS,
    TELEPORT_PAIR_SIZE,
    TILE_BY_CHAR,
    ContentValidationError,
    GridCell,
    Level,
    TileCode,
    bundled_content_root,
    load_bundled_pack,
    load_gimmick_demo_pack,
    load_level,
    load_pack,
    tile_codes_for,
    uses_gimmick_tiles,
)
from content_helpers import (
    SYNTHETIC_ROWS,
    build_pack,
    rejection,
    synthetic_level,
    synthetic_manifest,
    write_json,
)

PAD_CELLS: tuple[GridCell, GridCell] = (GridCell(2, 2), GridCell(13, 2))


def gimmick_rows(*, pads: int = 2, belts: str = "9ABC", row: int = 4) -> list[str]:
    """Synthetic version 2 rows: the classic synthetic layout plus gimmick terrain."""
    rows = [list(line) for line in SYNTHETIC_ROWS]
    for index, code in enumerate(belts):
        rows[row][4 + index] = code
    for index in range(pads):
        cell = (PAD_CELLS[0], PAD_CELLS[1], GridCell(7, 2))[index]
        rows[cell.y][cell.x] = TileCode.TELEPORT_PAD.value
    return ["".join(line) for line in rows]


def gimmick_level(**overrides: Any) -> dict[str, Any]:
    document = synthetic_level(schema_version=GIMMICK_LEVEL_SCHEMA_VERSION)
    document["grid"]["rows"] = gimmick_rows()
    document.update(overrides)
    return document


def load(tmp_path: Path, document: dict[str, Any]) -> Level:
    return load_level(write_json(tmp_path / "level.json", document))


def reject(tmp_path: Path, document: dict[str, Any]) -> ContentValidationError:
    path = write_json(tmp_path / "level.json", document)
    return rejection(lambda: load_level(path))


# -- the vocabulary -----------------------------------------------------------


def test_the_two_versions_declare_the_alphabets_they_are_meant_to() -> None:
    assert sorted(SUPPORTED_LEVEL_SCHEMA_VERSIONS) == [1, 2]
    assert set(tile_codes_for(LEVEL_SCHEMA_VERSION)) == set("012345678")
    assert set(tile_codes_for(GIMMICK_LEVEL_SCHEMA_VERSION)) == set("0123456789ABCD")
    assert tuple(TileCode) == CLASSIC_TILES + GIMMICK_TILES


def test_the_gimmick_codes_are_the_five_the_change_accepted() -> None:
    assert [tile.value for tile in GIMMICK_TILES] == ["9", "A", "B", "C", "D"]
    assert [tile.name for tile in GIMMICK_TILES] == [
        "CONVEYOR_N",
        "CONVEYOR_E",
        "CONVEYOR_S",
        "CONVEYOR_W",
        "TELEPORT_PAD",
    ]
    assert all(TILE_BY_CHAR[tile.value] is tile for tile in TileCode)


def test_a_row_is_reported_as_gimmick_only_when_it_carries_a_new_code() -> None:
    assert not uses_gimmick_tiles(SYNTHETIC_ROWS)
    assert uses_gimmick_tiles(tuple(gimmick_rows()))
    assert uses_gimmick_tiles(("0000", "00D0"))


def test_each_supported_version_has_its_own_schema_file() -> None:
    """A later format ships beside the classic schema, never as an edit to it."""
    assert set(LEVEL_SCHEMA_FILENAMES) == SUPPORTED_LEVEL_SCHEMA_VERSIONS
    schemas = bundled_content_root() / "schemas"
    for filename in LEVEL_SCHEMA_FILENAMES.values():
        assert (schemas / filename).is_file(), filename
    assert len(set(LEVEL_SCHEMA_FILENAMES.values())) == len(LEVEL_SCHEMA_FILENAMES)


# -- what version 2 accepts ---------------------------------------------------


def test_a_version_two_level_with_every_new_code_loads(tmp_path: Path) -> None:
    level = load(tmp_path, gimmick_level())
    assert level.schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    for tile in GIMMICK_TILES:
        assert level.grid.cells_of(tile), tile.name
    assert level.grid.cells_of(TileCode.TELEPORT_PAD) == PAD_CELLS


def test_a_classic_layout_relabelled_version_two_still_loads(tmp_path: Path) -> None:
    """Version 2 is version 1 plus five codes, so every classic layout is also valid v2."""
    document = synthetic_level(schema_version=GIMMICK_LEVEL_SCHEMA_VERSION)
    level = load(tmp_path, document)
    assert level.grid.rows == SYNTHETIC_ROWS
    assert not level.grid.cells_of(TileCode.TELEPORT_PAD)


def test_a_version_two_level_may_declare_no_pads_at_all(tmp_path: Path) -> None:
    level = load(
        tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": gimmick_rows(pads=0)})
    )
    assert not level.grid.cells_of(TileCode.TELEPORT_PAD)
    assert level.grid.cells_of(TileCode.CONVEYOR_E)


def test_the_pads_are_reported_in_row_major_order(tmp_path: Path) -> None:
    rows = gimmick_rows(pads=0)
    rows[9] = "D" + rows[9][1:]
    rows[3] = rows[3][:5] + "D" + rows[3][6:]
    level = load(tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": rows}))
    assert level.grid.cells_of(TileCode.TELEPORT_PAD) == (GridCell(5, 3), GridCell(0, 9))


# -- what version 2 refuses ---------------------------------------------------


@pytest.mark.parametrize("pads", [1, 3])
def test_a_broken_pad_pair_is_refused_before_the_stage_starts(tmp_path: Path, pads: int) -> None:
    error = reject(
        tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": gimmick_rows(pads=pads)})
    )
    assert error.field == "grid.rows"
    assert f"zero or exactly {TELEPORT_PAIR_SIZE} teleport pads" in error.message
    assert f"found {pads}" in error.message


def test_an_unknown_character_is_still_refused_in_version_two(tmp_path: Path) -> None:
    rows = gimmick_rows()
    rows[4] = "E" + rows[4][1:]
    error = reject(tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": rows}))
    assert error.field == "grid.rows[4][0]"


def test_a_lower_case_conveyor_code_is_not_a_conveyor(tmp_path: Path) -> None:
    """The alphabet is upper case; accepting ``a`` would make two spellings of one tile."""
    rows = gimmick_rows()
    rows[4] = "a" + rows[4][1:]
    error = reject(tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": rows}))
    assert error.field == "grid.rows[4][0]"


def test_version_two_keeps_every_rule_version_one_had(tmp_path: Path) -> None:
    rows = gimmick_rows()
    rows[15] = "0000000880000000"
    error = reject(tmp_path, gimmick_level(grid={"width": 16, "height": 16, "rows": rows}))
    assert error.field == "grid.rows"
    assert "exactly one home base" in error.message


def test_a_spawn_on_gimmick_terrain_is_refused(tmp_path: Path) -> None:
    """Traversable is not spawnable: a tank would be displaced on the tick it entered play."""
    document = gimmick_level()
    document["spawns"]["players"] = [{"slot": 1, "x": 4, "y": 4}]
    error = reject(tmp_path, document)
    assert error.field == "spawns.players[0]"
    assert "must be empty ground, found CONVEYOR_N" in error.message


# -- what version 1 must keep refusing ----------------------------------------


@pytest.mark.parametrize("tile", list(GIMMICK_TILES))
def test_a_classic_level_carrying_a_gimmick_code_is_refused(tmp_path: Path, tile: TileCode) -> None:
    document = synthetic_level()
    rows = [list(line) for line in SYNTHETIC_ROWS]
    rows[4][4] = tile.value
    document["grid"]["rows"] = ["".join(line) for line in rows]
    error = reject(tmp_path, document)
    assert error.field == "grid.rows[4][4]"
    assert tile.name in error.message
    assert "level schema version 2" in error.message


def test_the_classic_schema_file_still_pins_the_classic_alphabet() -> None:
    """The version 1 schema is not edited; a later format is a new file beside it."""
    import json

    schemas = bundled_content_root() / "schemas"
    v1 = json.loads((schemas / LEVEL_SCHEMA_FILENAMES[1]).read_text(encoding="utf-8"))
    v2 = json.loads((schemas / LEVEL_SCHEMA_FILENAMES[2]).read_text(encoding="utf-8"))
    assert v1["properties"]["schema_version"] == {"const": 1}
    assert v1["properties"]["grid"]["properties"]["rows"]["items"]["pattern"] == "^[0-8]{16}$"
    assert v2["properties"]["schema_version"] == {"const": 2}
    assert v2["properties"]["grid"]["properties"]["rows"]["items"]["pattern"] == "^[0-9A-D]{16}$"
    assert v1["$id"] != v2["$id"]


def test_the_three_classic_levels_are_still_version_one() -> None:
    pack = load_bundled_pack()
    assert pack.content_schema_version == LEVEL_SCHEMA_VERSION
    assert pack.level_ids == ("classic-01", "classic-02", "classic-03")
    for level in pack.levels:
        assert level.schema_version == LEVEL_SCHEMA_VERSION
        assert not uses_gimmick_tiles(level.grid.rows)


# -- pack agreement -----------------------------------------------------------


def test_a_pack_and_its_levels_must_agree_on_the_version(tmp_path: Path) -> None:
    manifest = build_pack(
        tmp_path,
        levels={"levels/synthetic-01.json": gimmick_level()},
        manifest=synthetic_manifest(content_schema_version=LEVEL_SCHEMA_VERSION),
    )
    error = rejection(lambda: load_pack(manifest, root=tmp_path))
    assert error.field == "schema_version"
    assert "is 2 but pack" in error.message


def test_a_version_two_pack_of_version_one_levels_is_refused(tmp_path: Path) -> None:
    manifest = build_pack(
        tmp_path,
        levels={"levels/synthetic-01.json": synthetic_level()},
        manifest=synthetic_manifest(content_schema_version=GIMMICK_LEVEL_SCHEMA_VERSION),
    )
    error = rejection(lambda: load_pack(manifest, root=tmp_path))
    assert error.field == "schema_version"
    assert "is 1 but pack" in error.message


def test_a_version_two_pack_loads_whole(tmp_path: Path) -> None:
    manifest = build_pack(
        tmp_path,
        levels={"levels/synthetic-01.json": gimmick_level()},
        manifest=synthetic_manifest(content_schema_version=GIMMICK_LEVEL_SCHEMA_VERSION),
    )
    pack = load_pack(manifest, root=tmp_path)
    assert pack.content_schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    assert pack.levels[0].grid.cells_of(TileCode.TELEPORT_PAD) == PAD_CELLS


# -- the bundled sample -------------------------------------------------------


def test_the_bundled_sample_pack_is_separate_from_the_classic_one() -> None:
    demo = load_gimmick_demo_pack()
    assert demo.pack_id == "gimmick-demo"
    assert demo.content_schema_version == GIMMICK_LEVEL_SCHEMA_VERSION
    assert demo.level_ids == ("gimmick-demo-01",)
    assert demo.pack_id not in {load_bundled_pack().pack_id}


def test_the_bundled_sample_exercises_both_gimmicks() -> None:
    level = load_gimmick_demo_pack().levels[0]
    for tile in GIMMICK_TILES:
        assert level.grid.cells_of(tile), tile.name
    assert len(level.grid.cells_of(TileCode.TELEPORT_PAD)) == TELEPORT_PAIR_SIZE
    assert level.waves == (level.waves[0],)


def test_the_sample_level_is_not_named_like_a_classic_fixture() -> None:
    """``classic-*.json`` is globbed by the simulation's stage fixtures, which pin three."""
    levels = bundled_content_root() / "levels"
    assert sorted(path.name for path in levels.glob("classic-*.json")) == [
        "classic-01.json",
        "classic-02.json",
        "classic-03.json",
    ]
    assert (levels / "gimmick-demo-01.json").is_file()
