# campaign-v1: single-player campaign rules

## Problem

The product specification defers "exact scoring, wave pacing, and win-state timing" to an
accepted gameplay change proposal, and every package downstream has honoured that
deferral rather than guessing:

- `battle_city_sim` ships no wave cadence, no stage-win timing and no score total. It
  reports points as `ScoreAwarded` events, spawns enemies only from an explicit
  `SpawnEnemyCommand`, and defines two run outcomes, both failures
  (`BASE_DESTROYED`, `PLAYERS_ELIMINATED`).
- `battle_city_content` carries a `waves` field that nothing interprets, and the three
  converted classic levels declare none because the historical stage data has none.
- `battle_city_client` says plainly on its stage-select screen that enemy cadence and
  stage victory are later-phase work, and `tests/client` guards that boundary with
  `test_no_live_run_reaches_an_outcome_in_this_build`.

The result is a playable board that cannot be won, lost to an enemy, or progressed. This
proposal states the missing rules so a campaign can be implemented without any layer
inventing one.

## Intended outcome

A single-player campaign over an ordered pack of stages, with:

- source-compatible score values and a campaign-owned running total,
- source-compatible enemy quotas and spawn cadence,
- an explicit stage-clear condition and an explicit campaign victory,
- game-over and restart rules, and a stated checkpoint policy,
- a deterministic, seeded, headless controller that owns all of the above and touches no
  display, clock, file or socket.

## Requirements

### R1 — Scoring

The campaign total is the sum of `ScoreAwarded.points` over every event the simulation
reports for the run. The point values stay the ones the simulation already publishes and
the historical runtime used: 50 for breaking a shielded enemy's shield, 100 for
destroying a normal enemy, 200 for destroying an unshielded enemy, so a shielded enemy is
worth 250 across its two hits.

The campaign does not re-derive or rebalance those values; rebalancing them is a separate
proposal with compatibility notes, as the product specification requires.

### R2 — Enemy quota per stage

A stage releases a fixed number of enemy tanks.

- A level that declares `waves` releases the sum of the declared `enemies` counts.
- A level that declares none falls back to `first_stage_enemies + stage_enemy_step * index`
  where `index` is the stage's zero-based position in the pack. With the defaults
  `first_stage_enemies = 5` and `stage_enemy_step = 2`, the three classic stages release
  5, 7 and 9 enemies, which is the historical `(level * 2) + 3` for levels 1, 2 and 3.

### R3 — Spawn cadence

One enemy enters play on the stage's tick 0 and then one every `spawn_interval_ticks`
(default 600) after the previous enemy actually entered, until the quota is exhausted.
There is no cap on how many enemies are alive at once; the historical runtime had none.

Each spawn draws its cell, its facing and its variant from a campaign-owned seeded
generator. Variants drawn at spawn are normal and shielded; unshielded is reached only by
breaking a shield, exactly as the historical `random.choice([0, 1])` and the shield ladder
do.

A spawn whose drawn cell is occupied by a live tank is retried: the campaign takes the
first unoccupied declared spawn cell after the drawn one, in declaration order, and if
every cell is occupied it emits no spawn that tick and retries on the next tick. The quota
is not spent by a blocked attempt.

### R4 — Stage clear

A stage is cleared when, after a tick resolves and with no run outcome recorded, all three
hold:

1. no enemies remain queued to spawn,
2. no enemy tank is alive, and
3. no projectile is in flight — including the player's own.

This is the historical condition, kept verbatim.

### R5 — Campaign victory

Clearing the last stage of the pack completes the campaign. This is a deliberate deviation
from the historical runtime, whose win state is unreachable (see design.md, D5).

### R6 — Game over

The campaign fails when the simulation records either of its outcomes: the base is
destroyed, or the player runs out of lives. The simulation already owns both, including
the three starting lives and the life-loss ladder; the campaign adds nothing to them.

### R7 — Lives across stages

Lives carry from one stage to the next. A new stage starts the simulation with
`starting_lives` set to the lives the player held when the previous stage was cleared. The
first stage of a campaign starts with `CampaignRules.starting_lives` (default 3).

### R8 — Restart and checkpoint

- **Restart stage.** Replays the current stage from its own beginning: the lives and the
  score the player held when that stage began, and the same per-stage seed, so a restarted
  stage is byte-identical given identical input.
- **Restart campaign.** Returns to the first stage of the pack with the campaign's
  starting lives and a score of **zero**. The historical runtime reset the stage and the
  lives but not the score; v1 resets all three (see design.md, D6).
- **Checkpoint.** v1 has no saved progress. The stage-select screen lists every stage in
  the pack and starting the campaign at a chosen stage is the whole of the checkpoint
  story. Persisted campaign progress is phase 13 (persistence), not this change.

### R9 — Determinism and purity

The controller is a pure function of its inputs: immutable state in, new state out. It
reads no clock, no file, no socket and no environment, and draws only from a seeded
`battle_city_sim.rng.Rng` derived from the campaign seed and the stage index. Identical
seed, plan, rules and per-tick intents produce identical states and identical totals.

### R10 — Enemy behaviour is injected, not owned

The campaign spawns enemies and decides cadence. It does not steer them. Enemy commands
come from an injected driver with a published protocol, so the campaign never contains
bot logic and the architecture's `client → sim, content, protocol` dependency direction is
preserved: the client package does not import `battle_city_ai`.

The default driver issues no commands, so the shipped client spawns enemies that hold
position and do not fire. That is stated plainly in the client, in this proposal, and in
the product specification rather than being presented as finished bot play. Wiring the AI
package into the shipped client needs a dependency this issue does not own.

## Non-goals

- **Powerup spawn pacing.** The historical runtime draws and collects powerups but never
  spawns one — nothing in it appends to the powerup list — so there is no source fact to
  preserve. The campaign spawns none and the deferral stays recorded.
- **Legacy cheats.** `hesoyam`, `pewpews` and `juancho` stay unimplemented; the product
  specification requires them to be disabled or marked in competitive modes, which is its
  own decision.
- **Multiplayer and co-op campaigns.** v1 is one local seat.
- **Score rebalancing, new scoring events, time or life bonuses.**
- **Per-wave gating** (holding wave N+1 until wave N is cleared). v1 releases a stage's
  whole quota on one cadence; the wave list is a total, not a schedule.
- **Saved progress, high scores, unlocks.**
- **Changes to `battle_city_sim`.** No simulation file is touched. Campaign victory is a
  controller concept; `RunOutcome` keeps its two failure values.

## Affected current specs

- `openspec/specs/product/spec.md` — "Core loop" and "Modes and progression": record the
  accepted scoring, cadence, stage-clear and win-state rules instead of deferring them.
- `openspec/specs/content/spec.md` — "Data format": say how a declared `waves` list is
  read and what a level that declares none falls back to.

No other accepted spec changes. The architecture, AI, networking, persistence and
art-pipeline specs are unaffected.
