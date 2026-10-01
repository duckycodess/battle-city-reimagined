"""The editable shape of a level: a mutable draft that can be invalid.

:class:`~battle_city_content.Level` is frozen and already validated, which is exactly
what a caller wants to *play* and exactly what an author cannot *edit*: painting a tile
over the home base, or lifting a spawn before putting it down, passes through states no
validated record is allowed to hold. :class:`LevelDraft` is that working copy. It
enforces only what a grid is -- rectangular, in bounds, made of known tile codes -- and
leaves every level rule to the content loader, so a mistake is reported by the same
validator the game uses, with the same file and the same field, rather than by a second
rule set that could drift from it.

Identity and provenance survive editing. ``level_id`` is the stable identifier the
content specification requires; ``source`` and ``waves`` are carried through untouched
even though nothing here interprets them, because dropping a converted level's
provenance on the first edit would quietly destroy the only record of where the layout
came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from battle_city_content import (
    CLASSIC_GRID_SIZE,
    LEVEL_SCHEMA_VERSION,
    TILE_BY_CHAR,
    GridCell,
    Level,
    LevelSource,
    PlayerSpawn,
    TileCode,
    Wave,
)

from .serialization import JsonValue

BLANK_BASE_CELL: Final[GridCell] = GridCell(x=7, y=15)
"""Where :meth:`LevelDraft.blank` puts the home base: bottom centre, as the classic stages do."""

BLANK_PLAYER_CELL: Final[GridCell] = GridCell(x=4, y=14)
BLANK_ENEMY_CELL: Final[GridCell] = GridCell(x=0, y=0)
"""A blank level still declares one of each: the level schema requires at least one."""


@dataclass(slots=True)
class LevelDraft:
    """A level being authored. Mutable, and free to be momentarily invalid."""

    level_id: str
    name: str
    rows: list[list[str]]
    player_spawns: list[PlayerSpawn] = field(default_factory=list)
    enemy_spawns: list[GridCell] = field(default_factory=list)
    source: LevelSource | None = None
    waves: tuple[Wave, ...] = ()
    schema_version: int = LEVEL_SCHEMA_VERSION

    # -- construction ----------------------------------------------------------

    @classmethod
    def blank(
        cls,
        *,
        level_id: str,
        name: str,
        size: int = CLASSIC_GRID_SIZE,
    ) -> LevelDraft:
        """An empty field with a base, one player slot and one enemy spawn.

        The three are not decoration. A level with no home base, no player spawn or no
        enemy spawn is rejected by the schema and by the loader, so a ``create`` that
        produced one would hand the author a file they cannot save, validate or play.
        """
        draft = cls(
            level_id=level_id,
            name=name,
            rows=[[TileCode.EMPTY.value] * size for _ in range(size)],
            player_spawns=[PlayerSpawn(slot=1, cell=BLANK_PLAYER_CELL)],
            enemy_spawns=[BLANK_ENEMY_CELL],
        )
        draft.paint(BLANK_BASE_CELL, TileCode.HOME)
        return draft

    @classmethod
    def from_level(cls, level: Level) -> LevelDraft:
        """Open a validated level for editing, keeping its identity and provenance."""
        return cls(
            level_id=level.level_id,
            name=level.name,
            rows=[list(row) for row in level.grid.rows],
            player_spawns=list(level.player_spawns),
            enemy_spawns=list(level.enemy_spawns),
            source=level.source,
            waves=level.waves,
            schema_version=level.schema_version,
        )

    # -- grid ------------------------------------------------------------------

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def contains(self, cell: GridCell) -> bool:
        return 0 <= cell.y < self.height and 0 <= cell.x < self.width

    def tile_at(self, cell: GridCell) -> TileCode:
        """The tile at ``cell``; raises :class:`IndexError` when out of bounds."""
        self._require_in_bounds(cell)
        return TILE_BY_CHAR[self.rows[cell.y][cell.x]]

    def paint(self, cell: GridCell, tile: TileCode) -> None:
        """Set ``cell`` to ``tile``. Out of bounds is a caller bug, not bad content."""
        self._require_in_bounds(cell)
        self.rows[cell.y][cell.x] = tile.value

    def grid_rows(self) -> tuple[str, ...]:
        """The grid as the row strings a level file declares."""
        return tuple("".join(row) for row in self.rows)

    def cells_of(self, tile: TileCode) -> tuple[GridCell, ...]:
        """Every cell holding ``tile``, in row-major order."""
        return tuple(
            GridCell(x, y)
            for y, row in enumerate(self.rows)
            for x, code in enumerate(row)
            if code == tile.value
        )

    def _require_in_bounds(self, cell: GridCell) -> None:
        if not self.contains(cell):
            raise IndexError(f"cell out of bounds: ({cell.x}, {cell.y})")

    # -- spawns ----------------------------------------------------------------

    @property
    def player_slots(self) -> tuple[int, ...]:
        """Declared player slots, ascending."""
        return tuple(sorted(spawn.slot for spawn in self.player_spawns))

    def next_free_slot(self) -> int:
        """The lowest one-based slot this draft does not already declare."""
        used = set(self.player_slots)
        slot = 1
        while slot in used:
            slot += 1
        return slot

    def player_spawn_at(self, cell: GridCell) -> PlayerSpawn | None:
        for spawn in self.player_spawns:
            if spawn.cell == cell:
                return spawn
        return None

    def set_player_spawn(self, slot: int, cell: GridCell) -> None:
        """Place ``slot`` at ``cell``, moving it if it was already somewhere else.

        Kept sorted so a draft and the record the loader hands back compare equal: the
        loader sorts player spawns, so an unsorted draft would round-trip into a
        different order and make a save-then-reload test look like a content change.
        """
        self._require_in_bounds(cell)
        self.player_spawns = sorted(
            [spawn for spawn in self.player_spawns if spawn.slot != slot]
            + [PlayerSpawn(slot=slot, cell=cell)]
        )

    def remove_player_spawn(self, slot: int) -> bool:
        """Drop ``slot``. Returns whether anything was removed."""
        remaining = [spawn for spawn in self.player_spawns if spawn.slot != slot]
        removed = len(remaining) != len(self.player_spawns)
        self.player_spawns = remaining
        return removed

    def add_enemy_spawn(self, cell: GridCell) -> bool:
        """Declare an enemy spawn at ``cell``. Returns whether it was new."""
        self._require_in_bounds(cell)
        if cell in self.enemy_spawns:
            return False
        self.enemy_spawns.append(cell)
        return True

    def remove_enemy_spawn(self, cell: GridCell) -> bool:
        """Drop the enemy spawn at ``cell``. Returns whether anything was removed."""
        if cell not in self.enemy_spawns:
            return False
        self.enemy_spawns.remove(cell)
        return True

    def remove_spawn_at(self, cell: GridCell) -> bool:
        """Drop whatever spawn stands on ``cell``. Returns whether anything was removed."""
        spawn = self.player_spawn_at(cell)
        if spawn is not None:
            return self.remove_player_spawn(spawn.slot)
        return self.remove_enemy_spawn(cell)

    # -- serialisation ---------------------------------------------------------

    def to_document(self) -> dict[str, JsonValue]:
        """The level document, with keys in the order the bundled levels use."""
        document: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "id": self.level_id,
            "name": self.name,
        }
        if self.source is not None:
            document["source"] = _source_document(self.source)
        document["grid"] = _grid_document(self.width, self.height, self.grid_rows())
        players: list[JsonValue] = [_player_document(spawn) for spawn in self.player_spawns]
        enemies: list[JsonValue] = [_cell_document(cell) for cell in self.enemy_spawns]
        document["spawns"] = {"players": players, "enemies": enemies}
        if self.waves:
            waves: list[JsonValue] = [{"enemies": wave.enemies} for wave in self.waves]
            document["waves"] = waves
        return document


def _source_document(source: LevelSource) -> dict[str, JsonValue]:
    return {
        "repository": source.repository,
        "revision": source.revision,
        "path": source.path,
    }


def _grid_document(width: int, height: int, rows: tuple[str, ...]) -> dict[str, JsonValue]:
    encoded: list[JsonValue] = list(rows)
    return {"width": width, "height": height, "rows": encoded}


def _player_document(spawn: PlayerSpawn) -> dict[str, JsonValue]:
    return {"slot": spawn.slot, "x": spawn.cell.x, "y": spawn.cell.y}


def _cell_document(cell: GridCell) -> dict[str, JsonValue]:
    return {"x": cell.x, "y": cell.y}
