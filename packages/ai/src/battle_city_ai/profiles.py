"""Selectable bot difficulty profiles with explicitly bounded parameters.

A profile is data, never behaviour: :mod:`battle_city_ai.policy` reads these numbers and
nothing else to shape a bot. Keeping the knobs in one frozen value makes a difficulty
change a data diff, lets a session record exactly which profile produced a replay, and
makes the "explicit ranges" the AI specification asks for machine-checkable instead of a
comment.

Every field is validated against a published range, so an out-of-range profile fails at
construction rather than producing a bot that quietly bypasses a fairness limit.

What a profile may *not* do
---------------------------
The AI specification forbids difficulty from bypassing collision, visibility, rate limits
or game rules. Nothing here can: the policy predicts movement with the simulation's own
collision arithmetic, gates firing on ``rules.max_projectiles_per_tank``, and emits only
the two commands a human player can emit. A profile tunes *when* a bot acts, never
*what* the rules allow.

Team coordination and player-style variation are deliberately absent. The specification
defers them to a proposal with fairness and performance acceptance criteria.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

MAX_HITTING_OFFSET_PX: Final[int] = 8
"""The largest lateral misalignment that can still connect, in pixels.

Derived from the simulation's own geometry rather than guessed. A projectile leaving a
tank facing ``RIGHT`` starts at ``y = shooter.y + rules.muzzle_offset`` (7) and inflates
to a ``2 * rules.projectile_radius + 1`` (3px) box, so it covers
``[shooter.y + 6, shooter.y + 9)``. A ``rules.tank_size`` (16px) target body covers
``[target.y + 0, target.y + 16)``. The two half-open boxes overlap exactly while
``-8 <= shooter.y - target.y <= 9``. Eight is the symmetric part of that window, so a
profile that tolerates more than eight pixels of misalignment would be aiming at
geometry that cannot be hit at all, which is a bug rather than a difficulty setting.
"""

REACTION_DELAY_BOUNDS: Final[tuple[int, int]] = (0, 60)
"""Inclusive range for :attr:`DifficultyProfile.reaction_delay_ticks`."""

PLAN_COMMIT_BOUNDS: Final[tuple[int, int]] = (1, 60)
"""Inclusive range for :attr:`DifficultyProfile.plan_commit_ticks`."""

PLANNING_HORIZON_BOUNDS: Final[tuple[int, int]] = (1, 16)
"""Inclusive range for :attr:`DifficultyProfile.planning_horizon_ticks`.

Sixteen ticks of tank movement is one full classic stage tile of look-ahead at
``rules.tank_speed`` 2, which is the point past which a straight-line probe stops saying
anything useful about a 16x16 grid. The cap is what keeps per-tick bot work bounded.
"""

AIM_TOLERANCE_BOUNDS: Final[tuple[int, int]] = (0, MAX_HITTING_OFFSET_PX)
"""Inclusive range for :attr:`DifficultyProfile.aim_tolerance_px`."""

PERCENT_BOUNDS: Final[tuple[int, int]] = (0, 100)
"""Inclusive range for the two percentage knobs."""

FIRE_COOLDOWN_BOUNDS: Final[tuple[int, int]] = (1, 240)
"""Inclusive range for :attr:`DifficultyProfile.fire_cooldown_ticks`."""


class TargetPreference(Enum):
    """How a bot picks among several hostiles.

    Both orderings are total and deterministic: ties fall back to the nearest hostile and
    then to the ascending entity identifier, so target choice never depends on container
    ordering.
    """

    NEAREST = 0
    """Pick the hostile at the smallest Manhattan distance."""

    ALIGNED = 1
    """Prefer a hostile already inside the aim tolerance, then fall back to nearest.

    A bot that notices an existing firing line converts opportunities a nearest-first bot
    walks past, which is a real skill difference rather than a raw speed bonus.
    """


@dataclass(frozen=True, slots=True)
class DifficultyProfile:
    """One bounded difficulty setting.

    Instances are values. Build a variant with :func:`dataclasses.replace`; never mutate
    a shared profile, because a session records the profile that produced its inputs.
    """

    name: str
    """Stable identifier. It is mixed into the bot's random stream, so renaming a profile
    changes every decision it makes and is therefore a replay-compatibility change."""

    reaction_delay_ticks: int
    """Consecutive ticks a firing solution must hold before the bot may shoot.

    This is the profile's reflex speed. A bot that has been lined up for fewer than this
    many ticks holds fire even when the shot is otherwise legal and clear.
    """

    plan_commit_ticks: int
    """Ticks a roaming direction is held before the bot re-plans.

    Commitment is what stops a bot vibrating between two equally-scored directions, and
    it bounds how stale a movement decision may be.
    """

    planning_horizon_ticks: int
    """Bounded look-ahead, in ticks, for both path clearance and threat detection.

    Used twice, deliberately: it caps how far a candidate direction is probed for
    obstacles, and how far an incoming hostile projectile is traced. A short horizon is
    what makes a low-skill bot walk into walls and fail to dodge.
    """

    aim_tolerance_px: int
    """Largest lateral misalignment, in pixels, the bot accepts as a firing solution.

    Zero demands pixel-perfect alignment; :data:`MAX_HITTING_OFFSET_PX` accepts every
    alignment that can still connect. Larger values fire sooner against a moving target
    and therefore miss more often, which is the intended aim-error dial.
    """

    miss_chance_percent: int
    """Seeded chance, per otherwise-valid shot, that the bot holds fire instead.

    This is the second half of the aim-error model and the only one that consumes the
    bot's random stream. Modelling error as a declined shot rather than as a deliberately
    wrong facing keeps every emitted command a command a skilled player could also have
    emitted, which is what the specification means by "legal inputs".
    """

    aggression_percent: int
    """Seeded chance, per re-plan, that the bot closes distance rather than holding.

    An unaggressive bot keeps its firing line but stops advancing, so it trades board
    control for safety without ever refusing to shoot.
    """

    fire_cooldown_ticks: int
    """Minimum ticks between two shots from this bot.

    A fairness limit on top of the simulation's own ``max_projectiles_per_tank`` gate:
    the rules alone would let a bot re-fire the tick its previous shot expires, which is
    a cadence no player input device can reach.
    """

    target_preference: TargetPreference
    """How the bot chooses among several hostiles."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("profile.name must not be empty")
        _require_within("reaction_delay_ticks", self.reaction_delay_ticks, REACTION_DELAY_BOUNDS)
        _require_within("plan_commit_ticks", self.plan_commit_ticks, PLAN_COMMIT_BOUNDS)
        _require_within(
            "planning_horizon_ticks", self.planning_horizon_ticks, PLANNING_HORIZON_BOUNDS
        )
        _require_within("aim_tolerance_px", self.aim_tolerance_px, AIM_TOLERANCE_BOUNDS)
        _require_within("miss_chance_percent", self.miss_chance_percent, PERCENT_BOUNDS)
        _require_within("aggression_percent", self.aggression_percent, PERCENT_BOUNDS)
        _require_within("fire_cooldown_ticks", self.fire_cooldown_ticks, FIRE_COOLDOWN_BOUNDS)


def _require_within(field: str, value: int, bounds: tuple[int, int]) -> None:
    low, high = bounds
    if not low <= value <= high:
        raise ValueError(f"profile.{field} must be within [{low}, {high}], found {value}")


ROOKIE: Final[DifficultyProfile] = DifficultyProfile(
    name="rookie",
    reaction_delay_ticks=18,
    plan_commit_ticks=24,
    planning_horizon_ticks=2,
    aim_tolerance_px=MAX_HITTING_OFFSET_PX,
    miss_chance_percent=55,
    aggression_percent=25,
    fire_cooldown_ticks=40,
    target_preference=TargetPreference.NEAREST,
)
"""Slow to react, short-sighted, hesitant to shoot. The approachable opponent."""

SOLDIER: Final[DifficultyProfile] = DifficultyProfile(
    name="soldier",
    reaction_delay_ticks=8,
    plan_commit_ticks=12,
    planning_horizon_ticks=5,
    aim_tolerance_px=5,
    miss_chance_percent=25,
    aggression_percent=60,
    fire_cooldown_ticks=20,
    target_preference=TargetPreference.NEAREST,
)
"""The reference opponent: every knob sits between the two extremes."""

VETERAN: Final[DifficultyProfile] = DifficultyProfile(
    name="veteran",
    reaction_delay_ticks=2,
    plan_commit_ticks=6,
    planning_horizon_ticks=8,
    aim_tolerance_px=2,
    miss_chance_percent=5,
    aggression_percent=90,
    fire_cooldown_ticks=10,
    target_preference=TargetPreference.ALIGNED,
)
"""Fast, patient about alignment, and willing to push. Still inside every fairness bound."""

PROFILE_ORDER: Final[tuple[DifficultyProfile, ...]] = (ROOKIE, SOLDIER, VETERAN)
"""The built-in profiles in ascending difficulty.

The ordering is asserted in the tests: reaction delay, commitment, miss chance and fire
cooldown fall monotonically across it while planning horizon and aggression rise, so
"harder" is a property of the data rather than a claim in a docstring.
"""

PROFILES: Final[Mapping[str, DifficultyProfile]] = {
    profile.name: profile for profile in PROFILE_ORDER
}
"""Name-keyed lookup for the built-in profiles."""


def profile_named(name: str) -> DifficultyProfile:
    """Return the built-in profile called ``name``.

    Raises :class:`KeyError` naming the known profiles, because a typo in a session
    configuration should not silently fall back to a different difficulty.
    """
    profile = PROFILES.get(name)
    if profile is None:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"unknown difficulty profile {name!r}; known profiles are {known}")
    return profile
