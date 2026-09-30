# Architecture specification

## Repository shape

Use a uv Python workspace with separately packaged members:

- packages/sim: deterministic headless rules and state.
- packages/content: versioned schemas, validators, loader, and level packs.
- packages/protocol: network message schemas and compatibility rules.
- packages/ai: bots that produce legal simulation inputs.
- packages/client: pygame-ce render, input, audio, menus, and presentation.
- packages/server: authoritative asyncio sessions and transport adapters.
- packages/tools: validation, pack inspection, and future editor commands.
- tests: cross-package contracts and deterministic regression fixtures.

Keep one root pyproject.toml policy and one uv.lock. Package manifests declare only package-specific dependencies. CI and integration/bootstrap issues own shared manifests, package membership, CI, and lockfile edits.

## Dependency direction

Allowed: AI → sim; client → sim, content, protocol; server → sim, content, protocol; tools → content, protocol. Content and protocol stay independent of client/server. Simulation depends on no project package. Tests may import all packages. No dependency cycles.

## Deterministic simulation boundary

The simulation is a pure state transition over immutable or explicitly owned state: step(state, tick_inputs, rules) -> next_state/events. It advances at a fixed integer tick rate. Randomness is seeded and uses a project-owned versioned algorithm. Simulation must not read clocks, render frames, sockets, files, environment, or unseeded randomness.

Stable IDs and explicit ordering govern entities and collision resolution. A canonical state encoding and hash support determinism checks, replay comparisons, server diagnostics, and future reconciliation. Changes to canonical serialization need a proposal and replay-compatibility policy.

## Client

Pygame-CE handles windows, input devices, rendering, audio, menus, and visual interpolation. It translates local controls into tick-indexed intents and renders simulation snapshots. It cannot own authoritative rules or silently modify server-owned results.

## Server

The Python asyncio server runs headless, authenticates session membership, validates bounded client intents, orders them into simulation ticks, runs the shared simulation, and sends authoritative snapshots/events. A server session owns mode rules, content version, RNG seed, player identity, and connection state.

## Transport seam

Game logic depends on a transport interface, never on sockets or a concrete protocol connection. Initial transport can be reliable TCP for lobby, session-control, and early gameplay messages. Keep message semantics transport-neutral so a later real-time channel can be introduced behind the interface. No prediction or UDP work is required for the first authoritative playable slice.

## Shared contract ownership

Root package manifests, uv.lock, CI, shared schemas, package public APIs, and protocol version policy have named integration/bootstrap owners. Feature issues list package-local allowed files. Cross-boundary changes require explicit dependencies and review; do not let parallel tasks edit the same root contract.

## Observability and failure

Logs include session ID, tick, protocol version, content hash, and reason codes without logging secrets. Invalid client input is rejected without partially applying a tick. Server restart, stale session, malformed save, and incompatible content/version behavior must be specified by the affected change.
