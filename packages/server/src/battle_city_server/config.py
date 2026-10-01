"""What a session is, decided before anyone connects.

There is no lobby in this release. A session's identifier, its stage, its content, its
rules, its seed and its per-slot tokens are arranged out of band and handed to the
server as one frozen value. Joining therefore proves membership rather than creating
it, and nothing a client sends can change any of it.

Every limit a client can push against lives in :class:`SessionLimits`, named and
bounded, so a deployment can tighten one without editing session logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from battle_city_protocol import (
    MAX_PLAYERS_PER_SNAPSHOT,
    MAX_TICK_RATE,
    ContentRef,
    MessageError,
)
from battle_city_protocol.validation import require_token
from battle_city_sim import DEFAULT_RULES, Rules, Stage

DEFAULT_TICK_RATE: Final[int] = 60


class ServerConfigurationError(ValueError):
    """A session was described in a way the server cannot run."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerCredential:
    """A preconfigured slot and the token that claims it.

    The token is a secret. It is compared with :func:`hmac.compare_digest`, it is never
    logged, and it is never echoed into a rejection detail.
    """

    slot: int
    token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionLimits:
    """Every bound a connected client can push against."""

    max_batches_per_tick: int = 4
    """Input batches one client may have accepted between two ticks.

    A client that keeps up sends one. The allowance covers a client that buffers a
    couple of ticks of input and flushes them together; beyond it the extra batches are
    refused rather than queued, so an impatient client cannot buy itself more server
    work than a patient one.
    """

    max_pending_batches: int = 8
    """Future ticks one client may have input queued for."""

    max_tick_lead: int = 8
    """How far ahead of the current tick a batch may be scheduled."""

    max_outbound_messages: int = 64
    """Messages that may be waiting to be written to one client.

    Past this the client is not keeping up with a reliable stream and never will: the
    queue is dropped, the connection is told why, and it is closed. Blocking the tick
    loop on it instead would let one slow client stall the whole session.
    """

    keyframe_interval: int = 30
    """Ticks between snapshots that carry the terrain grid."""

    max_connections: int = 8
    """Connections the server will hold at once, joined or not.

    Accepting without a cap is accepting a memory and task budget set by whoever dials
    in. The cap is separate from, and larger than, the player count: a connection that
    has not joined yet is still a connection, and a slot that is being handed over
    briefly needs room for two.
    """

    max_join_attempts: int = 3
    """Failed join attempts one connection may make before it is closed.

    Without a budget, a single socket can sit there trying tokens for as long as it
    likes. Three is enough for a client correcting a typo and few enough that guessing
    costs a new connection every time, which the connection cap then bounds.
    """

    join_deadline_seconds: float = 10.0
    """Seconds a connection has to prove membership before it is closed.

    This bounds the unauthenticated population. It applies only before joining: a joined
    client that says nothing for an hour is idle, which is allowed and normal.
    """

    frame_deadline_seconds: float = 5.0
    """Seconds a frame that has started arriving has to finish arriving.

    A peer that declares a payload and then stops sending costs a reader one parked task
    for ever. The deadline runs from the first byte of a frame, so it never fires on an
    idle connection waiting between frames.
    """

    def __post_init__(self) -> None:
        for name in (
            "max_batches_per_tick",
            "max_pending_batches",
            "max_tick_lead",
            "max_outbound_messages",
            "keyframe_interval",
            "max_connections",
            "max_join_attempts",
        ):
            if getattr(self, name) <= 0:
                raise ServerConfigurationError(f"limits.{name} must be positive")
        for name in ("join_deadline_seconds", "frame_deadline_seconds"):
            if getattr(self, name) <= 0.0:
                raise ServerConfigurationError(f"limits.{name} must be positive")


DEFAULT_LIMITS: Final[SessionLimits] = SessionLimits()


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionConfig:
    """One authoritative session, described completely."""

    session_id: str
    stage: Stage
    content: ContentRef
    credentials: tuple[PlayerCredential, ...]
    seed: int
    tick_rate: int = DEFAULT_TICK_RATE
    rules: Rules = DEFAULT_RULES
    limits: SessionLimits = field(default=DEFAULT_LIMITS)

    def __post_init__(self) -> None:
        if not self.credentials:
            raise ServerConfigurationError("a session needs at least one player credential")
        if len(self.credentials) > MAX_PLAYERS_PER_SNAPSHOT:
            raise ServerConfigurationError(
                f"a session carries at most {MAX_PLAYERS_PER_SNAPSHOT} players"
            )
        if not 1 <= self.tick_rate <= MAX_TICK_RATE:
            raise ServerConfigurationError(f"tick_rate must be 1 to {MAX_TICK_RATE}")
        stage_slots = {spawn.slot for spawn in self.stage.player_spawns}
        seen: set[int] = set()
        for credential in self.credentials:
            if credential.slot in seen:
                raise ServerConfigurationError(f"duplicate credential for slot {credential.slot}")
            seen.add(credential.slot)
            if credential.slot not in stage_slots:
                raise ServerConfigurationError(
                    f"slot {credential.slot} has no spawn in stage {self.stage.stage_id}"
                )
            _require_usable_token(credential)

    @property
    def slots(self) -> tuple[int, ...]:
        """The session's player slots, ascending. This is the simulation's slot list."""
        return tuple(sorted(credential.slot for credential in self.credentials))

    def token_for(self, slot: int) -> str | None:
        for credential in self.credentials:
            if credential.slot == slot:
                return credential.token
        return None


def _require_usable_token(credential: PlayerCredential) -> None:
    """Refuse a token the protocol would refuse, naming the slot and not the token."""
    try:
        require_token("token", credential.token)
    except MessageError as error:
        raise ServerConfigurationError(
            f"slot {credential.slot} has an unusable token: {error.detail}"
        ) from error
