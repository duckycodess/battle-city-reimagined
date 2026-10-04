# Battle City Reimagined

An open-ended rebuild of the author's early Python Battle-City project as a deterministic, data-driven tank game with single-player, bots, and authoritative multiplayer.

This repository starts a new implementation. Historical source remains at [duckycodess/Battle-City](https://github.com/duckycodess/Battle-City) unchanged. The first content pack preserves its three 16×16 stages as declarative data; no Pyxel runtime or monolithic game code is carried forward.

## Project status

Specifications and the initial implementation roadmap are in place. Runtime systems are not implemented yet. Roadmap issues live in GitHub Issues and form a Beads dependency graph. Creating an issue does not authorize work; only the exact agent:ready label does.

## Gameplay foundation

- Defend a home base while clearing enemy tanks from compact grid-based stages.
- Preserve classic tile behavior: empty ground, indestructible stone, two-hit brick, cracked brick, two projectile-reflecting mirrors, tank-blocking water, forest cover overlay, and destructible base.
- Preserve player and enemy tanks, enemy behavior, score, lives, three classic stages, gatling, invincibility, extra-life powerups, and the hesoyam, pewpews, and juancho cheats.
- Grow into long campaigns, authored level packs, local bots, co-op, PvP and team modes, modifiers, replays, spectators, and an editor.
- Keep competitive cosmetics visual-only. Support remappable controls, readable effects, and safe versioned saves and settings.

## Architecture

The Python workspace separates deterministic simulation, content, AI, protocol, client, server, and tools. Pygame-CE owns presentation, input, and audio. A headless fixed-tick simulation is shared by client, server, bots, and tests. An asyncio server owns multiplayer truth. Network transport stays behind an interface; initial lobby and control traffic can use reliable TCP.

Specifications under openspec/specs define current product, architecture, network, content, AI, asset, persistence, and accessibility contracts. Changes that alter those contracts need an OpenSpec-style proposal before implementation.

## Development

Python 3.14 or newer. Install uv, then run uv sync --all-packages --all-groups. Use make ci for formatting, lint, type checking, and tests.

Package boundaries, issue workflow, dependency ownership, and acceptance rules live in AGENTS.md and CONTRIBUTING.md. Blender-rendered spritesheets are the planned art source; generated concept images are references only.

## Distributions

`uv build --all-packages` produces a wheel and a source distribution for each of the seven workspace packages. Installing them gives four commands — `battle-city-client`, `battle-city-editor`, `battle-city-tools`, and `battle-city-assets` — plus the server as an importable library, and the schemas, classic stages, and pack manifests as package data. The starter sprite atlas stays in the source tree: no package owns it and the client renders procedurally.

Nothing is published. No package index, no tag, no release; the only supported route is building from a checkout, and no index entry under any `battle-city-*` name belongs to this project. **The project has no license yet**, so nobody may rely on permission to redistribute it, and the bundled stage data asserts no license over layouts converted from the unlicensed historical repository.

[docs/release](docs/release/) carries setup and upgrade instructions, the provenance, dependency, and license audit, and the release checks the CI release job runs on every pull request.

## Roadmap

See [open implementation issues](https://github.com/duckycodess/battle-city-reimagined/issues). Work proceeds from deterministic simulation and level data toward client/server play, content tools, art, accessibility, and release.
