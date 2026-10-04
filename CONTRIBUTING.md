# Contributing

## Start with a bounded issue

Use a GitHub issue with one outcome, a narrow file allowlist, explicit dependencies, inputs, acceptance commands, screenshot requirements when visual, and remaining risks. An open issue is not permission to implement it. Only exact agent:ready authorizes dispatcher work.

Do not add agent:ready from an automated worker. The project owner controls this authorization label. Follow Beads blockers even when an issue is labeled ready.

## Specifications

Current requirements live in openspec/specs/. For changes that add or alter a product, architecture, network, content, AI, asset, persistence, or accessibility contract, first create openspec/changes/<change-name>/proposal.md, design.md, and tasks.md. After acceptance, update affected current specs in the same change.

Keep proposals concise: problem, intended behavior, compatibility, security or balance impact, test plan, rollout, and unresolved decisions. Do not use proposal files to justify unrelated refactors.

## Code and content

- Keep simulation rules portable and deterministic; wall time, pygame, disk, and sockets stay outside packages/sim.
- Keep level and asset metadata declarative. Validate before runtime use and produce useful path-and-field errors.
- Do not use one-off generated AI sprites in the shipped build. Blender scene renders and deterministic sheet packing define final art.
- Competitive cosmetics cannot change gameplay, visibility, hitboxes, or simulation state.

## Pull requests

Use a focused PR with a clear summary, linked issue, test results, screenshots for visual changes, and known risks. Preserve dependency order. Never manually merge or bypass dispatcher merge checks.

## Local commands

Install Python 3.14+ and uv. Run uv sync --all-packages --all-groups, then make ci. Shared dependency changes must be coordinated through the integration/bootstrap issue that owns uv.lock.

## Packaging

Changing what a distribution contains means changing package build metadata, and a workspace cannot notice a packaging mistake on its own: every package is installed as an editable checkout, so a file a wheel forgot is still on the import path. Run uv build --all-packages and the release job's checks before trusting such a change. docs/release/README.md describes them and the release job in .github/workflows/quality.yml is the authoritative copy.

Adding a console script, a data file, or a package means updating the expectations written into that job; it asserts an exact set rather than a minimum, so an unannounced addition fails it. Keep the four installed commands and the two deliberate omissions — the server has no CLI, and no distribution carries the sprite atlas — consistent with docs/release/.
