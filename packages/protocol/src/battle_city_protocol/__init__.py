"""Versioned, transport-neutral session messages.

``battle_city_protocol`` owns the wire: what a client may say, what a server may say,
what every field may contain, and what a refusal is called. It is standard library only
and depends on no other project package — not on the simulation and not on content — so
the message model cannot quietly acquire a rules dependency and a tool can speak the
protocol without loading a game.

Contract
--------
* Every message carries the protocol version, its type and its session identifier, and
  every field has a published bound in :mod:`battle_city_protocol.limits`.
* A message validates itself on construction, so an instance that exists is an instance
  that passed every bound, whether it came from a peer or from local code.
* Decoding is total and strict: unknown types, unknown fields, wrong-direction messages,
  duplicate JSON keys, ``NaN``/``Infinity``, non-UTF-8 bytes, oversized frames and
  over-deep nesting are all refusals, never best-effort parses. Nothing deserialises into
  an executable object.
* Every refusal carries a stable :class:`~battle_city_protocol.codes.RejectionCode`. The
  values are wire constants: they are matched on, logged and asserted, so they do not
  change meaning.
* Transport sits behind :class:`~battle_city_protocol.transport.MessageChannel`. Sockets
  live in the server package; nothing here opens one.

What a client cannot say
------------------------
The client vocabulary is :class:`~battle_city_protocol.messages.ActionKind`: move, fire,
respawn, for the sender's own slot. There is no message and no field through which a
client can place an entity, set a score, choose a seed, damage a tile, or submit a state
delta. The networking specification's "never accept client-selected score, damage, RNG
seed or authoritative stage state" is enforced here by the absence of a field rather
than by a check somewhere that could be skipped.

Versioning
----------
:data:`~battle_city_protocol.limits.PROTOCOL_VERSION` covers the message set and their
fields. :data:`~battle_city_protocol.limits.SNAPSHOT_VERSION` covers the snapshot layout
alone, and a snapshot separately carries the simulation's canonical state version and a
digest of the rule constants the session runs. The three change for different reasons and
an incompatible peer is told which one it failed, so it can report an actionable upgrade
message instead of guessing.
"""

from .codec import (
    decode_client_message,
    decode_server_message,
    encode_message,
    message_type_of,
)
from .codes import RejectionCode
from .errors import MessageError, ProtocolError
from .events import EVENT_FIELDS, EventKind, GameEvent
from .framing import FRAME_HEADER_BYTES, decode_frame_length, encode_frame
from .jsonio import JsonValue, decode_json_object, encode_json_object
from .limits import (
    MAX_ACTIONS_PER_BATCH,
    MAX_EVENTS_PER_MESSAGE,
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_SEQUENCE,
    MAX_SLOT,
    MAX_TICK,
    MAX_TICK_RATE,
    PROTOCOL_VERSION,
    SNAPSHOT_VERSION,
)
from .messages import (
    ALLOWED_ACTION_KINDS,
    CLIENT_MESSAGE_TYPES,
    SERVER_MESSAGE_TYPES,
    ActionKind,
    BaseSnapshot,
    ClientMessage,
    ContentRef,
    DirectionCode,
    InputAccepted,
    InputBatch,
    JoinAccepted,
    JoinRequest,
    Message,
    MessageType,
    PlayerAction,
    PlayerSnapshot,
    PowerupSnapshot,
    ProjectileSnapshot,
    Rejected,
    ServerMessage,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TankSnapshot,
    TickEvents,
)
from .transport import (
    ByteStream,
    ClientChannel,
    FramedChannel,
    MessageChannel,
    ServerChannel,
    client_channel,
    server_channel,
)

__all__ = [
    "ALLOWED_ACTION_KINDS",
    "CLIENT_MESSAGE_TYPES",
    "EVENT_FIELDS",
    "FRAME_HEADER_BYTES",
    "MAX_ACTIONS_PER_BATCH",
    "MAX_EVENTS_PER_MESSAGE",
    "MAX_FRAME_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_PLAYERS_PER_SNAPSHOT",
    "MAX_SEQUENCE",
    "MAX_SLOT",
    "MAX_TICK",
    "MAX_TICK_RATE",
    "PROTOCOL_VERSION",
    "SERVER_MESSAGE_TYPES",
    "SNAPSHOT_VERSION",
    "ActionKind",
    "BaseSnapshot",
    "ByteStream",
    "ClientChannel",
    "ClientMessage",
    "ContentRef",
    "DirectionCode",
    "EventKind",
    "FramedChannel",
    "GameEvent",
    "InputAccepted",
    "InputBatch",
    "JoinAccepted",
    "JoinRequest",
    "JsonValue",
    "Message",
    "MessageChannel",
    "MessageError",
    "MessageType",
    "PlayerAction",
    "PlayerSnapshot",
    "PowerupSnapshot",
    "ProjectileSnapshot",
    "ProtocolError",
    "Rejected",
    "RejectionCode",
    "ServerChannel",
    "ServerMessage",
    "SessionClosed",
    "SessionInfo",
    "StateSnapshot",
    "TankSnapshot",
    "TickEvents",
    "client_channel",
    "decode_client_message",
    "decode_frame_length",
    "decode_json_object",
    "decode_server_message",
    "encode_frame",
    "encode_json_object",
    "encode_message",
    "message_type_of",
    "server_channel",
]
