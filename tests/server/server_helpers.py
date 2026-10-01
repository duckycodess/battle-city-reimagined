"""Shared builders and a loopback client for the server tests.

Tests state their own stage rather than loading a bundled level, so a failure names the
rule under test rather than a content edit somewhere else. The content mapping is
exercised separately in ``test_server_content``.

There is no async test plugin in this workspace, so each scenario is an ``async def``
that a plain test runs with :func:`asyncio.run`. That is not a workaround: it keeps the
event loop's lifetime visible in the test that owns it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from battle_city_protocol import (
    FRAME_HEADER_BYTES,
    ActionKind,
    ClientChannel,
    ClientMessage,
    ContentRef,
    DirectionCode,
    InputBatch,
    JoinAccepted,
    JoinRequest,
    PlayerAction,
    ServerMessage,
    StateSnapshot,
    client_channel,
    server_channel,
)
from battle_city_server import (
    LoopbackStream,
    PlayerCredential,
    SessionConfig,
    SessionLimits,
    SessionServer,
    loopback_pair,
)
from battle_city_sim import GridPos, PlayerSpawn, Stage, Tile

SESSION_ID = "session-1"
TOKENS: Mapping[int, str] = {1: "token-one-aaaaaaaa", 2: "token-two-bbbbbbbb"}
GRID_SIZE = 16
BASE_CELL = GridPos(8, 15)
SPAWN_ONE = GridPos(4, 10)
SPAWN_TWO = GridPos(12, 10)
ENEMY_SPAWN = GridPos(2, 1)


def content_ref() -> ContentRef:
    return ContentRef(
        pack_id="classic",
        pack_version="1.0.0",
        level_id="test-stage",
        content_schema_version=1,
    )


def build_rows(overrides: Mapping[GridPos, Tile] | None = None) -> tuple[str, ...]:
    """Return 16 tile-code rows: empty ground, one home base, plus ``overrides``."""
    cells = [[Tile.EMPTY for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[BASE_CELL.y][BASE_CELL.x] = Tile.HOME
    for cell, tile in (overrides or {}).items():
        cells[cell.y][cell.x] = tile
    return tuple("".join(str(tile.value) for tile in row) for row in cells)


def make_stage(
    *,
    slots: Sequence[int] = (1, 2),
    overrides: Mapping[GridPos, Tile] | None = None,
) -> Stage:
    cells = {1: SPAWN_ONE, 2: SPAWN_TWO}
    return Stage.create(
        stage_id="test-stage",
        name="Test Stage",
        rows=build_rows(overrides),
        player_spawns=[PlayerSpawn(slot=slot, cell=cells[slot]) for slot in slots],
        enemy_spawns=[ENEMY_SPAWN],
    )


def make_config(
    *,
    slots: Sequence[int] = (1, 2),
    limits: SessionLimits | None = None,
    stage: Stage | None = None,
    seed: int = 7,
) -> SessionConfig:
    return SessionConfig(
        session_id=SESSION_ID,
        stage=stage if stage is not None else make_stage(slots=slots),
        content=content_ref(),
        credentials=tuple(
            PlayerCredential(slot=slot, token=TOKENS[slot]) for slot in sorted(slots)
        ),
        seed=seed,
        tick_rate=60,
        limits=limits if limits is not None else SessionLimits(),
    )


def join_request(slot: int = 1, *, token: str | None = None) -> JoinRequest:
    return JoinRequest(
        session_id=SESSION_ID,
        slot=slot,
        token=TOKENS[slot] if token is None else token,
        content=content_ref(),
    )


def move(
    slot: int, sequence: int, direction: DirectionCode, *, tick: int | None = None
) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        target_tick=tick,
        actions=(PlayerAction(kind=ActionKind.MOVE, direction=direction),),
    )


def fire(slot: int, sequence: int, *, tick: int | None = None) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        target_tick=tick,
        actions=(PlayerAction(kind=ActionKind.FIRE),),
    )


def respawn(slot: int, sequence: int, *, tick: int | None = None) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        target_tick=tick,
        actions=(PlayerAction(kind=ActionKind.RESPAWN),),
    )


@dataclass
class Client:
    """One loopback client and the server task serving it."""

    channel: ClientChannel
    task: asyncio.Task[None]
    stream: LoopbackStream

    async def send(self, message: ClientMessage) -> None:
        await self.channel.send(message)

    async def receive(self) -> ServerMessage:
        message = await self.channel.receive()
        assert message is not None, "server closed the channel"
        return message

    async def receive_many(self, count: int) -> list[ServerMessage]:
        return [await self.receive() for _ in range(count)]

    async def join(self, slot: int = 1) -> tuple[JoinAccepted, StateSnapshot]:
        await self.send(join_request(slot))
        accepted = await self.receive()
        snapshot = await self.receive()
        assert isinstance(accepted, JoinAccepted)
        assert isinstance(snapshot, StateSnapshot)
        return accepted, snapshot

    async def close(self) -> None:
        await self.channel.close()
        await self.task


async def connect(server: SessionServer) -> Client:
    """Attach a loopback client to ``server`` and start serving it."""
    client_end, server_end = loopback_pair()
    task = asyncio.create_task(server.serve(server_channel(server_end)))
    await asyncio.sleep(0)
    return Client(channel=client_channel(client_end), task=task, stream=client_end)


async def joined(server: SessionServer, slot: int = 1) -> Client:
    client = await connect(server)
    await client.join(slot)
    return client


class BlockedStream:
    """A byte stream that says its piece and then stops reading for ever.

    This is the one failure a reliable transport cannot talk its way out of: a peer that
    stops reading. A full kernel send buffer looks exactly like a write that never
    returns, so that is what this models. ``incoming`` lets the client get as far as
    joining before it goes quiet, which is the only way a connection is worth
    broadcasting to.
    """

    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.released = asyncio.Event()
        self.closed = False

    @property
    def peer(self) -> str:
        return "blocked"

    async def read_exactly(self, count: int) -> bytes:
        if len(self.incoming) >= count:
            taken = bytes(self.incoming[:count])
            del self.incoming[:count]
            return taken
        await self.released.wait()
        return b""

    async def write(self, data: bytes) -> None:
        await self.released.wait()
        self.written.extend(data)

    async def close(self) -> None:
        self.closed = True
        self.released.set()


def split_frames(data: bytes) -> list[bytes]:
    """Split a length-prefixed byte stream back into payloads."""
    frames: list[bytes] = []
    offset = 0
    while offset + FRAME_HEADER_BYTES <= len(data):
        length = int.from_bytes(data[offset : offset + FRAME_HEADER_BYTES], "big")
        offset += FRAME_HEADER_BYTES
        frames.append(data[offset : offset + length])
        offset += length
    return frames
