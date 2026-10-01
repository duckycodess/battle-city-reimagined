"""The message contracts.

Every message is a frozen record that validates itself on construction, so an instance
that exists is an instance that satisfied every published bound. Each one carries the
protocol version, its type, and the session identifier it belongs to, as the networking
specification requires, and nothing carries an unbounded field.

Direction of travel is part of the type. :data:`ClientMessage` is everything a client
may send and :data:`ServerMessage` is everything a server may send; a decoder is asked
for one or the other, so a server can never be talked into treating a snapshot it was
handed as authoritative input.

What a client may *not* say is the load-bearing half of this module. The action
allowlist is :class:`ActionKind`: move, fire, respawn. There is no spawn action, no
score, no damage, no seed, no tile, no entity placement and no state delta anywhere in
:data:`ClientMessage`. A client states an intent for its own slot and the server decides
what that means, so the "never accept client-selected score, damage, RNG seed or
authoritative stage state" rule is enforced by the absence of a field rather than by a
check that could be forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .codes import RejectionCode
from .errors import MessageError
from .events import GameEvent
from .limits import (
    MAX_ACTIONS_PER_BATCH,
    MAX_COORDINATE,
    MAX_ENTITY_ID,
    MAX_ENUM_CODE,
    MAX_EVENTS_PER_MESSAGE,
    MAX_GRID_DIMENSION,
    MAX_KEYFRAME_INTERVAL,
    MAX_LIVES,
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_POWERUPS_PER_SNAPSHOT,
    MAX_PROJECTILES_PER_SNAPSHOT,
    MAX_SEQUENCE,
    MAX_SLOT,
    MAX_TANKS_PER_SNAPSHOT,
    MAX_TICK,
    MAX_TICK_RATE,
    PROTOCOL_VERSION,
    SNAPSHOT_VERSION,
)
from .validation import (
    require_bool,
    require_detail,
    require_digest,
    require_identifier,
    require_int,
    require_optional_int,
    require_token,
)


class MessageType(StrEnum):
    """The ``type`` discriminator. Member values are wire constants and never change."""

    JOIN_REQUEST = "join_request"
    INPUT_BATCH = "input_batch"
    JOIN_ACCEPTED = "join_accepted"
    INPUT_ACCEPTED = "input_accepted"
    SNAPSHOT = "snapshot"
    TICK_EVENTS = "tick_events"
    REJECTED = "rejected"
    SESSION_CLOSED = "session_closed"


class ActionKind(StrEnum):
    """The complete allowlist of things a client may ask its own tank to do."""

    MOVE = "move"
    FIRE = "fire"
    RESPAWN = "respawn"


ALLOWED_ACTION_KINDS: Final[frozenset[ActionKind]] = frozenset(ActionKind)
"""Every action a client may submit.

Spawning an enemy or a powerup, awarding score, changing the seed and editing terrain
are simulation commands a campaign or the server issues. They are absent from
:class:`ActionKind` on purpose and a test asserts that they stay absent.
"""


class DirectionCode(StrEnum):
    """A facing. Named rather than numbered so a frame is readable in a capture."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerAction:
    """One intent for the sending client's own tank."""

    kind: ActionKind
    direction: DirectionCode | None = None

    def __post_init__(self) -> None:
        if self.kind is ActionKind.MOVE and self.direction is None:
            raise MessageError(RejectionCode.INVALID_FIELD, "actions.move needs a direction")
        if self.kind is not ActionKind.MOVE and self.direction is not None:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"actions.{self.kind.value} must not carry a direction",
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentRef:
    """Exactly which content a peer is running.

    Compatibility is an equality check on this record, so a client that loaded a
    different pack, a different level or a different schema version is refused at join
    with :data:`~battle_city_protocol.codes.RejectionCode.CONTENT_MISMATCH` rather than
    desynchronising three ticks later.
    """

    pack_id: str
    pack_version: str
    level_id: str
    content_schema_version: int

    def __post_init__(self) -> None:
        require_identifier("content.pack_id", self.pack_id)
        require_identifier("content.pack_version", self.pack_version)
        require_identifier("content.level_id", self.level_id)
        require_int(
            "content.content_schema_version",
            self.content_schema_version,
            minimum=1,
            maximum=MAX_ENUM_CODE,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionInfo:
    """Everything a joining client needs to agree with the server about the run.

    ``rules_digest`` fingerprints the rule constants the session steps with, and
    ``state_version`` is the simulation's canonical state version. Both are opaque
    strings and integers here: this package never imports the simulation.
    """

    tick_rate: int
    keyframe_interval: int
    max_players: int
    content: ContentRef
    rules_digest: str
    state_version: int
    snapshot_version: int = SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        require_int("session.tick_rate", self.tick_rate, minimum=1, maximum=MAX_TICK_RATE)
        require_int(
            "session.keyframe_interval",
            self.keyframe_interval,
            minimum=1,
            maximum=MAX_KEYFRAME_INTERVAL,
        )
        require_int(
            "session.max_players", self.max_players, minimum=1, maximum=MAX_PLAYERS_PER_SNAPSHOT
        )
        require_digest("session.rules_digest", self.rules_digest)
        require_int("session.state_version", self.state_version, minimum=1, maximum=MAX_ENUM_CODE)
        _require_snapshot_version("session.snapshot_version", self.snapshot_version)


@dataclass(frozen=True, slots=True, kw_only=True)
class TankSnapshot:
    """A tank as the server sees it. ``variant`` and ``facing`` are canonical codes."""

    entity_id: int
    variant: int
    x: int
    y: int
    facing: int
    slot: int | None
    gatling_ticks: int
    invincible_ticks: int

    def __post_init__(self) -> None:
        _require_entity_id("tanks.entity_id", self.entity_id)
        _require_code("tanks.variant", self.variant)
        _require_coordinate("tanks.x", self.x)
        _require_coordinate("tanks.y", self.y)
        _require_code("tanks.facing", self.facing)
        require_optional_int("tanks.slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("tanks.gatling_ticks", self.gatling_ticks, minimum=0, maximum=MAX_TICK)
        require_int("tanks.invincible_ticks", self.invincible_ticks, minimum=0, maximum=MAX_TICK)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectileSnapshot:
    """A projectile as the server sees it."""

    entity_id: int
    owner_id: int
    faction: int
    x: int
    y: int
    direction: int

    def __post_init__(self) -> None:
        _require_entity_id("projectiles.entity_id", self.entity_id)
        _require_entity_id("projectiles.owner_id", self.owner_id)
        _require_code("projectiles.faction", self.faction)
        _require_coordinate("projectiles.x", self.x)
        _require_coordinate("projectiles.y", self.y)
        _require_code("projectiles.direction", self.direction)


@dataclass(frozen=True, slots=True, kw_only=True)
class PowerupSnapshot:
    """An uncollected powerup as the server sees it."""

    entity_id: int
    kind: int
    cell_x: int
    cell_y: int

    def __post_init__(self) -> None:
        _require_entity_id("powerups.entity_id", self.entity_id)
        _require_code("powerups.kind", self.kind)
        _require_coordinate("powerups.cell_x", self.cell_x)
        _require_coordinate("powerups.cell_y", self.cell_y)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerSnapshot:
    """Per-slot campaign state. ``tank_id`` is absent while the slot awaits a respawn."""

    slot: int
    lives: int
    tank_id: int | None
    spawn_x: int
    spawn_y: int

    def __post_init__(self) -> None:
        require_int("players.slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("players.lives", self.lives, minimum=0, maximum=MAX_LIVES)
        require_optional_int("players.tank_id", self.tank_id, minimum=1, maximum=MAX_ENTITY_ID)
        _require_coordinate("players.spawn_x", self.spawn_x)
        _require_coordinate("players.spawn_y", self.spawn_y)


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseSnapshot:
    """The home base."""

    cell_x: int
    cell_y: int
    destroyed: bool

    def __post_init__(self) -> None:
        _require_coordinate("base.cell_x", self.cell_x)
        _require_coordinate("base.cell_y", self.cell_y)
        require_bool("base.destroyed", self.destroyed)


@dataclass(frozen=True, slots=True, kw_only=True)
class JoinRequest:
    """A client asking to take a preconfigured slot with the token it was issued.

    There is no lobby here. Sessions, slots and tokens are arranged out of band, so
    joining proves membership rather than creating it.
    """

    session_id: str
    slot: int
    token: str
    content: ContentRef
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_token("token", self.token)


@dataclass(frozen=True, slots=True, kw_only=True)
class InputBatch:
    """One client's intents for one tick.

    ``sequence`` rises strictly per slot; gaps are allowed because a dropped batch is
    not worth resending, but a repeat or a rewind is refused. ``target_tick`` names the
    tick the batch is for; omitting it means the next tick that has not started, which
    is what a client that is simply keeping up wants.

    At most one action per kind: moving and firing on the same tick is legal and normal,
    while two moves in one tick would make the result depend on the order the actions
    happened to be listed in.
    """

    session_id: str
    slot: int
    sequence: int
    actions: tuple[PlayerAction, ...]
    target_tick: int | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("sequence", self.sequence, minimum=0, maximum=MAX_SEQUENCE)
        require_optional_int("target_tick", self.target_tick, minimum=0, maximum=MAX_TICK)
        if len(self.actions) > MAX_ACTIONS_PER_BATCH:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"actions carries {len(self.actions)} entries, over the limit of "
                f"{MAX_ACTIONS_PER_BATCH}",
            )
        seen: set[ActionKind] = set()
        for action in self.actions:
            if action.kind in seen:
                raise MessageError(
                    RejectionCode.DUPLICATE_ACTION,
                    f"actions repeats {action.kind.value}; one of each kind per tick",
                )
            seen.add(action.kind)


@dataclass(frozen=True, slots=True, kw_only=True)
class JoinAccepted:
    """Membership granted, with the tick the client is joining at and the session terms."""

    session_id: str
    slot: int
    tick: int
    session: SessionInfo
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("tick", self.tick, minimum=0, maximum=MAX_TICK)

    @property
    def tick_rate(self) -> int:
        """The session's fixed tick rate, as carried in :attr:`session`."""
        return self.session.tick_rate


@dataclass(frozen=True, slots=True, kw_only=True)
class InputAccepted:
    """A batch was queued, and for which tick.

    This is the acknowledgement context the networking specification asks for: it tells
    a client which sequence landed and which tick it will be applied on, without
    promising anything about what the simulation will make of it.
    """

    session_id: str
    slot: int
    sequence: int
    tick: int
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("sequence", self.sequence, minimum=0, maximum=MAX_SEQUENCE)
        require_int("tick", self.tick, minimum=0, maximum=MAX_TICK)


@dataclass(frozen=True, slots=True, kw_only=True)
class StateSnapshot:
    """The authoritative state after a tick.

    Terrain is sent on a keyframe only: it is static apart from damage, which arrives as
    a :data:`~battle_city_protocol.events.EventKind.TILE_DAMAGED` event, and a client
    that misses one gets the whole grid back on the next keyframe. Everything else is a
    full listing every tick, which is affordable because entity counts are bounded and
    which means a client never has to reconstruct authoritative state from a delta it
    may have missed.

    ``state_hash`` is the simulation's canonical hash. Two peers that disagree about it
    disagree about the run, which is a fact worth having on the wire.
    """

    session_id: str
    tick: int
    tick_rate: int
    state_version: int
    keyframe: bool
    tanks: tuple[TankSnapshot, ...]
    projectiles: tuple[ProjectileSnapshot, ...]
    powerups: tuple[PowerupSnapshot, ...]
    players: tuple[PlayerSnapshot, ...]
    base: BaseSnapshot
    state_hash: str
    grid: tuple[str, ...] | None = None
    outcome: int | None = None
    snapshot_version: int = SNAPSHOT_VERSION
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("tick", self.tick, minimum=0, maximum=MAX_TICK)
        require_int("tick_rate", self.tick_rate, minimum=1, maximum=MAX_TICK_RATE)
        require_int("state_version", self.state_version, minimum=1, maximum=MAX_ENUM_CODE)
        _require_snapshot_version("snapshot_version", self.snapshot_version)
        require_bool("keyframe", self.keyframe)
        _require_count("tanks", len(self.tanks), MAX_TANKS_PER_SNAPSHOT)
        _require_count("projectiles", len(self.projectiles), MAX_PROJECTILES_PER_SNAPSHOT)
        _require_count("powerups", len(self.powerups), MAX_POWERUPS_PER_SNAPSHOT)
        _require_count("players", len(self.players), MAX_PLAYERS_PER_SNAPSHOT)
        require_optional_int("outcome", self.outcome, minimum=0, maximum=MAX_ENUM_CODE)
        require_digest("state_hash", self.state_hash)
        if self.keyframe and self.grid is None:
            raise MessageError(RejectionCode.INVALID_FIELD, "keyframe snapshot needs a grid")
        if not self.keyframe and self.grid is not None:
            raise MessageError(
                RejectionCode.INVALID_FIELD, "grid belongs to a keyframe snapshot only"
            )
        if self.grid is not None:
            _require_grid(self.grid)


@dataclass(frozen=True, slots=True, kw_only=True)
class TickEvents:
    """What the simulation did during ``tick``.

    Events are presentation detail and score reporting. The snapshot for the same tick
    is the authority; a client that drops this message still renders the right world.
    """

    session_id: str
    tick: int
    events: tuple[GameEvent, ...]
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("tick", self.tick, minimum=0, maximum=MAX_TICK)
        _require_count("events", len(self.events), MAX_EVENTS_PER_MESSAGE)


@dataclass(frozen=True, slots=True, kw_only=True)
class Rejected:
    """A message was refused. The connection stays open.

    ``sequence`` echoes the input sequence being refused when there was one, so a client
    can tell which batch died rather than guessing from timing.
    """

    session_id: str
    code: RejectionCode
    detail: str = ""
    sequence: int | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_detail("detail", self.detail)
        require_optional_int("sequence", self.sequence, minimum=0, maximum=MAX_SEQUENCE)


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionClosed:
    """The server is done with this connection, and why.

    The first release has no reconnect and no host migration: a closed session is over
    for that client, and the simulation carries on without it.
    """

    session_id: str
    code: RejectionCode
    detail: str = ""
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_detail("detail", self.detail)


type ClientMessage = JoinRequest | InputBatch
"""Everything a client may send. Note what is not here: state, score, seed, terrain."""

type ServerMessage = (
    JoinAccepted | InputAccepted | StateSnapshot | TickEvents | Rejected | SessionClosed
)
"""Everything a server may send."""

type Message = ClientMessage | ServerMessage

CLIENT_MESSAGE_TYPES: Final[frozenset[MessageType]] = frozenset(
    {MessageType.JOIN_REQUEST, MessageType.INPUT_BATCH}
)
"""Discriminators a server will decode. Everything else from a client is refused."""

SERVER_MESSAGE_TYPES: Final[frozenset[MessageType]] = frozenset(
    {
        MessageType.JOIN_ACCEPTED,
        MessageType.INPUT_ACCEPTED,
        MessageType.SNAPSHOT,
        MessageType.TICK_EVENTS,
        MessageType.REJECTED,
        MessageType.SESSION_CLOSED,
    }
)
"""Discriminators a client will decode. The two sets are disjoint and cover the enum."""


def _require_protocol_version(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MessageError(RejectionCode.INVALID_FIELD, "protocol_version must be an integer")
    if value != PROTOCOL_VERSION:
        raise MessageError(
            RejectionCode.PROTOCOL_VERSION_UNSUPPORTED,
            f"this build speaks protocol version {PROTOCOL_VERSION}, peer sent {value}",
        )


def _require_snapshot_version(field: str, value: int) -> None:
    require_int(field, value, minimum=1, maximum=MAX_ENUM_CODE)
    if value != SNAPSHOT_VERSION:
        raise MessageError(
            RejectionCode.PROTOCOL_VERSION_UNSUPPORTED,
            f"this build reads snapshot version {SNAPSHOT_VERSION}, peer sent {value}",
        )


def _require_entity_id(field: str, value: int) -> None:
    require_int(field, value, minimum=1, maximum=MAX_ENTITY_ID)


def _require_code(field: str, value: int) -> None:
    require_int(field, value, minimum=0, maximum=MAX_ENUM_CODE)


def _require_coordinate(field: str, value: int) -> None:
    require_int(field, value, minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)


def _require_count(field: str, count: int, limit: int) -> None:
    if count > limit:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"{field} carries {count} entries, over the limit of {limit}",
        )


def _require_grid(rows: tuple[str, ...]) -> None:
    if not rows:
        raise MessageError(RejectionCode.INVALID_FIELD, "grid must declare at least one row")
    if len(rows) > MAX_GRID_DIMENSION:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"grid declares {len(rows)} rows, over the limit of {MAX_GRID_DIMENSION}",
        )
    width = len(rows[0])
    if not 1 <= width <= MAX_GRID_DIMENSION:
        raise MessageError(
            RejectionCode.INVALID_FIELD,
            f"grid rows must be 1 to {MAX_GRID_DIMENSION} columns",
        )
    for index, row in enumerate(rows):
        if len(row) != width:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"grid.rows[{index}] is {len(row)} columns, expected {width}",
            )
        if not row.isascii() or not row.isprintable():
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"grid.rows[{index}] must be printable ASCII tile codes",
            )
