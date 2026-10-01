"""Headless asyncio authoritative server.

``battle_city_server`` runs the shared simulation and is the only thing that decides
what happened. Clients submit bounded intents for their own slot; the server
authenticates membership, validates every message, normalises arrival order into fixed
tick order, steps the rules engine, and broadcasts authoritative snapshots and events.

Layers
------
* :mod:`battle_city_server.session` is the authority: membership, sequencing, input
  queues and the tick. It has no sockets, no clock and no asyncio, so the rules about
  who may say what can be tested one tick at a time.
* :mod:`battle_city_server.server` is the asyncio shell: per-connection outbound queues,
  frame-level failure handling and connection lifetime.
* :mod:`battle_city_server.tcp` and :mod:`battle_city_server.loopback` are transport
  adapters behind the protocol's byte-stream interface. Loopback is not a test double:
  it is how a same-process client plays without a socket.
* :mod:`battle_city_server.content` and :mod:`battle_city_server.translation` are the
  only places the content, protocol and simulation vocabularies meet.

Guarantees
----------
* A session's identity, stage, content, rules, seed and per-slot tokens are set before
  anyone connects. Joining proves membership; it cannot create or change it.
* A client can only ever move, fire or respawn its own tank. Score, damage, seed,
  terrain and state deltas have no field to arrive in.
* An invalid message is refused with a stable code and no partial tick mutation, and a
  tick either applies a client's whole batch or none of it.
* A bad or impossible intent cannot stall the session: the tick still advances, empty if
  need be, and the client is told.
* Tick cadence is injected, so a session advanced by hand in a test and the same session
  advanced in real time reach the same canonical state hash.

Not here
--------
No lobby, no prediction, no rollback, no reconnect and no host migration. The first
three need measured need and acceptance tests per the networking specification; the last
two are separate proposals, and until one lands a lost connection is a lost player and a
lost server is a lost session.
"""

from .clock import ManualClock, RealTimeClock, TickClock
from .config import (
    DEFAULT_LIMITS,
    DEFAULT_TICK_RATE,
    PlayerCredential,
    ServerConfigurationError,
    SessionConfig,
    SessionLimits,
)
from .content import content_ref_for, rules_digest, stage_from_level
from .loopback import LoopbackStream, loopback_pair
from .server import DEFAULT_FLUSH_TIMEOUT, FATAL_FRAME_CODES, SessionServer
from .session import GameSession, Reply
from .tcp import TcpStream, serve_tcp
from .translation import (
    IllegalActionError,
    commands_for,
    grid_rows,
    protocol_event,
    protocol_events,
    snapshot_of,
)

__all__ = [
    "DEFAULT_FLUSH_TIMEOUT",
    "DEFAULT_LIMITS",
    "DEFAULT_TICK_RATE",
    "FATAL_FRAME_CODES",
    "GameSession",
    "IllegalActionError",
    "LoopbackStream",
    "ManualClock",
    "PlayerCredential",
    "RealTimeClock",
    "Reply",
    "ServerConfigurationError",
    "SessionConfig",
    "SessionLimits",
    "SessionServer",
    "TcpStream",
    "TickClock",
    "commands_for",
    "content_ref_for",
    "grid_rows",
    "loopback_pair",
    "protocol_event",
    "protocol_events",
    "rules_digest",
    "serve_tcp",
    "snapshot_of",
    "stage_from_level",
]
