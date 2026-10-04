"""The two bounds a recording is held to, and the arithmetic that holds it to them.

:data:`~battle_city_protocol.MAX_REPLAY_TICKS` and
:data:`~battle_city_protocol.MAX_REPLAY_BYTES` are independent, and a run of busy ticks
reaches the second long before the first. A recorder that only watched the tick bound
would collect a document :func:`~battle_city_protocol.encode_replay` then refuses, which
loses the whole recording at the moment somebody tries to save it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from battle_city_client.persistence import ReplayLibrary
from battle_city_protocol import (
    MAX_COMMANDS_PER_TICK,
    MAX_REPLAY_BYTES,
    MAX_REPLAY_TICKS,
    DirectionCode,
    ReplayDocument,
    ReplayHash,
    ReplayMetadata,
    ReplayMove,
    ReplaySpawnEnemy,
    ReplayTick,
    encode_replay,
    replay_entry_bytes,
    replay_envelope_bytes,
)
from battle_city_server import (
    CHECKPOINT_RESERVE_BYTES,
    GameSession,
    ReplayRecorder,
    play_replay,
    replay_metadata,
)
from battle_city_sim import (
    Command,
    Direction,
    GridPos,
    SpawnEnemyCommand,
    TankVariant,
    TickInput,
    new_game,
    state_hash,
)
from replay_helpers import SEED, make_config, make_context, make_metadata, make_stage

WIDE_COMMANDS = tuple(
    SpawnEnemyCommand(
        cell=GridPos(index, index), variant=TankVariant.ENEMY_SHIELDED, facing=Direction.RIGHT
    )
    for index in range(7)
)
"""Seven spawns a tick: the shape that overflows a megabyte inside the tick bound."""


def offer_ticks(recorder: ReplayRecorder, count: int, commands: tuple[Command, ...]) -> None:
    """Drive a recorder directly, so the run's length is the test's to choose.

    The state is advanced by hand rather than stepped: this is a test about how much a
    recorder will write down, and stepping four thousand real ticks to find out would
    be measuring the rules engine.
    """
    state = new_game(make_stage(), seed=SEED, player_slots=(1, 2))
    recorder.opened(state)
    for tick in range(count):
        state = dataclasses.replace(state, tick=tick + 1)
        recorder.recorded(TickInput(tick=tick, commands=commands), state)


# -- the accounting ------------------------------------------------------------------


def test_the_size_arithmetic_matches_a_real_encode() -> None:
    """The budget is addition, so this is the test that keeps the addition honest."""
    metadata = make_metadata()
    ticks = tuple(
        ReplayTick(
            tick=tick,
            commands=(
                ReplayMove(tank_id=1, direction=DirectionCode.UP),
                ReplaySpawnEnemy(cell_x=2, cell_y=1, variant=2, facing=DirectionCode.DOWN),
            ),
        )
        for tick in range(9)
    )
    hashes = tuple(ReplayHash(tick=tick, digest="b" * 64) for tick in (0, 3, 6, 9))
    document = ReplayDocument(metadata=metadata, ticks=ticks, hashes=hashes)
    predicted = (
        replay_envelope_bytes(metadata)
        + sum(replay_entry_bytes(entry) for entry in ticks)
        + (len(ticks) - 1)
        + sum(replay_entry_bytes(entry) for entry in hashes)
        + (len(hashes) - 1)
    )
    assert predicted == len(encode_replay(document))


def test_an_empty_envelope_is_what_a_document_with_no_entries_encodes_to() -> None:
    metadata: ReplayMetadata = make_metadata()
    empty = ReplayDocument(metadata=metadata)
    assert replay_envelope_bytes(metadata) == len(encode_replay(empty))


def test_a_recorder_predicts_its_own_encoded_size_exactly() -> None:
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=4), max_ticks=64)
    offer_ticks(recorder, 25, WIDE_COMMANDS)
    assert recorder.encoded_bytes == len(encode_replay(recorder.document()))


def test_a_truncated_recorder_predicts_its_own_encoded_size_exactly() -> None:
    """``truncated`` flips a field in the envelope, so the prediction has to follow it."""
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=4), max_ticks=5)
    offer_ticks(recorder, 20, WIDE_COMMANDS)
    assert recorder.truncated
    assert recorder.encoded_bytes == len(encode_replay(recorder.document()))


# -- the joint bound -------------------------------------------------------------------


def test_a_run_that_would_pass_a_megabyte_is_truncated_and_still_saveable() -> None:
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=30))
    offer_ticks(recorder, MAX_REPLAY_TICKS, WIDE_COMMANDS)
    document = recorder.document()

    # The run the recorder was offered would not have fitted.
    per_tick = replay_entry_bytes(document.ticks[0]) + 1
    assert MAX_REPLAY_TICKS * per_tick > MAX_REPLAY_BYTES

    assert recorder.truncated
    assert document.metadata.truncated is True
    assert 0 < len(document.ticks) < MAX_REPLAY_TICKS
    assert len(encode_replay(document)) <= MAX_REPLAY_BYTES


def test_a_truncated_oversized_run_is_saved_rather_than_lost(tmp_path: Path) -> None:
    """The failure this bound exists to prevent: the save that loses the whole run."""
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=30))
    offer_ticks(recorder, MAX_REPLAY_TICKS, WIDE_COMMANDS)
    library = ReplayLibrary(lambda: tmp_path)
    assert library.save("long-run", recorder.document()) == ""
    loaded = library.load("long-run")
    assert loaded.value == recorder.document()


def test_a_byte_truncated_recording_still_closes_its_hash_chain() -> None:
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=30))
    offer_ticks(recorder, MAX_REPLAY_TICKS, WIDE_COMMANDS)
    document = recorder.document()
    assert document.hashes[-1].tick == document.ticks[-1].tick + 1


def test_a_byte_truncated_recording_replays_deterministically() -> None:
    """Short, and verified as far as it goes: truncation must not cost verifiability."""
    session, recorder = recording_budget_session()
    for _ in range(40):
        session.advance_tick()
    document = recorder.document()
    assert recorder.truncated
    assert len(document.ticks) < 40
    playback = play_replay(document, make_context())
    assert playback.verified
    assert playback.ticks_played == len(document.ticks)
    assert state_hash(playback.state) == document.hashes[-1].digest


def recording_budget_session() -> tuple[GameSession, ReplayRecorder]:
    """A real session with a recorder given only enough bytes for a handful of ticks."""
    config = make_config()
    session = GameSession(config)
    metadata = replay_metadata(config, hash_interval=4)
    budget = (
        replay_envelope_bytes(metadata)
        + 10 * (replay_entry_bytes(ReplayTick(tick=0)) + 1)
        + 6 * CHECKPOINT_RESERVE_BYTES
    )
    recorder = ReplayRecorder(metadata, max_ticks=64, max_bytes=budget)
    session.attach_recorder(recorder)
    return session, recorder


def test_the_reserve_leaves_room_for_the_widest_closing_checkpoint() -> None:
    widest = ReplayHash(tick=2**31 - 1, digest="f" * 64)
    assert replay_entry_bytes(widest) + 1 <= CHECKPOINT_RESERVE_BYTES


def test_every_recorded_tick_fits_the_bound_whatever_the_commands() -> None:
    """The widest tick the format allows, recorded until the budget says stop."""
    widest = tuple(
        SpawnEnemyCommand(
            cell=GridPos(index % 16, index % 16),
            variant=TankVariant.ENEMY_SHIELDED,
            facing=Direction.RIGHT,
        )
        for index in range(MAX_COMMANDS_PER_TICK)
    )
    recorder = ReplayRecorder(replay_metadata(make_config(), hash_interval=30))
    offer_ticks(recorder, 400, widest)
    assert recorder.truncated
    assert len(encode_replay(recorder.document())) <= MAX_REPLAY_BYTES


# -- bounds that are refused up front --------------------------------------------------


def test_a_byte_bound_over_the_format_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        ReplayRecorder(replay_metadata(make_config()), max_bytes=MAX_REPLAY_BYTES + 1)


def test_a_byte_bound_with_no_room_for_a_checkpoint_is_refused() -> None:
    metadata = replay_metadata(make_config())
    with pytest.raises(ValueError, match="no room"):
        ReplayRecorder(metadata, max_bytes=replay_envelope_bytes(metadata) + 1)


def test_recording_stops_rather_than_raising_when_the_budget_runs_out() -> None:
    """A recorder runs inside the tick loop; a full budget is not an exception."""
    metadata = replay_metadata(make_config(), hash_interval=30)
    recorder = ReplayRecorder(
        metadata, max_bytes=replay_envelope_bytes(metadata) + 4 * CHECKPOINT_RESERVE_BYTES
    )
    offer_ticks(recorder, 50, WIDE_COMMANDS)
    assert recorder.truncated
    assert recorder.exhausted
    assert len(encode_replay(recorder.document())) <= recorder.max_bytes
