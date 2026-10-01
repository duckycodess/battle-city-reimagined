# campaign-v1: tasks

## T1 — Campaign rules, plan and seeding (issue #6)

Implement `battle_city_client.campaign` rules, the stage plan and the seed derivation.

- Files: `packages/client/src/battle_city_client/campaign/{__init__,rules,plan,seeding}.py`,
  `packages/client/src/battle_city_client/stage_adapter.py` (`StageEntry.waves`).
- Covers: R2, R9, D2, D13.
- Dependencies: none inside this change.

## T2 — The driver seam (issue #6)

Publish `EnemyCommandDriver` and `IdleEnemyDriver`, and the controller-side filter that
keeps a driver's output legal.

- Files: `packages/client/src/battle_city_client/campaign/driver.py`.
- Covers: R10, D8, D9.
- Dependencies: T1.

## T3 — The controller (issue #6)

`CampaignRun`: spawn cadence, retry-on-occupied, score accumulation, stage clear, campaign
victory, game over, stage and campaign restart, lives carried through a per-stage `Rules`
copy.

- Files: `packages/client/src/battle_city_client/campaign/controller.py`,
  `packages/client/src/battle_city_client/session.py` (`StageSession.stepped`).
- Covers: R1, R3, R4, R5, R6, R7, R8, R9, D1, D3, D4, D5, D6, D7, D12.
- Dependencies: T1, T2.

## T4 — Client presentation (issue #6)

Shell `campaign` field, stage select starts the campaign, campaign-aware interstitial,
score/lives/stage/enemies HUD.

- Files: `packages/client/src/battle_city_client/{shell,rendering}.py`,
  `packages/client/src/battle_city_client/README.md`.
- Covers: R8 (checkpoint presentation), D10, D11.
- Dependencies: T3.

## T5 — Specs (issue #6)

Record the accepted rules.

- Files: `openspec/specs/product/spec.md`, `openspec/specs/content/spec.md`.
- Covers: every requirement; this is where the deferral is lifted.
- Dependencies: none. **Lands before any behaviour code**, per the issue's inputs.

## T6 — Deterministic tests (issue #6)

`tests/campaign`: headless, seeded, all three classic stages, plus an injected
`battle_city_ai` driver.

- Files: `tests/campaign/**`.
- Covers: R1–R10 as assertions.
- Dependencies: T3, T4.

## Deferred to follow-up issues

| Work | Issue | Why not here |
| --- | --- | --- |
| Wire `battle_city_ai` into the shipped client | #35 | `client → ai` is not an allowed dependency and the client manifest is owned by integration issues |
| Dedicated stage-clear and campaign-complete screens; replace `test_no_live_run_reaches_an_outcome_in_this_build`; refresh client captures | #36 | `tests/client` is outside this issue's allowed files |
| Powerup spawn pacing | none yet | No source fact exists; needs its own proposal |
| Persisted campaign progress | #13 | Phase 13 owns saves |
| Legacy cheats | none yet | Competitive-mode handling is its own decision |

## Acceptance commands

- `uv run --locked pytest tests/campaign`
- `uv run --locked mypy packages/content packages/client tests/campaign`
- `uv run --locked ruff format --check` and `ruff check` over the touched paths
- `make ci`

## Unresolved risks

1. **The shipped campaign is winnable against enemies that do not act.** Stated in the
   client, the specs and this change; removed by #35.
2. **The three classic stages declare no waves**, so their quotas come from the fallback
   arithmetic. If a later pack wants per-stage quotas it must declare `waves`; the
   converted files stay as the historical data left them.
3. **`tests/client/screenshots/` is now stale** with respect to the campaign HUD. The
   captures are not asserted against, so nothing fails; #36 refreshes them.
4. **Save compatibility is forward-looking only.** `CampaignRun` holds the fields a save
   would need (seed, pack identity, stage index, score, lives), but no save format is
   proposed here, and the persistence phase remains free to choose one.
