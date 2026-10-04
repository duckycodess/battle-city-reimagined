"""Recording a run's applied tick inputs, and replaying one to prove it.

This module is the third place the protocol and simulation vocabularies meet, after
:mod:`battle_city_server.content` and :mod:`battle_city_server.translation`. The protocol
package owns the replay *document* and may not import the simulation; the simulation owns
the commands and the canonical hash and may not import the protocol; so the mapping
between a :class:`~battle_city_sim.Command` and a
:class:`~battle_city_protocol.replay.ReplayCommand` lives here, exhaustively, in one
readable table.

What is recorded is what was applied
------------------------------------
:class:`ReplayRecorder` is handed the :class:`~battle_city_sim.TickInput` the rules engine
actually ran, after the session dropped the batches it refused and after it folded in the
commands the server itself issued. A tick the session had to run empty is recorded as an
empty tick, which is a fact about the run rather than a gap in the recording. Replaying
what clients *asked* for would not reproduce anything, because the authority did not run
that.

What is replayed is checked first
---------------------------------
:func:`play_replay` is given a :class:`ReplayContext` the caller rebuilt — a stage, the
content reference it came from and the rule constants — and never a
:class:`~battle_city_server.config.SessionConfig`. A session config carries per-slot
membership tokens, and a verifier has no business holding one: the seed, the slots and the
tick rate a replay needs are recorded in the document, and the stage comes from content.
Every version and identity in the metadata is compared before a single tick is stepped,
and a disagreement is an :class:`ReplayIncompatibleError` naming the field, what this
build speaks and what the document says. Nothing is migrated and nothing is guessed.

Determinism is then checked rather than assumed: the recorded canonical hashes are
compared as playback reaches their ticks, and the *first* disagreement is reported as a
structured :class:`ReplayMismatch` with the tick and both digests, which is the one piece
of information that makes a divergence findable.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from battle_city_protocol import (
    MAX_HASH_INTERVAL,
    MAX_REPLAY_BYTES,
    MAX_REPLAY_HASHES,
    MAX_REPLAY_TICKS,
    MAX_SEED,
    MAX_TICK,
    PROTOCOL_VERSION,
    REPLAY_ARRAY_SEPARATOR_BYTES,
    REPLAY_VERSION,
    ContentRef,
    DirectionCode,
    MatchSettings,
    ReplayCommand,
    ReplayDespawnPowerup,
    ReplayDocument,
    ReplayError,
    ReplayFire,
    ReplayHash,
    ReplayMetadata,
    ReplayMove,
    ReplayRespawn,
    ReplaySpawnEnemy,
    ReplaySpawnPowerup,
    ReplayTick,
    replay_entry_bytes,
    replay_envelope_bytes,
)
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    DEFAULT_RULES,
    Command,
    DespawnPowerupCommand,
    Direction,
    FireCommand,
    GridPos,
    InvalidInputError,
    MoveCommand,
    PowerupKind,
    RespawnCommand,
    Rules,
    SimulationState,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    Stage,
    TankVariant,
    TickInput,
    new_game,
    state_hash,
    step,
)

from .config import SessionConfig
from .content import rules_digest
from .translation import DIRECTIONS

DEFAULT_HASH_INTERVAL: Final[int] = 30
"""Ticks between recorded canonical hashes. Half a second at the default tick rate.

Frequent enough that a divergence is localised to a handful of ticks, and sparse enough
that a bounded recording spends most of its bytes on input rather than on digests.
"""

CHECKPOINT_RESERVE_BYTES: Final[int] = (
    replay_entry_bytes(ReplayHash(tick=MAX_TICK, digest="0" * 64)) + REPLAY_ARRAY_SEPARATOR_BYTES
)
"""Bytes a recorder holds back for the checkpoint :meth:`ReplayRecorder.document` adds.

The widest a hash entry and its comma can be, so the closing checkpoint always fits
however long the run got. A recording that filled its budget and then could not close
its own hash chain would be a recording that cannot be verified, which is a worse answer
than one tick fewer.
"""

WIRE_FACINGS: Final[dict[Direction, DirectionCode]] = {
    Direction.UP: DirectionCode.UP,
    Direction.DOWN: DirectionCode.DOWN,
    Direction.LEFT: DirectionCode.LEFT,
    Direction.RIGHT: DirectionCode.RIGHT,
}
"""The simulation facings and the recorded ones. :data:`.translation.DIRECTIONS` inverts it."""


class ReplayPlaybackError(ValueError):
    """A recorded run cannot be replayed by this build."""


class ReplayIncompatibleError(ReplayPlaybackError):
    """A replay was made by a build, a ruleset or a content pack this one is not.

    It names the field rather than saying "incompatible", because the compatibility
    specification asks for a failure a player can act on and "upgrade your content pack"
    and "upgrade your game" are different instructions.
    """

    def __init__(self, field: str, expected: object, found: object) -> None:
        self.field = field
        self.expected = expected
        self.found = found
        super().__init__(f"replay {field} is {found!r}; this build replays {expected!r}")


class ReplayUnplayableError(ReplayPlaybackError):
    """The recorded stream itself cannot be applied to the state it describes.

    A tick index that does not follow the state, an enum code no simulation value has,
    or an input the rules engine refuses. All three mean the document is wrong rather
    than that the run diverged, so they are raised rather than reported as a mismatch.
    """


@runtime_checkable
class TickRecorder(Protocol):
    """What a session tells a recorder. Output only, like a spectator.

    A recorder sees the applied input and the state it produced. It cannot schedule a
    command, answer a message or close a session; there is no method here through which
    it could.
    """

    def opened(self, state: SimulationState) -> None:
        """The state the run starts from, before any tick has been applied."""

    def recorded(self, tick_input: TickInput, state: SimulationState) -> None:
        """One applied tick input and the state the rules engine produced from it."""


# -- the command mapping -------------------------------------------------------------


def replay_command(command: Command) -> ReplayCommand:
    """Record one simulation command. Exhaustive over the six command variants."""
    match command:
        case MoveCommand():
            return ReplayMove(tank_id=command.tank_id, direction=WIRE_FACINGS[command.direction])
        case FireCommand():
            return ReplayFire(tank_id=command.tank_id)
        case RespawnCommand():
            return ReplayRespawn(slot=command.slot)
        case SpawnEnemyCommand():
            return ReplaySpawnEnemy(
                cell_x=command.cell.x,
                cell_y=command.cell.y,
                variant=command.variant.value,
                facing=WIRE_FACINGS[command.facing],
            )
        case SpawnPowerupCommand():
            return ReplaySpawnPowerup(
                cell_x=command.cell.x, cell_y=command.cell.y, powerup=command.kind.value
            )
        case DespawnPowerupCommand():
            return ReplayDespawnPowerup(powerup_id=command.powerup_id)


def replay_commands(commands: Sequence[Command]) -> tuple[ReplayCommand, ...]:
    """Record a whole tick's commands, in the order the rules engine was given them."""
    return tuple(replay_command(command) for command in commands)


def simulation_command(command: ReplayCommand) -> Command:
    """Read one recorded command back. Exhaustive over the six recorded variants."""
    match command:
        case ReplayMove():
            return MoveCommand(tank_id=command.tank_id, direction=DIRECTIONS[command.direction])
        case ReplayFire():
            return FireCommand(tank_id=command.tank_id)
        case ReplayRespawn():
            return RespawnCommand(slot=command.slot)
        case ReplaySpawnEnemy():
            return SpawnEnemyCommand(
                cell=GridPos(command.cell_x, command.cell_y),
                variant=_variant(command.variant),
                facing=DIRECTIONS[command.facing],
            )
        case ReplaySpawnPowerup():
            return SpawnPowerupCommand(
                cell=GridPos(command.cell_x, command.cell_y), kind=_powerup(command.powerup)
            )
        case ReplayDespawnPowerup():
            return DespawnPowerupCommand(powerup_id=command.powerup_id)


def simulation_commands(commands: Sequence[ReplayCommand]) -> tuple[Command, ...]:
    """Read a whole recorded tick back into simulation commands."""
    return tuple(simulation_command(command) for command in commands)


def _variant(code: int) -> TankVariant:
    try:
        return TankVariant(code)
    except ValueError as error:
        raise ReplayUnplayableError(f"replay names tank variant code {code}") from error


def _powerup(code: int) -> PowerupKind:
    try:
        return PowerupKind(code)
    except ValueError as error:
        raise ReplayUnplayableError(f"replay names powerup code {code}") from error


# -- recording -----------------------------------------------------------------------


def effective_seed(seed: int) -> int:
    """The 64 unsigned bits a seed actually reaches the simulation as.

    :class:`~battle_city_server.config.SessionConfig` accepts any integer seed and so
    does :class:`~battle_city_server.lobby.LobbyConfig`, because the simulation does:
    :meth:`battle_city_sim.Rng.from_seed` folds whatever it is given into 64 unsigned
    bits, and Python's ``&`` gives the low 64 bits of the two's-complement
    representation for a negative number. A seed of ``-1``, a seed of ``2**64 - 1`` and
    a seed of ``2**70 - 1`` therefore do not merely behave alike — they produce the
    *same* generator, and so the same run, bit for bit.

    A replay records that folded value rather than the integer somebody typed. The
    document's ``seed`` field is bounded at :data:`~battle_city_protocol.MAX_SEED`,
    which is the same 64 bits, because a replay records what the run was rather than
    how it was configured; and the alternative — refusing to record a session the
    server is perfectly happy to run — loses the recording over a difference that has
    no effect on anything. The local campaign save takes the same view and stores its
    seed the same way, for the same reason.

    This is a *normalisation*, not a repair. It happens where a run is described, never
    where a document is read: :func:`~battle_city_protocol.decode_replay` still refuses
    an out-of-range seed outright rather than folding it, so no stored document is ever
    silently reinterpreted.
    """
    return seed & MAX_SEED


def replay_metadata(
    config: SessionConfig,
    *,
    hash_interval: int = DEFAULT_HASH_INTERVAL,
    settings: MatchSettings | None = None,
) -> ReplayMetadata:
    """Describe ``config``'s run for a recording, without its credentials.

    Every field is read out here and the config is not kept, so there is one place to
    look to be sure a token never reaches a document: ``config.credentials`` is read for
    nothing but :attr:`~battle_city_server.config.SessionConfig.slots`, which is the slot
    numbers alone.

    The seed is recorded as :func:`effective_seed` gives it, which is the value the
    simulation runs on whatever the configuration said.
    """
    return ReplayMetadata(
        session_id=config.session_id,
        content=config.content,
        rules_digest=rules_digest(config.rules),
        seed=effective_seed(config.seed),
        slots=config.slots,
        tick_rate=config.tick_rate,
        hash_interval=hash_interval,
        state_version=CANONICAL_STATE_VERSION,
        protocol_version=PROTOCOL_VERSION,
        settings=settings,
    )


def check_recording_bounds(
    hash_interval: int, max_ticks: int, max_bytes: int = MAX_REPLAY_BYTES
) -> None:
    """Refuse a recording shape the format cannot carry, before a run depends on it.

    Separate from :class:`ReplayRecorder` so a caller that will only build a recorder
    later — a lobby accepting a recording request before the match it would record
    exists — can be refused at the point it asks rather than at the handover, where the
    only remaining options would be failing the match or dropping the recording.

    Note what is *not* checked here, because it cannot be: whether ``max_ticks`` ticks
    will fit in ``max_bytes``. That depends on how many commands each tick carries,
    which is not known until the run happens — at the format's widest tick, a quarter of
    :data:`~battle_city_protocol.MAX_REPLAY_TICKS` ticks already fills
    :data:`~battle_city_protocol.MAX_REPLAY_BYTES`. The recorder therefore holds both
    bounds while it runs and stops at whichever it reaches first.
    """
    if not 1 <= max_ticks <= MAX_REPLAY_TICKS:
        raise ValueError(f"max_ticks must be 1 to {MAX_REPLAY_TICKS}")
    if not 1 <= hash_interval <= MAX_HASH_INTERVAL:
        raise ValueError(f"hash_interval must be 1 to {MAX_HASH_INTERVAL}")
    if not 1 <= max_bytes <= MAX_REPLAY_BYTES:
        raise ValueError(f"max_bytes must be 1 to {MAX_REPLAY_BYTES}")
    checkpoints = max_ticks // hash_interval + 2
    if checkpoints > MAX_REPLAY_HASHES:
        raise ValueError(
            f"{max_ticks} ticks every {hash_interval} would need {checkpoints} "
            f"hashes, over the limit of {MAX_REPLAY_HASHES}"
        )


class ReplayRecorder:
    """Collects a bounded run of applied tick inputs and periodic canonical hashes.

    Bounded twice, because one bound does not imply the other. ``max_ticks`` bounds how
    long a run it describes; ``max_bytes`` bounds what that description encodes to, and
    a run of busy ticks reaches the second long before the first — the format's widest
    tick fills a megabyte in about a thousand ticks, a quarter of
    :data:`~battle_city_protocol.MAX_REPLAY_TICKS`. Without the byte bound a recorder
    would happily collect a document that :func:`~battle_city_protocol.encode_replay`
    then refuses, which loses the *whole* recording at the moment somebody tries to save
    it — the one moment it was wanted.

    The byte bound is held by arithmetic rather than by trial encoding: the envelope is
    measured once and each entry's exact encoded cost is added as it is appended, so a
    tick costs one small encode rather than a re-encode of everything recorded so far.
    A recorder that re-encoded its growing document every tick would make a session's
    per-tick cost grow with the length of the run.

    Reaching either bound stops the recording and marks the document ``truncated``. The
    recorder does not raise — a session whose cadence depended on a recorder having room
    would be a session a recorder could stall — and it does not drop earlier ticks to
    make room, because a replay that silently became a different replay is worse than a
    short one. Room for the closing checkpoint is reserved from the start, so a
    truncated document still verifies as far as it goes.
    """

    __slots__ = (
        "_budget",
        "_entries",
        "_envelope",
        "_hashes",
        "_last_state",
        "_max_ticks",
        "_metadata",
        "_opened",
        "_stopped",
        "_ticks",
        "_truncated",
    )

    def __init__(
        self,
        metadata: ReplayMetadata,
        *,
        max_ticks: int = MAX_REPLAY_TICKS,
        max_bytes: int = MAX_REPLAY_BYTES,
    ) -> None:
        check_recording_bounds(metadata.hash_interval, max_ticks, max_bytes)
        # Measured with ``truncated`` false, which is the longer of the two spellings,
        # so the running total is never under the document's real size.
        envelope = replay_envelope_bytes(dataclasses.replace(metadata, truncated=False))
        if envelope + 2 * CHECKPOINT_RESERVE_BYTES > max_bytes:
            raise ValueError(
                f"a document with this metadata encodes to {envelope} bytes, which leaves "
                f"no room inside {max_bytes} for the checkpoints a recording needs"
            )
        self._metadata = metadata
        self._max_ticks = max_ticks
        self._budget = max_bytes
        self._envelope = envelope
        self._entries = 0
        self._ticks: list[ReplayTick] = []
        self._hashes: list[ReplayHash] = []
        self._last_state: SimulationState | None = None
        self._stopped = False
        self._truncated = False
        self._opened = False

    @property
    def max_ticks(self) -> int:
        return self._max_ticks

    @property
    def max_bytes(self) -> int:
        return self._budget

    @property
    def encoded_bytes(self) -> int:
        """What this recording encodes to right now, including its closing checkpoint.

        Exact, and arrived at by addition rather than by encoding. The envelope is
        measured against the ``truncated`` flag the document will actually carry, which
        the running budget deliberately does not do: the budget always assumes the
        longer spelling, so it is never under. ``tests/replay`` checks this against a
        real encode, which is what keeps the arithmetic honest as the format changes.
        """
        envelope = replay_envelope_bytes(
            dataclasses.replace(self._metadata, truncated=self._truncated)
        )
        return envelope + self._entries + self._closing_cost()

    @property
    def tick_count(self) -> int:
        """Ticks recorded so far."""
        return len(self._ticks)

    @property
    def exhausted(self) -> bool:
        """Whether the recorder has stopped: at a bound, or on a tick it could not
        describe."""
        return self._stopped or len(self._ticks) >= self._max_ticks

    @property
    def truncated(self) -> bool:
        """Whether a tick was offered and refused because the bound was reached."""
        return self._truncated

    def opened(self, state: SimulationState) -> None:
        """Take the tick-zero checkpoint. Calling it twice is refused."""
        if self._opened:
            raise ValueError("this recorder has already been opened")
        self._opened = True
        self._append_hash(ReplayHash(tick=state.tick, digest=state_hash(state)))

    def recorded(self, tick_input: TickInput, state: SimulationState) -> None:
        """Record one applied tick, and its state hash when a checkpoint is due.

        This runs inside a session's tick, so it does not raise for anything but a
        caller error. A tick the format cannot carry — more commands in one tick than
        :data:`~battle_city_protocol.MAX_COMMANDS_PER_TICK` — stops the recording and
        marks it truncated, exactly as reaching the tick bound does. A recording that
        ends early is a recording; a session that stopped because its recorder could
        not describe a tick would be a game lost to an observer.
        """
        if not self._opened:
            raise ValueError("a recorder must be opened before it records a tick")
        if self.exhausted:
            self._truncated = True
            return
        try:
            entry = ReplayTick(tick=tick_input.tick, commands=replay_commands(tick_input.commands))
        except ReplayError:
            self._truncated = True
            self._stopped = True
            return

        checkpoint_due = state.tick % self._metadata.hash_interval == 0
        # The checkpoint this tick may add is charged at its widest, and the closing one
        # is charged on top, so the invariant "the closing checkpoint still fits" holds
        # after every accepted tick without this having to encode either of them yet.
        pending = self._entry_cost(entry, self._ticks) + CHECKPOINT_RESERVE_BYTES
        if checkpoint_due:
            pending += CHECKPOINT_RESERVE_BYTES
        if self._envelope + self._entries + pending > self._budget:
            self._truncated = True
            self._stopped = True
            return

        self._entries += self._entry_cost(entry, self._ticks)
        self._ticks.append(entry)
        self._last_state = state
        if checkpoint_due:
            self._append_hash(ReplayHash(tick=state.tick, digest=state_hash(state)))

    def _append_hash(self, entry: ReplayHash) -> None:
        self._entries += self._entry_cost(entry, self._hashes)
        self._hashes.append(entry)

    @staticmethod
    def _entry_cost(entry: ReplayTick | ReplayHash, array: Sequence[object]) -> int:
        """What appending ``entry`` to ``array`` adds to the encoded document."""
        separator = REPLAY_ARRAY_SEPARATOR_BYTES if array else 0
        return replay_entry_bytes(entry) + separator

    def _closing_cost(self) -> int:
        """What :meth:`document` will add for the closing checkpoint, exactly."""
        final = self._last_state
        if final is None or (self._hashes and self._hashes[-1].tick >= final.tick):
            return 0
        entry = ReplayHash(tick=final.tick, digest=state_hash(final))
        return self._entry_cost(entry, self._hashes)

    def document(self) -> ReplayDocument:
        """The recording so far, as a document ready to encode.

        A checkpoint for the final state is added here if the interval did not already
        land on it, so the hash chain always closes: a document whose last recorded tick
        is unverified by any hash is a document whose last commands nobody checked. A
        partial or truncated recording gets one too, for its own last tick. The final
        hash is never a duplicate — the periodic hashes are ascending, so it is added
        only when the last one is for an earlier tick.
        """
        hashes = list(self._hashes)
        final = self._last_state
        if final is not None and (not hashes or hashes[-1].tick < final.tick):
            hashes.append(ReplayHash(tick=final.tick, digest=state_hash(final)))
        return ReplayDocument(
            metadata=dataclasses.replace(self._metadata, truncated=self._truncated),
            ticks=tuple(self._ticks),
            hashes=tuple(hashes),
        )


# -- playback ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayContext:
    """What a verifier rebuilds from content in order to replay a recording.

    Deliberately not a :class:`~battle_city_server.config.SessionConfig`: that record
    carries per-slot membership tokens, and verifying a replay needs a board and a
    ruleset, not a credential. The seed, the slots and the tick rate come from the
    document.
    """

    stage: Stage
    content: ContentRef
    rules: Rules = DEFAULT_RULES


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayMismatch:
    """The first tick whose replayed state did not match the recorded one."""

    tick: int
    expected: str
    found: str

    def __str__(self) -> str:
        return f"tick {self.tick}: recorded {self.expected}, replayed {self.found}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayPlayback:
    """What replaying a document produced."""

    state: SimulationState
    ticks_played: int
    hashes_checked: int
    hashes_recorded: int
    missing_checkpoints: tuple[int, ...] = ()
    mismatch: ReplayMismatch | None = None

    @property
    def verified(self) -> bool:
        """Whether this document is internally consistent with the simulation.

        Four conditions, and all four are load-bearing:

        * **At least one tick replayed.** A document that records no ticks records no
          run, and an opening hash of a state nothing was applied to is not evidence of
          anything. It is a legitimate document — a recorder opened and never used — but
          it is not a verified recording of a game.

        * **No mismatch.** Every checkpoint the document carries agreed.
        * **No missing checkpoint.** Every tick :func:`required_checkpoints` says the
          document's own ``hash_interval`` commits it to — including one for the state
          after its last tick — is present. Without this, a document could drop every
          checkpoint but the first, rewrite all the commands after it, and be "verified"
          on the strength of a hash taken before any of them ran.
        * **No unchecked checkpoint.** Every hash in the document was reached, so a
          claim about a tick the stream never gets to cannot ride along unexamined.

        What this is *not* is authenticity. The hash chain proves the recorded inputs
        reproduce the recorded states under this build's rules; it says nothing about
        who recorded them. Nothing here is signed and nothing here is a secret, so a
        document that verifies is a document that is self-consistent — anyone can write
        one. Attributing a replay to a server needs a signature, which needs a key, which
        is an operational decision this release does not make.
        """
        return (
            self.mismatch is None
            and self.ticks_played > 0
            and not self.missing_checkpoints
            and self.hashes_checked == self.hashes_recorded
        )


def required_checkpoints(document: ReplayDocument) -> tuple[int, ...]:
    """The ticks a document's own metadata commits it to carrying a hash for.

    A document states its ``hash_interval``. That is a promise about how often the run
    was fingerprinted, and a verifier that did not hold the document to it would accept
    a recording with one hash at tick zero and any commands at all after it. The set is
    therefore derived from the document rather than read out of it: the tick the stream
    opens on, every multiple of the interval it passes through, and the state after its
    last tick, which is the one no interval is guaranteed to land on.
    """
    if not document.ticks:
        return (document.first_tick,)
    start = document.ticks[0].tick
    end = document.ticks[-1].tick + 1
    interval = document.metadata.hash_interval
    required = {start, end}
    required.update(tick for tick in range(start + 1, end + 1) if tick % interval == 0)
    return tuple(sorted(required))


def play_replay(document: ReplayDocument, context: ReplayContext) -> ReplayPlayback:
    """Replay ``document`` against a rebuilt ``context`` and report what happened.

    Every compatibility check runs before the first tick, so an incompatible document
    costs nothing and fails with a reason. Playback then stops at the first recorded hash
    that disagrees and reports it; a document that cannot be applied at all raises.

    A document missing a checkpoint its own interval commits it to is still replayed —
    it may simply be a partial recording — but it is reported as unverified with the
    missing ticks named, rather than being refused or quietly passed.
    """
    _require_compatible(document, context)
    metadata = document.metadata
    state = new_game(
        context.stage, seed=metadata.seed, rules=context.rules, player_slots=metadata.slots
    )

    recorded = len(document.hashes)
    present = {entry.tick for entry in document.hashes}
    missing = tuple(tick for tick in required_checkpoints(document) if tick not in present)
    checked = 0
    mismatch = _checkpoint(document, state)
    if mismatch is not None:
        return ReplayPlayback(
            state=state,
            ticks_played=0,
            hashes_checked=0,
            hashes_recorded=recorded,
            missing_checkpoints=missing,
            mismatch=mismatch,
        )
    if document.hash_at(state.tick) is not None:
        checked += 1

    played = 0
    for entry in document.ticks:
        if entry.tick != state.tick:
            raise ReplayUnplayableError(
                f"replay tick {entry.tick} does not follow state tick {state.tick}"
            )
        tick_input = TickInput(tick=entry.tick, commands=simulation_commands(entry.commands))
        try:
            result = step(state, tick_input, context.rules)
        except InvalidInputError as error:
            raise ReplayUnplayableError(
                f"replay tick {entry.tick} was refused by the rules engine: {error}"
            ) from error
        state = result.state
        played += 1
        mismatch = _checkpoint(document, state)
        if mismatch is not None:
            return ReplayPlayback(
                state=state,
                ticks_played=played,
                hashes_checked=checked,
                hashes_recorded=recorded,
                missing_checkpoints=missing,
                mismatch=mismatch,
            )
        if document.hash_at(state.tick) is not None:
            checked += 1

    return ReplayPlayback(
        state=state,
        ticks_played=played,
        hashes_checked=checked,
        hashes_recorded=recorded,
        missing_checkpoints=missing,
    )


def _checkpoint(document: ReplayDocument, state: SimulationState) -> ReplayMismatch | None:
    """Compare the recorded hash for ``state``'s tick, when there is one."""
    recorded = document.hash_at(state.tick)
    if recorded is None:
        return None
    found = state_hash(state)
    if found == recorded:
        return None
    return ReplayMismatch(tick=state.tick, expected=recorded, found=found)


def _require_compatible(document: ReplayDocument, context: ReplayContext) -> None:
    """Refuse a document this build, this ruleset or this content cannot reproduce."""
    if document.replay_version != REPLAY_VERSION:
        raise ReplayIncompatibleError("replay_version", REPLAY_VERSION, document.replay_version)
    metadata = document.metadata
    if metadata.protocol_version != PROTOCOL_VERSION:
        raise ReplayIncompatibleError(
            "protocol_version", PROTOCOL_VERSION, metadata.protocol_version
        )
    if metadata.state_version != CANONICAL_STATE_VERSION:
        raise ReplayIncompatibleError(
            "state_version", CANONICAL_STATE_VERSION, metadata.state_version
        )
    if metadata.content != context.content:
        raise ReplayIncompatibleError("content", context.content, metadata.content)
    expected_digest = rules_digest(context.rules)
    if metadata.rules_digest != expected_digest:
        raise ReplayIncompatibleError("rules_digest", expected_digest, metadata.rules_digest)
    settings = metadata.settings
    if settings is not None:
        if settings.content != metadata.content:
            raise ReplayIncompatibleError("settings.content", metadata.content, settings.content)
        if settings.tick_rate != metadata.tick_rate:
            raise ReplayIncompatibleError(
                "settings.tick_rate", metadata.tick_rate, settings.tick_rate
            )
    declared = {spawn.slot for spawn in context.stage.player_spawns}
    missing = sorted(set(metadata.slots) - declared)
    if missing:
        raise ReplayIncompatibleError("slots", sorted(declared), sorted(metadata.slots))
