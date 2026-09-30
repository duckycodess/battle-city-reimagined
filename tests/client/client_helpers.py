"""Headless bootstrap and shared builders for the client tests.

The SDL driver variables are set here, at import time and before pygame is imported,
because pygame reads them when its display subsystem initialises and a test process must
never try to open a real window or a real sound device. They are set rather than
defaulted: a developer with a desktop should still get the headless behaviour, so a
passing run on a laptop means the same thing as a passing run in CI.

Fixtures that assemble a *finished* simulation state live here too. The client cannot
reach a terminal outcome on its own -- this build has no enemy behaviour, and both
outcomes the simulation defines need a hostile projectile -- so the terminal screens are
covered by handing the shell a state the test built. That injection belongs to the tests
and to nothing else: no production path sets an outcome, and every screenshot taken from
one of these states is stamped as a fixture.
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

from collections.abc import Sequence  # noqa: E402
from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

import pygame  # noqa: E402
from battle_city_client import (  # noqa: E402
    ClientShell,
    PlayerIntent,
    ProceduralAssetLibrary,
    Renderer,
    StageSession,
    theme,
)
from battle_city_client.stage_adapter import StageEntry  # noqa: E402
from battle_city_content import (  # noqa: E402
    GridCell,
    Level,
    LevelGrid,
    LevelSource,
    Pack,
    PackLicense,
    Wave,
)
from battle_city_content import PlayerSpawn as ContentPlayerSpawn  # noqa: E402
from battle_city_sim import (  # noqa: E402
    DEFAULT_RULES,
    GridPos,
    PlayerSpawn,
    RunOutcome,
    SimulationState,
    Stage,
    Tile,
)

SCREENSHOT_DIR: Path = Path(__file__).parent / "screenshots"
FIXTURE_BANNER: str = "TEST FIXTURE - INJECTED SIMULATION STATE"
"""Stamped onto every capture taken from a state a test assembled."""


def observed[T](value: T) -> T:
    """Return ``value`` unchanged, as a call rather than as an attribute read.

    A strict type checker narrows ``shell.screen`` at the first ``assert ... is`` in a
    test and then reports every later assertion in the same test as comparing
    non-overlapping literals, so a test that walks through four screens fails to check
    three of them. Reading through a call keeps each assertion a real assertion. It is a
    checker workaround and nothing more, which is why it does exactly nothing.
    """
    return value


def ensure_display() -> None:
    """Bring up the dummy display if it is not already up."""
    if not pygame.display.get_init():
        pygame.display.init()


def open_window(scale: int = 1) -> pygame.Surface:
    """Open a dummy window, which some pygame calls need before they will work."""
    ensure_display()
    return pygame.display.set_mode(
        (theme.LOGICAL_SIZE[0] * scale, theme.LOGICAL_SIZE[1] * scale), pygame.RESIZABLE
    )


def logical_surface() -> pygame.Surface:
    """A surface the size of the client's logical frame."""
    return pygame.Surface(theme.LOGICAL_SIZE)


def renderer() -> Renderer:
    """A renderer over the procedural stand-in art."""
    return Renderer(ProceduralAssetLibrary(DEFAULT_RULES))


# -- stages ------------------------------------------------------------------


def rows_with(
    overrides: dict[tuple[int, int], Tile], *, fill: Tile = Tile.EMPTY
) -> tuple[str, ...]:
    """A 16x16 row set of ``fill`` with ``overrides`` painted in, plus a home tile."""
    grid = [[fill for _ in range(16)] for _ in range(16)]
    for (x, y), tile in overrides.items():
        grid[y][x] = tile
    return tuple("".join(str(tile.value) for tile in row) for row in grid)


def make_stage(
    overrides: dict[tuple[int, int], Tile] | None = None,
    *,
    player_cell: tuple[int, int] = (7, 12),
    enemy_cell: tuple[int, int] = (1, 1),
    home_cell: tuple[int, int] = (7, 15),
    stage_id: str = "test-stage",
) -> Stage:
    """A minimal playable stage, stated by the test rather than loaded from a pack."""
    painted = dict(overrides or {})
    painted[home_cell] = Tile.HOME
    return Stage.create(
        stage_id=stage_id,
        name="Test Stage",
        rows=rows_with(painted),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(*player_cell)),),
        enemy_spawns=(GridPos(*enemy_cell),),
    )


def make_entry(stage: Stage | None = None) -> StageEntry:
    """A catalog entry wrapping ``stage``."""
    built = stage or make_stage()
    return StageEntry(level_id=built.stage_id, name=built.name, stage=built)


def make_shell(entries: Sequence[StageEntry] | None = None) -> ClientShell:
    """A shell over ``entries``, defaulting to one test stage."""
    return ClientShell(catalog=tuple(entries if entries is not None else (make_entry(),)))


# -- content records ---------------------------------------------------------


def make_level(
    rows: Sequence[str] | None = None,
    *,
    level_id: str = "test-level",
    name: str = "Test Level",
    player_cells: Sequence[tuple[int, int]] = ((7, 12),),
    enemy_cells: Sequence[tuple[int, int]] = ((1, 1),),
    base_cell: tuple[int, int] = (7, 15),
    waves: Sequence[Wave] = (),
    origin: Path = Path("/fixture/test-level.json"),
) -> Level:
    """A content level record built in memory, with no file behind it."""
    painted = {base_cell: Tile.HOME}
    grid_rows = tuple(rows) if rows is not None else rows_with(painted)
    return Level(
        schema_version=1,
        level_id=level_id,
        name=name,
        grid=LevelGrid(width=16, height=16, rows=grid_rows),
        player_spawns=tuple(
            ContentPlayerSpawn(slot=index + 1, cell=GridCell(*cell))
            for index, cell in enumerate(player_cells)
        ),
        enemy_spawns=tuple(GridCell(*cell) for cell in enemy_cells),
        base_cell=GridCell(*base_cell),
        source=LevelSource(repository="fixture", revision="0", path="fixture"),
        waves=tuple(waves),
        origin=origin,
    )


def make_pack(levels: Sequence[Level]) -> Pack:
    """A content pack record built in memory."""
    return Pack(
        schema_version=1,
        pack_id="fixture",
        version="1.0.0",
        name="Fixture pack",
        content_schema_version=1,
        authors=("tests",),
        license=PackLicense(spdx_id="NOASSERTION", notice="fixture"),
        levels=tuple(levels),
        origin=Path("/fixture/pack.json"),
    )


# -- injected terminal states (tests only) -----------------------------------


def with_outcome(state: SimulationState, outcome: RunOutcome) -> SimulationState:
    """Record ``outcome`` on ``state``, and the matching base damage with it.

    Stamping only the outcome would produce a state no run could reach -- a destroyed
    base that the HUD still reports as standing -- and a screenshot of an impossible
    state proves nothing about the screen it is meant to show.
    """
    base = (
        state.base
        if outcome is not RunOutcome.BASE_DESTROYED
        else replace(state.base, destroyed=True)
    )
    return replace(state, outcome=outcome, base=base)


def finished_session(
    outcome: RunOutcome = RunOutcome.BASE_DESTROYED,
    *,
    stage: Stage | None = None,
    ticks: int = 40,
) -> StageSession:
    """A session advanced for real, then stamped with ``outcome`` by the test."""
    session = StageSession.start(stage or make_stage())
    session = session.advance(ticks, PlayerIntent(fire=True))
    return replace(session, state=with_outcome(session.state, outcome))


# -- capture -----------------------------------------------------------------


REFRESH_CAPTURES_ENV: str = "BATTLE_CITY_REFRESH_CAPTURES"
"""Set to ``1`` to rewrite the captures tracked in git. See :func:`capture_directory`."""


def capture_directory(scratch: Path) -> Path:
    """Where a capture run should write.

    Ordinary runs write to ``scratch`` -- a pytest ``tmp_path`` -- so that ``pytest`` and
    ``make ci`` never leave the working tree dirty. The committed captures carry
    machine-specific metadata (SDL build, Python version, platform), so regenerating them
    as a side effect of running the suite would turn every unrelated test run into a diff
    and every contributor's machine into a different one. Refreshing them is a deliberate
    act::

        BATTLE_CITY_REFRESH_CAPTURES=1 \\
            uv run --locked pytest tests/client/test_client_screenshots.py

    The rendering itself runs either way, so the captures cannot silently stop being
    reproducible between refreshes.
    """
    return SCREENSHOT_DIR if os.environ.get(REFRESH_CAPTURES_ENV) == "1" else scratch


def capture(
    shell: ClientShell,
    name: str,
    *,
    directory: Path,
    scale: int = 2,
    fixture: bool = False,
) -> Path:
    """Render ``shell`` and write a PNG, stamping a banner on injected states."""
    directory.mkdir(parents=True, exist_ok=True)
    surface = logical_surface()
    renderer().render(surface, shell)
    if fixture:
        _stamp_fixture_banner(surface)
    scaled = pygame.transform.scale(
        surface, (theme.LOGICAL_SIZE[0] * scale, theme.LOGICAL_SIZE[1] * scale)
    )
    path = directory / f"{name}.png"
    pygame.image.save(scaled, str(path))
    return path


def _stamp_fixture_banner(surface: pygame.Surface) -> None:
    """Draw the fixture banner. Done here, never by the renderer under test."""
    from battle_city_client.glyphs import GLYPH_HEIGHT

    band = pygame.Rect(0, 0, surface.get_width(), GLYPH_HEIGHT + 6)
    surface.fill(theme.DANGER, band)
    renderer().text_centered(surface, FIXTURE_BANNER, surface.get_width() // 2, 3, theme.BACKGROUND)


def opaque_pixels(surface: pygame.Surface) -> frozenset[tuple[int, int]]:
    """Coordinates of every pixel that is not fully transparent.

    Used to show two pieces of art differ in *shape*, not only in colour, which is what
    the accessibility specification asks of every state the board distinguishes.
    """
    return frozenset(
        (x, y)
        for y in range(surface.get_height())
        for x in range(surface.get_width())
        if surface.get_at((x, y)).a > 0
    )


def distinct_colors(surface: pygame.Surface) -> int:
    """How many distinct colours a surface holds. A blank frame holds one."""
    return len(
        {
            tuple(surface.get_at((x, y)))
            for y in range(surface.get_height())
            for x in range(surface.get_width())
        }
    )
