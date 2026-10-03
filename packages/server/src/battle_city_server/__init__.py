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

* :mod:`battle_city_server.lobby` is the phase before a run: seats, readiness, the
  agreed match settings and the moment of start. It mints the session's credentials and
  hands the connections it was holding to a game session, and it is as free of asyncio
  as the game session is.
* :mod:`battle_city_server.authority` is the seam the asyncio shell talks through, so
  neither phase is special to it.

Guarantees
----------
* A session's identity, stage, content, rules, seed and per-slot tokens are set before
  anyone connects. A lobby is how they get set when people arrange a match themselves;
  either way joining proves membership rather than creating it, and nothing a client
  sends can change the terms it was admitted under.
* No simulation advances before a match starts, and a membership token reaches one
  connection only — never the broadcast roster.
* Competitive modes are configurable, versioned and recorded, and this build refuses to
  start one: the shared simulation has a single player faction, no player-versus-player
  damage and no competitive result, so a server that started one would be reporting a
  match it was not running.
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
No prediction, no rollback, no reconnect, no host migration and no competitive rules.
The first two need measured need and acceptance tests per the networking specification;
reconnect and host migration are separate proposals, and until one lands a lost
connection is a lost player, a host that leaves ends its lobby and a lost server is a
lost session. Player-versus-player combat, scoring and results are simulation rules that
belong to an accepted gameplay proposal, not to anything this package could add.
"""

from .authority import SessionAuthority
from .clock import DEFAULT_MAX_CATCHUP_TICKS, ManualClock, RealTimeClock, TickClock
from .config import (
    DEFAULT_LIMITS,
    DEFAULT_TICK_RATE,
    PlayerCredential,
    ServerConfigurationError,
    SessionConfig,
    SessionLimits,
)
from .content import (
    UnspeakableTerrainError,
    content_ref_for,
    require_speakable_terrain,
    rules_digest,
    stage_from_level,
)
from .lobby import (
    OFFERED_MODES,
    PLAYABLE_MODES,
    Lobby,
    LobbyConfig,
    LobbyLevel,
    LobbyNotStartedError,
    LobbyTicket,
    MatchSession,
)
from .logs import FIELD_ORDER, LOGGER_NAME, content_label, log_event, session_logger
from .loopback import LoopbackStream, loopback_pair
from .server import DEFAULT_FLUSH_TIMEOUT, FATAL_FRAME_CODES, SessionServer
from .session import GameSession, Reply
from .tcp import TcpStream, serve_tcp
from .translation import (
    IllegalActionError,
    UntranslatableEventError,
    commands_for,
    grid_rows,
    protocol_event,
    protocol_events,
    snapshot_of,
)

__all__ = [
    "DEFAULT_FLUSH_TIMEOUT",
    "DEFAULT_LIMITS",
    "DEFAULT_MAX_CATCHUP_TICKS",
    "DEFAULT_TICK_RATE",
    "FATAL_FRAME_CODES",
    "FIELD_ORDER",
    "GameSession",
    "IllegalActionError",
    "LOGGER_NAME",
    "Lobby",
    "LobbyConfig",
    "LobbyLevel",
    "LobbyNotStartedError",
    "LobbyTicket",
    "LoopbackStream",
    "ManualClock",
    "MatchSession",
    "OFFERED_MODES",
    "PLAYABLE_MODES",
    "PlayerCredential",
    "RealTimeClock",
    "Reply",
    "ServerConfigurationError",
    "SessionAuthority",
    "SessionConfig",
    "SessionLimits",
    "SessionServer",
    "TcpStream",
    "TickClock",
    "UnspeakableTerrainError",
    "UntranslatableEventError",
    "commands_for",
    "content_label",
    "content_ref_for",
    "grid_rows",
    "log_event",
    "loopback_pair",
    "protocol_event",
    "protocol_events",
    "require_speakable_terrain",
    "rules_digest",
    "serve_tcp",
    "session_logger",
    "snapshot_of",
    "stage_from_level",
]
