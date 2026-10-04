"""The replay document: a bounded, versioned record of one run's tick inputs.

A replay is not a message. It is a stored artifact with its own discriminator
(:data:`REPLAY_FORMAT`), its own version (:data:`REPLAY_VERSION`), its own size bound
and its own entrypoints, :func:`encode_replay` and :func:`decode_replay`. None of it is
reachable from :func:`~battle_city_protocol.decode_client_message`, and no
:class:`~battle_city_protocol.MessageType` names it, so nothing a peer sends can become
a replay and nothing in a replay can become a command a server accepts over a wire.
That separation is the point: a replay carries commands no client is allowed to issue —
spawns, despawns, an enemy's move — and a document that could arrive as a frame would be
a way to issue them.

What it records
---------------
The *applied* tick input, one entry per simulated tick, in tick order: exactly what the
rules engine was given, including commands the server issued itself and including the
empty input of a tick that had to be run empty. Replaying a stream of applied inputs
against the same initial state reproduces the run; replaying what clients *asked* for
would not, because the authority dropped some of it.

Metadata names everything the stream needs to mean anything: the protocol version, the
simulation's canonical state version, the content pack, level and schema version, a
digest of the rule constants, the seed, the player slots, the tick rate, and the
agreed match settings when a lobby produced them. Periodic canonical state hashes —
always including tick zero — turn "it replayed" into "it replayed identically", and
name the first tick at which it did not.

What it must never record
-------------------------
A credential. The networking specification says to keep credentials out of logs and
saved replays, and this module enforces it twice: there is no field a token could
travel in, and :func:`decode_replay` refuses a document that carries a key from
:data:`CREDENTIAL_KEYS` anywhere in its tree with
:data:`ReplayRejection.CREDENTIAL_FIELD` rather than with the generic unknown-field
refusal, so a recording made by a build that got this wrong is rejected by name.

Compatibility
-------------
Nothing is migrated silently. A document whose format, replay version, protocol
version or state version is not this build's is refused with a reason that names the
field, the value this build speaks and the value it was handed. An old replay is a
document to open with the build that wrote it, not a document to guess at.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .errors import MessageError, ProtocolError
from .jsonio import JsonValue, decode_json_object, encode_json_object
from .limits import (
    MAX_COORDINATE,
    MAX_ENTITY_ID,
    MAX_ENUM_CODE,
    MAX_KEYFRAME_INTERVAL,
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_SLOT,
    MAX_TICK,
    MAX_TICK_RATE,
    PROTOCOL_VERSION,
)
from .messages import ContentRef, DirectionCode, MatchMode, MatchSettings
from .validation import require_digest, require_identifier, require_int, require_member

REPLAY_FORMAT: Final[str] = "battle-city-reimagined/replay"
"""The document discriminator. Deliberately not a :class:`~.messages.MessageType`."""

REPLAY_VERSION: Final[int] = 1
"""The layout version of this document.

It moves for a change to the replay layout alone. The wire version, the match settings
version and the simulation's canonical state version all travel inside a replay and all
move for their own reasons; a document that fails one of them says which.
"""

MAX_REPLAY_BYTES: Final[int] = 1 << 20
"""Largest replay document this build will decode.

Larger than a frame on purpose — a replay is a file, not a packet — and finite for the
same reason a frame is bounded: a document is refused on its length before it is parsed.
"""

MAX_REPLAY_TICKS: Final[int] = 4096
"""Ticks one document may carry. At sixty ticks a second this is a bounded minute.

A recorder stops at this bound and says so rather than growing without limit; a longer
run is several documents, which is a storage decision and not a format change.

This bound and :data:`MAX_REPLAY_BYTES` are independent, and neither implies the other:
this many ticks carrying more than about five commands each encodes to more than a
megabyte. A recorder is therefore bounded by *both* — see
:func:`replay_envelope_bytes` and :func:`replay_entry_bytes`, which let it add up what
it is about to write — and stops at whichever it reaches first. A document that fitted
one bound and not the other would be a recording nothing could save.
"""

MAX_COMMANDS_PER_TICK: Final[int] = 64
"""Commands one tick may carry: every player's intents plus the server's own."""

MAX_REPLAY_HASHES: Final[int] = 512
"""Checkpoint hashes one document may carry, including the one for tick zero."""

MAX_REPLAY_SLOTS: Final[int] = MAX_PLAYERS_PER_SNAPSHOT
MAX_SEED: Final[int] = (1 << 64) - 1
"""Highest seed. The simulation's generator is a 64-bit state."""

MAX_HASH_INTERVAL: Final[int] = MAX_KEYFRAME_INTERVAL

CREDENTIAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "passphrase",
        "password",
        "secret",
        "secrets",
        "session_token",
        "ticket",
        "tickets",
        "token",
        "tokens",
    }
)
"""Keys that may not appear anywhere in a replay, at any depth.

Every one of them is already an unknown field, so this is a second answer to a question
the first answer covers. It is here because "unknown field ``token``" and "this document
carries a credential" are different things to read in a log, and because the rule is
worth stating where somebody adding a field will see it.
"""


class ReplayRejection(StrEnum):
    """Why a replay document was refused. Values are stable and matched on."""

    REPLAY_TOO_LARGE = "replay_too_large"
    """The document is longer than :data:`MAX_REPLAY_BYTES`."""

    MALFORMED_REPLAY = "malformed_replay"
    """The bytes are not a strict, bounded JSON object."""

    UNKNOWN_DOCUMENT = "unknown_document"
    """The ``format`` field named something other than a replay."""

    REPLAY_VERSION_UNSUPPORTED = "replay_version_unsupported"
    """The document's replay layout version is not this build's. Nothing is migrated."""

    UNKNOWN_FIELD = "unknown_field"
    """The document carried a field this build does not define. Nothing is ignored."""

    CREDENTIAL_FIELD = "credential_field"
    """The document carried a key from :data:`CREDENTIAL_KEYS`. A replay holds no secret."""

    INVALID_FIELD = "invalid_field"
    """A field was missing, had the wrong type, or fell outside its published bound."""

    UNKNOWN_COMMAND = "unknown_command"
    """A tick named a command kind this build does not know."""


class ReplayError(ProtocolError):
    """A replay document is not acceptable, with the reason it was refused."""

    def __init__(self, code: ReplayRejection, detail: str) -> None:
        self.code = code
        self.detail = detail[:200]
        super().__init__(f"{code.value}: {self.detail}")


class ReplayCommandKind(StrEnum):
    """The complete command vocabulary a replay may carry.

    It is exactly the simulation's command set, named rather than numbered, and it is
    deliberately wider than :class:`~.messages.ActionKind`: a replay records what the
    authority applied, and the authority spawns things no client may ask for.
    """

    MOVE = "move"
    FIRE = "fire"
    RESPAWN = "respawn"
    SPAWN_ENEMY = "spawn_enemy"
    SPAWN_POWERUP = "spawn_powerup"
    DESPAWN_POWERUP = "despawn_powerup"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayMove:
    """Face a direction and try to advance one step."""

    tank_id: int
    direction: DirectionCode

    def __post_init__(self) -> None:
        _check("command.tank_id", self.tank_id, minimum=1, maximum=MAX_ENTITY_ID)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayFire:
    """Fire along the tank's current facing."""

    tank_id: int

    def __post_init__(self) -> None:
        _check("command.tank_id", self.tank_id, minimum=1, maximum=MAX_ENTITY_ID)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayRespawn:
    """Return a slot's tank to its spawn."""

    slot: int

    def __post_init__(self) -> None:
        _check("command.slot", self.slot, minimum=1, maximum=MAX_SLOT)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplaySpawnEnemy:
    """Place an enemy tank. ``variant`` is a canonical simulation enum code."""

    cell_x: int
    cell_y: int
    variant: int
    facing: DirectionCode

    def __post_init__(self) -> None:
        _check("command.cell_x", self.cell_x, minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        _check("command.cell_y", self.cell_y, minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        _check("command.variant", self.variant, minimum=0, maximum=MAX_ENUM_CODE)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplaySpawnPowerup:
    """Place an uncollected powerup. ``powerup`` is a canonical simulation enum code."""

    cell_x: int
    cell_y: int
    powerup: int

    def __post_init__(self) -> None:
        _check("command.cell_x", self.cell_x, minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        _check("command.cell_y", self.cell_y, minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        _check("command.powerup", self.powerup, minimum=0, maximum=MAX_ENUM_CODE)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayDespawnPowerup:
    """Remove an uncollected powerup."""

    powerup_id: int

    def __post_init__(self) -> None:
        _check("command.powerup_id", self.powerup_id, minimum=1, maximum=MAX_ENTITY_ID)


type ReplayCommand = (
    ReplayMove
    | ReplayFire
    | ReplayRespawn
    | ReplaySpawnEnemy
    | ReplaySpawnPowerup
    | ReplayDespawnPowerup
)


def command_kind_of(command: ReplayCommand) -> ReplayCommandKind:
    """Return the discriminator for ``command``. Exhaustive over the six variants."""
    match command:
        case ReplayMove():
            return ReplayCommandKind.MOVE
        case ReplayFire():
            return ReplayCommandKind.FIRE
        case ReplayRespawn():
            return ReplayCommandKind.RESPAWN
        case ReplaySpawnEnemy():
            return ReplayCommandKind.SPAWN_ENEMY
        case ReplaySpawnPowerup():
            return ReplayCommandKind.SPAWN_POWERUP
        case ReplayDespawnPowerup():
            return ReplayCommandKind.DESPAWN_POWERUP


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayTick:
    """One tick's applied input. An empty command tuple is a recorded fact, not a gap."""

    tick: int
    commands: tuple[ReplayCommand, ...] = ()

    def __post_init__(self) -> None:
        _check("tick.tick", self.tick, minimum=0, maximum=MAX_TICK)
        _count("tick.commands", len(self.commands), MAX_COMMANDS_PER_TICK)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayHash:
    """A canonical state hash and the tick whose state it fingerprints."""

    tick: int
    digest: str

    def __post_init__(self) -> None:
        _check("hash.tick", self.tick, minimum=0, maximum=MAX_TICK)
        _validated(lambda: require_digest("hash.digest", self.digest))


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayMetadata:
    """Everything a stream of tick inputs needs in order to mean something.

    Note what is absent and cannot be added without this docstring changing: there is no
    token, no ticket and no endpoint. A replay describes a run, not the arrangements that
    produced one.
    """

    session_id: str
    content: ContentRef
    rules_digest: str
    seed: int
    slots: tuple[int, ...]
    tick_rate: int
    hash_interval: int
    state_version: int
    protocol_version: int = PROTOCOL_VERSION
    truncated: bool = False
    settings: MatchSettings | None = None

    def __post_init__(self) -> None:
        _validated(lambda: require_identifier("metadata.session_id", self.session_id))
        _validated(lambda: require_digest("metadata.rules_digest", self.rules_digest))
        _check("metadata.seed", self.seed, minimum=0, maximum=MAX_SEED)
        _check("metadata.tick_rate", self.tick_rate, minimum=1, maximum=MAX_TICK_RATE)
        _check("metadata.hash_interval", self.hash_interval, minimum=1, maximum=MAX_HASH_INTERVAL)
        _check("metadata.state_version", self.state_version, minimum=1, maximum=MAX_ENUM_CODE)
        _check("metadata.protocol_version", self.protocol_version, minimum=1, maximum=MAX_ENUM_CODE)
        if not self.slots:
            raise ReplayError(ReplayRejection.INVALID_FIELD, "metadata.slots must not be empty")
        _count("metadata.slots", len(self.slots), MAX_REPLAY_SLOTS)
        previous = 0
        for slot in self.slots:
            _check("metadata.slots", slot, minimum=1, maximum=MAX_SLOT)
            if slot <= previous:
                raise ReplayError(
                    ReplayRejection.INVALID_FIELD,
                    "metadata.slots must be ascending and without duplicates",
                )
            previous = slot


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayDocument:
    """One recorded run, complete and self-describing."""

    metadata: ReplayMetadata
    ticks: tuple[ReplayTick, ...] = ()
    hashes: tuple[ReplayHash, ...] = ()
    replay_version: int = REPLAY_VERSION

    def __post_init__(self) -> None:
        _check("replay_version", self.replay_version, minimum=1, maximum=MAX_ENUM_CODE)
        if self.replay_version != REPLAY_VERSION:
            raise ReplayError(
                ReplayRejection.REPLAY_VERSION_UNSUPPORTED,
                f"this build writes replay version {REPLAY_VERSION}, "
                f"document is version {self.replay_version}",
            )
        _count("ticks", len(self.ticks), MAX_REPLAY_TICKS)
        _count("hashes", len(self.hashes), MAX_REPLAY_HASHES)
        _require_ascending("ticks", [entry.tick for entry in self.ticks])
        _require_ascending("hashes", [entry.tick for entry in self.hashes])

    @property
    def first_tick(self) -> int:
        """The tick the recording opens on, or zero when nothing was recorded."""
        return self.ticks[0].tick if self.ticks else 0

    def hash_at(self, tick: int) -> str | None:
        """The recorded canonical hash for ``tick``, or ``None`` when none was taken."""
        for entry in self.hashes:
            if entry.tick == tick:
                return entry.digest
        return None


# -- encoding ------------------------------------------------------------------------

_DOCUMENT_FIELDS: Final[tuple[str, ...]] = (
    "format",
    "replay_version",
    "metadata",
    "ticks",
    "hashes",
)
_METADATA_FIELDS: Final[tuple[str, ...]] = (
    "session_id",
    "content",
    "rules_digest",
    "seed",
    "slots",
    "tick_rate",
    "hash_interval",
    "state_version",
    "protocol_version",
    "truncated",
    "settings",
)
_CONTENT_FIELDS: Final[tuple[str, ...]] = (
    "pack_id",
    "pack_version",
    "level_id",
    "content_schema_version",
)
_SETTINGS_FIELDS: Final[tuple[str, ...]] = (
    "mode",
    "level_id",
    "content",
    "tick_rate",
    "max_players",
    "cheats_enabled",
    "settings_version",
)
_TICK_FIELDS: Final[tuple[str, ...]] = ("tick", "commands")
_HASH_FIELDS: Final[tuple[str, ...]] = ("tick", "digest")
_COMMAND_FIELDS: Final[dict[ReplayCommandKind, tuple[str, ...]]] = {
    ReplayCommandKind.MOVE: ("kind", "tank_id", "direction"),
    ReplayCommandKind.FIRE: ("kind", "tank_id"),
    ReplayCommandKind.RESPAWN: ("kind", "slot"),
    ReplayCommandKind.SPAWN_ENEMY: ("kind", "cell_x", "cell_y", "variant", "facing"),
    ReplayCommandKind.SPAWN_POWERUP: ("kind", "cell_x", "cell_y", "powerup"),
    ReplayCommandKind.DESPAWN_POWERUP: ("kind", "powerup_id"),
}
"""Every accepted key, per command kind. A kind missing from here cannot be decoded."""


def encode_replay(document: ReplayDocument) -> bytes:
    """Return the canonical UTF-8 JSON bytes of ``document``.

    Keys are sorted and separators fixed, as they are for a message, so one document is
    always one byte string: a recording is comparable in a test and its digest is stable.

    The bound is :data:`MAX_REPLAY_BYTES` and not the wire frame bound. A replay is a
    file rather than a packet, and encoding it at the frame bound would refuse to write
    documents :func:`decode_replay` is willing to read.
    """
    body: dict[str, JsonValue] = {
        "format": REPLAY_FORMAT,
        "replay_version": document.replay_version,
        "metadata": _metadata_body(document.metadata),
        "ticks": [_tick_body(entry) for entry in document.ticks],
        "hashes": [_hash_body(entry) for entry in document.hashes],
    }
    try:
        return encode_json_object(body, limit=MAX_REPLAY_BYTES)
    except MessageError as error:
        raise ReplayError(ReplayRejection.REPLAY_TOO_LARGE, error.detail) from error


def _metadata_body(metadata: ReplayMetadata) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "session_id": metadata.session_id,
        "content": _content_body(metadata.content),
        "rules_digest": metadata.rules_digest,
        "seed": metadata.seed,
        "slots": list(metadata.slots),
        "tick_rate": metadata.tick_rate,
        "hash_interval": metadata.hash_interval,
        "state_version": metadata.state_version,
        "protocol_version": metadata.protocol_version,
        "truncated": metadata.truncated,
    }
    if metadata.settings is not None:
        body["settings"] = _settings_body(metadata.settings)
    return body


def _content_body(content: ContentRef) -> dict[str, JsonValue]:
    return {
        "pack_id": content.pack_id,
        "pack_version": content.pack_version,
        "level_id": content.level_id,
        "content_schema_version": content.content_schema_version,
    }


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


def _hash_body(entry: ReplayHash) -> dict[str, JsonValue]:
    return {"tick": entry.tick, "digest": entry.digest}


def _tick_body(entry: ReplayTick) -> dict[str, JsonValue]:
    return {
        "tick": entry.tick,
        "commands": [_command_body(command) for command in entry.commands],
    }


def _command_body(command: ReplayCommand) -> dict[str, JsonValue]:
    """Encode one command. Exhaustive over the six variants, by construction."""
    kind = command_kind_of(command)
    body: dict[str, JsonValue] = {"kind": kind.value}
    match command:
        case ReplayMove():
            body["tank_id"] = command.tank_id
            body["direction"] = command.direction.value
        case ReplayFire():
            body["tank_id"] = command.tank_id
        case ReplayRespawn():
            body["slot"] = command.slot
        case ReplaySpawnEnemy():
            body["cell_x"] = command.cell_x
            body["cell_y"] = command.cell_y
            body["variant"] = command.variant
            body["facing"] = command.facing.value
        case ReplaySpawnPowerup():
            body["cell_x"] = command.cell_x
            body["cell_y"] = command.cell_y
            body["powerup"] = command.powerup
        case ReplayDespawnPowerup():
            body["powerup_id"] = command.powerup_id
    return body


# -- size accounting -----------------------------------------------------------------

REPLAY_ARRAY_SEPARATOR_BYTES: Final[int] = 1
"""Bytes one comma costs between two elements of an encoded array.

The encoder fixes its separators, so an array of ``n`` elements is the sum of the
elements plus ``n - 1`` of these. That is the whole arithmetic behind
:func:`replay_entry_bytes`, and it is a published constant rather than a literal in a
recorder because it is only true for as long as :func:`encode_json_object` keeps its
separators fixed.
"""


def replay_envelope_bytes(metadata: ReplayMetadata) -> int:
    """Encoded size of a document with this metadata and no ticks and no hashes.

    Together with :func:`replay_entry_bytes` this lets a recorder know what its document
    will encode to without encoding it: a recorder that re-encoded a growing document
    every tick would cost a session quadratic work for a number it can add up.
    """
    return len(encode_replay(ReplayDocument(metadata=metadata)))


def replay_entry_bytes(entry: ReplayTick | ReplayHash) -> int:
    """Encoded size of one array element, excluding the comma in front of it.

    Exact, not an estimate: the encoder sorts keys and fixes separators at every level,
    so an element encodes to the same bytes alone as it does inside the document.
    ``tests/replay`` holds that identity, which is what makes it safe to budget with.
    """
    body = _tick_body(entry) if isinstance(entry, ReplayTick) else _hash_body(entry)
    return len(encode_json_object(body, limit=MAX_REPLAY_BYTES))


# -- decoding ------------------------------------------------------------------------


def decode_replay(payload: bytes) -> ReplayDocument:
    """Decode an untrusted replay document, totally and without migration.

    Every refusal is a :class:`ReplayError` carrying a :class:`ReplayRejection`. Nothing
    is repaired, nothing is defaulted and nothing unknown is ignored.
    """
    if len(payload) > MAX_REPLAY_BYTES:
        raise ReplayError(
            ReplayRejection.REPLAY_TOO_LARGE,
            f"replay is {len(payload)} bytes, over the limit of {MAX_REPLAY_BYTES}",
        )
    try:
        document = decode_json_object(payload, limit=MAX_REPLAY_BYTES)
    except MessageError as error:
        raise ReplayError(ReplayRejection.MALFORMED_REPLAY, error.detail) from error

    _refuse_credentials(document)

    root = _Reader("replay", document, _DOCUMENT_FIELDS)
    if root.text("format") != REPLAY_FORMAT:
        raise ReplayError(
            ReplayRejection.UNKNOWN_DOCUMENT,
            f"format must be {REPLAY_FORMAT!r}; this is not a replay document",
        )
    version = root.integer("replay_version")
    if version != REPLAY_VERSION:
        raise ReplayError(
            ReplayRejection.REPLAY_VERSION_UNSUPPORTED,
            f"this build reads replay version {REPLAY_VERSION}, document is version {version}",
        )

    return ReplayDocument(
        metadata=_read_metadata(root.child("metadata", _METADATA_FIELDS)),
        ticks=tuple(_read_tick(value) for value in root.sequence("ticks", MAX_REPLAY_TICKS)),
        hashes=tuple(_read_hash(value) for value in root.sequence("hashes", MAX_REPLAY_HASHES)),
        replay_version=version,
    )


def _refuse_credentials(document: Mapping[str, JsonValue]) -> None:
    """Refuse a document carrying a credential-shaped key at any depth.

    The walk is iterative because the depth bound is enforced by the JSON decoder rather
    than by this function, and because a recursive walk over an untrusted tree is one
    more way to turn a document into a :class:`RecursionError`.
    """
    pending: list[JsonValue] = [dict(document)]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in CREDENTIAL_KEYS:
                    raise ReplayError(
                        ReplayRejection.CREDENTIAL_FIELD,
                        f"replay carries the key {key!r}; a replay never holds a credential",
                    )
                pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)


def _read_metadata(fields: _Reader) -> ReplayMetadata:
    protocol_version = fields.integer("protocol_version")
    return ReplayMetadata(
        session_id=fields.text("session_id"),
        content=_read_content(fields.child("content", _CONTENT_FIELDS)),
        rules_digest=fields.text("rules_digest"),
        seed=fields.integer("seed"),
        slots=tuple(
            _integer("metadata.slots", value)
            for value in fields.sequence("slots", MAX_REPLAY_SLOTS)
        ),
        tick_rate=fields.integer("tick_rate"),
        hash_interval=fields.integer("hash_interval"),
        state_version=fields.integer("state_version"),
        protocol_version=protocol_version,
        truncated=fields.flag("truncated"),
        settings=_read_settings(fields),
    )


def _read_content(fields: _Reader) -> ContentRef:
    return _validated(
        lambda: ContentRef(
            pack_id=fields.text("pack_id"),
            pack_version=fields.text("pack_version"),
            level_id=fields.text("level_id"),
            content_schema_version=fields.integer("content_schema_version"),
        )
    )


def _read_settings(fields: _Reader) -> MatchSettings | None:
    if fields.optional("settings") is None:
        return None
    child = fields.child("settings", _SETTINGS_FIELDS)
    mode = _validated(lambda: require_member("settings.mode", child.raw("mode"), MatchMode))
    return _validated(
        lambda: MatchSettings(
            mode=mode,
            level_id=child.text("level_id"),
            content=_read_content(child.child("content", _CONTENT_FIELDS)),
            tick_rate=child.integer("tick_rate"),
            max_players=child.integer("max_players"),
            cheats_enabled=child.flag("cheats_enabled"),
            settings_version=child.integer("settings_version"),
        )
    )


def _read_tick(value: JsonValue) -> ReplayTick:
    fields = _entry("tick", value, _TICK_FIELDS)
    return ReplayTick(
        tick=fields.integer("tick"),
        commands=tuple(
            _read_command(item) for item in fields.sequence("commands", MAX_COMMANDS_PER_TICK)
        ),
    )


def _read_hash(value: JsonValue) -> ReplayHash:
    fields = _entry("hash", value, _HASH_FIELDS)
    return ReplayHash(tick=fields.integer("tick"), digest=fields.text("digest"))


def _read_command(value: JsonValue) -> ReplayCommand:
    """Decode one command. Exhaustive over :class:`ReplayCommandKind`."""
    if not isinstance(value, dict):
        raise ReplayError(ReplayRejection.INVALID_FIELD, "command entry must be an object")
    raw = value.get("kind")
    if not isinstance(raw, str):
        raise ReplayError(ReplayRejection.UNKNOWN_COMMAND, "command.kind must be a string")
    try:
        kind = ReplayCommandKind(raw)
    except ValueError as error:
        raise ReplayError(
            ReplayRejection.UNKNOWN_COMMAND, "command.kind is not one this build knows"
        ) from error

    fields = _Reader("command", value, _COMMAND_FIELDS[kind])
    match kind:
        case ReplayCommandKind.MOVE:
            return ReplayMove(
                tank_id=fields.integer("tank_id"), direction=fields.direction("direction")
            )
        case ReplayCommandKind.FIRE:
            return ReplayFire(tank_id=fields.integer("tank_id"))
        case ReplayCommandKind.RESPAWN:
            return ReplayRespawn(slot=fields.integer("slot"))
        case ReplayCommandKind.SPAWN_ENEMY:
            return ReplaySpawnEnemy(
                cell_x=fields.integer("cell_x"),
                cell_y=fields.integer("cell_y"),
                variant=fields.integer("variant"),
                facing=fields.direction("facing"),
            )
        case ReplayCommandKind.SPAWN_POWERUP:
            return ReplaySpawnPowerup(
                cell_x=fields.integer("cell_x"),
                cell_y=fields.integer("cell_y"),
                powerup=fields.integer("powerup"),
            )
        case ReplayCommandKind.DESPAWN_POWERUP:
            return ReplayDespawnPowerup(powerup_id=fields.integer("powerup_id"))


class _Reader:
    """A checked view over one JSON object: no unknown keys, no untyped reads."""

    __slots__ = ("_document", "_label")

    def __init__(self, label: str, document: Mapping[str, JsonValue], names: Sequence[str]) -> None:
        self._label = label
        self._document = document
        allowed = set(names)
        for key in document:
            if key not in allowed:
                raise ReplayError(
                    ReplayRejection.UNKNOWN_FIELD, f"{self._name(key)} is not a field of {label}"
                )

    def _name(self, key: str) -> str:
        return key if self._label == "replay" else f"{self._label}.{key}"

    def raw(self, key: str) -> JsonValue:
        if key not in self._document:
            raise ReplayError(ReplayRejection.INVALID_FIELD, f"{self._name(key)} is missing")
        return self._document[key]

    def optional(self, key: str) -> JsonValue:
        return self._document.get(key)

    def text(self, key: str) -> str:
        value = self.raw(key)
        if not isinstance(value, str):
            raise ReplayError(ReplayRejection.INVALID_FIELD, f"{self._name(key)} must be a string")
        return value

    def integer(self, key: str) -> int:
        return _integer(self._name(key), self.raw(key))

    def flag(self, key: str) -> bool:
        value = self.raw(key)
        if not isinstance(value, bool):
            raise ReplayError(ReplayRejection.INVALID_FIELD, f"{self._name(key)} must be a boolean")
        return value

    def direction(self, key: str) -> DirectionCode:
        return _validated(lambda: require_member(self._name(key), self.raw(key), DirectionCode))

    def sequence(self, key: str, limit: int) -> list[JsonValue]:
        value = self.raw(key)
        if not isinstance(value, list):
            raise ReplayError(ReplayRejection.INVALID_FIELD, f"{self._name(key)} must be an array")
        _count(self._name(key), len(value), limit)
        return value

    def child(self, key: str, names: Sequence[str]) -> _Reader:
        value = self.raw(key)
        if not isinstance(value, dict):
            raise ReplayError(ReplayRejection.INVALID_FIELD, f"{self._name(key)} must be an object")
        return _Reader(self._name(key), value, names)


def _entry(label: str, value: JsonValue, names: Sequence[str]) -> _Reader:
    if not isinstance(value, dict):
        raise ReplayError(ReplayRejection.INVALID_FIELD, f"{label} entry must be an object")
    return _Reader(label, value, names)


def _integer(field_name: str, value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplayError(ReplayRejection.INVALID_FIELD, f"{field_name} must be an integer")
    return value


def _check(field_name: str, value: int, *, minimum: int, maximum: int) -> int:
    return _validated(lambda: require_int(field_name, value, minimum=minimum, maximum=maximum))


def _count(field_name: str, found: int, limit: int) -> None:
    if found > limit:
        raise ReplayError(
            ReplayRejection.INVALID_FIELD,
            f"{field_name} carries {found} entries, over the limit of {limit}",
        )


def _require_ascending(field_name: str, ticks: Sequence[int]) -> None:
    """Refuse an out-of-order or repeated tick index.

    Order is what makes a replay a stream rather than a bag: a duplicated tick would be
    applied twice and a reordered one would be applied against the wrong state, and both
    would look like a divergence rather than like the malformed document they are.
    """
    previous = -1
    for tick in ticks:
        if tick <= previous:
            raise ReplayError(
                ReplayRejection.INVALID_FIELD,
                f"{field_name} must be in ascending tick order without duplicates",
            )
        previous = tick


def _validated[T](build: Callable[[], T]) -> T:
    """Run a protocol validator, reporting its refusal as a replay refusal.

    The field validators are shared with the message model deliberately — a replay's
    identifiers and digests are held to exactly the bounds a message's are — but their
    :class:`~battle_city_protocol.codes.RejectionCode` belongs to the wire, and a stored
    document is not the wire.
    """
    try:
        return build()
    except MessageError as error:
        raise ReplayError(ReplayRejection.INVALID_FIELD, error.detail) from error
