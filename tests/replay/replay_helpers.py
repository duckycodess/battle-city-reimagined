"""Shared builders for the replay and spectator tests.

A test states its own stage and its own session rather than loading a bundled level, so
a failure names the rule under test rather than a content edit somewhere else. The
builders mirror ``tests/server/server_helpers`` deliberately: the two suites exercise the
same authority, and a replay test that quietly ran a different board would prove nothing
about the one the server runs.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from battle_city_protocol import (
    ActionKind,
    ContentRef,
    DirectionCode,
    InputBatch,
    JoinRequest,
    LobbyJoin,
    LobbyReady,
    LobbyStart,
    MatchMode,
    MatchSettings,
    PlayerAction,
    ReplayDocument,
    ReplayMetadata,
    ReplaySpawnEnemy,
    ReplayTick,
    encode_replay,
)
from battle_city_server import (
    GameSession,
    LobbyConfig,
    LobbyLevel,
    LobbyTicket,
    MatchSession,
    PlayerCredential,
    ReplayContext,
    ReplayRecorder,
    SessionConfig,
    SessionLimits,
    replay_metadata,
    rules_digest,
)
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    DEFAULT_RULES,
    GridPos,
    PlayerSpawn,
    Stage,
    Tile,
)

SESSION_ID = "replay-session"
TOKENS: Mapping[int, str] = {1: "token-one-aaaaaaaa", 2: "token-two-bbbbbbbb"}
GRID_SIZE = 16
BASE_CELL = GridPos(8, 15)
SPAWN_ONE = GridPos(4, 10)
SPAWN_TWO = GridPos(12, 10)
ENEMY_CELL = GridPos(2, 1)
SEED = 7


def content_ref() -> ContentRef:
    return ContentRef(
        pack_id="classic",
        pack_version="1.0.0",
        level_id="replay-stage",
        content_schema_version=1,
    )


def build_rows() -> tuple[str, ...]:
    """Return 16 tile-code rows: empty ground with one home base."""
    cells = [[Tile.EMPTY for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[BASE_CELL.y][BASE_CELL.x] = Tile.HOME
    return tuple("".join(str(tile.value) for tile in row) for row in cells)


def make_stage(*, slots: Sequence[int] = (1, 2)) -> Stage:
    cells = {1: SPAWN_ONE, 2: SPAWN_TWO}
    return Stage.create(
        stage_id="replay-stage",
        name="Replay Stage",
        rows=build_rows(),
        player_spawns=[PlayerSpawn(slot=slot, cell=cells[slot]) for slot in slots],
        enemy_spawns=[ENEMY_CELL],
    )


def make_config(
    *,
    slots: Sequence[int] = (1, 2),
    limits: SessionLimits | None = None,
    seed: int = SEED,
) -> SessionConfig:
    return SessionConfig(
        session_id=SESSION_ID,
        stage=make_stage(slots=slots),
        content=content_ref(),
        credentials=tuple(
            PlayerCredential(slot=slot, token=TOKENS[slot]) for slot in sorted(slots)
        ),
        seed=seed,
        tick_rate=60,
        limits=limits if limits is not None else SessionLimits(),
    )


def make_settings() -> MatchSettings:
    """A co-op configuration, the one this build will actually start."""
    return MatchSettings(
        mode=MatchMode.COOP,
        level_id="replay-stage",
        content=content_ref(),
        tick_rate=60,
        max_players=2,
        cheats_enabled=False,
    )


def make_context(*, slots: Sequence[int] = (1, 2)) -> ReplayContext:
    """What a verifier rebuilds: a board and a ruleset, and never a credential."""
    return ReplayContext(stage=make_stage(slots=slots), content=content_ref())


def recording_session(
    *,
    slots: Sequence[int] = (1, 2),
    hash_interval: int = 2,
    max_ticks: int = 64,
    seed: int = SEED,
) -> tuple[GameSession, ReplayRecorder]:
    """A session with a recorder attached from tick zero."""
    config = make_config(slots=slots, seed=seed)
    session = GameSession(config)
    recorder = ReplayRecorder(
        replay_metadata(config, hash_interval=hash_interval, settings=make_settings()),
        max_ticks=max_ticks,
    )
    session.attach_recorder(recorder)
    return session, recorder


def make_metadata(
    *,
    hash_interval: int = 2,
    **overrides: Any,
) -> ReplayMetadata:
    """A metadata record a test can break exactly one field of."""
    base = ReplayMetadata(
        session_id=SESSION_ID,
        content=content_ref(),
        rules_digest=rules_digest(DEFAULT_RULES),
        seed=SEED,
        slots=(1, 2),
        tick_rate=60,
        hash_interval=hash_interval,
        state_version=CANONICAL_STATE_VERSION,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def minimal_document() -> ReplayDocument:
    """A valid document with one tick, used as the thing a test then breaks."""
    return ReplayDocument(
        metadata=make_metadata(),
        ticks=(
            ReplayTick(
                tick=0,
                commands=(
                    ReplaySpawnEnemy(
                        cell_x=ENEMY_CELL.x,
                        cell_y=ENEMY_CELL.y,
                        variant=2,
                        facing=DirectionCode.DOWN,
                    ),
                ),
            ),
        ),
    )


def as_json(document: ReplayDocument) -> dict[str, Any]:
    """The document's own encoding, decoded back into plain JSON for tampering."""
    loaded: dict[str, Any] = json.loads(encode_replay(document).decode("utf-8"))
    return loaded


def to_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


def join_request(slot: int = 1) -> JoinRequest:
    return JoinRequest(session_id=SESSION_ID, slot=slot, token=TOKENS[slot], content=content_ref())


def move(slot: int, sequence: int, direction: DirectionCode = DirectionCode.UP) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        actions=(PlayerAction(kind=ActionKind.MOVE, direction=direction),),
    )


def fire(slot: int, sequence: int) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        actions=(PlayerAction(kind=ActionKind.FIRE),),
    )


TICKETS: Mapping[int, str] = {1: "ticket-one-aaaaaaaa", 2: "ticket-two-bbbbbbbb"}


def make_lobby_config(*, slots: Sequence[int] = (1, 2), seed: int = SEED) -> LobbyConfig:
    """A lobby that offers one level and seats the same slots the session tests use."""
    stage = make_stage(slots=slots)
    return LobbyConfig(
        session_id=SESSION_ID,
        tickets=tuple(
            LobbyTicket(slot=slot, ticket=TICKETS[slot], host=slot == min(slots))
            for slot in sorted(slots)
        ),
        levels=(LobbyLevel(level_id="replay-stage", stage=stage, content=content_ref()),),
        seed=seed,
        tick_rate=60,
    )


def started_match(
    *,
    slots: Sequence[int] = (1, 2),
    seed: int = SEED,
    arrange: Callable[[MatchSession], None] | None = None,
) -> MatchSession:
    """A lobby driven all the way to the handover, with ``arrange`` run before start."""
    match = MatchSession(make_lobby_config(slots=slots, seed=seed))
    connections = {}
    for slot in sorted(slots):
        connection = match.connect()
        connections[slot] = connection
        match.handle(
            connection,
            LobbyJoin(
                session_id=SESSION_ID,
                ticket=TICKETS[slot],
                display_name=f"player{slot}",
                content=content_ref(),
            ),
        )
    if arrange is not None:
        arrange(match)
    revision = match.lobby.revision
    for slot in sorted(slots):
        match.handle(
            connections[slot],
            LobbyReady(session_id=SESSION_ID, slot=slot, revision=revision, ready=True),
        )
    host = min(slots)
    match.handle(connections[host], LobbyStart(session_id=SESSION_ID, slot=host, revision=revision))
    return match
