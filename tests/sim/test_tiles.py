"""The terrain tables, asserted directly.

Terrain behaviour is data, so the tests read the tables rather than re-deriving them from
gameplay. A table edit that nobody meant shows up here.
"""

from __future__ import annotations

import pytest
from battle_city_sim import (
    DIRECTION_ORDER,
    MIRROR_REFLECTIONS,
    PROJECTILE_TILE_DAMAGE,
    TILES_BLOCKING_TANKS,
    TILES_PASSING_PROJECTILES,
    Direction,
    GridPos,
    Tile,
    TileGrid,
    blocks_tank,
    passes_projectile,
)


def test_tile_codes_match_the_content_specification() -> None:
    assert [(tile.name, tile.value) for tile in Tile] == [
        ("EMPTY", 0),
        ("STONE", 1),
        ("BRICK", 2),
        ("MIRROR_NE", 3),
        ("MIRROR_SE", 4),
        ("WATER", 5),
        ("CRACKED_BRICK", 6),
        ("FOREST", 7),
        ("HOME", 8),
    ]


def test_only_empty_ground_and_forest_are_traversable() -> None:
    traversable = {tile for tile in Tile if not blocks_tank(tile)}
    assert traversable == {Tile.EMPTY, Tile.FOREST}
    assert set(Tile) - traversable == TILES_BLOCKING_TANKS


def test_water_blocks_tanks_but_not_projectiles() -> None:
    assert blocks_tank(Tile.WATER)
    assert passes_projectile(Tile.WATER)


def test_projectiles_pass_only_empty_ground_water_and_forest() -> None:
    assert {Tile.EMPTY, Tile.WATER, Tile.FOREST} == TILES_PASSING_PROJECTILES


def test_brick_takes_two_hits_to_clear() -> None:
    assert PROJECTILE_TILE_DAMAGE[Tile.BRICK] is Tile.CRACKED_BRICK
    assert PROJECTILE_TILE_DAMAGE[Tile.CRACKED_BRICK] is Tile.EMPTY
    assert Tile.STONE not in PROJECTILE_TILE_DAMAGE
    assert Tile.HOME not in PROJECTILE_TILE_DAMAGE


def test_the_mirror_tables_cover_every_direction() -> None:
    for tile, table in MIRROR_REFLECTIONS.items():
        assert set(table) == set(DIRECTION_ORDER), tile
        assert set(table.values()) == set(DIRECTION_ORDER), tile


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
