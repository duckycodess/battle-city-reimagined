# gimmicks-v1 design decisions

Accepted for implementation under #12 after independent plan review. The proposal's R1–R7 and interaction-matrix.md are normative; existing contracts remain normative when not changed here.

## D1 — Explicit terrain encoding (R1, R6)

Append sim Tile values 9–13, but never derive row characters from `str(tile.value)` after 9. Provide one explicit, bijective Tile ↔ char mapping in sim/tiles.py (0–8 remain decimal, 9/A/B/C/D map to new members); use it in TileGrid.from_rows/to_rows and server/translation.grid_rows. Content TileCode uses exactly those code characters; editor/render.TILE_ART maps codes explicitly, not `Tile(int(code.value))`. Update pinned enumeration tests. V1 rows/schema remain strict; a dedicated v2 level JSON Schema accepts `[0-9A-D]` and schema_version 2. Loader checks pad count in addition to JSON Schema; Stage.create checks independently for direct construction. No pad count validation in renderer. Keep names short enough for existing tool output.

Do not extend the bundled classic manifest or its three levels. Add a separate v2 demo pack and level in content package (do not name the level `classic-*`, as fixture globs pin exactly three classic stages), and exercise explicit load_pack + stage_catalog(pack) + ClientShell(catalog=...) for gameplay screenshots. If a bounded external pack launch option is feasible, expose it; otherwise the default shipped menu remains classic-only and report that limitation openly. Never modify tests/campaign or tests/persistence to accommodate this.

## D2 — Sim phase 5 and events (R2)

Record each tank's center cell at phase-5 entry, after spawn/respawn; iterate all tanks by ascending ID even without commands. Attempt ordinary command (if any), one push iff the recorded center cell is an arrow, then one transport iff final center entered a pad from another starting center cell. Use the established full-body collision checks for all attempts and current ID-ordered occupancy. Pad arrival offset is the tank's top-left position minus the source pad origin; negative offsets while straddling are valid and not clamped. Arrival never chains; failed arrival remains at entry. Command and push can net 4px per tick. Successful push/arrival emits existing TankMoved with actual final position and unchanged tank facing; blocked push/arrival emits existing TankMoveBlocked without inventing new event kinds. Retain existing combat/projectile phase order and event IDs; document phase-5 suborder in step.py. Content spawns are empty-only; direct sim spawn contract stays as-is.

## D3 — AI and visual semantics (R3, R5)

Gate prediction of push/arrival on gimmick terrain so v1 decisions remain byte-identical. Current AI offers bounded roam and collision prediction, not full global A*: test its legal choices, clearance/predicted pose on arrows/pads and blocked exits; no perfect knowledge or randomized nondeterminism. All new tiles remain visible and transparent to projectiles. Draw five distinct static luminance silhouettes in the existing procedural client asset library; the four arrows must differ by direction and the link pad must differ from every classic tile in grayscale, with no animation required. No Blender/catalog/atlas change because the current client does not load that atlas. The editor selects new tiles via clickable palette, without letter hotkey conflicts, and supports v2 save/load and pair error feedback.

## D4 — Transport and compatibility (R4, R6)

Keep existing PROTOCOL_VERSION=2, SNAPSHOT_VERSION=1 and CANONICAL_STATE_VERSION=1. `ContentRef.content_schema_version` is the sole version negotiation signal; enforce content mismatch at session/lobby start, and check snapshot/keyframe rows against the agreed version at server session/client remote translation where ContentRef is known (protocol messages._require_grid remains version-blind and bounds printable row data; v1 alphabet must still accept every classic row). V1 wire encoding, message type codes and canonical state bytes/hashes are unchanged. Server sim calculates all outcomes, grid_rows uses D1 mapping, client remote translation does not infer movement. Existing match/session metadata carries ContentRef; validate replayability via deterministic run_ticks/golden vectors, not a new replay persistence subsystem.

## D5 — Rejected alternatives, risks and verification (R7)

Do not require whole-body tile containment, add cooldown state, clamp pad offsets, invent pair IDs, teleport projectiles, add global AI path planner, bump wire/snapshot/canonical versions or alter campaign/co-op/competitive rules. Adjacent pads may trap a repeatedly crossing tank; blocked exits and conveyor congestion affect balance; explicitly state these limitations in PR. Run dependency sync, locked pytest (original issue ordering, recording known #22 import-order baseline failure if present), comparable reordered pytest with sim last, locked mypy, and make ci. No edits to tests/sim/test_purity.py or out-of-scope fixtures to hide pre-existing failures. Capture real gameplay and editor screenshots and require independent exact-pushed-head verdict before ready_to_merge.

## D6 — Decisions the design left open, as implemented

Recorded after implementation so the accepted change and the shipped behaviour agree.

**Row alphabet (D1).** `Tile` gains `CONVEYOR_N/E/S/W` and `TELEPORT_PAD` as values 9-13,
and `TILE_CODE_CHARS` maps every tile to its one row character. `CONVEYOR_E` is value 10,
so `str(tile.value)` would have written two columns; `TileGrid.to_rows` and the server's
`grid_rows` go through the table instead. The editor's `TILE_ART` joins the two
vocabularies on the character rather than on `Tile(int(code.value))`, which would raise on
four of the five new codes.

**Schema file name (D1).** `classic-level.v2.schema.json`, the name the version 1 schema's
own description already promised a later grid format would take. The pack manifest format
is unchanged: a v2 pack is an ordinary manifest whose `content_schema_version` is 2.

**Player reachability (D1).** A bounded launch option was feasible, so it exists: the
client's `--pack` takes a pack manifest path or a bundled pack identifier, and
`resolve_pack`/`pack_stage_catalog` are the same path from the catalog API. The default
menu is still the three classic stages. The sample is `packs/gimmick-demo.json`, loaded by
`load_gimmick_demo_pack` or `--pack gimmick-demo`.

**Edge pushes (D2).** A displacement that moves nothing is a refusal, matching a commanded
step clamped at the world edge, so a belt running into the edge emits `TankMoveBlocked`
rather than a `TankMoved` to where the tank already stands.

**AI clearance (D3).** `clearance_ticks` counts ticks that make progress *along* the asked
direction instead of ticks that move the tank at all. A tank whose step is refused by a
wall while a belt shoves it the other way has moved, and counting that as clearance would
commit a plan to a direction the bot is being carried away from. The two readings are the
same measurement on a grid without gimmick terrain, where the commanded step is the only
displacement there is, which is what keeps classic decisions byte-identical.

**Editor keys (D3).** `9` takes the free digit. `A` to `D` are click-only: `B` and `E` are
already the paint and enemy-spawn tools. The palette moved to five narrower columns so
fourteen swatches fit beside the readings without changing the editor frame size, and the
panel gained a pad count and the schema version a save would declare.

**Version gate placement (D4).** The server checks once, while a session is described,
because no tick can turn classic terrain into gimmick terrain; the client checks once per
keyframe, where the agreed `ContentRef` is known. `protocol.messages._require_grid` is
untouched and stays version-blind.

**Verification caveat (D5).** The six committed client captures under
`tests/client/screenshots/` were already stale against UI work that landed before this
change — regenerating them from a pristine `fd8355b` worktree produces the same six
differences — so they are left as they were and the staleness is a follow-up for whoever
owns that UI work. The three gimmick captures added here are fresh, and the classic six
are byte-identical between that baseline and this implementation.
