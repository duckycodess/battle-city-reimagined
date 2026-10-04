"""Seeds a server accepts but a document could not have carried.

:class:`~battle_city_server.SessionConfig` and
:class:`~battle_city_server.LobbyConfig` accept any integer seed, because the simulation
does: :meth:`battle_city_sim.Rng.from_seed` folds whatever it is handed into 64 unsigned
bits. The replay document's ``seed`` field is bounded at those same 64 bits, so a session
seeded with ``-1`` or with something wider would be a session the server runs happily and
the recorder refuses to describe — losing the recording over a difference that changes
nothing about the run.
"""

from __future__ import annotations

import pytest
from battle_city_protocol import MAX_SEED, encode_replay
from battle_city_server import effective_seed, play_replay, replay_metadata, rules_digest
from battle_city_sim import DEFAULT_RULES, Rng, new_game, state_hash
from replay_helpers import (
    TICKETS,
    TOKENS,
    make_config,
    make_context,
    make_stage,
    recording_session,
    started_match,
)

AWKWARD_SEEDS = [
    -1,
    -(2**63),
    -(2**70) - 7,
    2**64,
    2**64 - 1,
    2**70 + 3,
    0,
    7,
]


# -- the fold is the simulation's own ------------------------------------------------


@pytest.mark.parametrize("seed", AWKWARD_SEEDS)
def test_the_recorded_seed_is_the_one_the_generator_actually_runs_on(seed: int) -> None:
    """Not "close enough": the same generator, which is the same run bit for bit."""
    folded = effective_seed(seed)
    assert 0 <= folded <= MAX_SEED
    assert Rng.from_seed(seed) == Rng.from_seed(folded)


@pytest.mark.parametrize("seed", AWKWARD_SEEDS)
def test_a_folded_seed_opens_the_very_same_state(seed: int) -> None:
    stage = make_stage()
    raw = new_game(stage, seed=seed, player_slots=(1, 2))
    folded = new_game(stage, seed=effective_seed(seed), player_slots=(1, 2))
    assert state_hash(raw) == state_hash(folded)


def test_folding_is_idempotent() -> None:
    for seed in AWKWARD_SEEDS:
        assert effective_seed(effective_seed(seed)) == effective_seed(seed)


def test_an_in_range_seed_is_recorded_untouched() -> None:
    assert effective_seed(7) == 7
    assert effective_seed(MAX_SEED) == MAX_SEED
    assert effective_seed(0) == 0


# -- recording -----------------------------------------------------------------------


@pytest.mark.parametrize("seed", AWKWARD_SEEDS)
def test_a_session_with_any_integer_seed_can_be_described(seed: int) -> None:
    metadata = replay_metadata(make_config(seed=seed))
    assert metadata.seed == effective_seed(seed)
    assert metadata.rules_digest == rules_digest(DEFAULT_RULES)


@pytest.mark.parametrize("seed", [-1, -(2**70) - 7, 2**70 + 3])
def test_a_run_on_an_out_of_range_seed_records_and_replays(seed: int) -> None:
    session, recorder = recording_session(seed=seed, hash_interval=2)
    for _ in range(5):
        session.advance_tick()
    document = recorder.document()
    assert document.metadata.seed == effective_seed(seed)

    playback = play_replay(document, make_context())
    assert playback.verified
    assert state_hash(playback.state) == state_hash(session.state)


@pytest.mark.parametrize("seed", [-1, 2**70 + 3])
def test_such_a_recording_survives_a_round_trip_through_bytes(seed: int) -> None:
    from battle_city_protocol import decode_replay

    session, recorder = recording_session(seed=seed, hash_interval=2)
    for _ in range(4):
        session.advance_tick()
    document = decode_replay(encode_replay(recorder.document()))
    playback = play_replay(document, make_context())
    assert playback.verified
    assert state_hash(playback.state) == state_hash(session.state)


def test_two_seeds_that_fold_together_record_the_same_run() -> None:
    """``-1`` and ``2**64 - 1`` are one seed as far as the simulation is concerned."""
    first, first_recorder = recording_session(seed=-1, hash_interval=2)
    second, second_recorder = recording_session(seed=MAX_SEED, hash_interval=2)
    for _ in range(4):
        first.advance_tick()
        second.advance_tick()
    assert first_recorder.document() == second_recorder.document()
    assert state_hash(first.state) == state_hash(second.state)


# -- the lobby recording this finding was about --------------------------------------


@pytest.mark.parametrize("seed", [-1, -(2**70) - 7, 2**70 + 3])
def test_a_lobby_match_on_such_a_seed_is_still_recorded(seed: int) -> None:
    """The dropped recording: the lobby starts, so the recorder must not refuse."""
    match = started_match(
        seed=seed, arrange=lambda session: session.record_replay(hash_interval=2, max_ticks=64)
    )
    assert match.recording_notice == ""
    assert match.recorder is not None
    assert match.game is not None
    for _ in range(4):
        match.advance_tick()
    document = match.recorder.document()
    assert document.metadata.seed == effective_seed(seed)
    playback = play_replay(document, make_context())
    assert playback.verified
    assert state_hash(playback.state) == state_hash(match.state)


# -- normalising a seed is not an excuse to normalise a document ---------------------


def test_a_stored_document_with_an_out_of_range_seed_is_still_refused() -> None:
    """The fold happens where a run is described, never where a document is read."""
    import json

    from battle_city_protocol import ReplayError, ReplayRejection, decode_replay

    session, recorder = recording_session(seed=-1)
    session.advance_tick()
    body = json.loads(encode_replay(recorder.document()).decode("utf-8"))
    body["metadata"]["seed"] = -1
    with pytest.raises(ReplayError) as error:
        decode_replay(json.dumps(body).encode("utf-8"))
    assert error.value.code is ReplayRejection.INVALID_FIELD


# -- and none of it leaks a credential -----------------------------------------------


@pytest.mark.parametrize("seed", [-1, 2**70 + 3])
def test_no_credential_reaches_a_recording_made_on_such_a_seed(seed: int) -> None:
    session, recorder = recording_session(seed=seed)
    connection = session.connect()
    from replay_helpers import join_request

    session.handle(connection, join_request(1))
    session.advance_tick()
    payload = encode_replay(recorder.document())
    for token in TOKENS.values():
        assert token.encode("utf-8") not in payload
    assert b"token" not in payload


@pytest.mark.parametrize("seed", [-1, 2**70 + 3])
def test_no_lobby_ticket_reaches_a_recording_made_on_such_a_seed(seed: int) -> None:
    match = started_match(seed=seed, arrange=lambda session: session.record_replay())
    assert match.recorder is not None
    match.advance_tick()
    payload = encode_replay(match.recorder.document())
    for ticket in TICKETS.values():
        assert ticket.encode("utf-8") not in payload
    assert b"ticket" not in payload
    assert b"token" not in payload
