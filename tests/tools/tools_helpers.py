"""Shared builders for the tools and editor tests, and the headless bootstrap.

The SDL driver variables are set here, at import time and before pygame is imported
anywhere, because SDL reads them when a subsystem initialises and a test process must
never open a real window or a real sound device. Every test module in this directory
imports this one first, which is what puts them in place in time.

Two rules about pygame hold across this whole directory.

**Nothing imports pygame at module scope.** pytest imports every selected test module
during collection, so a top-level ``import pygame`` would put pygame in ``sys.modules``
before the first test runs, and ``tests/sim/test_purity.py`` asserts that importing the
simulation pulls in no display library. The editor tests therefore ask for pygame inside
the functions that use it, through :func:`ensure_display` and the capture helpers below.

**There is deliberately no ``conftest.py`` here.** ``tests/client`` has one, and the
repository type-checks with ``mypy packages tests``, which rejects two modules both named
``conftest`` unless the root configuration excludes one or the test tree becomes a
package. That configuration belongs to the integration issue that owns the root
manifests, so this directory keeps the bootstrap in an ordinary module instead. Nothing
is lost: with no module-scope pygame import there is nothing to tidy up after, and in a
whole-suite run these tests sort after ``tests/sim`` anyway.
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

from collections.abc import Mapping, Sequence  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from battle_city_content import (  # noqa: E402
    CLASSIC_GRID_SIZE,
    GridCell,
    Level,
    LevelGrid,
    LevelSource,
    Pack,
    PackLicense,
    PlayerSpawn,
    TileCode,
    Wave,
)
from battle_city_tools import LevelDraft, serialize_document  # noqa: E402

SCREENSHOT_DIR: Path = Path(__file__).parent / "screenshots"
REFRESH_CAPTURES_ENV: str = "BATTLE_CITY_REFRESH_CAPTURES"
"""Set to ``1`` to rewrite the captures tracked in git. See :func:`capture_directory`."""

BASE_CELL: GridCell = GridCell(x=7, y=15)
PLAYER_CELL: GridCell = GridCell(x=4, y=14)
ENEMY_CELL: GridCell = GridCell(x=0, y=0)


def observed[T](value: T) -> T:
    """Return ``value`` unchanged, as a call rather than as an attribute read.

    A strict type checker narrows an enum attribute at the first ``assert ... is`` in a
    test and then calls every later assertion in the same test a comparison of
    non-overlapping literals. Reading through a call keeps each assertion real.
    """
    return value


# -- content builders --------------------------------------------------------


def rows_with(
    overrides: Mapping[tuple[int, int], TileCode] | None = None,
    *,
    base: tuple[int, int] | None = (BASE_CELL.x, BASE_CELL.y),
    size: int = CLASSIC_GRID_SIZE,
) -> tuple[str, ...]:
    """A grid of ground with ``overrides`` painted in and, unless told otherwise, a base."""
    grid = [[TileCode.EMPTY.value for _ in range(size)] for _ in range(size)]
    for (x, y), tile in (overrides or {}).items():
        grid[y][x] = tile.value
    if base is not None:
        grid[base[1]][base[0]] = TileCode.HOME.value
    return tuple("".join(row) for row in grid)


def make_level(
    *,
    level_id: str = "test-level",
    name: str = "Test Level",
    rows: Sequence[str] | None = None,
    player_cells: Sequence[tuple[int, int]] = ((PLAYER_CELL.x, PLAYER_CELL.y),),
    enemy_cells: Sequence[tuple[int, int]] = ((ENEMY_CELL.x, ENEMY_CELL.y),),
    base: tuple[int, int] = (BASE_CELL.x, BASE_CELL.y),
    source: LevelSource | None = None,
    waves: Sequence[Wave] = (),
    origin: Path = Path("/fixture/test-level.json"),
) -> Level:
    """A level record built in memory, with no file behind it."""
    grid_rows = tuple(rows) if rows is not None else rows_with(base=base)
    return Level(
        schema_version=1,
        level_id=level_id,
        name=name,
        grid=LevelGrid(width=CLASSIC_GRID_SIZE, height=CLASSIC_GRID_SIZE, rows=grid_rows),
        player_spawns=tuple(
            PlayerSpawn(slot=index + 1, cell=GridCell(*cell))
            for index, cell in enumerate(player_cells)
        ),
        enemy_spawns=tuple(GridCell(*cell) for cell in enemy_cells),
        base_cell=GridCell(*base),
        source=source,
        waves=tuple(waves),
        origin=origin,
    )


def make_pack(levels: Sequence[Level], *, origin: Path = Path("/fixture/pack.json")) -> Pack:
    """A pack record built in memory."""
    return Pack(
        schema_version=1,
        pack_id="fixture",
        version="1.2.3",
        name="Fixture pack",
        content_schema_version=1,
        authors=("tests",),
        license=PackLicense(spdx_id="NOASSERTION", notice="fixture"),
        levels=tuple(levels),
        origin=origin,
    )


def make_draft(**overrides: Any) -> LevelDraft:
    """A draft over :func:`make_level`."""
    return LevelDraft.from_level(make_level(**overrides))


def write_level(path: Path, draft: LevelDraft) -> Path:
    """Write ``draft`` to ``path`` without validating it, for the invalid-input tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_document(draft.to_document()))
    return path


def semantics(level: Level) -> tuple[object, ...]:
    """Everything about a level that a round trip must preserve, minus its origin."""
    return (
        level.schema_version,
        level.level_id,
        level.name,
        level.grid.rows,
        level.player_spawns,
        level.enemy_spawns,
        level.base_cell,
        level.source,
        level.waves,
    )


# -- captures ----------------------------------------------------------------


def ensure_display() -> None:
    """Bring up the dummy display, importing pygame no earlier than this call."""
    import pygame

    if not pygame.display.get_init():
        pygame.display.init()


def capture_directory(scratch: Path) -> Path:
    """Where a capture run writes: scratch, unless a refresh was explicitly asked for.

    The committed captures record the SDL build, the Python version and the platform that
    produced them, so regenerating them from an ordinary ``pytest`` or ``make ci`` would
    dirty the working tree on every run. Refreshing them is a deliberate act::

        BATTLE_CITY_REFRESH_CAPTURES=1 \\
            uv run --locked pytest tests/tools/test_editor_screenshots.py
    """
    return SCREENSHOT_DIR if os.environ.get(REFRESH_CAPTURES_ENV) == "1" else scratch
