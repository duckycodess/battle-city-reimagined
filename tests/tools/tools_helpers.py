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
manifests, so this directory keeps the bootstrap in an ordinary module instead.

**Pygame is released at every module boundary.** Without a conftest there is no
directory-wide teardown, so :func:`pygame_module_boundary` is a module-scoped autouse
fixture that each pygame-using test module imports by name. When that module's last test
finishes, :func:`release_pygame` shuts pygame down and drops it from ``sys.modules``
again, so no ordering of test directories can carry a loaded display library into
``tests/sim/test_purity.py`` -- ``pytest tests/tools tests/sim`` passes as well as
``pytest tests/sim tests/tools``. Doing it per module rather than once at the end is what
makes it independent of collection order and of any ``-k`` filter.
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys  # noqa: E402
from collections.abc import Iterator, Mapping, Sequence  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
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


def release_pygame() -> None:
    """Shut pygame down and drop it, so the interpreter is as these tests found it.

    ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is present in
    ``sys.modules``, as a proxy for "importing the simulation does not pull in a
    display". These tests load pygame on purpose, so they put it back.

    The client modules that import pygame are dropped with it. They hold a reference to
    the module object being discarded, and a later re-import creates a new one; leaving
    the old reference in place would have one half of the client driving a pygame that
    has been shut down. Membership is decided by asking each loaded client module whether
    it has a ``pygame`` attribute rather than by listing names, so a new module that
    imports pygame is covered without anyone remembering to add it here. Modules that do
    not touch pygame are deliberately kept: a test that imported a class from one at
    collection time must keep comparing against that same class afterwards.
    """
    module = sys.modules.get("pygame")
    if module is None:
        return
    if module.get_init():
        module.quit()
    stale = [
        name
        for name, loaded in sys.modules.items()
        if name == "pygame"
        or name.startswith("pygame.")
        or (name.startswith("battle_city_client") and getattr(loaded, "pygame", None) is not None)
    ]
    for name in stale:
        del sys.modules[name]


@pytest.fixture(scope="module", autouse=True)
def pygame_module_boundary() -> Iterator[None]:
    """Release pygame when the importing test module has finished.

    Imported by name into every test module here that loads pygame. A module-scoped
    autouse fixture is the only directory-wide teardown available without a
    ``conftest.py``, and firing per module rather than once at the end makes it
    independent of how the session was selected or ordered.
    """
    yield
    release_pygame()


def capture_directory(scratch: Path) -> Path:
    """Where a capture run writes: scratch, unless a refresh was explicitly asked for.

    The committed captures record the SDL build, the Python version and the platform that
    produced them, so regenerating them from an ordinary ``pytest`` or ``make ci`` would
    dirty the working tree on every run. Refreshing them is a deliberate act::

        BATTLE_CITY_REFRESH_CAPTURES=1 \\
            uv run --locked pytest tests/tools/test_editor_screenshots.py
    """
    return SCREENSHOT_DIR if os.environ.get(REFRESH_CAPTURES_ENV) == "1" else scratch


# -- the editor, built only when a test asks for it ---------------------------


def editor_app(document: Any = None, *, scale: int = 3, overwrite: bool = False) -> Any:
    """An editor over ``document``, with a dummy window at ``scale``.

    Imported here rather than at module scope: these imports pull in pygame, and the
    rule for this directory is that collection must not.
    """
    ensure_display()
    from battle_city_client.editor.app import build_app
    from battle_city_client.editor.document import EditorDocument
    from battle_city_client.editor.state import EditorState

    state = EditorState(document=document or EditorDocument.blank(), overwrite=overwrite)
    return build_app(state, scale=scale)


def capture_editor(state: Any, name: str, *, directory: Path, scale: int = 2) -> Path:
    """Render ``state`` into a PNG under ``directory`` and return the path."""
    ensure_display()
    import pygame
    from battle_city_client.assets import ProceduralAssetLibrary
    from battle_city_client.editor import layout
    from battle_city_client.editor.render import EditorRenderer

    directory.mkdir(parents=True, exist_ok=True)
    surface = pygame.Surface(layout.LOGICAL_SIZE)
    EditorRenderer(ProceduralAssetLibrary()).render(surface, state)
    scaled = pygame.transform.scale(
        surface, (layout.LOGICAL_SIZE[0] * scale, layout.LOGICAL_SIZE[1] * scale)
    )
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path
