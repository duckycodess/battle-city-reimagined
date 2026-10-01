"""The bot policy: a pure function from a snapshot and a bot's memory to tick commands.

Contract
--------
``decide(bot, state, rules)`` returns the commands for exactly one tick and the successor
bot. It reads nothing but its arguments, writes nothing, and never calls a clock, a
socket or :mod:`random`. Given the same profile, seed, state and tick history it returns
the same commands, which is the determinism requirement the AI specification states.

A bot's randomness lives in its own memory, not in ``state.rng``. The rules engine copies
``state.rng`` from tick to tick without advancing it, so a bot that drew from the state
would draw the same number forever; see :mod:`battle_city_ai.seeding`.

Only legal commands
-------------------
A decision is at most one :class:`~battle_city_sim.inputs.MoveCommand` and one
:class:`~battle_city_sim.inputs.FireCommand`, both naming the bot's own live tank. Those
are the two commands a human player can submit, and the simulation's validator accepts
them for any live tank, so a bot can never produce a tick the engine rejects. The bot
emits no ``RespawnCommand``, no ``SpawnEnemyCommand`` and no powerup command: respawn
timing and wave cadence are campaign policy, and a bot that respawned itself would be
writing that policy.

The tick a decision is built for
--------------------------------
The rules engine moves every tank (phase 5) before any tank fires (phase 6), and a
``MoveCommand`` turns the tank whether or not the step succeeds. A bot that aimed from
its pre-move pose would therefore aim from a pose that no longer exists by the time its
shot spawns. Every firing test here runs against
:func:`~battle_city_ai.perception.predicted_pose`, the pose the tank will actually hold
when the muzzle is placed.

How the random stream is spent
------------------------------
At most two draws per tick, each in a fixed place, so the stream position is a function of
the decision history and nothing else:

1. one aggression draw on a tick that re-plans, and
2. one accuracy draw on a tick where a shot is otherwise cleared to go.

Neither draw happens on a tick that cannot use it, so two bots that never see a target
stay at the same stream position.

Deliberately absent
-------------------
Team coordination, player-style variation, and any use of forest concealment. The AI
specification defers the first two to a proposal with fairness and performance criteria,
and :mod:`battle_city_sim.visibility` says concealment has no gameplay effect until a
proposal defines one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from battle_city_sim import (
    DEFAULT_RULES,
    DIRECTION_ORDER,
    Command,
    Direction,
    FireCommand,
    MoveCommand,
    Projectile,
    Rules,
    SimulationState,
    Tank,
    TickInput,
)
from battle_city_sim.rng import Rng

from .perception import (
    aim_candidates,
    clearance_ticks,
    hostiles_of,
    incoming_threat,
    manhattan,
    muzzle_of,
    predicted_pose,
    projectile_reaches,
    shot_target_id,
)
from .profiles import DifficultyProfile, TargetPreference
from .seeding import derive_bot_rng

PERCENT: Final[int] = 100
"""The denominator for both percentage knobs. Draws are ``rng.below(PERCENT)``."""

_PERPENDICULAR: Final[dict[Direction, tuple[Direction, ...]]] = {
    Direction.UP: (Direction.LEFT, Direction.RIGHT),
    Direction.DOWN: (Direction.LEFT, Direction.RIGHT),
    Direction.LEFT: (Direction.UP, Direction.DOWN),
    Direction.RIGHT: (Direction.UP, Direction.DOWN),
}
"""Sidestep options for a given incoming direction. Running away along the shot's own
axis loses, because a projectile outruns a tank by 3px to 2px per tick."""


@dataclass(frozen=True, slots=True)
class BotMemory:
    """Everything a bot carries between ticks.

    This is the bot's whole mutable world, and it is a value: :func:`decide` returns a
    successor rather than updating anything in place, exactly like
    :class:`~battle_city_sim.rng.Rng`. Holding an old memory is a valid way to rewind a
    bot, and two bots with equal memories are interchangeable.
    """

    rng: Rng
    """The bot's private stream position. Derived once at creation, threaded every tick."""

    plan_direction: Direction | None = None
    """The roaming direction currently committed to, if the bot is roaming."""

    plan_ticks_left: int = 0
    """Ticks remaining on the current commitment. Zero forces a re-plan."""

    pursuing: bool = False
    """The last aggression draw: whether the bot closes distance while it has a firing line."""

    engaged_ticks: int = 0
    """Consecutive ticks the bot has held a firing solution, saturating at the profile's
    reaction delay. This is the reflex counter the reaction-delay gate reads."""

    ticks_since_fire: int = 0
    """Ticks since the last shot, saturating at the profile's fire cooldown."""


@dataclass(frozen=True, slots=True)
class Bot:
    """One bot: which tank it drives, how it behaves, and what it remembers."""

    tank_id: int
    profile: DifficultyProfile
    memory: BotMemory

    @classmethod
    def create(cls, *, tank_id: int, profile: DifficultyProfile, seed: int) -> Bot:
        """Build a bot whose stream is derived once, here, from the session seed.

        ``ticks_since_fire`` starts saturated so a freshly created bot is not silently
        serving a cooldown it never earned; the reaction-delay gate still applies.
        """
        return cls(
            tank_id=tank_id,
            profile=profile,
            memory=BotMemory(
                rng=derive_bot_rng(seed=seed, tank_id=tank_id, profile_name=profile.name),
                ticks_since_fire=profile.fire_cooldown_ticks,
            ),
        )


@dataclass(frozen=True, slots=True)
class BotDecision:
    """One tick's commands and the successor bot that produced them."""

    bot: Bot
    commands: tuple[Command, ...]


@dataclass(frozen=True, slots=True)
class TickPlan:
    """A whole tick's worth of bot intent, ready to hand to ``step``."""

    tick_input: TickInput
    bots: tuple[Bot, ...]


def seed_from_state(state: SimulationState) -> int:
    """Return the session seed to derive bot streams from.

    ``new_game`` builds ``state.rng`` with :meth:`~battle_city_sim.rng.Rng.from_seed` and
    ``step`` never advances it, so the generator's position still *is* the session seed at
    any tick. Reading it here keeps a caller from having to carry the seed separately and
    risk pairing a state with the wrong one.
    """
    return state.rng.state


def decide(
    bot: Bot,
    state: SimulationState,
    rules: Rules = DEFAULT_RULES,
) -> BotDecision:
    """Return one tick of commands for ``bot`` and the successor bot.

    The order of work is the order the rules engine resolves a tick: choose a target,
    choose a move, project the pose that move produces, and only then decide whether a
    shot leaving that pose would connect.
    """
    profile = bot.profile
    memory = bot.memory
    tank = state.find_tank(bot.tank_id)
    if state.finished or tank is None:
        return BotDecision(bot=replace(bot, memory=_stand_down(memory)), commands=())

    rng = memory.rng
    plan_direction = memory.plan_direction
    plan_ticks_left = max(memory.plan_ticks_left - 1, 0)
    pursuing = memory.pursuing

    stuck = (
        plan_direction is not None and clearance_ticks(state, tank, plan_direction, 1, rules) == 0
    )
    if memory.plan_ticks_left <= 0 or stuck:
        roll, rng = rng.below(PERCENT)
        pursuing = roll < profile.aggression_percent
        plan_direction = None
        plan_ticks_left = profile.plan_commit_ticks

    target = _select_target(state, tank, profile)
    move, engaged = _engagement(state, tank, target, profile, pursuing, rules)

    if engaged:
        # A firing line outranks the roaming plan, and outranks dodging: a bot that has
        # the shot takes it rather than stepping out of its own line of fire.
        plan_direction = None
    else:
        threat = incoming_threat(state, tank, profile.planning_horizon_ticks, rules)
        dodge = None if threat is None else _dodge_direction(state, tank, threat, profile, rules)
        if dodge is not None:
            plan_direction = dodge
            plan_ticks_left = profile.plan_commit_ticks
        elif plan_direction is None:
            plan_direction = _roam_direction(state, tank, target, profile, rules)
        move = plan_direction

    pose = predicted_pose(state, tank, move, rules)
    engaged_ticks = min(memory.engaged_ticks + 1, profile.reaction_delay_ticks) if engaged else 0
    ticks_since_fire = min(memory.ticks_since_fire + 1, profile.fire_cooldown_ticks)

    fire = False
    if (
        engaged
        and engaged_ticks >= profile.reaction_delay_ticks
        and ticks_since_fire >= profile.fire_cooldown_ticks
        and _shot_is_available(state, pose, rules)
    ):
        roll, rng = rng.below(PERCENT)
        fire = roll >= profile.miss_chance_percent
        if fire:
            ticks_since_fire = 0

    commands: list[Command] = []
    if move is not None:
        commands.append(MoveCommand(tank_id=tank.entity_id, direction=move))
    if fire:
        commands.append(FireCommand(tank_id=tank.entity_id))

    return BotDecision(
        bot=replace(
            bot,
            memory=BotMemory(
                rng=rng,
                plan_direction=plan_direction,
                plan_ticks_left=plan_ticks_left,
                pursuing=pursuing,
                engaged_ticks=engaged_ticks,
                ticks_since_fire=ticks_since_fire,
            ),
        ),
        commands=tuple(commands),
    )


def plan_tick(
    bots: Sequence[Bot],
    state: SimulationState,
    rules: Rules = DEFAULT_RULES,
) -> TickPlan:
    """Decide for every bot and assemble one :class:`~battle_city_sim.inputs.TickInput`.

    Bots are walked in ascending tank order and the returned bots keep that order, so a
    caller cannot make the plan depend on how it happened to build its list. Two bots
    driving one tank is refused here rather than at the engine's duplicate-command check,
    because the engine's message would name a command index instead of the real mistake.
    """
    ordered = sorted(bots, key=lambda item: item.tank_id)
    seen: set[int] = set()
    for bot in ordered:
        if bot.tank_id in seen:
            raise ValueError(f"two bots drive tank {bot.tank_id}")
        seen.add(bot.tank_id)

    commands: list[Command] = []
    successors: list[Bot] = []
    for bot in ordered:
        decision = decide(bot, state, rules)
        commands.extend(decision.commands)
        successors.append(decision.bot)
    return TickPlan(
        tick_input=TickInput(tick=state.tick, commands=tuple(commands)),
        bots=tuple(successors),
    )


def _stand_down(memory: BotMemory) -> BotMemory:
    """Return the memory of a bot whose tank is gone or whose run has ended.

    The stream position is kept, because re-deriving it would replay draws the bot has
    already spent. Everything about the lost tank's situation is cleared.
    """
    return replace(memory, plan_direction=None, plan_ticks_left=0, engaged_ticks=0)


def _select_target(
    state: SimulationState,
    tank: Tank,
    profile: DifficultyProfile,
) -> Tank | None:
    """Return the hostile this bot is working on, or ``None`` when there are none.

    The key is total: preference bucket, then Manhattan distance, then entity identifier.
    A bot therefore never oscillates between two equidistant hostiles.
    """
    hostiles = hostiles_of(state, tank)
    if not hostiles:
        return None
    prefers_aligned = profile.target_preference is TargetPreference.ALIGNED

    def ranking(other: Tank) -> tuple[int, int, int]:
        aligned = prefers_aligned and bool(aim_candidates(tank, other, profile.aim_tolerance_px))
        return (0 if aligned else 1, manhattan(tank.position, other.position), other.entity_id)

    return min(hostiles, key=ranking)


def _engagement(
    state: SimulationState,
    tank: Tank,
    target: Tank | None,
    profile: DifficultyProfile,
    pursuing: bool,
    rules: Rules = DEFAULT_RULES,
) -> tuple[Direction | None, bool]:
    """Return ``(move, engaged)`` for this tick's firing attempt.

    A facing qualifies only when the pose the bot will actually hold after its move is
    still aligned within the profile's tolerance *and* a projectile leaving that pose's
    muzzle reaches a hostile. The second test is the one that keeps a bot honest about
    walls, water and mirrors, and it is why ``move`` is decided before ``engaged``.

    While aggressive the bot keeps advancing along its firing line; while cautious it
    holds position and issues no move at all, which preserves its facing for free.
    """
    if target is None:
        return None, False
    for direction in aim_candidates(tank, target, profile.aim_tolerance_px):
        move = None if (tank.facing is direction and not pursuing) else direction
        pose = predicted_pose(state, tank, move, rules)
        if pose.facing is not direction:
            continue
        if direction not in aim_candidates(pose, target, profile.aim_tolerance_px):
            continue
        hit = shot_target_id(
            state,
            origin=muzzle_of(pose, rules),
            direction=direction,
            faction=tank.faction,
            rules=rules,
        )
        if hit is None:
            continue
        return move, True
    return None, False


def _shot_is_available(state: SimulationState, tank: Tank, rules: Rules) -> bool:
    """Return whether the fire phase would actually produce a projectile for ``tank``.

    This mirrors ``step._phase_fire``'s gate so the bot never spends a tick on a command
    the engine accepts and then quietly drops. The gatling test compares against ``1``
    because the expiry phase decrements the timer before the fire phase reads it, so a
    tank on its last gatling tick is no longer under gatling when it would matter.
    """
    if tank.gatling_ticks > 1:
        return True
    return len(state.projectiles_owned_by(tank.entity_id)) < rules.max_projectiles_per_tank


def _roam_direction(
    state: SimulationState,
    tank: Tank,
    target: Tank | None,
    profile: DifficultyProfile,
    rules: Rules,
) -> Direction | None:
    """Return the direction to commit to while no firing line exists.

    Candidates are scored on closing distance first and clearance second, with
    :data:`~battle_city_sim.geometry.DIRECTION_ORDER` breaking every remaining tie.
    Directions whose very first step is refused are dropped, so the bot does not commit
    to a wall. Returning ``None`` means every direction is blocked; the bot then issues no
    command and holds its pose, which is a legal tick rather than a wasted one.
    """
    horizon = profile.planning_horizon_ticks
    best: tuple[int, int, int] | None = None
    chosen: Direction | None = None
    for index, direction in enumerate(DIRECTION_ORDER):
        clearance = clearance_ticks(state, tank, direction, horizon, rules)
        if clearance == 0:
            continue
        if target is None:
            gain = 0
        else:
            after = predicted_pose(state, tank, direction, rules)
            gain = manhattan(tank.position, target.position) - manhattan(
                after.position, target.position
            )
        score = (gain, clearance, -index)
        if best is None or score > best:
            best = score
            chosen = direction
    return chosen


def _dodge_direction(
    state: SimulationState,
    tank: Tank,
    threat: Projectile,
    profile: DifficultyProfile,
    rules: Rules,
) -> Direction | None:
    """Return a sidestep out of ``threat``'s path, or ``None`` when none helps.

    Only the two facings perpendicular to the shot are considered, because a projectile
    moves 3px a tick and a tank 2px: fleeing along the shot's axis loses the race. A
    candidate is checked at the pose it reaches after its full clearance run, and a
    candidate that escapes the line always beats one that merely moves.
    """
    horizon = profile.planning_horizon_ticks
    best: tuple[int, int, int] | None = None
    chosen: Direction | None = None
    for index, direction in enumerate(DIRECTION_ORDER):
        if direction not in _PERPENDICULAR[threat.direction]:
            continue
        clearance = clearance_ticks(state, tank, direction, horizon, rules)
        if clearance == 0:
            continue
        pose = tank
        for _ in range(clearance):
            pose = predicted_pose(state, pose, direction, rules)
        escaped = not projectile_reaches(state, threat, pose, horizon, rules)
        score = (1 if escaped else 0, clearance, -index)
        if best is None or score > best:
            best = score
            chosen = direction
    return chosen
