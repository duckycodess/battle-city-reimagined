"""Shared builders for the accessibility tests, and the rules this directory plays by.

Three rules hold across this whole directory, and they are the same three
``tests/campaign`` and ``tests/persistence`` record.

**Most of what is under test is pure, and most of these tests prove it by never leaving
that purity.** :mod:`battle_city_client.accessibility` and
:mod:`battle_city_client.options` import no display library, which is the whole point of
splitting them from :mod:`battle_city_client.keymap` and
:mod:`battle_city_client.gamepad`;
``test_accessibility_import_safety`` asserts it in a subprocess rather than trusting this
one.

**Nothing imports pygame at module scope.** pytest imports every selected test module
during collection, and ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is
present in ``sys.modules``. The modules here that need a device or a surface ask for
pygame inside the functions that use it and release it again through
:func:`pygame_module_boundary`.

**There is deliberately no ``conftest.py`` here.** ``tests/client`` has one, and the
repository type-checks with ``mypy packages tests``, which rejects two modules both named
``conftest``. ``tests/campaign``, ``tests/persistence`` and ``tests/tools`` record the
same reason; this directory follows it.

No real pad is needed, and none is assumed
------------------------------------------
SDL joystick events carry an instance identifier, an axis or button number and a value,
and :class:`~battle_city_client.gamepad.GamepadHub` reads exactly those fields. So the
pad tests post the events a pad would post and never open a device: they prove the
translation, the dead zone, the hot-plug neutralisation and the repeat without hardware
that CI does not have. What they therefore do *not* prove is that a particular physical
pad reports the axis numbering this build assumes; that is recorded as a remaining risk
rather than claimed.
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from battle_city_client.accessibility import (  # noqa: E402
    AccessibilityPreferences,
    GamepadControl,
    GamepadControlKind,
)
from battle_city_client.keymap import DEFAULT_BINDINGS  # noqa: E402
from battle_city_client.online import OnlineConfig  # noqa: E402
from battle_city_client.shell import ClientShell, Screen  # noqa: E402
from battle_city_client.stage_adapter import StageEntry  # noqa: E402
from battle_city_protocol import (  # noqa: E402
    BaseSnapshot,
    ContentRef,
    JoinAccepted,
    LobbyInfo,
    LobbyMember,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchSettings,
    MatchStarting,
    PlayerSnapshot,
    SessionInfo,
    StateSnapshot,
    TankSnapshot,
)
from battle_city_sim import GridPos, PlayerSpawn, Stage, Tile  # noqa: E402

SCREENSHOT_DIR: Path = Path(__file__).parent / "screenshots"
REFRESH_CAPTURES_ENV: str = "BATTLE_CITY_REFRESH_CAPTURES"
"""Set to ``1`` to rewrite the captures tracked in git. See :func:`capture_directory`."""

CAPTURE_SCALE: int = 2
ENLARGED_CAPTURE_SCALE: int = 4
"""The two window scales the captures are taken at.

The client draws one fixed logical frame and the window shows a whole multiple of it, so
enlarging the interface *is* raising that multiple. Capturing the same screen at two of
them is what shows the enlargement keeps every pixel square and moves no geometry.
"""


def observed[T](value: T) -> T:
    """Return ``value`` unchanged, as a call rather than as an attribute read.

    A strict type checker narrows an enum attribute at the first ``assert ... is`` in a
    test and then calls every later assertion in the same test a comparison of
    non-overlapping literals. Reading through a call keeps each assertion real.
    """
    return value


# -- stages and shells --------------------------------------------------------


def make_stage(stage_id: str = "range-01") -> Stage:
    """An open stage with one player spawn, one enemy spawn and a home tile."""
    grid = [[Tile.EMPTY for _ in range(16)] for _ in range(16)]
    grid[15][7] = Tile.HOME
    grid[4][4] = Tile.BRICK
    grid[4][5] = Tile.CRACKED_BRICK
    grid[6][9] = Tile.STONE
    grid[8][2] = Tile.WATER
    grid[9][11] = Tile.FOREST
    rows = tuple("".join(str(tile.value) for tile in row) for row in grid)
    return Stage.create(
        stage_id=stage_id,
        name="Range",
        rows=rows,
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(7, 12)),),
        enemy_spawns=(GridPos(1, 1),),
    )


def make_entry(stage_id: str = "range-01") -> StageEntry:
    """A catalog entry over :func:`make_stage`."""
    stage = make_stage(stage_id)
    return StageEntry(level_id=stage.stage_id, name=stage.name, stage=stage)


def make_shell(*, bindings: bool = True) -> ClientShell:
    """A shell with the shipped key tables, as a real launch builds one.

    ``bindings=False`` leaves the preferences with no device tables at all, which is the
    default a shell is constructed with and the state the pure tests use.
    """
    preferences = (
        AccessibilityPreferences(bindings=DEFAULT_BINDINGS)
        if bindings
        else AccessibilityPreferences()
    )
    return ClientShell(catalog=(make_entry(),), accessibility=preferences)


# -- pad controls -------------------------------------------------------------


def button(index: int) -> GamepadControl:
    """The pad button at ``index``."""
    return GamepadControl(GamepadControlKind.BUTTON, index)


def axis(index: int, direction: int) -> GamepadControl:
    """One side of the stick axis at ``index``."""
    return GamepadControl(GamepadControlKind.AXIS, index, direction)


def hat(index: int, direction: int, sub_axis: int) -> GamepadControl:
    """One side of one axis of the hat at ``index``."""
    return GamepadControl(GamepadControlKind.HAT, index, direction, sub_axis)


# -- pygame, borrowed and put back --------------------------------------------


def ensure_display() -> None:
    """Bring up the dummy display, importing pygame only when a test asks for it."""
    import pygame

    if not pygame.display.get_init():
        pygame.display.init()


def release_pygame() -> None:
    """Shut pygame down and drop it, so the interpreter is as these tests found it.

    ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is present in
    ``sys.modules``, as a proxy for "importing the simulation does not pull in a
    display". The modules here that load pygame on purpose put it back. Client modules
    holding a reference to the discarded module object go with it; membership is decided
    by asking each loaded client module whether it has a ``pygame`` attribute, so a new
    module that imports pygame is covered without anyone remembering to list it.
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

    Imported by name into each module here that loads pygame. A module-scoped autouse
    fixture is the only directory-wide teardown available without a ``conftest.py``, and
    firing per module rather than once at the end makes it independent of how the session
    was ordered or filtered.
    """
    yield
    release_pygame()


def key_event(key: int, *, down: bool = True) -> Any:
    """One keyboard event, built where pygame is already loaded."""
    import pygame

    return pygame.event.Event(pygame.KEYDOWN if down else pygame.KEYUP, key=key)


def axis_event(instance: int, index: int, value: float) -> Any:
    """One stick reading, as SDL reports it."""
    import pygame

    return pygame.event.Event(pygame.JOYAXISMOTION, instance_id=instance, axis=index, value=value)


def hat_event(instance: int, index: int, value: tuple[int, int]) -> Any:
    """One hat reading, as SDL reports it -- up is ``+1`` on SDL's vertical axis."""
    import pygame

    return pygame.event.Event(pygame.JOYHATMOTION, instance_id=instance, hat=index, value=value)


def button_event(instance: int, index: int, *, down: bool = True) -> Any:
    """One pad button event, as SDL reports it."""
    import pygame

    return pygame.event.Event(
        pygame.JOYBUTTONDOWN if down else pygame.JOYBUTTONUP,
        instance_id=instance,
        button=index,
    )


def removal_event(instance: int) -> Any:
    """A pad being unplugged."""
    import pygame

    return pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=instance)


def focus_event(*, gained: bool) -> Any:
    """The window gaining or losing focus."""
    import pygame

    return pygame.event.Event(pygame.WINDOWFOCUSGAINED if gained else pygame.WINDOWFOCUSLOST)


def capture_directory(scratch: Path) -> Path:
    """Where a capture run writes: scratch, unless a refresh was explicitly asked for.

    The committed captures record the machine that produced them, so regenerating them
    from an ordinary ``pytest`` or ``make ci`` would dirty the working tree on every
    run::

        BATTLE_CITY_REFRESH_CAPTURES=1 \\
            uv run --locked pytest tests/accessibility/test_accessibility_screenshots.py
    """
    return SCREENSHOT_DIR if os.environ.get(REFRESH_CAPTURES_ENV) == "1" else scratch


def luminance_pattern(surface: Any) -> tuple[int, ...]:
    """A coarse brightness fingerprint, which is what survives losing hue."""
    return tuple(
        (surface.get_at((x, y)).r + surface.get_at((x, y)).g + surface.get_at((x, y)).b) // 96
        for y in range(surface.get_height())
        for x in range(surface.get_width())
    )


def opaque_pixels(surface: Any) -> frozenset[tuple[int, int]]:
    """Coordinates of every pixel that is not fully transparent."""
    return frozenset(
        (x, y)
        for y in range(surface.get_height())
        for x in range(surface.get_width())
        if surface.get_at((x, y)).a > 0
    )


# -- an online client, built only from server messages ------------------------

ONLINE_SESSION_ID: str = "session-accessibility"
ONLINE_CONTENT = ContentRef(
    pack_id="duo-pack", pack_version="1.0.0", level_id="duo-arena", content_schema_version=1
)
ONLINE_GRID: tuple[str, ...] = ("0" * 16,) * 15 + ("0" * 8 + "8" + "0" * 7,)
"""An empty arena with the base on the bottom row, as a keyframe carries it."""


def online_shell(*, teams: bool, grid: tuple[str, ...] = ONLINE_GRID) -> ClientShell:
    """A seated client in a started match, built only from server messages."""
    shell = make_shell()
    shell.online_config = OnlineConfig(
        endpoint="loopback:0",
        session_id=ONLINE_SESSION_ID,
        ticket="ticket-host-aaaaaaaa",
        display_name="ducky",
        content=ONLINE_CONTENT,
    )
    assert shell.open_online()
    settings = MatchSettings(
        mode=MatchMode.TEAM_BATTLE if teams else MatchMode.COOP,
        level_id="duo-arena",
        content=ONLINE_CONTENT,
        tick_rate=60,
        max_players=2,
    )
    shell.receive(
        LobbyWelcome(
            session_id=ONLINE_SESSION_ID,
            slot=1,
            host=True,
            lobby=LobbyInfo(
                capacity=2,
                offered_modes=(MatchMode.COOP, MatchMode.TEAM_BATTLE),
                playable_modes=(MatchMode.COOP,),
                offered_levels=("duo-arena",),
            ),
        )
    )
    shell.receive(
        LobbyState(
            session_id=ONLINE_SESSION_ID,
            revision=1,
            settings=settings,
            members=(
                LobbyMember(
                    slot=1,
                    display_name="ducky",
                    ready=True,
                    host=True,
                    connected=True,
                    team=1 if teams else None,
                ),
                LobbyMember(
                    slot=2,
                    display_name="tj",
                    ready=True,
                    host=False,
                    connected=True,
                    team=2 if teams else None,
                ),
            ),
            host_slot=1,
            startable=True,
            blocked=None,
        )
    )
    session_info = SessionInfo(
        tick_rate=60,
        keyframe_interval=30,
        max_players=2,
        content=ONLINE_CONTENT,
        rules_digest="a" * 64,
        state_version=1,
    )
    shell.receive(
        MatchStarting(
            session_id=ONLINE_SESSION_ID,
            slot=1,
            token="token-slot-1-abcdef",
            session=session_info,
            settings=settings,
        )
    )
    shell.receive(JoinAccepted(session_id=ONLINE_SESSION_ID, slot=1, tick=0, session=session_info))
    shell.receive(
        StateSnapshot(
            session_id=ONLINE_SESSION_ID,
            tick=0,
            tick_rate=60,
            state_version=1,
            keyframe=True,
            grid=grid,
            tanks=(
                TankSnapshot(
                    entity_id=1,
                    variant=0,
                    x=64,
                    y=160,
                    facing=0,
                    slot=1,
                    gatling_ticks=0,
                    invincible_ticks=0,
                ),
                TankSnapshot(
                    entity_id=2,
                    variant=0,
                    x=160,
                    y=160,
                    facing=0,
                    slot=2,
                    gatling_ticks=0,
                    invincible_ticks=0,
                ),
            ),
            projectiles=(),
            powerups=(),
            players=(
                PlayerSnapshot(slot=1, lives=3, tank_id=1, spawn_x=4, spawn_y=10),
                PlayerSnapshot(slot=2, lives=3, tank_id=2, spawn_x=10, spawn_y=10),
            ),
            base=BaseSnapshot(cell_x=8, cell_y=15, destroyed=False),
            state_hash="b" * 64,
        )
    )
    assert shell.screen is Screen.ONLINE_PLAY
    return shell
