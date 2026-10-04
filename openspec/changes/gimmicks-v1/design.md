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

**Superseded in part by the October 4 recovery decision; see D7.** The owner's amendment on #12 authorises one repair to `tests/sim/test_purity.py` so the exact pytest command passes in its exact order. The prohibition it replaces is narrowed, not waived: the exact command is still run and still reported honestly, and no other out-of-scope fixture is touched. Everything else in D5 stands.

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
untouched and stays version-blind. *Every* client-side keyframe reader is a place the gate
belongs, not only the playing one — see the spectator seam under D7.

**Verification caveat (D5).** The six committed client captures under
`tests/client/screenshots/` were already stale against UI work that landed before this
change — regenerating them from a pristine `fd8355b` worktree produces the same six
differences — so they are left as they were and the staleness is a follow-up for whoever
owns that UI work. The three gimmick captures added here are fresh, and the classic six
are byte-identical between that baseline and this implementation.


## D7 — The October 4 recovery, as implemented

Recorded after merging `main` into this branch under the owner's PR #51 recovery
amendment. The amendment authorises exactly this work on exactly this issue; nothing here
widens the change's scope, and no gameplay, scoring, pacing or serialization decision the
accepted specs reserve is settled here.

**What the merge actually conflicted on.** Four files, all of them a both-sides addition
rather than a disagreement: `client/app.py` (a `pack` argument beside a new
`accessibility`/`gamepads` pair), `client/assets.py` (new tile art beside a palette
threaded through every draw), `server/__init__.py` (two export lists) and
`server/config.py` (two imports). Each resolution keeps both sides.

**Gimmick colour belongs to the palette (D3).** #14 made the client's palette a value the
asset cache is built from, so a contrast option is a real change of pixels. The merge
would have left the five gimmick tiles reading the module constants directly — the one
part of the board that silently ignored the setting. `Palette` gains `conveyor`,
`conveyor_rib`, `conveyor_arrow`, `pad`, `pad_ring` and `pad_link`, defaulting to the
module constants of the same name, so the default look is unchanged pixel for pixel; the
three committed gimmick captures are byte-identical across the merge, which is the
evidence. `_draw_conveyor` and `_draw_teleport_pad` take the palette, so `with_palette`
rebuilds them like everything else. The grayscale separation the accessibility
specification asks for is now asserted once per shipped palette rather than once in the
default one, so a palette added later has to answer for these tiles too.

**A watcher reads the same wire a player does (D4).** #9 added a client spectator view
that translated keyframe rows without the agreed content version, which would have drawn a
watcher terrain the session never agreed to. It now reads the version from the session
terms it was opened with, exactly as the playing session does, and a view handed a
keyframe before its terms refuses rather than guessing. This implements the existing
networking contract ("validate terrain rows in context of the agreed content version")
rather than changing it.

**An external manifest's levels are beside it (D1).** `--pack` resolved a manifest's level
paths against the *parent* of any directory named `packs`, which is the packaged layout
read off a directory name. An outside `/foo/packs/demo.json` declaring `levels/stage.json`
means `/foo/packs/levels`, the documented `load_pack` default, and was being rejected. The
root now follows from the manifest being inside the packaged content, so a bundled
manifest keeps the shared content root whether it is named by identifier or by absolute
path, and every other manifest keeps the loader's own default.

**A saved version is kept (D1).** An editor document that gained a gimmick wrote version 2
and went on believing it was version 1, so erasing the gimmick and saving again wrote
version 1 back — a silent downgrade away from the v2 pack manifest that declares it, and
the same two edits producing different files depending on whether the author had reopened
the file in between. A successful save now records the version it declared. A refused save
records nothing, because nothing was written.

**The exact acceptance command (D5).** `tests/sim/test_purity.py` measured the *test
session's* `sys.modules`, so any earlier test that imported pygame failed it — and in
every other order it passed vacuously, because a simulation that really did import pygame
would have looked exactly like a client test running first. It now imports the simulation
in a fresh interpreter, which measures the import and nothing else, with a case that
imports pygame into the parent (inside the test, never at module scope) to prove the
reading no longer depends on what ran before. The issue's exact pytest command passes in
its exact order: 1725 passed, 0 failed. This is the repair the amendment authorises and it
supersedes #22; the acceptance command itself is unchanged and unweakened.

**Still not refreshed.** Four of the six classic client captures remain stale against UI
work owned elsewhere, as D5 recorded; `main` refreshed two of them on its way in. They are
left alone because refreshing them is that work's business, not this change's.
