# Implementation roadmap

Specifications and repository contracts are bootstrapped. GitHub Issues contain bounded tasks and exact dependency links; Beads mirrors them as the executable ready graph.

| Phase | Deliverable | GitHub issue | Depends on |
| --- | --- | --- | --- |
| 1 | Fixed-tick deterministic simulation core and regression fixtures | [#1](https://github.com/duckycodess/battle-city-reimagined/issues/1) | — |
| 2 | Declarative content schema/loader; preserve three historical stages | [#2](https://github.com/duckycodess/battle-city-reimagined/issues/2) | 1 |
| 3 | Pygame-CE client shell, renderer, and input | [#3](https://github.com/duckycodess/battle-city-reimagined/issues/3) | 1, 2 |
| 4 | Versioned protocol and authoritative asyncio server over transport interface | [#4](https://github.com/duckycodess/battle-city-reimagined/issues/4) | 1, 2 |
| 5 | Seeded AI profiles that emit legal simulation inputs | [#5](https://github.com/duckycodess/battle-city-reimagined/issues/5) | 1 |
| 6 | Single-player campaign and extensible level packs | [#6](https://github.com/duckycodess/battle-city-reimagined/issues/6) | 2, 3, 5 |
| 7 | Multiplayer lobby, co-op, PvP, and team sessions | [#7](https://github.com/duckycodess/battle-city-reimagined/issues/7) | 3, 4, 5 |
| 8 | Measure latency; add prediction/interpolation/reconciliation only where needed | [#8](https://github.com/duckycodess/battle-city-reimagined/issues/8) | 7 |
| 9 | Versioned replay and spectator hooks | [#9](https://github.com/duckycodess/battle-city-reimagined/issues/9) | 4, 6, 7 |
| 10 | Headless content validation, editor, and level-pack tools | [#10](https://github.com/duckycodess/battle-city-reimagined/issues/10) | 2, 3 |
| 11 | Blender orthographic render-to-spritesheet pipeline | [#11](https://github.com/duckycodess/battle-city-reimagined/issues/11) | 3, 10 |
| 12 | Conveyor and paired-teleport stage gimmicks | [#12](https://github.com/duckycodess/battle-city-reimagined/issues/12) | 2, 5, 6 |
| 13 | Versioned saves/config, progression, and balance-safe cosmetics | [#13](https://github.com/duckycodess/battle-city-reimagined/issues/13) | 6, 7 |
| 14 | Accessibility, input remapping, and gameplay/UI polish | [#14](https://github.com/duckycodess/battle-city-reimagined/issues/14) | 3, 6, 13 |
| 15 | Packaging, release checks, and provenance/license audit | [#15](https://github.com/duckycodess/battle-city-reimagined/issues/15) | 6–14 |

Each issue lists exact allowed files and acceptance commands. Root dependency policy, CI, shared schemas, package APIs, and uv.lock stay with integration/bootstrap ownership. Tasks may proceed in parallel only where the Beads graph says contracts are independent.
