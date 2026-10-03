"""Turning messages into frames and untrusted frames back into messages.

The wire format is strict JSON: readable in a capture, cheap to reason about, and
already hardened by :mod:`battle_city_protocol.jsonio` against the documents that are
JSON-shaped but not JSON. Nothing here reflects on a class, looks up a type by name or
unpickles anything. A decoder reads named fields, checks each one, and calls a frozen
dataclass constructor that checks them again; there is no path from a frame to an
arbitrary object.

Three rules make decoding total rather than best-effort:

* **Unknown fields are refused.** Ignoring an unexpected field would let one build
  silently disagree with another about what a message meant.
* **Direction is enforced.** :func:`decode_client_message` refuses a server message and
  :func:`decode_server_message` refuses a client message, so a peer cannot answer an
  input with something a server would have trusted.
* **The version is checked first.** An incompatible peer gets
  :data:`~battle_city_protocol.codes.RejectionCode.PROTOCOL_VERSION_UNSUPPORTED`, not a
  field-by-field guess at what its message might have meant.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .events import EVENT_FIELDS, EventKind, GameEvent
from .jsonio import JsonValue, decode_json_object, encode_json_object
from .limits import (
    MAX_ACTIONS_PER_BATCH,
    MAX_EVENT_VALUES,
    MAX_EVENTS_PER_MESSAGE,
    MAX_GRID_DIMENSION,
    MAX_LEVELS_PER_LOBBY,
    MAX_LOBBY_MEMBERS,
    MAX_MODES_PER_LOBBY,
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_POWERUPS_PER_SNAPSHOT,
    MAX_PROJECTILES_PER_SNAPSHOT,
    MAX_TANKS_PER_SNAPSHOT,
    PROTOCOL_VERSION,
)
from .messages import (
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
    LobbyConfigure,
    LobbyInfo,
    LobbyJoin,
    LobbyLeave,
    LobbyMember,
    LobbyReady,
    LobbyStart,
    LobbyState,
    LobbyWelcome,
    MatchMode,
    MatchSettings,
    MatchStarting,
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
    TeamAssignment,
    TickEvents,
)
from .validation import require_member

_TYPE_FIELD: Final[str] = "type"


def message_type_of(message: Message) -> MessageType:
    """Return the discriminator for ``message``."""
    match message:
        case JoinRequest():
            return MessageType.JOIN_REQUEST
        case InputBatch():
            return MessageType.INPUT_BATCH
        case JoinAccepted():
            return MessageType.JOIN_ACCEPTED
        case InputAccepted():
            return MessageType.INPUT_ACCEPTED
        case StateSnapshot():
            return MessageType.SNAPSHOT
        case TickEvents():
            return MessageType.TICK_EVENTS
        case Rejected():
            return MessageType.REJECTED
        case SessionClosed():
            return MessageType.SESSION_CLOSED
        case LobbyJoin():
            return MessageType.LOBBY_JOIN
        case LobbyConfigure():
            return MessageType.LOBBY_CONFIGURE
        case LobbyReady():
            return MessageType.LOBBY_READY
        case LobbyStart():
            return MessageType.LOBBY_START
        case LobbyLeave():
            return MessageType.LOBBY_LEAVE
        case LobbyWelcome():
            return MessageType.LOBBY_WELCOME
        case LobbyState():
            return MessageType.LOBBY_STATE
        case MatchStarting():
            return MessageType.MATCH_STARTING


def encode_message(message: Message) -> bytes:
    """Return the frame payload for ``message``.

    Encoding is deterministic: keys are sorted and separators fixed, so one message is
    always one byte string and a broadcast can encode once and send many times.
    """
    document: dict[str, JsonValue] = {_TYPE_FIELD: message_type_of(message).value}
    document.update(_body(message))
    return encode_json_object(document)


def decode_client_message(payload: bytes) -> ClientMessage:
    """Decode a frame a client sent. Refuses anything only a server may send."""
    document = decode_json_object(payload)
    kind = _message_type(document)
    if kind not in CLIENT_MESSAGE_TYPES:
        raise MessageError(
            RejectionCode.UNEXPECTED_MESSAGE, f"{kind.value} is not a client message"
        )
    reader = _CLIENT_READERS[kind]
    return reader(document)


def decode_server_message(payload: bytes) -> ServerMessage:
    """Decode a frame a server sent. Refuses anything only a client may send."""
    document = decode_json_object(payload)
    kind = _message_type(document)
    if kind not in SERVER_MESSAGE_TYPES:
        raise MessageError(
            RejectionCode.UNEXPECTED_MESSAGE, f"{kind.value} is not a server message"
        )
    reader = _SERVER_READERS[kind]
    return reader(document)


def _message_type(document: Mapping[str, JsonValue]) -> MessageType:
    if _TYPE_FIELD not in document:
        raise MessageError(RejectionCode.UNKNOWN_MESSAGE_TYPE, "message has no type")
    raw = document[_TYPE_FIELD]
    if not isinstance(raw, str):
        raise MessageError(RejectionCode.UNKNOWN_MESSAGE_TYPE, "message type must be a string")
    try:
        return MessageType(raw)
    except ValueError as error:
        raise MessageError(
            RejectionCode.UNKNOWN_MESSAGE_TYPE, "message type is not one this build knows"
        ) from error


class _Fields:
    """A checked view over one JSON object: no unknown keys, no untyped reads."""

    __slots__ = ("_document", "_label")

    def __init__(self, label: str, document: Mapping[str, JsonValue], names: Sequence[str]) -> None:
        self._label = label
        self._document = document
        allowed = set(names)
        allowed.add(_TYPE_FIELD)
        for key in document:
            if key not in allowed:
                raise MessageError(
                    RejectionCode.UNKNOWN_FIELD, f"{self._name(key)} is not a field of {label}"
                )

    def _name(self, key: str) -> str:
        return key if self._label == "message" else f"{self._label}.{key}"

    def raw(self, key: str) -> JsonValue:
        if key not in self._document:
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} is missing")
        return self._document[key]

    def optional(self, key: str) -> JsonValue:
        return self._document.get(key)

    def sequence(self, key: str, limit: int) -> list[JsonValue]:
        value = self.raw(key)
        if not isinstance(value, list):
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} must be an array")
        if len(value) > limit:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"{self._name(key)} carries {len(value)} entries, over the limit of {limit}",
            )
        return value

    def optional_sequence(self, key: str, limit: int) -> list[JsonValue] | None:
        if self._document.get(key) is None:
            return None
        return self.sequence(key, limit)

    def text(self, key: str) -> str:
        value = self.raw(key)
        if not isinstance(value, str):
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} must be a string")
        return value

    def integer(self, key: str) -> int:
        value = self.raw(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} must be an integer")
        return value

    def optional_integer(self, key: str) -> int | None:
        value = self._document.get(key)
        if value is None:
            return None
        return self.integer(key)

    def flag(self, key: str) -> bool:
        value = self.raw(key)
        if not isinstance(value, bool):
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} must be a boolean")
        return value

    def child(self, key: str, names: Sequence[str]) -> _Fields:
        value = self.raw(key)
        if not isinstance(value, dict):
            raise MessageError(RejectionCode.INVALID_FIELD, f"{self._name(key)} must be an object")
        return _Fields(self._name(key), value, names)


def _entry(label: str, value: JsonValue, names: Sequence[str]) -> _Fields:
    if not isinstance(value, dict):
        raise MessageError(RejectionCode.INVALID_FIELD, f"{label} entry must be an object")
    return _Fields(label, value, names)


def _require_version(fields: _Fields) -> int:
    version = fields.integer("protocol_version")
    if version != PROTOCOL_VERSION:
        raise MessageError(
            RejectionCode.PROTOCOL_VERSION_UNSUPPORTED,
            f"this build speaks protocol version {PROTOCOL_VERSION}, peer sent {version}",
        )
    return version


_CONTENT_FIELDS: Final[tuple[str, ...]] = (
    "pack_id",
    "pack_version",
    "level_id",
    "content_schema_version",
)
_SESSION_FIELDS: Final[tuple[str, ...]] = (
    "tick_rate",
    "keyframe_interval",
    "max_players",
    "content",
    "rules_digest",
    "state_version",
    "snapshot_version",
)
_ACTION_FIELDS: Final[tuple[str, ...]] = ("kind", "direction")
_TANK_FIELDS: Final[tuple[str, ...]] = (
    "entity_id",
    "variant",
    "x",
    "y",
    "facing",
    "slot",
    "gatling_ticks",
    "invincible_ticks",
)
_PROJECTILE_FIELDS: Final[tuple[str, ...]] = (
    "entity_id",
    "owner_id",
    "faction",
    "x",
    "y",
    "direction",
)
_POWERUP_FIELDS: Final[tuple[str, ...]] = ("entity_id", "kind", "cell_x", "cell_y")
_PLAYER_FIELDS: Final[tuple[str, ...]] = ("slot", "lives", "tank_id", "spawn_x", "spawn_y")
_BASE_FIELDS: Final[tuple[str, ...]] = ("cell_x", "cell_y", "destroyed")
_EVENT_FIELDS: Final[tuple[str, ...]] = ("kind", "values")


def _content_body(content: ContentRef) -> dict[str, JsonValue]:
    return {
        "pack_id": content.pack_id,
        "pack_version": content.pack_version,
        "level_id": content.level_id,
        "content_schema_version": content.content_schema_version,
    }


def _read_content(fields: _Fields) -> ContentRef:
    child = fields.child("content", _CONTENT_FIELDS)
    return ContentRef(
        pack_id=child.text("pack_id"),
        pack_version=child.text("pack_version"),
        level_id=child.text("level_id"),
        content_schema_version=child.integer("content_schema_version"),
    )


def _session_body(session: SessionInfo) -> dict[str, JsonValue]:
    return {
        "tick_rate": session.tick_rate,
        "keyframe_interval": session.keyframe_interval,
        "max_players": session.max_players,
        "content": _content_body(session.content),
        "rules_digest": session.rules_digest,
        "state_version": session.state_version,
        "snapshot_version": session.snapshot_version,
    }


def _read_session(fields: _Fields) -> SessionInfo:
    child = fields.child("session", _SESSION_FIELDS)
    return SessionInfo(
        tick_rate=child.integer("tick_rate"),
        keyframe_interval=child.integer("keyframe_interval"),
        max_players=child.integer("max_players"),
        content=_read_content(child),
        rules_digest=child.text("rules_digest"),
        state_version=child.integer("state_version"),
        snapshot_version=child.integer("snapshot_version"),
    )


def _action_body(action: PlayerAction) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {"kind": action.kind.value}
    if action.direction is not None:
        body["direction"] = action.direction.value
    return body


def _read_action(value: JsonValue) -> PlayerAction:
    fields = _entry("actions", value, _ACTION_FIELDS)
    raw_direction = fields.optional("direction")
    direction = (
        None
        if raw_direction is None
        else require_member("actions.direction", raw_direction, DirectionCode)
    )
    return PlayerAction(
        kind=require_member("actions.kind", fields.raw("kind"), ActionKind),
        direction=direction,
    )


def _tank_body(tank: TankSnapshot) -> dict[str, JsonValue]:
    return {
        "entity_id": tank.entity_id,
        "variant": tank.variant,
        "x": tank.x,
        "y": tank.y,
        "facing": tank.facing,
        "slot": tank.slot,
        "gatling_ticks": tank.gatling_ticks,
        "invincible_ticks": tank.invincible_ticks,
    }


def _read_tank(value: JsonValue) -> TankSnapshot:
    fields = _entry("tanks", value, _TANK_FIELDS)
    return TankSnapshot(
        entity_id=fields.integer("entity_id"),
        variant=fields.integer("variant"),
        x=fields.integer("x"),
        y=fields.integer("y"),
        facing=fields.integer("facing"),
        slot=fields.optional_integer("slot"),
        gatling_ticks=fields.integer("gatling_ticks"),
        invincible_ticks=fields.integer("invincible_ticks"),
    )


def _projectile_body(projectile: ProjectileSnapshot) -> dict[str, JsonValue]:
    return {
        "entity_id": projectile.entity_id,
        "owner_id": projectile.owner_id,
        "faction": projectile.faction,
        "x": projectile.x,
        "y": projectile.y,
        "direction": projectile.direction,
    }


def _read_projectile(value: JsonValue) -> ProjectileSnapshot:
    fields = _entry("projectiles", value, _PROJECTILE_FIELDS)
    return ProjectileSnapshot(
        entity_id=fields.integer("entity_id"),
        owner_id=fields.integer("owner_id"),
        faction=fields.integer("faction"),
        x=fields.integer("x"),
        y=fields.integer("y"),
        direction=fields.integer("direction"),
    )


def _powerup_body(powerup: PowerupSnapshot) -> dict[str, JsonValue]:
    return {
        "entity_id": powerup.entity_id,
        "kind": powerup.kind,
        "cell_x": powerup.cell_x,
        "cell_y": powerup.cell_y,
    }


def _read_powerup(value: JsonValue) -> PowerupSnapshot:
    fields = _entry("powerups", value, _POWERUP_FIELDS)
    return PowerupSnapshot(
        entity_id=fields.integer("entity_id"),
        kind=fields.integer("kind"),
        cell_x=fields.integer("cell_x"),
        cell_y=fields.integer("cell_y"),
    )


def _player_body(player: PlayerSnapshot) -> dict[str, JsonValue]:
    return {
        "slot": player.slot,
        "lives": player.lives,
        "tank_id": player.tank_id,
        "spawn_x": player.spawn_x,
        "spawn_y": player.spawn_y,
    }


def _read_player(value: JsonValue) -> PlayerSnapshot:
    fields = _entry("players", value, _PLAYER_FIELDS)
    return PlayerSnapshot(
        slot=fields.integer("slot"),
        lives=fields.integer("lives"),
        tank_id=fields.optional_integer("tank_id"),
        spawn_x=fields.integer("spawn_x"),
        spawn_y=fields.integer("spawn_y"),
    )


def _base_body(base: BaseSnapshot) -> dict[str, JsonValue]:
    return {"cell_x": base.cell_x, "cell_y": base.cell_y, "destroyed": base.destroyed}


def _read_base(fields: _Fields) -> BaseSnapshot:
    child = fields.child("base", _BASE_FIELDS)
    return BaseSnapshot(
        cell_x=child.integer("cell_x"),
        cell_y=child.integer("cell_y"),
        destroyed=child.flag("destroyed"),
    )


def _event_body(event: GameEvent) -> dict[str, JsonValue]:
    return {"kind": event.kind.value, "values": list(event.values)}


def _read_event(value: JsonValue) -> GameEvent:
    fields = _entry("events", value, _EVENT_FIELDS)
    kind = require_member("events.kind", fields.raw("kind"), EventKind)
    raw_values = fields.sequence("values", MAX_EVENT_VALUES)
    values: list[int] = []
    for index, item in enumerate(raw_values):
        if isinstance(item, bool) or not isinstance(item, int):
            raise MessageError(
                RejectionCode.INVALID_FIELD, f"events.values[{index}] must be an integer"
            )
        values.append(item)
    if len(values) != len(EVENT_FIELDS[kind]):
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"event {kind.value} takes {len(EVENT_FIELDS[kind])} values, got {len(values)}",
        )
    return GameEvent(kind=kind, values=tuple(values))


def _read_grid(fields: _Fields) -> tuple[str, ...] | None:
    rows = fields.optional_sequence("grid", MAX_GRID_DIMENSION)
    if rows is None:
        return None
    parsed: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, str):
            raise MessageError(RejectionCode.INVALID_FIELD, f"grid.rows[{index}] must be a string")
        parsed.append(row)
    return tuple(parsed)


_SETTINGS_FIELDS: Final[tuple[str, ...]] = (
    "mode",
    "level_id",
    "content",
    "tick_rate",
    "max_players",
    "cheats_enabled",
    "settings_version",
)
_LOBBY_INFO_FIELDS: Final[tuple[str, ...]] = (
    "capacity",
    "offered_modes",
    "playable_modes",
    "offered_levels",
    "settings_version",
)
_MEMBER_FIELDS: Final[tuple[str, ...]] = (
    "slot",
    "display_name",
    "ready",
    "host",
    "connected",
    "team",
)
_TEAM_FIELDS: Final[tuple[str, ...]] = ("slot", "team")


def _settings_body(settings: MatchSettings) -> dict[str, JsonValue]:
    return {
        "mode": settings.mode.value,
        "level_id": settings.level_id,
        "content": _content_body(settings.content),
        "tick_rate": settings.tick_rate,
        "max_players": settings.max_players,
        "cheats_enabled": settings.cheats_enabled,
        "settings_version": settings.settings_version,
    }


def _read_settings(fields: _Fields) -> MatchSettings:
    child = fields.child("settings", _SETTINGS_FIELDS)
    return MatchSettings(
        mode=require_member("settings.mode", child.raw("mode"), MatchMode),
        level_id=child.text("level_id"),
        content=_read_content(child),
        tick_rate=child.integer("tick_rate"),
        max_players=child.integer("max_players"),
        cheats_enabled=child.flag("cheats_enabled"),
        settings_version=child.integer("settings_version"),
    )


def _read_modes(fields: _Fields, key: str) -> tuple[MatchMode, ...]:
    return tuple(
        require_member(f"lobby.{key}", value, MatchMode)
        for value in fields.sequence(key, MAX_MODES_PER_LOBBY)
    )


def _lobby_info_body(lobby: LobbyInfo) -> dict[str, JsonValue]:
    return {
        "capacity": lobby.capacity,
        "offered_modes": [mode.value for mode in lobby.offered_modes],
        "playable_modes": [mode.value for mode in lobby.playable_modes],
        "offered_levels": list(lobby.offered_levels),
        "settings_version": lobby.settings_version,
    }


def _read_lobby_info(fields: _Fields) -> LobbyInfo:
    child = fields.child("lobby", _LOBBY_INFO_FIELDS)
    levels: list[str] = []
    for index, value in enumerate(child.sequence("offered_levels", MAX_LEVELS_PER_LOBBY)):
        if not isinstance(value, str):
            raise MessageError(
                RejectionCode.INVALID_FIELD, f"lobby.offered_levels[{index}] must be a string"
            )
        levels.append(value)
    return LobbyInfo(
        capacity=child.integer("capacity"),
        offered_modes=_read_modes(child, "offered_modes"),
        playable_modes=_read_modes(child, "playable_modes"),
        offered_levels=tuple(levels),
        settings_version=child.integer("settings_version"),
    )


def _member_body(member: LobbyMember) -> dict[str, JsonValue]:
    return {
        "slot": member.slot,
        "display_name": member.display_name,
        "ready": member.ready,
        "host": member.host,
        "connected": member.connected,
        "team": member.team,
    }


def _read_member(value: JsonValue) -> LobbyMember:
    fields = _entry("members", value, _MEMBER_FIELDS)
    return LobbyMember(
        slot=fields.integer("slot"),
        display_name=fields.text("display_name"),
        ready=fields.flag("ready"),
        host=fields.flag("host"),
        connected=fields.flag("connected"),
        team=fields.optional_integer("team"),
    )


def _team_body(assignment: TeamAssignment) -> dict[str, JsonValue]:
    return {"slot": assignment.slot, "team": assignment.team}


def _read_team(value: JsonValue) -> TeamAssignment:
    fields = _entry("teams", value, _TEAM_FIELDS)
    return TeamAssignment(slot=fields.integer("slot"), team=fields.integer("team"))


_JOIN_REQUEST_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "token",
    "content",
)
_INPUT_BATCH_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "sequence",
    "target_tick",
    "actions",
)
_JOIN_ACCEPTED_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "tick",
    "session",
)
_INPUT_ACCEPTED_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "sequence",
    "tick",
)
_SNAPSHOT_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "tick",
    "tick_rate",
    "state_version",
    "snapshot_version",
    "keyframe",
    "grid",
    "tanks",
    "projectiles",
    "powerups",
    "players",
    "base",
    "outcome",
    "state_hash",
)
_TICK_EVENTS_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "tick",
    "events",
)
_REJECTED_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "code",
    "detail",
    "sequence",
)
_SESSION_CLOSED_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "code",
    "detail",
)


_LOBBY_JOIN_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "ticket",
    "display_name",
    "content",
    "team",
)
_LOBBY_CONFIGURE_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "revision",
    "mode",
    "level_id",
    "teams",
)
_LOBBY_READY_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "revision",
    "ready",
)
_LOBBY_START_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "revision",
)
_LOBBY_LEAVE_FIELDS: Final[tuple[str, ...]] = ("protocol_version", "session_id", "slot")
_LOBBY_WELCOME_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "host",
    "lobby",
)
_LOBBY_STATE_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "revision",
    "settings",
    "members",
    "host_slot",
    "startable",
    "blocked",
)
_MATCH_STARTING_FIELDS: Final[tuple[str, ...]] = (
    "protocol_version",
    "session_id",
    "slot",
    "token",
    "session",
    "settings",
)


def _read_lobby_join(document: Mapping[str, JsonValue]) -> ClientMessage:
    fields = _Fields("message", document, _LOBBY_JOIN_FIELDS)
    return LobbyJoin(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        ticket=fields.text("ticket"),
        display_name=fields.text("display_name"),
        content=_read_content(fields),
        team=fields.optional_integer("team"),
    )


def _read_lobby_configure(document: Mapping[str, JsonValue]) -> ClientMessage:
    fields = _Fields("message", document, _LOBBY_CONFIGURE_FIELDS)
    version = _require_version(fields)
    teams = fields.sequence("teams", MAX_LOBBY_MEMBERS)
    return LobbyConfigure(
        protocol_version=version,
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        revision=fields.integer("revision"),
        mode=require_member("mode", fields.raw("mode"), MatchMode),
        level_id=fields.text("level_id"),
        teams=tuple(_read_team(item) for item in teams),
    )


def _read_lobby_ready(document: Mapping[str, JsonValue]) -> ClientMessage:
    fields = _Fields("message", document, _LOBBY_READY_FIELDS)
    return LobbyReady(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        revision=fields.integer("revision"),
        ready=fields.flag("ready"),
    )


def _read_lobby_start(document: Mapping[str, JsonValue]) -> ClientMessage:
    fields = _Fields("message", document, _LOBBY_START_FIELDS)
    return LobbyStart(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        revision=fields.integer("revision"),
    )


def _read_lobby_leave(document: Mapping[str, JsonValue]) -> ClientMessage:
    fields = _Fields("message", document, _LOBBY_LEAVE_FIELDS)
    return LobbyLeave(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
    )


def _read_lobby_welcome(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _LOBBY_WELCOME_FIELDS)
    return LobbyWelcome(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        host=fields.flag("host"),
        lobby=_read_lobby_info(fields),
    )


def _read_lobby_state(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _LOBBY_STATE_FIELDS)
    version = _require_version(fields)
    members = fields.sequence("members", MAX_LOBBY_MEMBERS)
    raw_blocked = fields.optional("blocked")
    return LobbyState(
        protocol_version=version,
        session_id=fields.text("session_id"),
        revision=fields.integer("revision"),
        settings=_read_settings(fields),
        members=tuple(_read_member(item) for item in members),
        host_slot=fields.integer("host_slot"),
        startable=fields.flag("startable"),
        blocked=(
            None if raw_blocked is None else require_member("blocked", raw_blocked, RejectionCode)
        ),
    )


def _read_match_starting(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _MATCH_STARTING_FIELDS)
    return MatchStarting(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        token=fields.text("token"),
        session=_read_session(fields),
        settings=_read_settings(fields),
    )


def _read_join_request(document: Mapping[str, JsonValue]) -> JoinRequest:
    fields = _Fields("message", document, _JOIN_REQUEST_FIELDS)
    return JoinRequest(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        token=fields.text("token"),
        content=_read_content(fields),
    )


def _read_input_batch(document: Mapping[str, JsonValue]) -> InputBatch:
    fields = _Fields("message", document, _INPUT_BATCH_FIELDS)
    version = _require_version(fields)
    actions = fields.sequence("actions", MAX_ACTIONS_PER_BATCH)
    return InputBatch(
        protocol_version=version,
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        sequence=fields.integer("sequence"),
        target_tick=fields.optional_integer("target_tick"),
        actions=tuple(_read_action(action) for action in actions),
    )


def _read_join_accepted(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _JOIN_ACCEPTED_FIELDS)
    return JoinAccepted(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        tick=fields.integer("tick"),
        session=_read_session(fields),
    )


def _read_input_accepted(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _INPUT_ACCEPTED_FIELDS)
    return InputAccepted(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        slot=fields.integer("slot"),
        sequence=fields.integer("sequence"),
        tick=fields.integer("tick"),
    )


def _read_snapshot(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _SNAPSHOT_FIELDS)
    return StateSnapshot(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        tick=fields.integer("tick"),
        tick_rate=fields.integer("tick_rate"),
        state_version=fields.integer("state_version"),
        snapshot_version=fields.integer("snapshot_version"),
        keyframe=fields.flag("keyframe"),
        grid=_read_grid(fields),
        tanks=tuple(_read_tank(item) for item in fields.sequence("tanks", MAX_TANKS_PER_SNAPSHOT)),
        projectiles=tuple(
            _read_projectile(item)
            for item in fields.sequence("projectiles", MAX_PROJECTILES_PER_SNAPSHOT)
        ),
        powerups=tuple(
            _read_powerup(item) for item in fields.sequence("powerups", MAX_POWERUPS_PER_SNAPSHOT)
        ),
        players=tuple(
            _read_player(item) for item in fields.sequence("players", MAX_PLAYERS_PER_SNAPSHOT)
        ),
        base=_read_base(fields),
        outcome=fields.optional_integer("outcome"),
        state_hash=fields.text("state_hash"),
    )


def _read_tick_events(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _TICK_EVENTS_FIELDS)
    return TickEvents(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        tick=fields.integer("tick"),
        events=tuple(
            _read_event(item) for item in fields.sequence("events", MAX_EVENTS_PER_MESSAGE)
        ),
    )


def _read_rejected(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _REJECTED_FIELDS)
    return Rejected(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        code=require_member("code", fields.raw("code"), RejectionCode),
        detail=fields.text("detail"),
        sequence=fields.optional_integer("sequence"),
    )


def _read_session_closed(document: Mapping[str, JsonValue]) -> ServerMessage:
    fields = _Fields("message", document, _SESSION_CLOSED_FIELDS)
    return SessionClosed(
        protocol_version=_require_version(fields),
        session_id=fields.text("session_id"),
        code=require_member("code", fields.raw("code"), RejectionCode),
        detail=fields.text("detail"),
    )


_CLIENT_READERS: Final[dict[MessageType, Callable[[Mapping[str, JsonValue]], ClientMessage]]] = {
    MessageType.JOIN_REQUEST: _read_join_request,
    MessageType.INPUT_BATCH: _read_input_batch,
    MessageType.LOBBY_JOIN: _read_lobby_join,
    MessageType.LOBBY_CONFIGURE: _read_lobby_configure,
    MessageType.LOBBY_READY: _read_lobby_ready,
    MessageType.LOBBY_START: _read_lobby_start,
    MessageType.LOBBY_LEAVE: _read_lobby_leave,
}

_SERVER_READERS: Final[dict[MessageType, Callable[[Mapping[str, JsonValue]], ServerMessage]]] = {
    MessageType.JOIN_ACCEPTED: _read_join_accepted,
    MessageType.INPUT_ACCEPTED: _read_input_accepted,
    MessageType.SNAPSHOT: _read_snapshot,
    MessageType.TICK_EVENTS: _read_tick_events,
    MessageType.REJECTED: _read_rejected,
    MessageType.SESSION_CLOSED: _read_session_closed,
    MessageType.LOBBY_WELCOME: _read_lobby_welcome,
    MessageType.LOBBY_STATE: _read_lobby_state,
    MessageType.MATCH_STARTING: _read_match_starting,
}


def _body(message: Message) -> dict[str, JsonValue]:
    match message:
        case JoinRequest():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "token": message.token,
                "content": _content_body(message.content),
            }
        case InputBatch():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "sequence": message.sequence,
                "target_tick": message.target_tick,
                "actions": [_action_body(action) for action in message.actions],
            }
        case JoinAccepted():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "tick": message.tick,
                "session": _session_body(message.session),
            }
        case InputAccepted():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "sequence": message.sequence,
                "tick": message.tick,
            }
        case StateSnapshot():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "tick": message.tick,
                "tick_rate": message.tick_rate,
                "state_version": message.state_version,
                "snapshot_version": message.snapshot_version,
                "keyframe": message.keyframe,
                "grid": None if message.grid is None else list(message.grid),
                "tanks": [_tank_body(tank) for tank in message.tanks],
                "projectiles": [_projectile_body(item) for item in message.projectiles],
                "powerups": [_powerup_body(item) for item in message.powerups],
                "players": [_player_body(item) for item in message.players],
                "base": _base_body(message.base),
                "outcome": message.outcome,
                "state_hash": message.state_hash,
            }
        case TickEvents():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "tick": message.tick,
                "events": [_event_body(event) for event in message.events],
            }
        case Rejected():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "code": message.code.value,
                "detail": message.detail,
                "sequence": message.sequence,
            }
        case SessionClosed():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "code": message.code.value,
                "detail": message.detail,
            }
        case LobbyJoin():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "ticket": message.ticket,
                "display_name": message.display_name,
                "content": _content_body(message.content),
                "team": message.team,
            }
        case LobbyConfigure():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "revision": message.revision,
                "mode": message.mode.value,
                "level_id": message.level_id,
                "teams": [_team_body(assignment) for assignment in message.teams],
            }
        case LobbyReady():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "revision": message.revision,
                "ready": message.ready,
            }
        case LobbyStart():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "revision": message.revision,
            }
        case LobbyLeave():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
            }
        case LobbyWelcome():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "host": message.host,
                "lobby": _lobby_info_body(message.lobby),
            }
        case LobbyState():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "revision": message.revision,
                "settings": _settings_body(message.settings),
                "members": [_member_body(member) for member in message.members],
                "host_slot": message.host_slot,
                "startable": message.startable,
                "blocked": None if message.blocked is None else message.blocked.value,
            }
        case MatchStarting():
            return {
                "protocol_version": message.protocol_version,
                "session_id": message.session_id,
                "slot": message.slot,
                "token": message.token,
                "session": _session_body(message.session),
                "settings": _settings_body(message.settings),
            }
