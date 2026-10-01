"""Seeded bot policies that emit legal simulation inputs.

``battle_city_ai`` turns a simulation snapshot into the same two commands a player can
submit. It depends on :mod:`battle_city_sim` and on nothing else in the project, which is
the dependency direction the architecture specification fixes: AI knows the rules, the
rules know nothing about AI.

Using it
--------
::

    from battle_city_ai import Bot, VETERAN, plan_tick, seed_from_state
    from battle_city_sim import step

    bots = (Bot.create(tank_id=2, profile=VETERAN, seed=seed_from_state(state)),)
    for _ in range(ticks):
        plan = plan_tick(bots, state)
        state = step(state, plan.tick_input).state
        bots = plan.bots

Three guarantees hold, and the tests in ``tests/ai`` assert each of them:

* **Legality.** A decision is at most one ``MoveCommand`` and one ``FireCommand`` for the
  bot's own live tank, so ``step`` never rejects a bot's tick.
* **Determinism.** The same profile, seed, state and tick history produce byte-identical
  commands. Nothing reads a clock, a frame counter, the environment or :mod:`random`, and
  the random stream is the simulation's own versioned generator.
* **Fairness.** Difficulty tunes reaction delay, planning horizon, target selection, aim
  error and aggression inside published bounds. It never bypasses collision, the
  ``max_projectiles_per_tank`` gate, or any other rule, and bots see concealed tanks
  exactly as the simulation's presentation-only concealment rule implies they should.

Fairness limits
---------------
Stated here rather than implied, because "difficulty may not bypass the rules" is only
meaningful if the list of things it may not bypass is written down.

* **Inputs.** A bot emits only ``MoveCommand`` and ``FireCommand``, at most one of each
  per tick, naming its own live tank. It never respawns itself, never spawns an enemy or
  a powerup, and never drives a tank it was not created for.
* **Collision.** Movement is predicted with the engine's own pixel arithmetic, so a bot
  plans around exactly the obstacles a player faces and gains nothing from predicting.
* **Rate.** Firing is gated on ``rules.max_projectiles_per_tank`` exactly as
  ``step`` gates it, and then again on the profile's ``fire_cooldown_ticks``, which is
  stricter than the rules alone and exists because the rules alone would permit a cadence
  no input device can produce.
* **Accuracy.** Aim tolerance is capped at the widest misalignment the engine's geometry
  can still convert, and the remaining error is a declined shot, never a shot that bends.
* **Knowledge.** A bot reads the public snapshot. It sees concealed tanks, because
  concealment is presentation-only until a proposal says otherwise, and it does not see
  anything a renderer could not also derive from the same state.
* **Base.** A bot never fires at the home base. A line that reaches the base tile is
  reported as reaching nothing, so no profile can decide to end a run on its own.
* **Coordination.** None. Bots do not share information or divide targets; the AI
  specification defers team play to a proposal with its own fairness criteria.

Performance limits
------------------
One decision costs: one pass over the hostiles to pick a target, at most two firing-line
traces bounded by the playfield's diagonal, at most four roaming probes and two dodge
probes of ``planning_horizon_ticks`` steps each, and one bounded trace per live
projectile. Nothing is cached between ticks and a bot's whole memory is six fields, three
of them counters that saturate at a profile bound, so cost per tick is flat over the
length of a run. The only term that grows with the scene is the per-projectile threat
trace, which a stage's own ``max_projectiles_per_tank`` budget already bounds.

Read :mod:`battle_city_ai.profiles` for the knobs, :mod:`battle_city_ai.perception` for
what a bot may know, :mod:`battle_city_ai.policy` for how it decides, and
:mod:`battle_city_ai.seeding` for where its randomness comes from.
"""

from .perception import (
    aim_candidates,
    body_is_blocked,
    clearance_ticks,
    hostiles_of,
    incoming_threat,
    manhattan,
    max_flight_ticks,
    muzzle_of,
    predicted_pose,
    projectile_reaches,
    shot_target_id,
)
from .policy import (
    PERCENT,
    Bot,
    BotDecision,
    BotMemory,
    TickPlan,
    decide,
    plan_tick,
    seed_from_state,
)
from .profiles import (
    AIM_TOLERANCE_BOUNDS,
    FIRE_COOLDOWN_BOUNDS,
    MAX_HITTING_OFFSET_PX,
    PERCENT_BOUNDS,
    PLAN_COMMIT_BOUNDS,
    PLANNING_HORIZON_BOUNDS,
    PROFILE_ORDER,
    PROFILES,
    REACTION_DELAY_BOUNDS,
    ROOKIE,
    SOLDIER,
    VETERAN,
    DifficultyProfile,
    TargetPreference,
    profile_named,
)
from .seeding import BOT_RNG_DOMAIN, BOT_SEEDING_VERSION, derive_bot_rng, profile_code

__all__ = [
    "AIM_TOLERANCE_BOUNDS",
    "BOT_RNG_DOMAIN",
    "BOT_SEEDING_VERSION",
    "FIRE_COOLDOWN_BOUNDS",
    "MAX_HITTING_OFFSET_PX",
    "PERCENT",
    "PERCENT_BOUNDS",
    "PLANNING_HORIZON_BOUNDS",
    "PLAN_COMMIT_BOUNDS",
    "PROFILES",
    "PROFILE_ORDER",
    "REACTION_DELAY_BOUNDS",
    "ROOKIE",
    "SOLDIER",
    "VETERAN",
    "Bot",
    "BotDecision",
    "BotMemory",
    "DifficultyProfile",
    "TargetPreference",
    "TickPlan",
    "aim_candidates",
    "body_is_blocked",
    "clearance_ticks",
    "decide",
    "derive_bot_rng",
    "hostiles_of",
    "incoming_threat",
    "manhattan",
    "max_flight_ticks",
    "muzzle_of",
    "plan_tick",
    "predicted_pose",
    "profile_code",
    "profile_named",
    "projectile_reaches",
    "seed_from_state",
    "shot_target_id",
]
