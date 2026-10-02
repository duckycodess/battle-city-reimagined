# Product specification

## Purpose

Rebuild the author's early Python Battle-City game as a maintainable tank-combat game that works offline, supports bots, and grows into authoritative online multiplayer. New implementation must be modular, data-driven, testable without a display, and automatable through the repository's issue workflow.

## Core loop

Players maneuver tanks through compact stages, use terrain and line of fire, destroy enemy tanks, collect temporary powerups, and protect a vulnerable home base. A stage is won when its required enemies are cleared while the base survives. A destroyed base ends the run.

Exact scoring, wave pacing, and win-state timing are accepted in the campaign-v1 gameplay change and recorded under "Campaign rules" below. Changing any of those values is a further proposal with compatibility notes, not an implementation decision.

## Classic foundation to preserve

The historical project uses a 16×16 tile grid, player and enemy tanks, base defense, score, lives, three stages, normal and shielded enemy variants, and a shield-to-unshielded damage state. Keep the following recognizable behavior:

- Empty ground is traversable.
- Stone is solid and cannot be destroyed.
- Brick stops tanks and projectiles; one hit converts it to cracked brick.
- Cracked brick stops tanks and projectiles; one more hit removes it.
- North-east and south-east mirror tiles redirect projectiles.
- Water blocks tanks; projectiles pass through it.
- Forest is an overlay that does not block movement and can obscure tanks.
- A projectile hitting the home tile destroys the base and ends the run.
- Gatling temporarily increases firing cadence; invincibility prevents damage for a duration; extra-life grants one life.
- Legacy cheats are hesoyam (extra life), pewpews (gatling), and juancho (clear current enemies). Cheats are optional in competitive modes and must be disabled or explicitly marked in replay/session metadata.
- Preserve score and life semantics as campaign concepts. Rebalance only through a proposal with compatibility notes.

Historical input material: duckycodess/Battle-City, assets/stage.py, README.md, and the three converted stage files under the content package. The original repository stays unchanged.

## Modes and progression

Plan for:

1. Single-player campaign with authored stages, enemy waves, score, lives, checkpoints or restart policy, and a scalable level-pack format.
2. Local AI battles with selectable bot difficulty profiles and deterministic seeds.
3. Online co-op defending stages together.
4. Online PvP and team-versus-team modes with server-owned rules and competitive settings.
5. Replays and spectators using versioned input/state streams.

Mode definitions must not fork core simulation rules. A mode supplies validated rules and content to the shared simulation.

## Campaign rules

Accepted in the campaign-v1 change. Values come from the historical runtime at revision `5c9d81cd0de89a05f5946448d19c40fb343b0a2d` except where a deviation is named.

- **Score.** A run's score is the sum of the points the simulation reports: 50 for breaking a shielded enemy's shield, 100 for destroying a normal enemy, 200 for destroying an unshielded enemy. A shielded enemy is therefore worth 250 across its two hits. The simulation reports points and keeps no total; the campaign owns the total.
- **Lives.** A campaign begins with three lives and carries the surviving count from one stage into the next. Losing the last life ends the run.
- **Enemy quota.** A stage releases the sum of its declared waves, or, when it declares none, `5 + 2 * index` enemies for its zero-based position in the pack. The three classic stages release 5, 7 and 9.
- **Spawn cadence.** One enemy enters on a stage's tick 0 and one every 600 ticks after the previous enemy entered, until the quota is spent. Spawn cell, facing and variant are drawn from a seeded campaign generator; spawned variants are normal and shielded, and unshielded is reached only by breaking a shield. A spawn whose cell is occupied retries rather than displacing a tank, and a blocked attempt does not spend the quota. There is no cap on concurrent enemies.
- **Stage clear.** A stage is cleared when no enemies remain queued, no enemy tank is alive, and no projectile is in flight — including the player's own.
- **Campaign victory.** Clearing the last stage of the pack completes the campaign. *Deviation:* the historical runtime's win state is unreachable, because clearing the final stage takes its `level <= max(level)` branch and leaves the run in a level-completed state forever. That is an incidental bug in an unreachable branch, not a rule.
- **Game over.** The run ends when the simulation records an outcome: the base destroyed, or every life spent.
- **Restart.** Restarting a stage replays it from the lives, score and seed it began with. Restarting a campaign returns to the first stage with the starting lives and a score of zero. *Deviation:* the historical restart reset the stage and the lives but not the score, so points accumulated across restarts; a run score that survives the run it measures is not a rule worth preserving.
- **Checkpoint.** Until saved progress exists, the stage list is the checkpoint: a campaign may be started at any stage in the pack, with the starting lives and a score of zero.
- **Enemy behaviour is not campaign behaviour.** The campaign spawns enemies and sets cadence; steering comes from an injected driver so a mode never contains bot logic. The shipped client injects an idle driver, so its spawned enemies hold position and do not fire. That limitation is stated in the client rather than hidden, and lifting it means giving the client a declared dependency on the AI package.
- **Powerups.** The campaign spawns none. The historical runtime drew and collected powerups but never spawned one, so there is no source pacing to preserve; pacing needs its own proposal.

## Expansion

Support modular level packs, a level editor and validation tools, terrain and projectile gimmicks, stage modifiers, replay and spectator hooks, and versioned progression. New gimmicks must define movement, projectile, damage, visibility, AI, network, and accessibility effects.

Cosmetics, unlocks, and progression must not alter competitive balance. Competitive mode configuration is explicit, versioned, and included in session and replay metadata.

## Accessibility and reliability

Support remappable keyboard and gamepad input, configurable repeat/dead-zone behavior where relevant, readable contrast and effects, non-color-only signals, reduced-motion options, and clear sound controls. Persist config and campaign saves with schema versions, validation, backups or recoverable migration, and atomic writes.

## Product boundaries

- Do not refactor or change the historical Pyxel repository.
- Do not put game rules in rendering, networking, content scripts, or UI callbacks.
- Do not use generated concept art as final sprites.
- Do not introduce pay-to-win or competitive gameplay advantages through cosmetics.
- Do not implement an online economy or account service until a separate approved proposal defines privacy, abuse, and operational requirements.

## Acceptance signals

- Identical seed, initial state, and fixed-tick input sequence produce identical simulation state hashes.
- Headless tests run without pygame, display, sound hardware, or network access.
- All bundled stages validate before play and preserve the three historical layouts.
- Client and authoritative server consume the same simulation package.
- Network clients cannot decide authoritative movement, collision, score, damage, or progression.
- Competitive cosmetics do not affect simulation state or visibility.
