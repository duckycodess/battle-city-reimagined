"""Shared builders for the multiplayer tests.

Two things are worth stating up front, because they shape every test in this directory.

**The stage is loaded, not invented.** The bundled classic levels each declare one
player spawn, so none of them can seat a second player: a co-op match needs a stage with
a spawn per slot, and this module writes one as a content pack and loads it through
:func:`battle_city_content.load_pack`. That means every multi-player test here runs on a
stage that passed the content schema, the content validator and the simulation's own
stage contract, rather than on a grid a test made up — and it means the co-op path is
exercised end to end, from JSON on disk to an authoritative snapshot on the wire.

**There is no pygame in here.** The client code these tests drive — the online session,
the message it offers, the snapshot it reads — imports no display library, which is why
it can be tested against a real server with nothing but an event loop. The one module
that does need a window is the screenshot tool beside these tests, which pytest does not
collect; see ``screenshots/README.md``.

There is no async test plugin in this workspace, so each scenario is an ``async def``
that a plain test runs with :func:`asyncio.run`, matching ``tests/server``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from battle_city_content import Level, Pack, load_pack
from battle_city_protocol import (
    ActionKind,
    ClientChannel,
    ClientMessage,
    ContentRef,
    DirectionCode,
    InputBatch,
    JoinRequest,
    LobbyConfigure,
    LobbyJoin,
    LobbyLeave,
    LobbyReady,
    LobbyStart,
    MatchMode,
    PlayerAction,
    ServerMessage,
    client_channel,
)
from battle_city_server import (
    LobbyConfig,
    LobbyLevel,
    LobbyTicket,
    MatchSession,
    SessionLimits,
    SessionServer,
    content_ref_for,
    loopback_pair,
    stage_from_level,
)
from battle_city_sim import Tile

SESSION_ID = "match-1"
PACK_ID = "duo-pack"
PACK_VERSION = "1.0.0"
LEVEL_ID = "duo-arena"
SECOND_LEVEL_ID = "solo-arena"
TICKETS: Mapping[int, str] = {
    1: "ticket-host-aaaaaaaa",
    2: "ticket-guest-bbbbbbb",
    3: "ticket-third-ccccccc",
}
GRID_SIZE = 16
BASE_CELL = (8, 15)
PLAYER_CELLS: Mapping[int, tuple[int, int]] = {1: (4, 10), 2: (12, 10), 3: (4, 4)}
ENEMY_CELLS: tuple[tuple[int, int], ...] = ((1, 1), (14, 1))


# -- content ------------------------------------------------------------------


def _rows() -> list[str]:
    """Sixteen rows of empty ground with one home base, as tile-code strings."""
    cells = [[Tile.EMPTY.value for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    cells[BASE_CELL[1]][BASE_CELL[0]] = Tile.HOME.value
    return ["".join(str(value) for value in row) for row in cells]


def _level_document(level_id: str, name: str, slots: Sequence[int]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": level_id,
        "name": name,
        "grid": {"width": GRID_SIZE, "height": GRID_SIZE, "rows": _rows()},
        "spawns": {
            "players": [
                {"slot": slot, "x": PLAYER_CELLS[slot][0], "y": PLAYER_CELLS[slot][1]}
                for slot in slots
            ],
            "enemies": [{"x": x, "y": y} for x, y in ENEMY_CELLS],
        },
    }


def write_pack(root: Path, *, slots: Sequence[int] = (1, 2, 3)) -> Pack:
    """Write and load a pack with a multi-spawn arena and a single-spawn one.

    The second level exists so a test can ask the lobby to choose a stage that cannot
    seat the roster and watch it refuse, which is a different failure from a stage that
    does not exist.
    """
    levels = root / "levels"
    levels.mkdir(parents=True, exist_ok=True)
    (levels / f"{LEVEL_ID}.json").write_text(
        json.dumps(_level_document(LEVEL_ID, "Duo Arena", slots)), encoding="utf-8"
    )
    (levels / f"{SECOND_LEVEL_ID}.json").write_text(
        json.dumps(_level_document(SECOND_LEVEL_ID, "Solo Arena", (1,))), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "id": PACK_ID,
        "version": PACK_VERSION,
        "name": "Multiplayer test pack",
        "content_schema_version": 1,
        "authors": ["Battle City Reimagined contributors"],
        "license": {
            "spdx_id": "NOASSERTION",
            "notice": "Original layouts written for the multiplayer tests of this rebuild.",
        },
        "levels": [
            {"id": LEVEL_ID, "path": f"levels/{LEVEL_ID}.json"},
            {"id": SECOND_LEVEL_ID, "path": f"levels/{SECOND_LEVEL_ID}.json"},
        ],
    }
    path = root / "pack.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return load_pack(path)


def lobby_levels(pack: Pack) -> tuple[LobbyLevel, ...]:
    """Map every level in ``pack`` onto the lobby's offer, through the server's adapter."""
    return tuple(
        LobbyLevel(
            level_id=level.level_id,
            stage=stage_from_level(level),
            content=content_ref_for(pack, level),
        )
        for level in pack.levels
    )


def content_ref(pack: Pack, level_id: str = LEVEL_ID) -> ContentRef:
    return content_ref_for(pack, pack.level(level_id))


def level_of(pack: Pack, level_id: str = LEVEL_ID) -> Level:
    return pack.level(level_id)


# -- server -------------------------------------------------------------------


def make_lobby_config(
    pack: Pack,
    *,
    slots: Sequence[int] = (1, 2),
    capacity: int | None = None,
    mode: MatchMode = MatchMode.COOP,
    limits: SessionLimits | None = None,
    seed: int = 7,
) -> LobbyConfig:
    return LobbyConfig(
        session_id=SESSION_ID,
        tickets=tuple(
            LobbyTicket(slot=slot, ticket=TICKETS[slot], host=slot == min(slots))
            for slot in sorted(slots)
        ),
        levels=lobby_levels(pack),
        seed=seed,
        capacity=capacity,
        mode=mode,
        limits=limits if limits is not None else SessionLimits(),
    )


def make_server(pack: Pack, **options: Any) -> tuple[SessionServer, MatchSession]:
    match = MatchSession(make_lobby_config(pack, **options))
    return SessionServer(match), match


# -- messages -----------------------------------------------------------------


def lobby_join(
    pack: Pack, slot: int, *, name: str = "player", ticket: str | None = None
) -> LobbyJoin:
    return LobbyJoin(
        session_id=SESSION_ID,
        ticket=TICKETS[slot] if ticket is None else ticket,
        display_name=name,
        content=content_ref(pack),
    )


def lobby_ready(slot: int, revision: int = 0, *, ready: bool = True) -> LobbyReady:
    return LobbyReady(session_id=SESSION_ID, slot=slot, revision=revision, ready=ready)


def lobby_start(slot: int, revision: int = 0) -> LobbyStart:
    return LobbyStart(session_id=SESSION_ID, slot=slot, revision=revision)


def lobby_leave(slot: int) -> LobbyLeave:
    return LobbyLeave(session_id=SESSION_ID, slot=slot)


def lobby_configure(
    slot: int,
    *,
    revision: int = 0,
    mode: MatchMode = MatchMode.COOP,
    level_id: str = LEVEL_ID,
    teams: tuple[Any, ...] = (),
) -> LobbyConfigure:
    return LobbyConfigure(
        session_id=SESSION_ID,
        slot=slot,
        revision=revision,
        mode=mode,
        level_id=level_id,
        teams=teams,
    )


def join_request(pack: Pack, slot: int, token: str) -> JoinRequest:
    return JoinRequest(session_id=SESSION_ID, slot=slot, token=token, content=content_ref(pack))


def move(slot: int, sequence: int, direction: DirectionCode) -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=slot,
        sequence=sequence,
        actions=(PlayerAction(kind=ActionKind.MOVE, direction=direction),),
    )


# -- loopback client ----------------------------------------------------------


@dataclass
class Client:
    """One loopback client and the server task serving it."""

    channel: ClientChannel
    task: asyncio.Task[None]

    async def send(self, message: ClientMessage) -> None:
        await self.channel.send(message)

    async def receive(self) -> ServerMessage:
        message = await self.channel.receive()
        assert message is not None, "server closed the channel"
        return message

    async def receive_until[T](self, kind: type[T], limit: int = 24) -> T:
        """Read until a message of ``kind`` arrives, so a test can skip the chatter."""
        for _ in range(limit):
            message = await self.receive()
            if isinstance(message, kind):
                return message
        raise AssertionError(f"no {kind.__name__} arrived within {limit} messages")

    async def drain(self) -> tuple[ServerMessage, ...]:
        """Take everything already queued without waiting for anything new."""
        taken: list[ServerMessage] = []
        while True:
            try:
                message = await asyncio.wait_for(self.channel.receive(), 0.05)
            except TimeoutError:
                return tuple(taken)
            if message is None:
                return tuple(taken)
            taken.append(message)

    async def close(self) -> None:
        await self.channel.close()
        await self.task


async def connect(server: SessionServer) -> Client:
    """Attach a loopback client to ``server`` and start serving it."""
    client_end, server_end = loopback_pair()
    task = asyncio.create_task(server.serve(server.channel_for(server_end)))
    await asyncio.sleep(0)
    return Client(channel=client_channel(client_end), task=task)


async def close_all(server: SessionServer, *clients: Client) -> None:
    await server.close()
    for client in clients:
        await client.close()
