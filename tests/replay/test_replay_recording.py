"""Recording a run: what is recorded, what is bounded, and what is never recorded."""

from __future__ import annotations

import pytest
from battle_city_protocol import (
    MAX_COMMANDS_PER_TICK,
    MAX_REPLAY_TICKS,
    DirectionCode,
    ReplayCommand,
    ReplayCommandKind,
    ReplayDespawnPowerup,
    ReplayFire,
    ReplayMove,
    ReplayRespawn,
    ReplaySpawnEnemy,
    ReplaySpawnPowerup,
    command_kind_of,
    encode_replay,
)
from battle_city_server import (
    DEFAULT_HASH_INTERVAL,
    GameSession,
    ReplayRecorder,
    replay_command,
    replay_commands,
    replay_metadata,
    simulation_command,
    simulation_commands,
)
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    Command,
    DespawnPowerupCommand,
    Direction,
    FireCommand,
    GridPos,
    MoveCommand,
    PowerupKind,
    RespawnCommand,
    SimulationState,
    SpawnEnemyCommand,
    SpawnPowerupCommand,
    TankVariant,
    TickInput,
    state_hash,
)
from replay_helpers import (
    ENEMY_CELL,
    SEED,
    SESSION_ID,
    SPAWN_ONE,
    TOKENS,
    fire,
    join_request,
    make_config,
    make_settings,
    move,
    recording_session,
)

EVERY_SIMULATION_COMMAND = (
    MoveCommand(tank_id=1, direction=Direction.UP),
    FireCommand(tank_id=1),
    RespawnCommand(slot=2),
    SpawnEnemyCommand(
        cell=GridPos(2, 1), variant=TankVariant.ENEMY_SHIELDED, facing=Direction.LEFT
    ),
    SpawnPowerupCommand(cell=GridPos(3, 4), kind=PowerupKind.GATLING),
    DespawnPowerupCommand(powerup_id=9),
)


# -- the command mapping -------------------------------------------------------------


def test_every_simulation_command_maps_to_a_recorded_command() -> None:
    kinds = {command_kind_of(replay_command(command)) for command in EVERY_SIMULATION_COMMAND}
    assert kinds == set(ReplayCommandKind)


def test_every_command_survives_the_round_trip_unchanged() -> None:
    recorded = replay_commands(EVERY_SIMULATION_COMMAND)
    assert simulation_commands(recorded) == EVERY_SIMULATION_COMMAND


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (EVERY_SIMULATION_COMMAND[0], ReplayMove(tank_id=1, direction=DirectionCode.UP)),
        (EVERY_SIMULATION_COMMAND[1], ReplayFire(tank_id=1)),
        (EVERY_SIMULATION_COMMAND[2], ReplayRespawn(slot=2)),
        (
            EVERY_SIMULATION_COMMAND[3],
            ReplaySpawnEnemy(cell_x=2, cell_y=1, variant=2, facing=DirectionCode.LEFT),
        ),
        (
            EVERY_SIMULATION_COMMAND[4],
            ReplaySpawnPowerup(cell_x=3, cell_y=4, powerup=1),
        ),
        (EVERY_SIMULATION_COMMAND[5], ReplayDespawnPowerup(powerup_id=9)),
    ],
)
def test_each_command_records_the_fields_it_carries(
    command: Command, expected: ReplayCommand
) -> None:
    assert replay_command(command) == expected
    assert simulation_command(expected) == command


def test_the_recorded_order_is_the_order_the_rules_engine_was_given() -> None:
    """Spawn order is the caller's decision, so a recording must not sort it away."""
    commands = (
        SpawnEnemyCommand(cell=GridPos(2, 1), variant=TankVariant.ENEMY_NORMAL),
        SpawnEnemyCommand(cell=GridPos(5, 1), variant=TankVariant.ENEMY_SHIELDED),
    )
    assert simulation_commands(replay_commands(commands)) == commands


# -- metadata ------------------------------------------------------------------------


def test_metadata_describes_the_run_without_its_credentials() -> None:
    config = make_config()
    metadata = replay_metadata(config, settings=make_settings())
    assert metadata.session_id == SESSION_ID
    assert metadata.seed == SEED
    assert metadata.slots == (1, 2)
    assert metadata.tick_rate == 60
    assert metadata.state_version == CANONICAL_STATE_VERSION
    assert metadata.content == config.content
    assert metadata.settings is not None
    assert metadata.hash_interval == DEFAULT_HASH_INTERVAL


def test_no_token_reaches_the_encoded_document() -> None:
    session, recorder = recording_session()
    connection = session.connect()
    session.handle(connection, join_request(1))
    session.advance_tick()
    payload = encode_replay(recorder.document())
    for token in TOKENS.values():
        assert token.encode("utf-8") not in payload
    assert b"token" not in payload


# -- what is recorded ----------------------------------------------------------------


def test_the_opening_hash_is_the_tick_zero_state() -> None:
    session, recorder = recording_session()
    assert recorder.document().hash_at(0) == state_hash(session.state)


def test_hashes_are_taken_at_the_recorded_interval() -> None:
    session, recorder = recording_session(hash_interval=2)
    for _ in range(4):
        session.advance_tick()
    assert [entry.tick for entry in recorder.document().hashes] == [0, 2, 4]


def test_the_export_closes_the_chain_with_a_hash_for_the_final_state() -> None:
    """Without it, the commands after the last interval hash would go unchecked."""
    session, recorder = recording_session(hash_interval=2)
    for _ in range(5):
        session.advance_tick()
    document = recorder.document()
    assert [entry.tick for entry in document.hashes] == [0, 2, 4, 5]
    assert document.hash_at(5) == state_hash(session.state)


def test_the_final_hash_is_not_duplicated_when_the_interval_lands_on_it() -> None:
    session, recorder = recording_session(hash_interval=2)
    for _ in range(4):
        session.advance_tick()
    ticks = [entry.tick for entry in recorder.document().hashes]
    assert ticks == sorted(set(ticks))
    assert ticks[-1] == 4


def test_a_short_recording_still_closes_its_chain() -> None:
    session, recorder = recording_session(hash_interval=30)
    session.advance_tick()
    document = recorder.document()
    assert [entry.tick for entry in document.hashes] == [0, 1]
    assert document.hash_at(1) == state_hash(session.state)


def test_a_truncated_recording_closes_its_chain_at_the_tick_it_stopped_on() -> None:
    session, recorder = recording_session(hash_interval=2, max_ticks=3)
    for _ in range(6):
        session.advance_tick()
    document = recorder.document()
    assert [entry.tick for entry in document.ticks] == [0, 1, 2]
    assert [entry.tick for entry in document.hashes] == [0, 2, 3]


def test_a_recording_with_no_ticks_carries_only_its_opening_hash() -> None:
    _, recorder = recording_session()
    assert [entry.tick for entry in recorder.document().hashes] == [0]


def test_a_tick_records_the_input_that_was_applied() -> None:
    session, recorder = recording_session()
    connection = session.connect()
    session.handle(connection, join_request(1))
    session.handle(connection, move(1, sequence=1, direction=DirectionCode.LEFT))
    session.advance_tick()
    recorded = recorder.document().ticks[0]
    assert recorded.tick == 0
    assert recorded.commands == (ReplayMove(tank_id=1, direction=DirectionCode.LEFT),)


def test_a_tick_records_the_commands_the_server_issued_itself() -> None:
    """A wave the client never asked for is still part of what was applied."""
    session, recorder = recording_session()
    session.schedule_commands(
        session.tick, [SpawnEnemyCommand(cell=ENEMY_CELL, variant=TankVariant.ENEMY_NORMAL)]
    )
    session.advance_tick()
    assert recorder.document().ticks[0].commands == (
        ReplaySpawnEnemy(
            cell_x=ENEMY_CELL.x, cell_y=ENEMY_CELL.y, variant=1, facing=DirectionCode.DOWN
        ),
    )


def test_a_tick_the_session_had_to_run_empty_is_recorded_empty() -> None:
    """The recording follows the authority, not the request. See GameSession.advance_tick."""
    session, recorder = recording_session()
    connection = session.connect()
    session.handle(connection, join_request(1))
    session.handle(connection, fire(1, sequence=1))
    # A spawn on top of slot one's tank: the rules engine refuses the whole tick, so the
    # session runs it empty and the client's fire is dropped with it.
    session.schedule_commands(
        session.tick, [SpawnEnemyCommand(cell=SPAWN_ONE, variant=TankVariant.ENEMY_NORMAL)]
    )
    session.advance_tick()
    assert recorder.document().ticks[0].commands == ()


def test_every_tick_is_recorded_in_order() -> None:
    session, recorder = recording_session()
    for _ in range(4):
        session.advance_tick()
    assert [entry.tick for entry in recorder.document().ticks] == [0, 1, 2, 3]


# -- bounds --------------------------------------------------------------------------


def test_a_recorder_stops_at_its_bound_and_says_so() -> None:
    session, recorder = recording_session(max_ticks=3)
    for _ in range(6):
        session.advance_tick()
    assert recorder.exhausted
    assert recorder.truncated
    assert recorder.tick_count == 3
    document = recorder.document()
    assert len(document.ticks) == 3
    assert document.metadata.truncated is True


def test_an_unexhausted_recording_is_not_marked_truncated() -> None:
    session, recorder = recording_session(max_ticks=8)
    for _ in range(3):
        session.advance_tick()
    assert not recorder.truncated
    assert recorder.document().metadata.truncated is False


def test_a_bound_over_the_format_limit_is_refused() -> None:
    config = make_config()
    with pytest.raises(ValueError, match="max_ticks"):
        ReplayRecorder(replay_metadata(config), max_ticks=MAX_REPLAY_TICKS + 1)


def test_a_tick_the_format_cannot_carry_stops_the_recording_without_raising() -> None:
    """A recorder runs inside the tick loop, so it must never be able to end a run."""
    session, recorder = recording_session()
    crowded = TickInput(
        tick=0,
        commands=tuple(
            FireCommand(tank_id=index + 1) for index in range(MAX_COMMANDS_PER_TICK + 1)
        ),
    )
    recorder.recorded(crowded, session.state)
    assert recorder.truncated
    assert recorder.exhausted
    assert recorder.document().ticks == ()


def test_a_recorder_that_fails_is_detached_and_the_tick_still_runs() -> None:
    class BrokenRecorder:
        def opened(self, state: SimulationState) -> None:
            return None

        def recorded(self, tick_input: TickInput, state: SimulationState) -> None:
            raise RuntimeError("this recorder is broken")

    session = GameSession(make_config())
    session.attach_recorder(BrokenRecorder())
    replies = session.advance_tick()
    assert session.tick == 1
    assert replies == () or replies
    assert session.recorder is None
    session.advance_tick()
    assert session.tick == 2


def test_a_hash_interval_that_would_overflow_the_hash_bound_is_refused() -> None:
    config = make_config()
    with pytest.raises(ValueError, match="hashes"):
        ReplayRecorder(replay_metadata(config, hash_interval=1), max_ticks=MAX_REPLAY_TICKS)


# -- attaching -----------------------------------------------------------------------


def test_a_recorder_cannot_be_attached_after_the_run_has_started() -> None:
    session, _ = recording_session()
    session.advance_tick()
    other = GameSession(make_config())
    other.advance_tick()
    with pytest.raises(ValueError, match="tick 0"):
        other.attach_recorder(ReplayRecorder(replay_metadata(make_config())))


def test_a_session_takes_one_recorder() -> None:
    session, _ = recording_session()
    with pytest.raises(ValueError, match="already has a recorder"):
        session.attach_recorder(ReplayRecorder(replay_metadata(make_config())))


def test_a_recorder_refuses_to_record_before_it_is_opened() -> None:
    recorder = ReplayRecorder(replay_metadata(make_config()))
    session = GameSession(make_config())
    with pytest.raises(ValueError, match="opened"):
        recorder.recorded(TickInput(tick=0), session.state)
