"""Validated stage definitions.

A :class:`Stage` is the simulation's view of a level: terrain plus spawn points. The
simulation never reads a file, so stages arrive as already-decoded rows and coordinates.
Decoding JSON and validating a pack manifest belongs to the content package.

Coordinate convention
---------------------
The external convention is the content spec's 16x16 grid: ``x`` is the column and ``y``
is the row, both in ``[0, 15]``, and cell ``(0, 0)`` is the top-left playable tile.

The historical runtime stored an 18x18 grid and wrote the 16x16 stage into the interior
at an offset of one cell, using the extra ring as an indestructible border. The rebuild
drops that ring: the playfield is exactly the declared grid, and the world edge is an
explicit bound checked in the rules engine. The ring was never visible in the stage data
and the classic layouts already wall themselves in with brick, so removing it preserves
play while keeping stage coordinates and simulation coordinates identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from .errors import StageValidationError
from .geometry import GridPos
from .tiles import Tile, TileGrid

CLASSIC_GRID_SIZE: Final[int] = 16
"""The classic stage is 16 columns by 16 rows. See the content specification."""


@dataclass(frozen=True, slots=True, order=True)
class PlayerSpawn:
    """Where a player slot enters the stage."""

    slot: int
    cell: GridPos


@dataclass(frozen=True, slots=True)
class Stage:
    """A validated stage. Construct it through :meth:`create`, never directly."""

    stage_id: str
    name: str
    grid: TileGrid
    player_spawns: tuple[PlayerSpawn, ...]
    enemy_spawns: tuple[GridPos, ...]
    base_cell: GridPos

    @classmethod
    def create(
        cls,
        *,
        stage_id: str,
        name: str,
        rows: Sequence[str],
        player_spawns: Sequence[PlayerSpawn],
        enemy_spawns: Sequence[GridPos],
        require_classic_grid: bool = True,
    ) -> Stage:
        """Validate and build a stage.

        ``require_classic_grid`` keeps the classic 16x16 contract on by default. A future
        level format may pass ``False``, but only alongside an accepted content proposal
        that states what the new dimensions mean.

        Raises :class:`StageValidationError` naming the offending field and coordinate.
        """
        if not stage_id:
            raise StageValidationError("id: must not be empty")
        if not name:
            raise StageValidationError("name: must not be empty")

        grid = TileGrid.from_rows(rows)
        if require_classic_grid and (
            grid.width != CLASSIC_GRID_SIZE or grid.height != CLASSIC_GRID_SIZE
        ):
            raise StageValidationError(
                f"grid: classic stages are {CLASSIC_GRID_SIZE}x{CLASSIC_GRID_SIZE}, "
                f"found {grid.width}x{grid.height}"
            )

        base_cells = grid.positions_of(Tile.HOME)
        if len(base_cells) != 1:
            raise StageValidationError(
                f"grid: expected exactly one home base tile, found {len(base_cells)}"
            )

        ordered_players = tuple(sorted(player_spawns))
        if not ordered_players:
            raise StageValidationError("spawns.players: must declare at least one spawn")
        seen_slots: set[int] = set()
        seen_player_cells: set[tuple[int, int]] = set()
        for spawn in ordered_players:
            if spawn.slot < 1:
                raise StageValidationError(
                    f"spawns.players: slot must be at least 1, found {spawn.slot}"
                )
            if spawn.slot in seen_slots:
                raise StageValidationError(f"spawns.players: duplicate slot {spawn.slot}")
            seen_slots.add(spawn.slot)
            cell_key = (spawn.cell.x, spawn.cell.y)
            if cell_key in seen_player_cells:
                # Two slots on one cell start the run with two bodies exactly coincident.
                # Movement tests a target rect against the other tank's current body, so
                # neither could ever step off the other: the run would begin soft-locked.
                raise StageValidationError(
                    f"spawns.players[slot={spawn.slot}]: duplicate spawn at "
                    f"({spawn.cell.x}, {spawn.cell.y})"
                )
            seen_player_cells.add(cell_key)
            _require_free_spawn(grid, spawn.cell, f"spawns.players[slot={spawn.slot}]")

        ordered_enemies = tuple(enemy_spawns)
        if not ordered_enemies:
            raise StageValidationError("spawns.enemies: must declare at least one spawn")
        seen_cells: set[tuple[int, int]] = set()
        for index, cell in enumerate(ordered_enemies):
            key = (cell.x, cell.y)
            if key in seen_cells:
                raise StageValidationError(
                    f"spawns.enemies[{index}]: duplicate spawn at ({cell.x}, {cell.y})"
                )
            seen_cells.add(key)
            _require_free_spawn(grid, cell, f"spawns.enemies[{index}]")

        shared = sorted(seen_player_cells & seen_cells)
        if shared:
            x, y = shared[0]
            raise StageValidationError(f"spawns: player and enemy spawns share cell ({x}, {y})")

        return cls(
            stage_id=stage_id,
            name=name,
            grid=grid,
            player_spawns=ordered_players,
            enemy_spawns=ordered_enemies,
            base_cell=base_cells[0],
        )

    def player_spawn_for(self, slot: int) -> PlayerSpawn:
        """Return the spawn for ``slot``; raises :class:`KeyError` when unknown."""
        for spawn in self.player_spawns:
            if spawn.slot == slot:
                return spawn
        raise KeyError(f"unknown player slot: {slot}")


def _require_free_spawn(grid: TileGrid, cell: GridPos, field: str) -> None:
    """Reject a spawn that is off-grid or standing on anything but empty ground.

    The historical runtime raised on the same condition while populating blocks. Blocking
    terrain would trap a tank inside it from tick zero. Forest is traversable yet still
    rejected, so a stage cannot start a tank already concealed; a stage that wants that
    needs a content proposal saying what it means.
    """
    if not grid.contains(cell):
        raise StageValidationError(
            f"{field}: cell ({cell.x}, {cell.y}) is outside the {grid.width}x{grid.height} grid"
        )
    tile = grid.at(cell)
    if tile is not Tile.EMPTY:
        raise StageValidationError(
            f"{field}: cell ({cell.x}, {cell.y}) must be empty ground, found {tile.name}"
        )
