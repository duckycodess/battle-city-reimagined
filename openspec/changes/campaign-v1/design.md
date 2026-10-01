# campaign-v1: design

## Source material

Inspected at `duckycodess/Battle-City` revision
`5c9d81cd0de89a05f5946448d19c40fb343b0a2d`, the revision `docs/historical-source.md`
records. Read, not copied; no Pyxel runtime code is reused. The facts below are what the
decisions rest on, each with the construct it was read from.

| Fact | Historical construct |
| --- | --- |
| Three lives, reset only on restart | `self.lives = 3` in `__init__` and in `init_positions(restart=True)` |
| Score 50 / 100 / 200 | `self.score += 50` on shield break, `+= 100` normal, `+= 200` unshielded, in `check_bullet_collision` |
| Enemies per stage `(level * 2) + 3` | `load_level` |
| One enemy every 600 ticks from tick 0 | `if self.tick % 600 == 0 and self.enemy_spawning > 0` in `check_enemy_ai`, with `self.tick += 1` at the end of `update` |
| Spawn cell, facing and variant are random | `random.choice(self.current_enemy_spawns)`, `random.choice(['UP','DOWN','LEFT','RIGHT'])`, `random.choice([0, 1])` |
| Stage clear needs no live enemies, no queued enemies and no bullets of any owner | the three-term condition at the end of `update` |
| Lives carry between stages | `init_positions()` without `restart` does not touch `self.lives` |
| Restart resets stage and lives but not score | `init_positions(restart=True)` sets `self.level` and `self.lives` only |
| The win state is unreachable | see D5 |
| Powerups are never spawned | nothing appends to `Powerup.powerups`; only `check_powerup_collission` removes from it |

## Decisions

### D1 — The controller lives in the client package, not in content

`battle_city_content` may depend on no other project package, and a campaign controller
must step `battle_city_sim`. The architecture specification allows
`client → sim, content, protocol`, so `battle_city_client.campaign` is the only allowed
home for it inside this issue's file ownership.

The package is pygame-free and is reachable without importing pygame: `battle_city_client`
resolves its pygame-backed names lazily, and nothing in `campaign/` imports the window,
the renderer or the asset library. A server-side campaign would import the same module.

### D2 — Enemy quota: declared waves first, classic fallback second

`Level.waves` already exists in the content schema and carries only a count, which is all
v1 needs. Summing the declared counts and falling back to the classic arithmetic keeps the
three bundled stages at 5/7/9 without writing wave data into files this issue would then
own, and lets an authored pack state its own quotas today.

The fallback is indexed by pack position, not by a level identifier, so a pack of any
length gets a monotonically rising quota and the classic pack reproduces the source.

### D3 — Cadence is measured from the previous spawn, not from an absolute schedule

With no blocking the two are identical: ticks 0, 600, 1200, … A blocked spawn is the only
difference, and measuring from the previous successful spawn is the rule that is simple to
state and has no burst behaviour. A blocked attempt retries on the very next tick rather
than waiting another interval, because the blocker is usually a tank that is about to move
and a 10-second stall for a one-tick overlap would be felt as a bug.

### D4 — Spawn draws are made before occupancy is checked

Each attempt draws cell, facing and variant in that fixed order, then resolves occupancy
by walking declared spawn cells from the drawn one. The draws happen on every attempt,
including a blocked one, so the generator's position is a function of the number of
attempts alone. That keeps the stream easy to reason about and keeps a restarted stage
byte-identical.

The historical runtime placed an enemy on an occupied cell happily, because it had no
placement validation. `battle_city_sim` rejects the whole tick for an overlapping
`SpawnEnemyCommand`, and rejecting is correct — two coincident bodies can never step
apart. Retrying is the smallest adaptation that keeps the cadence and never submits an
illegal command.

### D5 — The historical win state is unreachable; v1 fixes it

In `update`, clearing a stage runs:

- `if self.level <= max(levels)`: increment `self.level` and enter `LEVEL_COMPLETED`;
- `elif self.level > max(levels)`: enter `WIN`.

Clearing level 3 of 3 takes the first branch, because `3 <= 3`. `self.level` becomes 4 and
the state becomes `LEVEL_COMPLETED`. The `LEVEL_COMPLETED` handler then runs
`if self.level <= max(levels)` — `4 <= 3` is false — so it does nothing, every tick,
forever. `WIN` is never assigned and the "You Win" screen cannot be shown.

This is an incidental bug in an unreachable branch, not a rule. The content specification
says specifications and deterministic tests, not incidental legacy bugs, govern the
rebuild. v1 therefore completes the campaign when the last stage is cleared, and records
the deviation here, in the product specification and in the pull request.

**Compatibility.** Nothing can depend on the historical behaviour: no save format, no
replay and no score table existed, and the unreachable state produced no observable
output. The deviation is one-way (a reachable win where there was a hang) and is reversible
by a later proposal.

### D6 — A campaign restart clears the score

The historical `init_positions(restart=True)` resets `self.level` and `self.lives` and
leaves `self.score` alone, so pressing `R` carried the previous run's points into a fresh
campaign and the score grew across restarts without bound. Nothing in the historical code
treats that as intentional — there is no cumulative or career score anywhere, the HUD
calls it "Score", and the same field is shown on the game-over screen as the result of the
run that just ended.

v1 clears the score on a campaign restart. It is the lowest-surprise reading of a per-run
score, it is what makes the number on the game-over screen mean something, and it is
reversible: a later proposal that wants a career total adds one beside the run total
rather than reinterpreting it.

Restarting a *stage* rewinds the score to the total the player held when that stage began,
for the same reason: the attempt being discarded should not leave its points behind.

### D7 — Campaign victory is a controller phase, not a `RunOutcome`

`battle_city_sim.RunOutcome` has two values and both are failures. Adding a third would
change the canonical state encoding, which the architecture specification makes a proposal
with a replay-compatibility policy, and this issue may not edit simulation files anyway.

A cleared stage leaves the simulation state unfinished and perfectly ordinary; "cleared"
is a statement about the campaign, not about the run. `CampaignPhase` therefore holds
`PLAYING`, `STAGE_CLEARED`, `COMPLETED` and `FAILED`, and `FAILED` is derived from
`SimulationState.outcome` rather than set independently. The simulation stays the only
thing that can end a run.

### D8 — Enemy commands are injected through a protocol

`EnemyCommandDriver` is a two-method protocol: one call when a stage begins, one call per
tick returning the successor driver and that tick's commands. It is a value, like
everything else in the run, so a `CampaignRun` stays immutable and replayable.

The controller filters whatever a driver returns: at most one `MoveCommand` and one
`FireCommand` per tank, and only for tanks that are alive and on the enemy faction. A
driver therefore cannot command the player's tank, cannot respawn anything, cannot spawn
anything, and cannot make the simulation raise. That guardrail is what makes injecting a
third-party driver safe rather than merely convenient.

`IdleEnemyDriver` is the default and returns nothing.

### D9 — The shipped client uses the idle driver, and says so

The architecture specification does not allow `client → ai`, and the client manifest is
owned by integration issues that this issue may not edit. Importing `battle_city_ai` from
`battle_city_client` would create an undeclared dependency and break the acyclic package
contract, so the shipped client does not do it.

What ships is therefore honest and limited: **enemies spawn on the classic cadence, hold
position and never fire.** A stage is cleared by destroying them, the quota and the clear
condition are real, and the campaign can be won or lost to a destroyed base — but the
enemies do not fight back. The client README, the stage-select screen and the product
specification all say this in those words. Nothing in this change claims bots are
integrated.

The deterministic tests inject a driver backed by `battle_city_ai`, because `tests/` may
import every package. That proves the seam carries a real bot without the client
depending on one.

Wiring the AI package into the shipped client is **issue #35** — an unlabelled follow-up
that adds `battle-city-ai` to the client manifest, extends the architecture spec's allowed
dependency direction with `client → ai`, and selects a difficulty profile. It is narrow
and it is outside this issue's allowed files by construction.

### D10 — No new client `Screen` members

`tests/client/test_client_presentation.py::test_every_screen_draws_something` asserts
`set(frames) == set(Screen)`, and `tests/client` is not in this issue's allowed files.
Adding `STAGE_CLEARED` and `CAMPAIGN_COMPLETE` screens would fail a test this issue may
not edit.

The existing `Screen.RUN_OVER` is therefore the interstitial for all three campaign
endings. Its overlay reads the campaign phase and changes its headline, its sub-heading
and its menu labels — "STAGE CLEAR" with *NEXT STAGE*, "CAMPAIGN COMPLETE" with *PLAY
AGAIN*, or the failure headline with *RESTART CAMPAIGN*. The free-play wording is
unchanged when no campaign is running.

This is a presentation compromise and nothing more: the phases, their transitions and
their tests are complete. Splitting the interstitial into its own screens belongs to
**issue #36**, the unlabelled follow-up that owns `tests/client`.

### D11 — Selecting a stage starts the campaign at that stage

The main menu is fixed at `PLAY`, `CONTROLS`, `QUIT` by assertions in `tests/client`, so a
separate "CAMPAIGN" entry is not available to this issue. `PLAY → STAGE_SELECT → confirm`
now starts the campaign at the highlighted stage instead of starting a one-off run.

That is not a workaround dressed up as a feature: with no persistence, choosing where the
campaign begins *is* the checkpoint policy (R8), and one mode over the shared simulation is
what the product specification asks for. A campaign begun at stage 3 starts with the
campaign's starting lives and a score of zero, which the screen states.

### D12 — Lives carry through a per-stage `Rules` copy

`new_game` reads `rules.starting_lives`, and `SimulationState` has no way to be handed a
life count otherwise. The controller therefore starts each stage with
`replace(sim_rules, starting_lives=carried)`. Nothing else in `Rules` changes, the value
is always at least 1 (a run with zero lives has already ended), and the simulation needs
no modification.

The alternative — a `lives` argument to `new_game` — is a simulation API change this issue
may not make, and it would buy nothing: `Rules` is already the per-session contract value
the architecture expects a mode to supply.

### D13 — Per-stage seeds are derived, and a stage restart replays

The campaign seed is folded with a domain separator and the stage index through one
SplitMix64 round, using `battle_city_sim.rng.Rng` rather than any algorithm of this
module's own. Each stage gets its own simulation seed and its own campaign generator, both
functions of `(campaign seed, stage index)` alone — which is exactly what makes restarting
a stage reproduce it, and what keeps two stages from drawing the same spawn sequence.

The derivation mirrors `battle_city_ai.seeding` deliberately rather than importing it: AI
is not an allowed client dependency. The duplication is twelve lines, it is recorded in
both places, and neither copy owns an algorithm — both call the simulation's generator.

## Interfaces

```python
# battle_city_client.campaign
CampaignRules(starting_lives=3, spawn_interval_ticks=600,
              first_stage_enemies=5, stage_enemy_step=2,
              spawn_variants=(ENEMY_NORMAL, ENEMY_SHIELDED),
              spawn_facings=DIRECTION_ORDER)

StagePlan(index, level_id, name, stage, enemy_quota)
campaign_plan(catalog, rules=DEFAULT_CAMPAIGN_RULES) -> tuple[StagePlan, ...]

class EnemyCommandDriver(Protocol):
    def entering_stage(self, state, rules) -> EnemyCommandDriver: ...
    def commands(self, state, rules) -> tuple[EnemyCommandDriver, tuple[Command, ...]]: ...

IdleEnemyDriver()                       # the default; issues nothing

class CampaignPhase(Enum):
    PLAYING; STAGE_CLEARED; COMPLETED; FAILED

CampaignRun.start(plan, *, seed, rules, sim_rules, driver, stage_index=0) -> CampaignRun
CampaignRun.advance(ticks, intent) -> CampaignRun
CampaignRun.advanced_stage() -> CampaignRun      # STAGE_CLEARED -> the next stage
CampaignRun.restarted_stage() -> CampaignRun     # this stage, its own starting score/lives
CampaignRun.restarted() -> CampaignRun           # stage 0, starting lives, score 0
```

`StageEntry` gains `waves: tuple[int, ...] = ()`, filled by `stage_catalog` from
`Level.waves`. The field has a default, so every existing construction keeps working, and
`stage_from_level` still drops waves — the simulation stage is unchanged by them, which
`tests/client/test_client_stage_adapter.py::test_declared_waves_do_not_reach_the_stage`
continues to assert.

`StageSession` gains `stepped(commands)`: one tick with a caller-supplied command tuple.
`advance` is reimplemented on top of it and behaves identically. This is what lets the
campaign add spawn and enemy commands to the player's own without duplicating the session.

## Compatibility and migration

- **Content.** No schema change, no level file change. A level that declares no `waves`
  behaves exactly as before plus the classic fallback. A pack pinned to level schema
  version 1 keeps loading.
- **Simulation.** Untouched. No canonical encoding change, so existing replays and state
  hashes are unaffected.
- **Saves.** None exist. When persistence lands, a campaign save needs the campaign seed,
  the pack identifier and version, the stage index, the score, the lives and the schema
  version; `CampaignRun` holds all of them as plain values for exactly that reason. It
  must *not* persist the generator position alone, because a restored stage is replayed
  from its derived seed.
- **Client API.** Additive. `ClientShell` gains a `campaign` field and keeps `session`
  pointing at the live session, so existing readers — including `tests/client` — are
  unaffected.

## Balance and safety

- Score values, quotas, cadence and lives are the source's. The only balance-visible
  deviations are D5 (a reachable win) and D6 (a cleared score on restart), both recorded.
- The shipped enemies do not fire (D9), which makes v1 *easier* than the source. It is
  stated everywhere a player or a reviewer would look, and it is removed by #35, not by
  quietly adding steering to the client.
- A driver cannot escalate: the controller filters its commands to legal enemy
  move/fire (D8).
- Nothing reads a clock, a file or the environment, and no unseeded randomness exists
  anywhere in the controller.

## Alternatives considered

1. **Put wave cadence in the simulation.** Rejected: the simulation's documented position
   is that cadence is campaign policy, and encoding a scheduler in the state would change
   the canonical encoding and bind every mode to one pacing rule.
2. **Add a `VICTORY` value to `RunOutcome`.** Rejected: a replay-compatibility change
   (D7), and outside this issue's allowed files.
3. **Import `battle_city_ai` from the client.** Rejected: forbidden dependency direction
   and a manifest this issue does not own (D9).
4. **Write `waves` into the three classic level files.** Rejected as redundant: the
   fallback already reproduces 5/7/9, and writing the numbers into converted files would
   claim wave data the historical stage data does not contain.
5. **Keep free play and campaign as two modes behind a new main-menu entry.** Rejected:
   the main menu's contents are asserted by `tests/client` (D11), and one mode is what the
   product specification's "modes supply rules and content to the shared simulation"
   describes anyway.
6. **A spawn cap on concurrent enemies.** Rejected: no source fact supports a number, and
   inventing one is a balance decision this change does not need to make.
