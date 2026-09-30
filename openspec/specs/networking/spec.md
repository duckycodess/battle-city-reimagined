# Networking specification

## Authority

The server is authoritative for session membership, game mode, tick ordering, movement, collisions, projectile outcomes, terrain damage, score, lives, powerups, wave spawning, base destruction, and match results. Clients submit bounded player intents; they never submit trusted state deltas.

## Initial transport

Hide transport behind a protocol-owned interface. The first implementation may use asyncio TCP for reliable lobby and control messages and an initial playable session. Keep transport selection out of the simulation and message model. A future real-time transport may add loss-tolerant snapshots without changing game rules.

## Versioned messages

Every message includes protocol version, message type, session identifier, and bounded payload. Input messages include player identity, monotonically increasing sequence, intended tick or acknowledgement context, and allowed action fields. Server output includes authoritative tick, state/event version, and content/rules identifiers.

Validate message size, shape, enum values, rates, sequence, player ownership, session membership, and content/version compatibility before mutation. Reject invalid data with stable reason codes and no partial state change. Never deserialize executable objects.

## Session behavior

Plan for lobby creation/join, ready state, mode selection, stage/content agreement, start, disconnect, reconnect, session end, and result recording. Reconnection and host migration are separate proposals; the first release may end a session when its authoritative server is lost.

## Consistency and performance

The server advances a fixed-rate simulation independent of socket arrival timing. Arrival order is normalized into explicit tick input order. Clients may render interpolation between authoritative snapshots. Prediction, rollback, and reconciliation require measured need, protocol versioning, and acceptance tests before adoption.

## Security and abuse

Treat every client as untrusted. Bound payloads, rates, lobby names, queues, entity counts, and content references. Prevent one client from issuing actions for another. Never accept client-selected score, damage, RNG seed after session start, or authoritative stage state. Keep credentials out of logs and saved replays.

## Compatibility

Protocol messages and content/rules versions are explicit. Incompatible clients fail with actionable upgrade messages rather than attempting unsafe best-effort parsing. Replay metadata records protocol and simulation/content versions.
