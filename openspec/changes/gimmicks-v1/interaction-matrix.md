# gimmicks-v1 interaction matrix

| Subject | Conveyor `9/A/B/C` | Paired pad `D` | Existing rules / order |
|---|---|---|---|
| Tank motion | Recorded start-center cell activates one 2px arrow push after commanded move; idle included; no entry-tick activation or chaining; facing unchanged | Final center entered from different start cell transfers once at matching offset; no chain, no standing repeat | Ascending ID; each displacement checks full body, world bounds, blocking tiles and current tank bodies; command, push, transport, then next tank |
| Blocked / occupied | Cancel only attempted push | Failed arrival remains on entry; no retry until new entry; earlier ID gets contested exit | No overlap, forced swap, tunneling or partial displacement |
| Spawn / respawn | Existing spawn restrictions apply | Existing spawn restrictions apply; no arrival triggered on spawn | No new spawn rule |
| Projectiles | Transparent; never steered | Transparent; never transported | Mirror/brick/base/projectile hit ordering unchanged |
| Combat / scoring / powerups | No direct effect | No direct effect | Damage, lives, score, win and collection rules unchanged |
| Visibility / art | Never conceals; fixed compass arrow showing orientation | Never conceals; fixed distinct two-ended link/paired-pad shape | Forest remains sole terrain concealment; monochrome and reduced-motion cues |
| AI | Predict start-cell push and blocked resolution | Predict legal arrival only, occupancy checked | Legal tick intents, bounded seeded planning and existing visibility |
| Network | Server applies push; snapshots carry v2 terrain codes and version | Server applies transfer; snapshots carry authoritative resulting positions | Preserve v1 wire path; explicit v2/version mismatch rejection; clients never select outcomes |
| Replay/hash | New appended tile bytes in unchanged canonical layout | Pair determined by grid row-major, no mutable flags | V1 states/input byte/hash unchanged; content/rules version in session/replay metadata |
| Invalid content | V1 rejects codes; v2 validates all directions | V2 requires zero or exactly two pads | Pack/level version agreement and strict rows; reject before stage start, no fallback |

No multi-pair IDs, cooldowns, projectile teleports, stage balance changes or competitive mode toggles. The v2 row alphabet, editor authoring, server translation, client cues and real screenshots are part of #12 under the owner's amended scope.
