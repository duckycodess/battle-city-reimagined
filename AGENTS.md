# Repository instructions

## Product and architecture

- Read relevant specs in openspec/specs/ and the complete linked GitHub issue before editing.
- Implement only the issue's goal and allowed files. If acceptance criteria conflict with a spec, stop and report the conflict; do not silently redefine the contract.
- Keep packages/sim headless, deterministic, and independent of pygame, network, file, clock, and process APIs. Simulation advances only through explicit fixed-tick inputs.
- Client renders state and translates input. Server owns authoritative multiplayer state. AI emits legal simulation inputs. Content is declarative data validated by schemas.
- Preserve the classic 16×16 stages and tile behavior recorded in the product and content specs. New mechanics need explicit rules and tests.
- Never copy the original Pyxel runtime into this repository. Treat duckycodess/Battle-City as historical source material; new code and assets belong to this rebuild.

## Task workflow

- GitHub Issues are the human inbox. Exact label agent:ready is the only authorization to dispatch an issue. Do not self-label new issues or follow-up issues ready unless the user explicitly authorized that exact issue set.
- Each implementation task must define Goal, Allowed files, Dependencies, Inputs, Acceptance commands, Screenshots where relevant, and Remaining risks.
- Check Beads readiness and dependencies before implementation. Keep issue dependency links under the Dependencies heading so dispatcher can wire them.
- Work on one bounded issue per branch and pull request. Do not include unrelated work.
- Do not invoke ccmux. Use the configured Pi/Herdr workflow and Codiv/OpenJev router when dispatched.
- The dispatcher may merge only after its merge-ready report, required checks, independent review where required, dependency completion, and exact-head checks all pass. Do not bypass those gates or manually merge a pull request.
- If you discover work outside current issue scope, open an unlabelled follow-up issue. Ask through the GitHub issue when product intent is genuinely missing; dispatcher must move it to needs-human.

## Shared contracts and dependencies

- Root pyproject.toml, uv.lock, CI, package membership, public shared interfaces, and cross-package schemas are owned by integration/bootstrap issues. Other issues must not edit these files.
- One issue owns each package subtree. Cross-package changes require an explicit dependency or integration issue; avoid concurrent edits to shared contracts.
- Add dependencies only for a demonstrated need. Keep simulation dependencies standard-library-only unless an accepted spec change says otherwise.
- Keep runtime package boundaries acyclic: sim → none; AI → sim; content → none; protocol → none; client/server → sim, content, protocol; tools → content and protocol.

## Quality

- Maintain type annotations and strict mypy compatibility. Format with Ruff.
- Add deterministic simulation tests for state transitions, collisions, tile damage, projectile reflection, seeded AI, and replayable input sequences as those features land.
- Never depend on wall-clock time, iteration order of unordered collections, or unseeded randomness in authoritative simulation.
- Validate all external content and network inputs. Persist data with schema versions and atomic replacement.
- Update specs when behavior or shared contracts change. Include screenshots for material visual changes and remaining risks in every pull request.
