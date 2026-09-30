# Content and level specification

## Data format

Level packs are declarative JSON, not Python modules. Validate against a checked-in JSON Schema before loading. Each level declares schema version, stable ID, display name, grid dimensions, tile data, player and enemy spawn points, wave or encounter data, and optional mode/modifier metadata.

The initial grid remains 16 columns by 16 rows. Level format may version into other dimensions later, but the first migration must preserve the original coordinate system and all three layouts.

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

## Baseline interactions

Stone blocks tanks and projectiles and is indestructible. Brick blocks both and becomes cracked after one projectile hit. Cracked brick blocks both and becomes empty after another hit. Mirrors block tank movement and redirect projectiles using explicit direction tables. Water blocks tanks but allows projectiles to pass. Forest overlays the ground, permits movement, and may conceal entities according to explicit visibility rules. The home base is not traversable and is destroyed by a hostile projectile.

These rules are a source-material baseline. Specs and deterministic tests, not incidental legacy bugs, govern the rebuild. Any behavior change needs a proposal and interaction-matrix update.

## Packs and validation

Pack manifests declare pack ID, version, compatible content schema, levels, authorship, and licensing metadata. IDs remain stable across edits. Loading a pack is all-or-nothing: report file path and field for validation errors; never partially start a stage from malformed data.

Content validation runs headlessly in CI and editor tooling. Do not execute level data as code. Bundled classic levels are regression fixtures. New gimmicks must declare effects on tanks, projectiles, AI navigation, visibility, modes, and deterministic simulation.
