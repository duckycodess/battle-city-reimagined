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
    MATCH_SETTINGS_VERSION,
    MAX_ACTIONS_PER_BATCH,
    MAX_COORDINATE,
    MAX_ENTITY_ID,
    MAX_ENUM_CODE,
    MAX_EVENTS_PER_MESSAGE,
    MAX_GRID_DIMENSION,
    MAX_KEYFRAME_INTERVAL,
    MAX_LEVELS_PER_LOBBY,
    MAX_LIVES,
    MAX_LOBBY_MEMBERS,
    MAX_MODES_PER_LOBBY,
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_POWERUPS_PER_SNAPSHOT,
    MAX_PROJECTILES_PER_SNAPSHOT,
    MAX_REVISION,
    MAX_SEQUENCE,
    MAX_SLOT,
    MAX_TANKS_PER_SNAPSHOT,
    MAX_TEAM,
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
    require_name,
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
    LOBBY_JOIN = "lobby_join"
    LOBBY_CONFIGURE = "lobby_configure"
    LOBBY_READY = "lobby_ready"
    LOBBY_START = "lobby_start"
    LOBBY_LEAVE = "lobby_leave"
    LOBBY_WELCOME = "lobby_welcome"
    LOBBY_STATE = "lobby_state"
    MATCH_STARTING = "match_starting"


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


class MatchMode(StrEnum):
    """What a match is for. Member values are wire constants and never change.

    The mode is chosen in the lobby and travels in the match settings, so a session and
    a replay both record which rules a run was configured for. A mode supplies validated
    configuration to the shared simulation; it never forks its rules.
    """

    COOP = "coop"
    """Players share one side and defend one base together. Playable in this build."""

    FREE_FOR_ALL = "free_for_all"
    """Every player against every other. Configurable here; not startable yet."""

    TEAM_BATTLE = "team_battle"
    """Teams against each other. Configurable here; not startable yet."""


COMPETITIVE_MODES: Final[frozenset[MatchMode]] = frozenset(
    {MatchMode.FREE_FOR_ALL, MatchMode.TEAM_BATTLE}
)
"""Modes in which players fight each other rather than a shared opponent.

They are named, configurable and versioned on the wire so a lobby can describe one
honestly. Whether a given build can *run* one is a separate question that the server
answers with :attr:`LobbyInfo.playable_modes`: the shared simulation currently has one
player faction, no player-versus-player damage and no competitive result, so a server
refuses to start a competitive match with
:data:`~battle_city_protocol.codes.RejectionCode.MODE_UNSUPPORTED` rather than running a
co-op match and calling it a duel. Making these modes selectable without making them
fake is the point: the metadata is real, the refusal is explicit, and nothing in the
protocol lets a client or a server claim a competitive result it did not produce.
"""

TEAM_MODES: Final[frozenset[MatchMode]] = frozenset({MatchMode.TEAM_BATTLE})
"""Modes in which a roster entry carries a team. Everywhere else a team is absent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchSettings:
    """The configuration a lobby agreed on, explicit and versioned.

    The product specification requires competitive configuration to be explicit,
    versioned and carried in session and replay metadata. This record is that
    configuration for every mode, not only the competitive ones, because a co-op run
    recorded without its mode and its content is a run nobody can reproduce.

    ``cheats_enabled`` is stated rather than implied. The legacy cheats are campaign
    conveniences; a competitive match must not carry them, which is checked here rather
    than trusted to a server.
    """

    mode: MatchMode
    level_id: str
    content: ContentRef
    tick_rate: int
    max_players: int
    cheats_enabled: bool = False
    settings_version: int = MATCH_SETTINGS_VERSION

    def __post_init__(self) -> None:
        _require_settings_version("settings.settings_version", self.settings_version)
        require_identifier("settings.level_id", self.level_id)
        require_int("settings.tick_rate", self.tick_rate, minimum=1, maximum=MAX_TICK_RATE)
        require_int("settings.max_players", self.max_players, minimum=1, maximum=MAX_LOBBY_MEMBERS)
        require_bool("settings.cheats_enabled", self.cheats_enabled)
        if self.cheats_enabled and self.mode in COMPETITIVE_MODES:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"settings.cheats_enabled must be false in {self.mode.value}",
            )

    @property
    def competitive(self) -> bool:
        """Whether this configuration describes players fighting each other."""
        return self.mode in COMPETITIVE_MODES


@dataclass(frozen=True, slots=True, kw_only=True)
class TeamAssignment:
    """One host decision about which team a slot plays for."""

    slot: int
    team: int

    def __post_init__(self) -> None:
        require_int("teams.slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("teams.team", self.team, minimum=1, maximum=MAX_TEAM)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyMember:
    """One roster entry, as every member of the lobby sees it.

    Note what is absent: there is no token field here, and there is no field a token
    could travel in. The roster is the one lobby message that is broadcast, so a
    credential in it would be a credential handed to every other player. Membership
    secrets are only ever sent to the connection they belong to, in
    :class:`MatchStarting`.
    """

    slot: int
    display_name: str
    ready: bool
    host: bool
    connected: bool
    team: int | None = None

    def __post_init__(self) -> None:
        require_int("members.slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_name("members.display_name", self.display_name)
        require_bool("members.ready", self.ready)
        require_bool("members.host", self.host)
        require_bool("members.connected", self.connected)
        require_optional_int("members.team", self.team, minimum=1, maximum=MAX_TEAM)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyInfo:
    """What a lobby can be asked for, told to a member as it arrives.

    ``offered_modes`` is what the lobby will let a host select. ``playable_modes`` is
    the subset this server will actually start, and it is a separate field on purpose:
    a client that shows the difference tells a player the truth about the build in front
    of them, rather than offering a mode that fails at the last step with no explanation.
    """

    capacity: int
    offered_modes: tuple[MatchMode, ...]
    playable_modes: tuple[MatchMode, ...]
    offered_levels: tuple[str, ...]
    settings_version: int = MATCH_SETTINGS_VERSION

    def __post_init__(self) -> None:
        _require_settings_version("lobby.settings_version", self.settings_version)
        require_int("lobby.capacity", self.capacity, minimum=1, maximum=MAX_LOBBY_MEMBERS)
        _require_count("lobby.offered_modes", len(self.offered_modes), MAX_MODES_PER_LOBBY)
        _require_count("lobby.playable_modes", len(self.playable_modes), MAX_MODES_PER_LOBBY)
        _require_count("lobby.offered_levels", len(self.offered_levels), MAX_LEVELS_PER_LOBBY)
        if not self.offered_modes:
            raise MessageError(RejectionCode.INVALID_FIELD, "lobby.offered_modes must not be empty")
        if not self.offered_levels:
            raise MessageError(
                RejectionCode.INVALID_FIELD, "lobby.offered_levels must not be empty"
            )
        for level_id in self.offered_levels:
            require_identifier("lobby.offered_levels", level_id)
        if not set(self.playable_modes) <= set(self.offered_modes):
            raise MessageError(
                RejectionCode.INVALID_FIELD, "lobby.playable_modes must be offered modes"
            )


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

    The slot and the token were arranged before this message: by a deployment, or by the
    lobby that just sent this client its :class:`MatchStarting`. Either way joining
    proves membership rather than creating it, and the server decides what the claim is
    worth.
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


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyJoin:
    """A client asking for a seat in a lobby, with the ticket it was issued.

    A lobby is bounded and admission is proved, not assumed: a ticket is arranged out of
    band exactly as a session credential was before there was a lobby, and the server
    decides which slot the ticket takes. The client never names its own slot here, so a
    client cannot choose to be player one by saying so.

    ``team`` is a request and nothing more. The host owns team assignment; the server
    records a preference only when the mode uses teams and the number is free.
    """

    session_id: str
    ticket: str
    display_name: str
    content: ContentRef
    team: int | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_token("ticket", self.ticket)
        require_name("display_name", self.display_name)
        require_optional_int("team", self.team, minimum=1, maximum=MAX_TEAM)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyConfigure:
    """The host choosing the mode, the stage and the team assignments.

    ``revision`` is the settings revision the host was looking at. A configuration
    built against a roster that has since changed is refused with
    :data:`~battle_city_protocol.codes.RejectionCode.SETTINGS_STALE` rather than applied
    to a lobby nobody agreed to.
    """

    session_id: str
    slot: int
    revision: int
    mode: MatchMode
    level_id: str
    teams: tuple[TeamAssignment, ...] = ()
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("revision", self.revision, minimum=0, maximum=MAX_REVISION)
        require_identifier("level_id", self.level_id)
        _require_count("teams", len(self.teams), MAX_LOBBY_MEMBERS)
        seen: set[int] = set()
        for assignment in self.teams:
            if assignment.slot in seen:
                raise MessageError(
                    RejectionCode.INVALID_FIELD,
                    f"teams names slot {assignment.slot} twice",
                )
            seen.add(assignment.slot)
        if self.teams and self.mode not in TEAM_MODES:
            raise MessageError(
                RejectionCode.INVALID_FIELD,
                f"teams has no meaning in {self.mode.value}",
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyReady:
    """A member agreeing, or withdrawing agreement, to a specific settings revision.

    Readiness is content agreement. It names the revision it agreed to, so a host that
    changes the stage underneath a ready member does not inherit that member's consent.
    """

    session_id: str
    slot: int
    revision: int
    ready: bool
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("revision", self.revision, minimum=0, maximum=MAX_REVISION)
        require_bool("ready", self.ready)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyStart:
    """The host asking to start the match on the revision it names."""

    session_id: str
    slot: int
    revision: int
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_int("revision", self.revision, minimum=0, maximum=MAX_REVISION)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyLeave:
    """A member leaving the lobby on purpose, which is not the same as vanishing.

    A graceful departure frees the seat immediately and tells everyone why the roster
    changed. A dropped connection reaches the same place, a little later and without the
    explanation; both are handled, and neither pauses anything.
    """

    session_id: str
    slot: int
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyWelcome:
    """A seat granted, with the slot it carries and what this lobby can be asked for.

    Sent to the arriving connection alone. The roster that follows is broadcast; this is
    not, because it says which slot *you* are, and because a client needs to know what
    the server will agree to before it offers a player a choice.
    """

    session_id: str
    slot: int
    host: bool
    lobby: LobbyInfo
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_bool("host", self.host)


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyState:
    """The whole lobby as the server sees it: settings, roster and whether it can start.

    This is the only broadcast lobby message, and it carries no secret. ``startable``
    and ``blocked`` together are the server's answer to "may we go now?", given before
    anyone presses start: a client can show *why* not, and a competitive mode this build
    will not run says so in the lobby rather than at the last moment.
    """

    session_id: str
    revision: int
    settings: MatchSettings
    members: tuple[LobbyMember, ...]
    host_slot: int
    startable: bool
    blocked: RejectionCode | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("revision", self.revision, minimum=0, maximum=MAX_REVISION)
        require_int("host_slot", self.host_slot, minimum=1, maximum=MAX_SLOT)
        require_bool("startable", self.startable)
        _require_count("members", len(self.members), MAX_LOBBY_MEMBERS)
        seen: set[int] = set()
        for member in self.members:
            if member.slot in seen:
                raise MessageError(
                    RejectionCode.INVALID_FIELD, f"members names slot {member.slot} twice"
                )
            seen.add(member.slot)
        if self.startable and self.blocked is not None:
            raise MessageError(
                RejectionCode.INVALID_FIELD, "a startable lobby must not name a blocking reason"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchStarting:
    """The match is beginning, and this is your credential for it.

    Sent to one connection only, because it carries that slot's membership token. The
    token is the same kind of secret a session credential always was: it proves
    membership of the session the lobby just created, and it is never logged, never
    echoed into a rejection and never broadcast.

    The settings travel with it so that the agreement the lobby reached is recorded in
    the session a client is about to join, not only in the lobby that has now ended.
    """

    session_id: str
    slot: int
    token: str
    session: SessionInfo
    settings: MatchSettings
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_protocol_version(self.protocol_version)
        require_identifier("session_id", self.session_id)
        require_int("slot", self.slot, minimum=1, maximum=MAX_SLOT)
        require_token("token", self.token)


type ClientMessage = (
    JoinRequest | InputBatch | LobbyJoin | LobbyConfigure | LobbyReady | LobbyStart | LobbyLeave
)
"""Everything a client may send. Note what is not here: state, score, seed, terrain.

The lobby additions keep that property. A client may ask for a seat, agree to settings,
and — if it is the host — propose settings and ask to start. It cannot name its own slot
at join, cannot award itself a team outside the host's assignment, cannot mint a token
and cannot declare a result.
"""

type ServerMessage = (
    JoinAccepted
    | InputAccepted
    | StateSnapshot
    | TickEvents
    | Rejected
    | SessionClosed
    | LobbyWelcome
    | LobbyState
    | MatchStarting
)
"""Everything a server may send."""

type Message = ClientMessage | ServerMessage

CLIENT_MESSAGE_TYPES: Final[frozenset[MessageType]] = frozenset(
    {
        MessageType.JOIN_REQUEST,
        MessageType.INPUT_BATCH,
        MessageType.LOBBY_JOIN,
        MessageType.LOBBY_CONFIGURE,
        MessageType.LOBBY_READY,
        MessageType.LOBBY_START,
        MessageType.LOBBY_LEAVE,
    }
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
        MessageType.LOBBY_WELCOME,
        MessageType.LOBBY_STATE,
        MessageType.MATCH_STARTING,
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


def _require_settings_version(field: str, value: int) -> None:
    require_int(field, value, minimum=1, maximum=MAX_ENUM_CODE)
    if value != MATCH_SETTINGS_VERSION:
        raise MessageError(
            RejectionCode.PROTOCOL_VERSION_UNSUPPORTED,
            f"this build reads match settings version {MATCH_SETTINGS_VERSION}, peer sent {value}",
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
