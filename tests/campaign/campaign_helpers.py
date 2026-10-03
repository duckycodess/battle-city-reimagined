"""Shared builders for the campaign tests, and the rules this directory plays by.

Three rules hold across this whole directory.

**The campaign itself is pure, and most of these tests prove it by never leaving that
purity.** :mod:`battle_city_client.campaign` imports no display library, and neither do
the shell, the session, the intents or the stage adapter, so a campaign can be driven to
completion in a bare interpreter. Only :mod:`test_campaign_screenshots` draws anything.

**Nothing imports pygame at module scope.** pytest imports every selected test module
during collection, and ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is
present in ``sys.modules``. The one module here that draws asks for pygame inside the
functions that use it, and releases it again through :func:`pygame_module_boundary`.

**There is deliberately no ``conftest.py`` here.** ``tests/client`` has one, and the
repository type-checks with ``mypy packages tests``, which rejects two modules both named
``conftest``. ``tests/tools`` records the same reason; this directory follows it.

Where combat is asserted, and where it is not
---------------------------------------------
The three converted classic layouts are mazes, and neither a bot-driven player nor
bot-driven enemies reliably reach each other on them inside any tick budget a test should
spend: 120,000 ticks of a veteran bot driving the player on ``classic-01`` scores nothing.
That is a fact about pathfinding, not about the campaign, and a campaign test that waited
on it would be measuring the wrong thing.

So the two are separated. Scoring, stage clear, victory and defeat are asserted on stages
built here for exactly that -- an open column with the enemy spawn in the player's line of
fire -- where every kill is deterministic and a whole campaign runs in a few hundred
ticks. The classic stages carry what only they can: that the real layouts produce the
historical quotas, on the historical cadence, onto their own declared spawn cells.
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import sys  # noqa: E402
from collections.abc import Iterator, Mapping, Sequence  # noqa: E402
from dataclasses import dataclass, replace  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

# ``battle_city_ai`` ships no ``py.typed`` marker, so mypy refuses it as untyped when the
# AI package is not itself part of the checked set -- which is exactly what the issue's
# acceptance command does: ``mypy packages/content packages/client tests/campaign``. The
# repository-wide ``mypy packages tests`` checks the AI sources directly and resolves it
# fine, which would make a bare ignore unused there. Naming both codes satisfies the two
# invocations with one comment. Issue #37 adds the marker, as #19 and #24 did for the
# simulation and content packages, and this comment goes with it.
from battle_city_ai import (  # type: ignore[import-untyped,unused-ignore]  # noqa: E402
    Bot,
    decide,
    profile_named,
)
from battle_city_client.campaign import (  # noqa: E402
    CampaignPhase,
    CampaignRules,
    CampaignRun,
    StagePlan,
    campaign_plan,
)
from battle_city_client.intents import PlayerIntent  # noqa: E402
from battle_city_client.session import StageSession  # noqa: E402
from battle_city_client.stage_adapter import StageEntry  # noqa: E402
from battle_city_sim import (  # noqa: E402
    Command,
    Direction,
    Faction,
    FireCommand,
    GridPos,
    MoveCommand,
    PlayerSpawn,
    PowerupKind,
    RespawnCommand,
    Rules,
    RunOutcome,
    SimulationState,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    Stage,
    TankVariant,
    Tile,
)

SCREENSHOT_DIR: Path = Path(__file__).parent / "screenshots"
REFRESH_CAPTURES_ENV: str = "BATTLE_CITY_REFRESH_CAPTURES"
"""Set to ``1`` to rewrite the captures tracked in git. See :func:`capture_directory`."""

TEST_SEED: int = 20260601
"""One seed for the whole directory, so a failure is reproduced by running the test."""


def observed[T](value: T) -> T:
    """Return ``value`` unchanged, as a call rather than as an attribute read.

    A strict type checker narrows an enum attribute at the first ``assert ... is`` in a
    test and then calls every later assertion in the same test a comparison of
    non-overlapping literals. Reading through a call keeps each assertion real.
    """
    return value


# -- stages built for a test -------------------------------------------------


def rows_with(
    overrides: Mapping[tuple[int, int], Tile] | None = None,
    *,
    home: tuple[int, int] = (7, 15),
) -> tuple[str, ...]:
    """A 16x16 field of empty ground with ``overrides`` painted in, plus one home tile."""
    grid = [[Tile.EMPTY for _ in range(16)] for _ in range(16)]
    for (x, y), tile in (overrides or {}).items():
        grid[y][x] = tile
    grid[home[1]][home[0]] = Tile.HOME
    return tuple("".join(str(tile.value) for tile in row) for row in grid)


def firing_range_stage(
    stage_id: str = "range-01",
    *,
    player_cell: tuple[int, int] = (7, 12),
    enemy_cells: Sequence[tuple[int, int]] = ((7, 2),),
    home_cell: tuple[int, int] = (7, 15),
) -> Stage:
    """An open stage with the enemy spawn directly up-range of the player.

    The player enters facing up, which is the simulation's fixed initial facing, so a run
    driven with nothing but ``fire`` destroys whatever spawns in that column. Every kill
    is then a function of the tick count alone, which is what makes a scoring assertion an
    assertion rather than a hope.
    """
    return Stage.create(
        stage_id=stage_id,
        name=stage_id.replace("-", " ").title(),
        rows=rows_with(home=home_cell),
        player_spawns=(PlayerSpawn(slot=1, cell=GridPos(*player_cell)),),
        enemy_spawns=tuple(GridPos(*cell) for cell in enemy_cells),
    )


def entry_for(stage: Stage, *, waves: Sequence[int] = ()) -> StageEntry:
    """A catalog entry over ``stage``, optionally declaring wave counts."""
    return StageEntry(level_id=stage.stage_id, name=stage.name, stage=stage, waves=tuple(waves))


def plan_of(
    *stages: Stage, quota: int | None = None, rules: CampaignRules | None = None
) -> tuple[StagePlan, ...]:
    """A campaign plan over ``stages``.

    ``quota`` overrides every stage's enemy count, which is how a test says "two enemies,
    then the stage is over" without also saying anything about the quota *rule*; that rule
    is asserted on its own in :mod:`test_campaign_rules`.
    """
    plan = campaign_plan([entry_for(stage) for stage in stages], rules or CampaignRules())
    if quota is None:
        return plan
    return tuple(replace(stage, enemy_quota=quota) for stage in plan)


QUICK_INTERVAL: int = 60
"""A short spawn interval, so a whole campaign fits inside a test.

Only the cadence is shortened. Everything these tests assert about *behaviour* -- the
quota rule, the clear condition, carry, restart -- is independent of the interval, and the
historical 600 is asserted where it belongs, on the classic stages.
"""


# -- running -----------------------------------------------------------------

FIRING: PlayerIntent = PlayerIntent(fire=True)
"""Hold fire and nothing else. The player's facing is the stage's initial facing."""


def advance_until(
    run: CampaignRun,
    *,
    intent: PlayerIntent = FIRING,
    limit: int = 4000,
) -> CampaignRun:
    """Advance one tick at a time until the phase leaves ``PLAYING`` or ``limit`` is hit.

    One tick at a time rather than one call of ``limit`` ticks because a test that wants
    to know *when* something happened reads ``run.session.state.tick`` afterwards, and a
    bulk call would stop at the same place but tell a reader less about why.
    """
    for _ in range(limit):
        if run.phase is not CampaignPhase.PLAYING:
            return run
        run = run.advance(1, intent)
    return run


def advance_while_spawning(run: CampaignRun, *, limit: int = 12000) -> CampaignRun:
    """Advance until the stage's enemy quota is spent, or ``limit`` ticks pass."""
    for _ in range(limit):
        if run.pending_enemies == 0 or run.phase is not CampaignPhase.PLAYING:
            return run
        run = run.advance(1, PlayerIntent())
    return run


# -- states a test assembles rather than reaches ------------------------------


def session_with_outcome(session: StageSession, outcome: RunOutcome) -> StageSession:
    """``session`` with ``outcome`` recorded on its state, and the matching base damage.

    The campaign never does this: an outcome is the simulation's to record and the
    campaign only reads one. A *caller* does it, which is the whole reason
    :attr:`ClientShell.session` is writable -- ``tests/client`` reaches its terminal
    screens exactly this way -- so the campaign has to take such a session as given. See
    ``test_campaign_shell``.

    Stamping only the outcome would describe a state no run could reach: a destroyed base
    the HUD still draws standing. The base is damaged with it.
    """
    state = session.state
    base = (
        state.base
        if outcome is not RunOutcome.BASE_DESTROYED
        else replace(state.base, destroyed=True)
    )
    return replace(session, state=replace(state, outcome=outcome, base=base), last_events=())


# -- drivers -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BotEnemyDriver:
    """An :class:`EnemyCommandDriver` backed by the AI package.

    This is the point of the driver seam, demonstrated. The client may not depend on
    ``battle_city_ai`` -- the architecture specification allows ``ai -> sim`` and not
    ``client -> ai`` -- but a test may import every package, so the seam is shown here to
    carry a real bot rather than only a stub. Issue #35 does the same thing inside the
    shipped client, with the dependency declared.

    A bot is created for each enemy the first tick it is seen and dropped when its tank
    is gone, so the driver's memory is a function of the tanks on the board. Its seed is
    the campaign's, folded with the tank identifier by the AI package's own derivation.
    """

    profile_name: str = "soldier"
    seed: int = TEST_SEED
    bots: tuple[Bot, ...] = ()

    def entering_stage(self, state: SimulationState, rules: Rules) -> BotEnemyDriver:
        return replace(self, bots=())

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[BotEnemyDriver, tuple[Command, ...]]:
        profile = profile_named(self.profile_name)
        live = {tank.entity_id for tank in state.tanks if tank.faction is Faction.ENEMY}
        kept = [bot for bot in self.bots if bot.tank_id in live]
        known = {bot.tank_id for bot in kept}
        for tank_id in sorted(live - known):
            kept.append(Bot.create(tank_id=tank_id, profile=profile, seed=self.seed + tank_id))

        commands: list[Command] = []
        successors: list[Bot] = []
        for bot in sorted(kept, key=lambda bot: bot.tank_id):
            decision = decide(bot, state, rules)
            commands.extend(decision.commands)
            successors.append(decision.bot)
        return replace(self, bots=tuple(successors)), tuple(commands)


@dataclass(frozen=True, slots=True)
class ScriptedEnemyDriver:
    """Points every live enemy one way and holds its trigger. Deliberately crude.

    It exists to reach outcomes a fair bot will not. A bot never fires at the home base --
    the AI specification makes that a fairness rule -- so something other than a bot has to
    aim at it before the campaign's game-over path can be asserted. Pointing every enemy
    down the open column is also the shortest way to spend the player's lives on purpose.
    """

    direction: Direction | None = Direction.DOWN
    fire: bool = True

    def entering_stage(self, state: SimulationState, rules: Rules) -> ScriptedEnemyDriver:
        return self

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[ScriptedEnemyDriver, tuple[Command, ...]]:
        commands: list[Command] = []
        for tank in sorted(state.tanks, key=lambda tank: tank.entity_id):
            if tank.faction is not Faction.ENEMY:
                continue
            if self.direction is not None:
                commands.append(MoveCommand(tank_id=tank.entity_id, direction=self.direction))
            if self.fire:
                commands.append(FireCommand(tank_id=tank.entity_id))
        return self, tuple(commands)


@dataclass(frozen=True, slots=True)
class OutlawDriver:
    """A driver that returns everything it is not allowed to return.

    The campaign filters a driver's output rather than trusting it, and the only way to
    show that is to hand it a driver that tries: commanding the player's tank, respawning
    a slot, spawning a tank of its own, and commanding one enemy twice in a tick.
    """

    def entering_stage(self, state: SimulationState, rules: Rules) -> OutlawDriver:
        return self

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[OutlawDriver, tuple[Command, ...]]:
        enemies = [tank for tank in state.tanks if tank.faction is Faction.ENEMY]
        commands: list[Command] = [
            RespawnCommand(slot=1),
            SpawnEnemyCommand(cell=GridPos(x=1, y=1), variant=TankVariant.ENEMY_NORMAL),
            SpawnPowerupCommand(cell=GridPos(x=1, y=2), kind=PowerupKind.EXTRA_LIFE),
        ]
        for tank in state.tanks:
            if tank.faction is Faction.PLAYER:
                commands.append(MoveCommand(tank_id=tank.entity_id, direction=Direction.DOWN))
                commands.append(FireCommand(tank_id=tank.entity_id))
        for tank in enemies:
            commands.append(MoveCommand(tank_id=tank.entity_id, direction=Direction.LEFT))
            commands.append(MoveCommand(tank_id=tank.entity_id, direction=Direction.RIGHT))
        return self, tuple(commands)


@dataclass(frozen=True, slots=True)
class CountingDriver:
    """Records how many times each hook was called. Used to pin the per-stage contract."""

    stages: int = 0
    ticks: int = 0

    def entering_stage(self, state: SimulationState, rules: Rules) -> CountingDriver:
        return replace(self, stages=self.stages + 1, ticks=0)

    def commands(
        self, state: SimulationState, rules: Rules
    ) -> tuple[CountingDriver, tuple[Command, ...]]:
        return replace(self, ticks=self.ticks + 1), ()


# -- pygame, for the one module that draws ------------------------------------


def ensure_display() -> None:
    """Bring up the dummy display, importing pygame only when a test asks for it."""
    import pygame

    if not pygame.display.get_init():
        pygame.display.init()


def release_pygame() -> None:
    """Shut pygame down and drop it, so the interpreter is as these tests found it.

    ``tests/sim/test_purity.py`` asserts that no ``pygame`` module is present in
    ``sys.modules``, as a proxy for "importing the simulation does not pull in a display".
    The one module here that draws loads pygame on purpose, so it puts it back. Client
    modules that hold a reference to the discarded module object are dropped with it;
    membership is decided by asking each loaded client module whether it has a ``pygame``
    attribute, so a new module that imports pygame is covered without anyone remembering
    to list it.
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

    Imported by name into the one module here that loads pygame. A module-scoped autouse
    fixture is the only directory-wide teardown available without a ``conftest.py``, and
    firing per module rather than once at the end makes it independent of how the session
    was ordered or filtered.
    """
    yield
    release_pygame()


def capture_directory(scratch: Path) -> Path:
    """Where a capture run writes: scratch, unless a refresh was explicitly asked for.

    The committed captures record the machine that produced them, so regenerating them
    from an ordinary ``pytest`` or ``make ci`` would dirty the working tree on every run::

        BATTLE_CITY_REFRESH_CAPTURES=1 \\
            uv run --locked pytest tests/campaign/test_campaign_screenshots.py
    """
    return SCREENSHOT_DIR if os.environ.get(REFRESH_CAPTURES_ENV) == "1" else scratch
