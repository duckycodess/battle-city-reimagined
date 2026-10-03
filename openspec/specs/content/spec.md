# Content and level specification

## Data format

Level packs are declarative JSON, not Python modules. Validate against a checked-in JSON Schema before loading. Each level declares schema version, stable ID, display name, grid dimensions, tile data, player and enemy spawn points, wave or encounter data, and optional mode/modifier metadata.

The initial grid remains 16 columns by 16 rows. Level format may version into other dimensions later, but the first migration must preserve the original coordinate system and all three layouts.

Wave data is optional and states only how many enemy tanks a wave releases. The campaign reads it as a stage's total enemy quota: the sum of the declared counts. A level that declares no waves falls back to `5 + 2 * index` enemies for its zero-based position in its pack, which is what keeps the three converted classic stages at 5, 7 and 9 without writing wave data the historical stage data does not contain. Variant mix, spawn cadence, and win timing stay campaign rules and are recorded in the product specification; nothing in this package interprets them. Per-wave gating — holding one wave until the previous is cleared — is not part of the current format and needs a proposal that says how a wave is sequenced.

## Classic tile IDs

Each grid row is a 16-character string. Each character encodes one tile:

- 0: empty
- 1: stone
- 2: brick
- 3: north-east mirror
- 4: south-east mirror
- 5: water
- 6: cracked brick
- 7: forest overlay
- 8: home base

Grid order is top-to-bottom, left-to-right. Spawns use integer coordinates with x as column and y as row. Validate bounds, row width/count, known tile IDs, exactly one home base unless a mode proposal says otherwise, and spawn placement.

## Opt-in gimmick terrain (content schema v2)

Classic schema v1 stays strict (`0`–`8`) and its three stages and hashes do not change. A separate level schema v2 adds `9`/`A`/`B`/`C` as north/east/south/west conveyor and `D` as a teleport pad. The pack's content schema and every level schema must agree. Each 16×16 v2 stage has either zero or exactly two `D` cells, paired in row-major order; validation rejects any other count before play, without fallback. New codes never overwrite classic meanings. Content spawn points remain empty-ground-only. A demo v2 pack is separate from the three-stage classic pack. Each level schema version ships as its own schema file; the version a document declares selects both the schema that checks it and the tile codes its rows may contain, and an unsupported declared version is refused by name rather than validated against rules it never claimed.

A tank whose center starts phase-5 movement in a conveyor cell receives one collision-checked 2px push after commanded motion; a tank whose center enters a pad cell after motion/push attempts one collision-checked jump to its partner preserving its top-left pixel offset from the source origin. Destination occupied or blocked leaves it at the entry; arrival does not chain, and standing still does not retrigger. Projectiles pass through both as empty ground; no damage, score, wave, life, victory or visibility rule changes. See `openspec/changes/gimmicks-v1/design.md` and `interaction-matrix.md` for the exact ordering and compatibility contract.

## Baseline interactions

Stone blocks tanks and projectiles and is indestructible. Brick blocks both and becomes cracked after one projectile hit. Cracked brick blocks both and becomes empty after another hit. Mirrors block tank movement and redirect projectiles using explicit direction tables. Water blocks tanks but allows projectiles to pass. Forest overlays the ground, permits movement, and may conceal entities according to explicit visibility rules. The home base is not traversable and is destroyed by a hostile projectile.

These rules are a source-material baseline. Specs and deterministic tests, not incidental legacy bugs, govern the rebuild. Any behavior change needs a proposal and interaction-matrix update.

## Packs and validation

Pack manifests declare pack ID, version, compatible content schema, levels, authorship, and licensing metadata. IDs remain stable across edits. Loading a pack is all-or-nothing: report file path and field for validation errors; never partially start a stage from malformed data.

Content validation runs headlessly in CI and editor tooling. Do not execute level data as code. Bundled classic levels are regression fixtures. New gimmicks must declare effects on tanks, projectiles, AI navigation, visibility, modes, and deterministic simulation.
