"""The authority: membership, ownership, sequencing, tick ordering and no-stall.

These tests drive :class:`~battle_city_server.GameSession` directly. It has no sockets
and no clock, so a scenario here is a list of messages and a count of ticks, and a
failure names the rule rather than a race.
"""

from __future__ import annotations

import dataclasses

import pytest
from battle_city_protocol import (
    ActionKind,
    ContentRef,
    DirectionCode,
    EventKind,
    InputAccepted,
    InputBatch,
    JoinAccepted,
    PlayerAction,
    Rejected,
    RejectionCode,
    SessionClosed,
    StateSnapshot,
    TickEvents,
)
from battle_city_server import GameSession, Reply, SessionLimits, rules_digest
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    DEFAULT_RULES,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    SpawnEnemyCommand,
    TankVariant,
    TickInput,
    state_hash,
    step,
)
from server_helpers import (
    SESSION_ID,
    TOKENS,
    content_ref,
    join_request,
    make_config,
    move,
    respawn,
)


def messages_of[T](replies: tuple[Reply, ...], kind: type[T]) -> tuple[T, ...]:
    return tuple(reply.message for reply in replies if isinstance(reply.message, kind))


def only_rejection(replies: tuple[Reply, ...]) -> Rejected:
    rejections = messages_of(replies, Rejected)
    assert len(rejections) == 1, f"expected one rejection, got {len(rejections)}"
    return rejections[0]


def new_session(**kwargs: object) -> GameSession:
    return GameSession(make_config(**kwargs))  # type: ignore[arg-type]


def joined(session: GameSession, slot: int = 1) -> int:
    connection = session.connect()
    replies = session.handle(connection, join_request(slot))
    assert isinstance(replies[0].message, JoinAccepted)
    return connection


# --- joining -----------------------------------------------------------------


def test_join_states_the_tick_rate_content_rules_and_both_versions() -> None:
    session = GameSession(make_config())
    connection = session.connect()
    replies = session.handle(connection, join_request(1))

    accepted = replies[0].message
    assert isinstance(accepted, JoinAccepted)
    assert accepted.tick == 0
    assert accepted.tick_rate == 60
    assert accepted.session.content == content_ref()
    assert accepted.session.rules_digest == rules_digest(DEFAULT_RULES)
    assert accepted.session.state_version == CANONICAL_STATE_VERSION
    assert accepted.session.keyframe_interval == SessionLimits().keyframe_interval


def test_join_is_followed_by_a_keyframe_carrying_the_terrain() -> None:
    session = GameSession(make_config())
    connection = session.connect()
    replies = session.handle(connection, join_request(1))

    snapshot = replies[1].message
    assert isinstance(snapshot, StateSnapshot)
    assert snapshot.keyframe
    assert snapshot.grid is not None
    assert len(snapshot.grid) == 16
    assert snapshot.tick == 0
    assert snapshot.tick_rate == 60
    assert snapshot.state_hash == state_hash(session.state)


def test_a_wrong_session_identifier_is_refused() -> None:
    session = GameSession(make_config())
    connection = session.connect()
    request = dataclasses.replace(join_request(1), session_id="other-session")
    assert only_rejection(session.handle(connection, request)).code is RejectionCode.UNKNOWN_SESSION


def test_an_unconfigured_slot_is_refused() -> None:
    session = GameSession(make_config(slots=(1,)))
    connection = session.connect()
    request = dataclasses.replace(join_request(1), slot=2, token=TOKENS[2])
    assert only_rejection(session.handle(connection, request)).code is RejectionCode.UNKNOWN_SLOT


def test_a_wrong_token_is_refused_without_quoting_it() -> None:
    session = GameSession(make_config())
    connection = session.connect()
    secret = "wrong-token-aaaaaaaa"
    rejection = only_rejection(session.handle(connection, join_request(1, token=secret)))
    assert rejection.code is RejectionCode.INVALID_TOKEN
    assert secret not in rejection.detail
    assert TOKENS[1] not in rejection.detail


MISMATCHED_CONTENT = [
    ("pack_id", dataclasses.replace(content_ref(), pack_id="other-pack")),
    ("pack_version", dataclasses.replace(content_ref(), pack_version="2.0.0")),
    ("level_id", dataclasses.replace(content_ref(), level_id="other-level")),
    ("content_schema_version", dataclasses.replace(content_ref(), content_schema_version=99)),
]


@pytest.mark.parametrize(
    ("field", "claimed"), MISMATCHED_CONTENT, ids=lambda value: str(value)[:24]
)
def test_every_part_of_the_content_reference_must_agree(field: str, claimed: ContentRef) -> None:
    session = GameSession(make_config())
    connection = session.connect()
    request = dataclasses.replace(join_request(1), content=claimed)

    rejection = only_rejection(session.handle(connection, request))
    assert rejection.code is RejectionCode.CONTENT_MISMATCH
    assert field in rejection.detail


def test_an_occupied_slot_is_refused() -> None:
    session = GameSession(make_config())
    joined(session, 1)
    second = session.connect()
    assert (
        only_rejection(session.handle(second, join_request(1))).code is RejectionCode.SLOT_OCCUPIED
    )


def test_one_connection_may_not_hold_two_slots() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    assert (
        only_rejection(session.handle(connection, join_request(2))).code
        is RejectionCode.ALREADY_JOINED
    )


def test_a_departed_slot_cannot_be_rejoined() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.disconnect(connection)

    reconnect = session.connect()
    rejection = only_rejection(session.handle(reconnect, join_request(1)))
    assert rejection.code is RejectionCode.MEMBERSHIP_REVOKED


def test_an_unknown_connection_is_ignored_entirely() -> None:
    session = GameSession(make_config())
    assert session.handle(999, join_request(1)) == ()


# --- ownership and sequencing ------------------------------------------------


def test_input_before_joining_is_refused() -> None:
    session = GameSession(make_config())
    connection = session.connect()
    rejection = only_rejection(session.handle(connection, move(1, 1, DirectionCode.UP)))
    assert rejection.code is RejectionCode.NOT_JOINED
    assert rejection.sequence == 1


def test_a_client_may_not_act_for_another_slot() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    rejection = only_rejection(session.handle(connection, move(2, 1, DirectionCode.UP)))
    assert rejection.code is RejectionCode.WRONG_PLAYER
    assert session.pending_ticks(2) == ()


def test_a_repeated_sequence_is_refused() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.handle(connection, move(1, 5, DirectionCode.UP))
    rejection = only_rejection(session.handle(connection, move(1, 5, DirectionCode.DOWN)))
    assert rejection.code is RejectionCode.SEQUENCE_NOT_MONOTONIC


def test_a_rewound_sequence_is_refused() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.handle(connection, move(1, 5, DirectionCode.UP))
    rejection = only_rejection(session.handle(connection, move(1, 4, DirectionCode.DOWN)))
    assert rejection.code is RejectionCode.SEQUENCE_NOT_MONOTONIC


def test_a_sequence_gap_is_accepted() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.handle(connection, move(1, 1, DirectionCode.UP))
    replies = session.handle(connection, move(1, 90, DirectionCode.DOWN, tick=1))
    assert isinstance(replies[0].message, InputAccepted)


# --- scheduling --------------------------------------------------------------


def test_an_omitted_target_tick_means_the_next_unstarted_tick() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.advance_tick()

    replies = session.handle(connection, move(1, 1, DirectionCode.UP))
    accepted = replies[0].message
    assert isinstance(accepted, InputAccepted)
    assert accepted.tick == session.tick == 1


def test_a_tick_that_has_already_run_is_refused() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.advance_tick()
    rejection = only_rejection(session.handle(connection, move(1, 1, DirectionCode.UP, tick=0)))
    assert rejection.code is RejectionCode.TICK_IN_PAST


def test_a_tick_too_far_ahead_is_refused() -> None:
    session = GameSession(make_config(limits=SessionLimits(max_tick_lead=2)))
    connection = joined(session, 1)
    rejection = only_rejection(session.handle(connection, move(1, 1, DirectionCode.UP, tick=3)))
    assert rejection.code is RejectionCode.TICK_OUT_OF_RANGE


def test_the_input_rate_is_capped_per_tick() -> None:
    session = GameSession(make_config(limits=SessionLimits(max_batches_per_tick=2)))
    connection = joined(session, 1)
    session.handle(connection, move(1, 1, DirectionCode.UP, tick=0))
    session.handle(connection, move(1, 2, DirectionCode.UP, tick=1))
    rejection = only_rejection(session.handle(connection, move(1, 3, DirectionCode.UP, tick=2)))
    assert rejection.code is RejectionCode.RATE_LIMITED


def test_the_input_rate_budget_refills_each_tick() -> None:
    session = GameSession(make_config(limits=SessionLimits(max_batches_per_tick=1)))
    connection = joined(session, 1)
    session.handle(connection, move(1, 1, DirectionCode.UP, tick=0))
    session.advance_tick()
    replies = session.handle(connection, move(1, 2, DirectionCode.UP, tick=1))
    assert isinstance(replies[0].message, InputAccepted)


def test_the_input_queue_is_bounded() -> None:
    limits = SessionLimits(max_pending_batches=2, max_batches_per_tick=8, max_tick_lead=8)
    session = GameSession(make_config(limits=limits))
    connection = joined(session, 1)
    session.handle(connection, move(1, 1, DirectionCode.UP, tick=1))
    session.handle(connection, move(1, 2, DirectionCode.UP, tick=2))
    rejection = only_rejection(session.handle(connection, move(1, 3, DirectionCode.UP, tick=3)))
    assert rejection.code is RejectionCode.QUEUE_OVERFLOW
    assert session.pending_ticks(1) == (1, 2)


def test_a_second_batch_for_a_tick_that_has_not_run_replaces_the_first() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.handle(connection, move(1, 1, DirectionCode.UP, tick=0))
    session.handle(connection, move(1, 2, DirectionCode.DOWN, tick=0))
    session.advance_tick()

    tank = session.state.tank(session.state.player(1).tank_id or 0)
    assert tank.facing is Direction.DOWN


# --- the tick ----------------------------------------------------------------


def test_moving_and_firing_on_one_tick_both_happen() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    batch = InputBatch(
        session_id=SESSION_ID,
        slot=1,
        sequence=1,
        actions=(
            PlayerAction(kind=ActionKind.MOVE, direction=DirectionCode.LEFT),
            PlayerAction(kind=ActionKind.FIRE),
        ),
    )
    session.handle(connection, batch)
    replies = session.advance_tick()

    events = messages_of(replies, TickEvents)[0].events
    kinds = {event.kind for event in events}
    assert EventKind.TANK_MOVED in kinds
    assert EventKind.PROJECTILE_FIRED in kinds
    assert len(session.state.projectiles) == 1


def test_every_tick_broadcasts_a_snapshot_to_every_joined_client() -> None:
    session = GameSession(make_config())
    joined(session, 1)
    joined(session, 2)
    replies = session.advance_tick()
    snapshots = messages_of(replies, StateSnapshot)
    assert len(snapshots) == 2
    assert snapshots[0] == snapshots[1]
    assert snapshots[0].tick == 1


def test_terrain_rides_only_on_the_keyframe() -> None:
    session = GameSession(make_config(limits=SessionLimits(keyframe_interval=3)))
    joined(session, 1)
    grids = []
    for _ in range(3):
        snapshot = messages_of(session.advance_tick(), StateSnapshot)[0]
        grids.append(snapshot.grid)
    assert grids[0] is None
    assert grids[1] is None
    assert grids[2] is not None


def test_arrival_order_does_not_change_the_tick() -> None:
    """Two clients whose packets crossed must reach the same authoritative state."""
    forward = GameSession(make_config())
    one = joined(forward, 1)
    two = joined(forward, 2)
    forward.handle(one, move(1, 1, DirectionCode.LEFT))
    forward.handle(two, move(2, 1, DirectionCode.RIGHT))
    forward.advance_tick()

    reversed_session = GameSession(make_config())
    two_first = joined(reversed_session, 2)
    one_second = joined(reversed_session, 1)
    reversed_session.handle(two_first, move(2, 1, DirectionCode.RIGHT))
    reversed_session.handle(one_second, move(1, 1, DirectionCode.LEFT))
    reversed_session.advance_tick()

    assert state_hash(forward.state) == state_hash(reversed_session.state)


def test_a_rejected_batch_leaves_the_state_untouched() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    before = state_hash(session.state)
    session.handle(connection, move(2, 1, DirectionCode.UP))
    assert state_hash(session.state) == before


def test_an_illegal_respawn_is_refused_without_stalling_the_tick() -> None:
    session = GameSession(make_config())
    one = joined(session, 1)
    two = joined(session, 2)
    session.handle(one, respawn(1, 1))
    session.handle(two, move(2, 1, DirectionCode.LEFT))

    replies = session.advance_tick()
    rejection = only_rejection(replies)
    assert rejection.code is RejectionCode.ILLEGAL_COMMAND
    assert rejection.sequence == 1
    assert session.tick == 1
    assert session.state.tank(session.state.player(2).tank_id or 0).facing is Direction.LEFT


def test_a_slot_with_no_live_tank_cannot_move() -> None:
    session = GameSession(make_config(slots=(1, 2)))
    one = joined(session, 1)
    _kill_slot_one(session)

    session.handle(one, move(1, 50, DirectionCode.UP))
    replies = session.advance_tick()
    assert only_rejection(replies).code is RejectionCode.ILLEGAL_COMMAND


def test_a_batch_the_rules_engine_refuses_runs_the_tick_empty() -> None:
    """The engine validates a tick whole. A refusal must still advance the session.

    A respawn onto a cell another tank is standing on passes this layer's checks — the
    slot really is waiting to respawn — and is then refused by the rules engine. The
    session must not freeze on it.
    """
    session = GameSession(make_config(slots=(1, 2)))
    one = joined(session, 1)
    enemy_id = _kill_slot_one(session)
    _park_enemy_on_slot_one_spawn(session, enemy_id)

    assert session.state.player(1).awaiting_respawn
    session.handle(one, respawn(1, 500))
    before = session.state
    expected = step(before, TickInput(tick=before.tick), DEFAULT_RULES).state

    replies = session.advance_tick()

    rejection = only_rejection(replies)
    assert rejection.code is RejectionCode.ILLEGAL_COMMAND
    assert rejection.sequence == 500
    assert "spawn cell is occupied" in rejection.detail
    assert session.tick == before.tick + 1
    assert state_hash(session.state) == state_hash(expected)
    assert session.state.player(1).awaiting_respawn


def test_a_finished_run_closes_the_session() -> None:
    session = GameSession(make_config(slots=(1,)))
    joined(session, 1)
    session.schedule_commands(
        session.tick,
        [
            SpawnEnemyCommand(
                cell=GridPos(8, 14), variant=TankVariant.ENEMY_NORMAL, facing=Direction.DOWN
            )
        ],
    )
    session.advance_tick()
    enemy = session.state.tanks_of(session.state.tanks[-1].faction)[-1]
    session.schedule_commands(session.tick, [FireCommand(tank_id=enemy.entity_id)])

    closed: SessionClosed | None = None
    for _ in range(20):
        replies = session.advance_tick()
        found = messages_of(replies, SessionClosed)
        if found:
            closed = found[0]
            break
    assert closed is not None
    assert session.state.base.destroyed
    assert session.closed


def test_a_closed_session_refuses_input_and_stops_ticking() -> None:
    session = GameSession(make_config())
    connection = joined(session, 1)
    session.close()
    assert only_rejection(session.handle(connection, move(1, 1, DirectionCode.UP))).code is (
        RejectionCode.SESSION_CLOSED
    )
    assert session.advance_tick() == ()


# --- disconnect ---------------------------------------------------------------


def test_a_disconnect_drops_queued_input_and_the_run_carries_on() -> None:
    session = GameSession(make_config())
    one = joined(session, 1)
    joined(session, 2)
    session.handle(one, move(1, 1, DirectionCode.LEFT, tick=0))
    assert session.pending_ticks(1) == (0,)

    session.disconnect(one)
    assert session.pending_ticks(1) == ()

    replies = session.advance_tick()
    assert session.tick == 1
    assert len(messages_of(replies, StateSnapshot)) == 1
    assert session.state.tank(session.state.player(1).tank_id or 0).facing is Direction.UP


def test_server_commands_are_refused_outside_the_scheduling_window() -> None:
    session = GameSession(make_config(limits=SessionLimits(max_tick_lead=2)))
    with pytest.raises(ValueError):
        session.schedule_commands(session.tick + 3, [])
    session.advance_tick()
    with pytest.raises(ValueError):
        session.schedule_commands(0, [])


def _kill_slot_one(session: GameSession) -> int:
    """Spawn an enemy level with slot one and have it shoot the player's tank."""
    session.schedule_commands(
        session.tick,
        [
            SpawnEnemyCommand(
                cell=GridPos(8, 10), variant=TankVariant.ENEMY_NORMAL, facing=Direction.LEFT
            )
        ],
    )
    session.advance_tick()
    enemy = session.state.tanks[-1]
    session.schedule_commands(session.tick, [FireCommand(tank_id=enemy.entity_id)])
    for _ in range(40):
        session.advance_tick()
        if session.state.player(1).awaiting_respawn:
            return enemy.entity_id
    raise AssertionError("the enemy never destroyed the player tank")


def _park_enemy_on_slot_one_spawn(session: GameSession, enemy_id: int) -> None:
    """Walk the enemy onto the slot's spawn cell so a respawn there is impossible."""
    spawn = session.state.player(1).spawn
    target_x = spawn.x * DEFAULT_RULES.tile_size
    for _ in range(80):
        if session.state.tank(enemy_id).position.x == target_x:
            return
        session.schedule_commands(
            session.tick, [MoveCommand(tank_id=enemy_id, direction=Direction.LEFT)]
        )
        session.advance_tick()
    raise AssertionError("the enemy never reached the spawn cell")
