"""Shared builders for the protocol tests.

Tests build a valid message here and then break exactly one thing with
:func:`dataclasses.replace`, so a failure names the rule under test rather than a
constructor that drifted.
"""

from __future__ import annotations

from battle_city_protocol import (
    ActionKind,
    BaseSnapshot,
    ContentRef,
    DirectionCode,
    GameEvent,
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
    PlayerAction,
    PlayerSnapshot,
    PowerupSnapshot,
    ProjectileSnapshot,
    RejectionCode,
    SessionInfo,
    StateSnapshot,
    TankSnapshot,
    TeamAssignment,
    TickEvents,
)

SESSION_ID = "session-1"
TOKEN = "token-slot-1-abcdef"
TICKET = "ticket-slot-1-abcdef"
RULES_DIGEST = "a" * 64
STATE_HASH = "b" * 64
GRID: tuple[str, ...] = ("0" * 16,) * 15 + ("0" * 8 + "8" + "0" * 7,)


def content_ref() -> ContentRef:
    return ContentRef(
        pack_id="classic",
        pack_version="1.0.0",
        level_id="classic-01",
        content_schema_version=1,
    )


def session_info() -> SessionInfo:
    return SessionInfo(
        tick_rate=60,
        keyframe_interval=30,
        max_players=2,
        content=content_ref(),
        rules_digest=RULES_DIGEST,
        state_version=1,
    )


def join_request() -> JoinRequest:
    return JoinRequest(session_id=SESSION_ID, slot=1, token=TOKEN, content=content_ref())


def join_accepted() -> JoinAccepted:
    return JoinAccepted(session_id=SESSION_ID, slot=1, tick=0, session=session_info())


def input_batch() -> InputBatch:
    return InputBatch(
        session_id=SESSION_ID,
        slot=1,
        sequence=1,
        actions=(
            PlayerAction(kind=ActionKind.MOVE, direction=DirectionCode.UP),
            PlayerAction(kind=ActionKind.FIRE),
        ),
    )


def snapshot() -> StateSnapshot:
    return StateSnapshot(
        session_id=SESSION_ID,
        tick=7,
        tick_rate=60,
        state_version=1,
        keyframe=False,
        tanks=(
            TankSnapshot(
                entity_id=1,
                variant=0,
                x=128,
                y=160,
                facing=0,
                slot=1,
                gatling_ticks=0,
                invincible_ticks=0,
            ),
        ),
        projectiles=(
            ProjectileSnapshot(entity_id=2, owner_id=1, faction=0, x=131, y=150, direction=0),
        ),
        powerups=(PowerupSnapshot(entity_id=3, kind=1, cell_x=4, cell_y=4),),
        players=(PlayerSnapshot(slot=1, lives=3, tank_id=1, spawn_x=8, spawn_y=10),),
        base=BaseSnapshot(cell_x=8, cell_y=15, destroyed=False),
        state_hash=STATE_HASH,
    )


def keyframe() -> StateSnapshot:
    return StateSnapshot(
        session_id=SESSION_ID,
        tick=0,
        tick_rate=60,
        state_version=1,
        keyframe=True,
        grid=GRID,
        tanks=(),
        projectiles=(),
        powerups=(),
        players=(PlayerSnapshot(slot=1, lives=3, tank_id=1, spawn_x=8, spawn_y=10),),
        base=BaseSnapshot(cell_x=8, cell_y=15, destroyed=False),
        state_hash=STATE_HASH,
    )


def tick_events(*events: GameEvent, tick: int = 7) -> TickEvents:
    return TickEvents(session_id=SESSION_ID, tick=tick, events=tuple(events))


# -- lobby --------------------------------------------------------------------
#
# One builder per lobby message, so the round-trip set in ``test_protocol_codec``
# covers every member of ``MessageType`` rather than the six that existed before the
# lobby. Each one is the smallest message the constructor will accept, with the optional
# fields exercised where they change the encoding: a team preference on the join, a team
# assignment on the configure, a blocking reason on the roster.


def match_settings(mode: MatchMode = MatchMode.COOP) -> MatchSettings:
    return MatchSettings(
        mode=mode,
        level_id="classic-01",
        content=content_ref(),
        tick_rate=60,
        max_players=2,
    )


def lobby_info() -> LobbyInfo:
    return LobbyInfo(
        capacity=2,
        offered_modes=(MatchMode.COOP, MatchMode.FREE_FOR_ALL, MatchMode.TEAM_BATTLE),
        playable_modes=(MatchMode.COOP,),
        offered_levels=("classic-01", "classic-02"),
    )


def lobby_member(slot: int = 1, *, host: bool = True, team: int | None = None) -> LobbyMember:
    return LobbyMember(
        slot=slot,
        display_name=f"player-{slot}",
        ready=False,
        host=host,
        connected=True,
        team=team,
    )


def lobby_join() -> LobbyJoin:
    return LobbyJoin(
        session_id=SESSION_ID,
        ticket=TICKET,
        display_name="player-1",
        content=content_ref(),
        team=1,
    )


def lobby_configure(mode: MatchMode = MatchMode.TEAM_BATTLE) -> LobbyConfigure:
    return LobbyConfigure(
        session_id=SESSION_ID,
        slot=1,
        revision=3,
        mode=mode,
        level_id="classic-01",
        teams=(TeamAssignment(slot=1, team=1), TeamAssignment(slot=2, team=2)),
    )


def lobby_ready(*, ready: bool = True) -> LobbyReady:
    return LobbyReady(session_id=SESSION_ID, slot=2, revision=3, ready=ready)


def lobby_start() -> LobbyStart:
    return LobbyStart(session_id=SESSION_ID, slot=1, revision=3)


def lobby_leave() -> LobbyLeave:
    return LobbyLeave(session_id=SESSION_ID, slot=2)


def lobby_welcome() -> LobbyWelcome:
    return LobbyWelcome(session_id=SESSION_ID, slot=1, host=True, lobby=lobby_info())


def lobby_state(
    *, startable: bool = False, blocked: RejectionCode | None = RejectionCode.MEMBERS_NOT_READY
) -> LobbyState:
    return LobbyState(
        session_id=SESSION_ID,
        revision=3,
        settings=match_settings(),
        members=(lobby_member(1), lobby_member(2, host=False)),
        host_slot=1,
        startable=startable,
        blocked=blocked,
    )


def match_starting() -> MatchStarting:
    return MatchStarting(
        session_id=SESSION_ID,
        slot=1,
        token=TOKEN,
        session=session_info(),
        settings=match_settings(),
    )


class FakeStream:
    """An in-memory byte stream for channel tests.

    Reads come from a fixed buffer and writes accumulate, so a channel can be driven
    without a socket, an event loop policy or a port. It implements the published
    :class:`~battle_city_protocol.ByteStream` protocol; ``test_protocol_transport``
    asserts that it still satisfies it.
    """

    def __init__(self, incoming: bytes = b"", *, peer: str = "fake") -> None:
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.closed = False
        self._peer = peer

    @property
    def peer(self) -> str:
        return self._peer

    async def read_exactly(self, count: int) -> bytes:
        taken = bytes(self.incoming[:count])
        del self.incoming[:count]
        return taken

    async def write(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionResetError("stream is closed")
        self.written.extend(data)

    async def close(self) -> None:
        self.closed = True
