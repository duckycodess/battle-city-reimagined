"""The terrain tables, asserted directly.

Terrain behaviour is data, so the tests read the tables rather than re-deriving them from
gameplay. A table edit that nobody meant shows up here.
"""

from __future__ import annotations

import pytest
from battle_city_sim import (
    CLASSIC_TILES,
    CONVEYOR_DIRECTIONS,
    DIRECTION_ORDER,
    GIMMICK_TILES,
    MIRROR_REFLECTIONS,
    PROJECTILE_TILE_DAMAGE,
    TILE_BY_CHAR,
    TILE_CODE_CHARS,
    TILES_BLOCKING_TANKS,
    TILES_PASSING_PROJECTILES,
    Direction,
    GridPos,
    Tile,
    TileGrid,
    blocks_tank,
    conveyor_direction,
    is_teleport_pad,
    passes_projectile,
    tile_code_char,
)


def test_tile_codes_match_the_content_specification() -> None:
    assert [(tile.name, tile.value, tile_code_char(tile)) for tile in Tile] == [
        ("EMPTY", 0, "0"),
        ("STONE", 1, "1"),
        ("BRICK", 2, "2"),
        ("MIRROR_NE", 3, "3"),
        ("MIRROR_SE", 4, "4"),
        ("WATER", 5, "5"),
        ("CRACKED_BRICK", 6, "6"),
        ("FOREST", 7, "7"),
        ("HOME", 8, "8"),
        ("CONVEYOR_N", 9, "9"),
        ("CONVEYOR_E", 10, "A"),
        ("CONVEYOR_S", 11, "B"),
        ("CONVEYOR_W", 12, "C"),
        ("TELEPORT_PAD", 13, "D"),
    ]


def test_the_classic_codes_are_untouched_by_the_gimmick_additions() -> None:
    """Appending to the enum must not renumber or re-letter a classic tile."""
    assert [tile.value for tile in CLASSIC_TILES] == list(range(9))
    assert "".join(tile_code_char(tile) for tile in CLASSIC_TILES) == "012345678"
    assert tuple(Tile) == CLASSIC_TILES + GIMMICK_TILES


def test_the_row_alphabet_is_a_bijection() -> None:
    """A duplicated code would silently collapse two tiles into one on decode."""
    assert len(TILE_CODE_CHARS) == len(Tile)
    assert len(TILE_BY_CHAR) == len(Tile)
    assert all(TILE_BY_CHAR[tile_code_char(tile)] is tile for tile in Tile)
    assert all(len(code) == 1 for code in TILE_CODE_CHARS.values())


def test_a_row_character_is_never_derived_from_the_member_value() -> None:
    """``str(Tile.CONVEYOR_E.value)`` is ``"10"``: two columns where a row allows one."""
    assert str(Tile.CONVEYOR_E.value) == "10"
    assert tile_code_char(Tile.CONVEYOR_E) == "A"
    grid = TileGrid.from_rows(["AAAA"])
    assert grid.to_rows() == ("AAAA",)


def test_every_conveyor_pushes_the_way_its_name_reads() -> None:
    assert CONVEYOR_DIRECTIONS == {
        Tile.CONVEYOR_N: Direction.UP,
        Tile.CONVEYOR_E: Direction.RIGHT,
        Tile.CONVEYOR_S: Direction.DOWN,
        Tile.CONVEYOR_W: Direction.LEFT,
    }
    assert conveyor_direction(Tile.CONVEYOR_W) is Direction.LEFT
    assert conveyor_direction(Tile.TELEPORT_PAD) is None
    assert conveyor_direction(Tile.EMPTY) is None


def test_only_the_pad_tile_is_a_teleport_pad() -> None:
    assert [tile for tile in Tile if is_teleport_pad(tile)] == [Tile.TELEPORT_PAD]


def test_only_ground_forest_and_the_gimmick_tiles_are_traversable() -> None:
    traversable = {tile for tile in Tile if not blocks_tank(tile)}
    assert traversable == {Tile.EMPTY, Tile.FOREST, *GIMMICK_TILES}
    assert set(Tile) - traversable == TILES_BLOCKING_TANKS


def test_water_blocks_tanks_but_not_projectiles() -> None:
    assert blocks_tank(Tile.WATER)
    assert passes_projectile(Tile.WATER)


def test_projectiles_pass_only_empty_ground_water_and_forest() -> None:
    assert {Tile.EMPTY, Tile.WATER, Tile.FOREST, *GIMMICK_TILES} == TILES_PASSING_PROJECTILES


def test_brick_takes_two_hits_to_clear() -> None:
    assert PROJECTILE_TILE_DAMAGE[Tile.BRICK] is Tile.CRACKED_BRICK
    assert PROJECTILE_TILE_DAMAGE[Tile.CRACKED_BRICK] is Tile.EMPTY
    assert Tile.STONE not in PROJECTILE_TILE_DAMAGE
    assert Tile.HOME not in PROJECTILE_TILE_DAMAGE


def test_the_mirror_tables_cover_every_direction() -> None:
    for tile, table in MIRROR_REFLECTIONS.items():
        assert set(table) == set(DIRECTION_ORDER), tile
        assert set(table.values()) == set(DIRECTION_ORDER), tile


@pytest.mark.parametrize("tile", list(GIMMICK_TILES))
def test_a_gimmick_tile_passes_projectiles_and_conceals_nothing(tile: Tile) -> None:
    """A shot crossing a conveyor or a pad is neither steered, stopped nor transported."""
    from battle_city_sim.tiles import TILES_CONCEALING_TANKS

    assert passes_projectile(tile)
    assert tile not in PROJECTILE_TILE_DAMAGE
    assert tile not in MIRROR_REFLECTIONS
    assert tile not in TILES_CONCEALING_TANKS


@pytest.mark.parametrize("tile", [Tile.MIRROR_NE, Tile.MIRROR_SE])
def test_a_mirror_reflection_is_its_own_inverse(tile: Tile) -> None:
    table = MIRROR_REFLECTIONS[tile]
    for direction in DIRECTION_ORDER:
        assert table[table[direction]] is direction


def test_the_mirror_tables_reproduce_the_historical_velocity_swaps() -> None:
    """Tile 3 swapped (vx, vy); tile 4 swapped and negated. See MIRROR_REFLECTIONS."""
    for direction in DIRECTION_ORDER:
        dx, dy = direction.delta
        assert MIRROR_REFLECTIONS[Tile.MIRROR_NE][direction].delta == (dy, dx)
        assert MIRROR_REFLECTIONS[Tile.MIRROR_SE][direction].delta == (-dy, -dx)


def test_a_grid_edit_returns_a_new_grid() -> None:
    grid = TileGrid.from_rows(["012", "345", "678"])
    changed = grid.with_tile(GridPos(0, 0), Tile.BRICK)
    assert grid.at(GridPos(0, 0)) is Tile.EMPTY
    assert changed.at(GridPos(0, 0)) is Tile.BRICK
    assert grid.with_tile(GridPos(1, 1), Tile.MIRROR_SE) is grid


def test_grid_lookups_outside_the_grid_raise() -> None:
    grid = TileGrid.from_rows(["01", "23"])
    assert not grid.contains(GridPos(2, 0))
    with pytest.raises(IndexError):
        grid.at(GridPos(-1, 0))
    with pytest.raises(IndexError):
        grid.with_tile(GridPos(0, 2), Tile.EMPTY)


def test_a_grid_round_trips_through_tile_code_rows() -> None:
    rows = ["0123", "4567", "8000", "0000"]
    assert TileGrid.from_rows(rows).to_rows() == tuple(rows)


def test_direction_deltas_point_the_expected_way() -> None:
    assert Direction.UP.delta == (0, -1)
    assert Direction.DOWN.delta == (0, 1)
    assert Direction.LEFT.delta == (-1, 0)
    assert Direction.RIGHT.delta == (1, 0)
    assert Direction.RIGHT.scaled(3) == (3, 0)


def test_only_ascii_tile_codes_are_accepted() -> None:
    assert set(TILE_BY_CHAR) == set("0123456789ABCD")
    assert all(character.isascii() for character in TILE_BY_CHAR)


@pytest.mark.parametrize(
    ("code", "label"),
    [
        ("٣", "arabic-indic three"),
        ("²", "superscript two"),
        ("０", "fullwidth zero"),
        ("E", "out-of-range ascii letter"),
        ("a", "lower-case letter"),
        (" ", "space"),
    ],
    ids=lambda value: value if isinstance(value, str) and value.isascii() else "nonascii",
)
def test_a_non_tile_character_raises_a_stage_validation_error(code: str, label: str) -> None:
    """A Unicode digit is not a tile code, and no bare ValueError may escape.

    ``str.isdigit()`` accepts U+0663 and friends, and ``int()`` then either mislabels the
    cell or raises a plain ``ValueError`` that names neither the field nor the coordinate.
    """
    from battle_city_sim import StageValidationError

    rows = ["000", "0" + code + "0", "000"]
    with pytest.raises(StageValidationError, match=r"grid.rows\[1\]\[1\]: unknown tile code"):
        TileGrid.from_rows(rows)
