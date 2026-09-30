# Implementation roadmap

Specifications and repository contracts are bootstrapped. GitHub Issues contain bounded tasks and exact dependency links; Beads mirrors them as the executable ready graph.

| Phase | Deliverable | Depends on |
| --- | --- | --- |
| 1 | Fixed-tick deterministic simulation core and regression fixtures | — |
| 2 | Declarative content schema/loader; preserve three historical stages | 1 |
| 3 | Pygame-CE client shell, renderer, and input | 1, 2 |
| 4 | Versioned protocol and authoritative asyncio server over transport interface | 1, 2 |
| 5 | Seeded AI profiles that emit legal simulation inputs | 1 |
| 6 | Single-player campaign and extensible level packs | 2, 3, 5 |
| 7 | Multiplayer lobby, co-op, PvP, and team sessions | 3, 4, 5 |
| 8 | Measure latency; add prediction/interpolation/reconciliation only where needed | 7 |
| 9 | Versioned replay and spectator hooks | 4, 6, 7 |
| 10 | Headless content validation, editor, and level-pack tools | 2 |
| 11 | Blender orthographic render-to-spritesheet pipeline | 3 |
| 12 | Approved stage gimmicks, modifiers, and expanded content | 2, 5, 6 |
| 13 | Versioned saves/config, progression, and balance-safe cosmetics | 6, 7 |
| 14 | Accessibility, input remapping, and gameplay/UI polish | 3, 6, 7, 13 |
| 15 | Packaging, release checks, and licensing decision | 6–14 |

Each issue lists exact allowed files and acceptance commands. Root dependency policy, CI, shared schemas, package APIs, and uv.lock stay with integration/bootstrap ownership. Tasks may proceed in parallel only where the Beads graph says contracts are independent.
