"""Level validation: every rejection names the file and the field that caused it.

Each case mutates one field of a valid synthetic level, so the assertion on the reported
field is meaningful: only one thing is wrong at a time.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from battle_city_content import (
    ContentValidationError,
    GridCell,
    Level,
    LevelSource,
    PlayerSpawn,
    TileCode,
    Wave,
    load_level,
)
from content_helpers import FREE_CELL, SYNTHETIC_ROWS, mutated_level, rejection, write_json


def load(tmp_path: Path, document: dict[str, Any], name: str = "level.json") -> Level:
    return load_level(write_json(tmp_path / name, document))


def reject(tmp_path: Path, document: dict[str, Any]) -> ContentValidationError:
    path = write_json(tmp_path / "level.json", document)
    error = rejection(lambda: load_level(path))
    assert error.path == path
    assert str(error).startswith(str(path))
    return error


def test_a_valid_synthetic_level_loads(tmp_path: Path) -> None:
    level = load(tmp_path, mutated_level(lambda document: None))
    assert level.level_id == "synthetic-01"
    assert level.name == "Synthetic Stage 1"
    assert level.schema_version == 1
    assert level.grid.rows == SYNTHETIC_ROWS
    assert level.player_spawns == (PlayerSpawn(slot=1, cell=GridCell(*FREE_CELL)),)
    assert level.enemy_spawns == (GridCell(0, 0), GridCell(15, 0))
    assert level.base_cell == GridCell(7, 15)
    assert level.grid.tile_at(level.base_cell) is TileCode.HOME
    assert level.source is None
    assert level.waves == ()
    assert level.origin == tmp_path / "level.json"


def test_a_loaded_level_is_immutable(tmp_path: Path) -> None:
    level = load(tmp_path, mutated_level(lambda document: None))
    with pytest.raises(dataclasses.FrozenInstanceError):
        level.level_id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        level.grid.width = 8  # type: ignore[misc]
    assert isinstance(level.grid.rows, tuple)
    assert isinstance(level.player_spawns, tuple)
    assert isinstance(level.enemy_spawns, tuple)
    assert isinstance(level.waves, tuple)


def test_a_level_accepts_optional_provenance(tmp_path: Path) -> None:
    source = {
        "repository": "https://example.invalid/levels",
        "revision": "0" * 40,
        "path": "stages/one.json",
    }
    level = load(tmp_path, mutated_level(lambda document: document.update(source=source)))
    assert level.source == LevelSource(
        repository="https://example.invalid/levels", revision="0" * 40, path="stages/one.json"
    )


def test_a_level_accepts_optional_forward_declared_waves(tmp_path: Path) -> None:
    waves = [{"enemies": 4}, {"enemies": 20}]
    level = load(tmp_path, mutated_level(lambda document: document.update(waves=waves)))
    assert level.waves == (Wave(enemies=4), Wave(enemies=20))


@pytest.mark.parametrize("field", ["schema_version", "id", "name", "grid", "spawns"])
def test_a_missing_required_field_is_named(tmp_path: Path, field: str) -> None:
    error = reject(tmp_path, mutated_level(lambda document: document.pop(field)))
    assert error.field == field
    assert error.message == "is required"


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda document: document.update(extra=1), "extra"),
        (lambda document: document["grid"].update(depth=1), "grid.depth"),
        (lambda document: document["spawns"].update(bosses=[]), "spawns.bosses"),
        (lambda document: document["spawns"]["players"][0].update(z=1), "spawns.players[0].z"),
    ],
)
def test_an_unknown_field_is_rejected(tmp_path: Path, mutate: Any, field: str) -> None:
    error = reject(tmp_path, mutated_level(mutate))
    assert error.field == field
    assert error.message == "is not a known field"


@pytest.mark.parametrize("version", [0, 3, 99, "1", 1.0, True, None])
def test_an_unsupported_schema_version_is_rejected(tmp_path: Path, version: Any) -> None:
    """An unknown version is checked against the classic schema, whose const names it.

    Guessing a supported version instead would validate a document against rules it
    never claimed to follow.
    """
    error = reject(
        tmp_path, mutated_level(lambda document: document.update(schema_version=version))
    )
    assert error.field == "schema_version"


@pytest.mark.parametrize(
    "level_id",
    [
        "Classic-01",
        "classic_01",
        "classic--01",
        "-classic",
        "classic-",
        "",
        "classic 1",
        "classic-01\n",
        "\nclassic-01",
        "classic-01\nclassic-02",
        "classic-01\r\n",
    ],
)
def test_an_invalid_stable_identifier_is_rejected(tmp_path: Path, level_id: str) -> None:
    """The newline cases are the ones a ``$`` anchor used to let through.

    An identifier carrying a trailing newline would reach the pack, where it has to
    equal the manifest entry, and every consumer that prints or keys on it.
    """
    error = reject(tmp_path, mutated_level(lambda document: document.update(id=level_id)))
    assert error.field == "id"


def test_an_empty_name_is_rejected(tmp_path: Path) -> None:
    error = reject(tmp_path, mutated_level(lambda document: document.update(name="")))
    assert error.field == "name"
    assert "at least 1 character" in error.message


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda document: document["grid"].update(width=15), "grid.width"),
        (lambda document: document["grid"].update(width=17), "grid.width"),
        (lambda document: document["grid"].update(height=15), "grid.height"),
        (lambda document: document["grid"].update(width="16"), "grid.width"),
        (lambda document: document["grid"].pop("rows"), "grid.rows"),
    ],
)
def test_a_malformed_dimension_is_rejected(tmp_path: Path, mutate: Any, field: str) -> None:
    assert reject(tmp_path, mutated_level(mutate)).field == field


@pytest.mark.parametrize("count", [0, 1, 15, 17, 32])
def test_a_wrong_row_count_is_rejected(tmp_path: Path, count: int) -> None:
    rows = [SYNTHETIC_ROWS[index % 16] for index in range(count)]
    error = reject(tmp_path, mutated_level(lambda document: document["grid"].update(rows=rows)))
    assert error.field == "grid.rows"
    assert "item(s)" in error.message


@pytest.mark.parametrize("row", ["", "0", "0" * 15, "0" * 17, "0" * 64])
def test_a_wrong_row_width_is_rejected(tmp_path: Path, row: str) -> None:
    error = reject(
        tmp_path, mutated_level(lambda document: document["grid"]["rows"].__setitem__(0, row))
    )
    assert error.field == "grid.rows[0]"
    assert error.message == f"must have 16 tile codes, found {len(row)}"


@pytest.mark.parametrize("code", ["E", "a", "-", " ", "٣", "０", "²", "8"])
def test_an_unknown_tile_code_names_its_column(tmp_path: Path, code: str) -> None:
    row = "000" + code + "0" * 12
    error = reject(
        tmp_path, mutated_level(lambda document: document["grid"]["rows"].__setitem__(4, row))
    )
    if code == "8":
        # A second home base is a legal tile in an illegal quantity, checked separately.
        assert error.field == "grid.rows"
        return
    assert error.field == "grid.rows[4][3]"
    assert error.message == f"is not a known tile code: {code!r}"


@pytest.mark.parametrize("code", ["9", "A", "B", "C", "D"])
def test_a_gimmick_code_in_a_classic_level_names_the_version_it_needs(
    tmp_path: Path, code: str
) -> None:
    """ "Not a tile code" would be wrong and unhelpful: it is a code from a later version."""
    row = "000" + code + "0" * 12
    error = reject(
        tmp_path, mutated_level(lambda document: document["grid"]["rows"].__setitem__(4, row))
    )
    assert error.field == "grid.rows[4][3]"
    assert "level schema version 2" in error.message
    assert "declares an earlier version" in error.message


def test_a_non_string_row_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path, mutated_level(lambda document: document["grid"]["rows"].__setitem__(0, 16))
    )
    assert error.field == "grid.rows[0]"
    assert error.message == "must be a string, found integer"


def test_a_level_without_a_home_base_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path, mutated_level(lambda document: document["grid"]["rows"].__setitem__(15, "0" * 16))
    )
    assert error.field == "grid.rows"
    assert error.message == "must declare exactly one home base tile, found 0"


def test_a_level_with_two_home_bases_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["grid"]["rows"].__setitem__(15, "0000000800000008")
        ),
    )
    assert error.field == "grid.rows"
    assert error.message == "must declare exactly one home base tile, found 2"


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda document: document["spawns"].pop("players"), "spawns.players"),
        (lambda document: document["spawns"].pop("enemies"), "spawns.enemies"),
        (lambda document: document["spawns"].update(players=[]), "spawns.players"),
        (lambda document: document["spawns"].update(enemies=[]), "spawns.enemies"),
    ],
)
def test_a_level_must_declare_both_spawn_lists(tmp_path: Path, mutate: Any, field: str) -> None:
    assert reject(tmp_path, mutated_level(mutate)).field == field


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"slot": True, "x": 8, "y": 14}, "spawns.players[0].slot"),
        ({"slot": 1.5, "x": 8, "y": 14}, "spawns.players[0].slot"),
        ({"slot": "1", "x": 8, "y": 14}, "spawns.players[0].slot"),
        ({"slot": 0, "x": 8, "y": 14}, "spawns.players[0].slot"),
        ({"slot": 1, "x": "8", "y": 14}, "spawns.players[0].x"),
        ({"slot": 1, "x": 8.0, "y": 14}, "spawns.players[0].x"),
        ({"slot": 1, "y": 14}, "spawns.players[0].x"),
        ({"slot": 1, "x": 8}, "spawns.players[0].y"),
        ({"x": 8, "y": 14}, "spawns.players[0].slot"),
    ],
)
def test_a_malformed_player_spawn_is_rejected(
    tmp_path: Path, value: dict[str, Any], field: str
) -> None:
    error = reject(
        tmp_path,
        mutated_level(lambda document: document["spawns"]["players"].__setitem__(0, value)),
    )
    assert error.field == field


@pytest.mark.parametrize(("x", "y"), [(16, 14), (-1, 14), (8, 16), (8, -1), (99, 99)])
def test_a_spawn_outside_the_grid_is_rejected(tmp_path: Path, x: int, y: int) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["players"].__setitem__(
                0, {"slot": 1, "x": x, "y": y}
            )
        ),
    )
    assert error.field in {"spawns.players[0].x", "spawns.players[0].y"}


@pytest.mark.parametrize(
    ("row", "tile"),
    [
        (1, "STONE"),
        (3, "BRICK"),
        (5, "WATER"),
        (7, "MIRROR_NE"),
        (9, "CRACKED_BRICK"),
        (11, "FOREST"),
    ],
)
def test_a_spawn_on_blocking_or_concealing_terrain_is_rejected(
    tmp_path: Path, row: int, tile: str
) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["players"].__setitem__(
                0, {"slot": 1, "x": 0, "y": row}
            )
        ),
    )
    assert error.field == "spawns.players[0]"
    assert error.message == f"cell (0, {row}) must be empty ground, found {tile}"


def test_a_spawn_on_the_home_base_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["enemies"].__setitem__(0, {"x": 7, "y": 15})
        ),
    )
    assert error.field == "spawns.enemies[0]"
    assert error.message == "cell (7, 15) must be empty ground, found HOME"


def test_a_duplicate_player_slot_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["players"].append({"slot": 1, "x": 2, "y": 0})
        ),
    )
    assert error.field == "spawns.players[1].slot"
    assert error.message == "repeats slot 1, already declared at spawns.players[0]"


def test_a_duplicate_player_cell_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["players"].append(
                {"slot": 2, "x": FREE_CELL[0], "y": FREE_CELL[1]}
            )
        ),
    )
    assert error.field == "spawns.players[1]"
    assert "repeats cell (8, 14)" in error.message


def test_a_duplicate_enemy_cell_is_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(lambda document: document["spawns"]["enemies"].append({"x": 0, "y": 0})),
    )
    assert error.field == "spawns.enemies[2]"
    assert "repeats cell (0, 0)" in error.message


def test_overlapping_player_and_enemy_spawns_are_rejected(tmp_path: Path) -> None:
    error = reject(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"]["enemies"].append(
                {"x": FREE_CELL[0], "y": FREE_CELL[1]}
            )
        ),
    )
    assert error.field == "spawns"
    assert error.message == "player and enemy spawns share cell (8, 14)"


def test_player_spawns_are_returned_in_slot_order(tmp_path: Path) -> None:
    level = load(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"].update(
                players=[{"slot": 3, "x": 2, "y": 0}, {"slot": 1, "x": 4, "y": 0}]
            )
        ),
    )
    assert [spawn.slot for spawn in level.player_spawns] == [1, 3]
    assert level.player_spawn_for(3).cell == GridCell(2, 0)
    with pytest.raises(KeyError):
        level.player_spawn_for(2)


def test_a_high_player_slot_is_accepted(tmp_path: Path) -> None:
    # Slot numbering has no invented upper bound: the simulation only requires >= 1.
    level = load(
        tmp_path,
        mutated_level(
            lambda document: document["spawns"].update(players=[{"slot": 4096, "x": 2, "y": 0}])
        ),
    )
    assert level.player_spawns == (PlayerSpawn(slot=4096, cell=GridCell(2, 0)),)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (
            lambda document: document.update(source={"revision": "0" * 40, "path": "a"}),
            "source.repository",
        ),
        (
            lambda document: document.update(
                source={"repository": "https://x.invalid", "revision": "nope", "path": "a"}
            ),
            "source.revision",
        ),
        (
            lambda document: document.update(
                source={"repository": "ftp://x.invalid", "revision": "0" * 40, "path": "a"}
            ),
            "source.repository",
        ),
        (
            lambda document: document.update(
                source={
                    "repository": "https://x.invalid",
                    "revision": "0" * 40,
                    "path": "a",
                    "tag": 1,
                }
            ),
            "source.tag",
        ),
        # A revision is a 40-character hex commit id and nothing else; a trailing
        # newline used to satisfy the pattern's "$" and travel on into the record.
        (
            lambda document: document.update(
                source={"repository": "https://x.invalid", "revision": "0" * 40 + "\n", "path": "a"}
            ),
            "source.revision",
        ),
        (
            lambda document: document.update(
                source={"repository": "https://x.invalid\n", "revision": "0" * 40, "path": "a"}
            ),
            "source.repository",
        ),
    ],
)
def test_malformed_provenance_is_rejected(tmp_path: Path, mutate: Any, field: str) -> None:
    assert reject(tmp_path, mutated_level(mutate)).field == field


@pytest.mark.parametrize(
    ("waves", "field"),
    [
        ([], "waves"),
        ([{"enemies": 0}], "waves[0].enemies"),
        ([{"enemies": -1}], "waves[0].enemies"),
        ([{"enemies": "4"}], "waves[0].enemies"),
        ([{}], "waves[0].enemies"),
        ([{"enemies": 4, "cadence": 30}], "waves[0].cadence"),
    ],
)
def test_malformed_wave_metadata_is_rejected(tmp_path: Path, waves: Any, field: str) -> None:
    assert (
        reject(tmp_path, mutated_level(lambda document: document.update(waves=waves))).field
        == field
    )


def test_a_rejected_level_is_reported_with_file_and_field(tmp_path: Path) -> None:
    path = write_json(tmp_path / "broken.json", mutated_level(lambda d: d.pop("name")))
    error = rejection(lambda: load_level(path))
    assert error.path == path
    assert error.field == "name"
    assert error.message == "is required"
    assert str(error) == f"{path}: name: is required"
