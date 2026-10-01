# Repository instructions

## Product and architecture

- Read relevant specs in openspec/specs/ and the complete linked GitHub issue before editing.
- Implement only the issue's goal and allowed files. Never silently redefine a contract: state every behavior or contract change in the pull request, and on the issue when the issue's own acceptance criteria changed.
- When acceptance criteria and a spec disagree, reconcile them inside the issue's goal, the accepted specs, and the allowed files by taking the most conservative reversible option, and record the choice and its rationale. Where a spec reserves a decision to an accepted change proposal — gameplay rules, scoring, wave pacing, win-state timing, score and life rebalance, canonical serialization — that reservation stands: keep the recorded contract, narrow this issue, and raise a proposal or an unlabelled follow-up issue instead of deciding it here.
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
- An issue carrying the exact label agent:ready is the owner delegating that issue's ordinary design, implementation, and spec-reconciliation decisions to you, and the delegation survives the dispatcher's transition to agent:running. Choose the conservative, reversible option that fits the goal, the accepted specs, and the allowed files, document the choice and why in the pull request — and on the issue when acceptance criteria changed — and continue. Do not ask the owner to pick among ordinary alternatives. The delegation covers that one issue; it never authorizes a new issue, a label, or an edit outside the allowed files.
- If you discover work outside current issue scope, narrow this issue to its in-scope part and open an unlabelled follow-up issue for the rest.
- Escalate only when narrowing leaves no coherent in-scope option, or when the work needs authority, credentials, or permissions beyond that delegation — including a decision the specs reserve to an accepted proposal, or genuinely missing product intent. Ask through the GitHub issue; dispatcher must move it to needs-human. Ordinary ambiguity or resolvable issue/spec tension is not an escalation.

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
