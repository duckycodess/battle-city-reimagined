"""The lobby handover: a recording that starts at tick zero, and watchers that carry over.

A recording has to begin at the first tick and a watcher that arrives after the handover
has missed the start, so both are arranged on the lobby and attached the moment the
session exists. These tests drive a real :class:`~battle_city_server.MatchSession` from
join to start, because the handover is exactly the seam where "it works on a GameSession"
stops being evidence.
"""

from __future__ import annotations

import pytest
from battle_city_protocol import (
    MAX_REPLAY_TICKS,
    MatchMode,
    RejectionCode,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TickEvents,
)
from battle_city_server import (
    DEFAULT_MAX_OBSERVERS,
    MatchSession,
    ObserverRefusedError,
    TooManyObserversError,
    play_replay,
)
from battle_city_sim import state_hash
from replay_helpers import (
    SEED,
    SESSION_ID,
    TICKETS,
    make_context,
    make_lobby_config,
    started_match,
)


class Watcher:
    """A minimal observer that keeps what it was handed."""

    def __init__(self) -> None:
        self.opened_with: tuple[SessionInfo, StateSnapshot] | None = None
        self.ticks: list[tuple[StateSnapshot, TickEvents | None]] = []
        self.closing: SessionClosed | None = None

    def opened(self, info: SessionInfo, snapshot: StateSnapshot) -> None:
        self.opened_with = (info, snapshot)

    def observed(self, snapshot: StateSnapshot, events: TickEvents | None) -> None:
        self.ticks.append((snapshot, events))

    def closed(self, notice: SessionClosed) -> None:
        self.closing = notice


# -- recording -----------------------------------------------------------------------


def test_a_lobby_records_nothing_unless_it_is_asked_to() -> None:
    match = started_match()
    assert match.game is not None
    assert not match.recording
    assert match.recorder is None
    assert match.game.recorder is None


def test_a_requested_recording_starts_at_tick_zero() -> None:
    match = started_match(
        arrange=lambda session: session.record_replay(hash_interval=2, max_ticks=64)
    )
    assert match.game is not None
    recorder = match.recorder
    assert recorder is not None
    assert match.game.recorder is recorder
    for _ in range(4):
        match.advance_tick()
    document = recorder.document()
    assert [entry.tick for entry in document.ticks] == [0, 1, 2, 3]
    assert document.hash_at(0) is not None


def test_a_recorded_lobby_match_replays_to_the_same_state() -> None:
    match = started_match(
        arrange=lambda session: session.record_replay(hash_interval=2, max_ticks=64)
    )
    assert match.game is not None and match.recorder is not None
    for _ in range(6):
        match.advance_tick()
    playback = play_replay(match.recorder.document(), make_context())
    assert playback.verified
    assert state_hash(playback.state) == state_hash(match.state)


def test_the_recording_carries_the_settings_the_roster_agreed_to() -> None:
    match = started_match(arrange=lambda session: session.record_replay())
    assert match.recorder is not None
    metadata = match.recorder.document().metadata
    assert metadata.settings is not None
    assert metadata.settings.mode is MatchMode.COOP
    assert metadata.settings.cheats_enabled is False
    assert metadata.settings.content == metadata.content
    assert metadata.settings.tick_rate == metadata.tick_rate
    assert metadata.seed == SEED
    assert metadata.session_id == SESSION_ID


def test_the_recording_of_a_lobby_match_carries_no_ticket() -> None:
    match = started_match(arrange=lambda session: session.record_replay())
    assert match.recorder is not None
    from battle_city_protocol import encode_replay

    payload = encode_replay(match.recorder.document())
    for ticket in TICKETS.values():
        assert ticket.encode("utf-8") not in payload
    assert b"ticket" not in payload


def test_recording_cannot_be_arranged_after_the_handover() -> None:
    match = started_match(arrange=lambda session: session.record_replay())
    with pytest.raises(ValueError, match="before the match starts"):
        match.record_replay()


def test_a_lobby_records_its_match_once() -> None:
    match = MatchSession(make_lobby_config())
    match.record_replay()
    with pytest.raises(ValueError, match="already recording"):
        match.record_replay()


def test_a_recording_shape_the_format_cannot_carry_is_refused_at_the_lobby() -> None:
    """Refused where it is asked for, not at a handover a roster is waiting on."""
    match = MatchSession(make_lobby_config())
    with pytest.raises(ValueError, match="max_ticks"):
        match.record_replay(max_ticks=MAX_REPLAY_TICKS + 1)
    with pytest.raises(ValueError, match="hashes"):
        match.record_replay(hash_interval=1, max_ticks=MAX_REPLAY_TICKS)
    assert not match.recording


def test_a_match_still_starts_when_its_recording_cannot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody loses an agreed game because a recording could not be described."""
    from battle_city_server import lobby as lobby_module

    def refuse(*args: object, **kwargs: object) -> object:
        raise ValueError("no recorder today")

    monkeypatch.setattr(lobby_module, "ReplayRecorder", refuse)
    match = started_match(arrange=lambda session: session.record_replay())
    assert match.game is not None
    assert match.recorder is None
    assert "recording dropped" in match.recording_notice
    match.advance_tick()
    assert match.tick == 1


# -- watching ------------------------------------------------------------------------


def test_an_observer_registered_before_the_start_sees_the_run_from_tick_zero() -> None:
    watcher = Watcher()
    match = started_match(arrange=lambda session: session.add_observer(watcher))
    assert watcher.opened_with is not None
    assert watcher.opened_with[1].tick == 0
    assert watcher.opened_with[1].grid is not None
    match.advance_tick()
    assert [snapshot.tick for snapshot, _ in watcher.ticks] == [1]


def test_an_observer_registered_after_the_start_is_passed_straight_through() -> None:
    match = started_match()
    match.advance_tick()
    watcher = Watcher()
    match.add_observer(watcher)
    assert match.game is not None
    assert match.game.observers == (watcher,)
    match.advance_tick()
    assert [snapshot.tick for snapshot, _ in watcher.ticks] == [2]


def test_a_pending_observer_can_be_removed_before_the_start() -> None:
    watcher = Watcher()
    match = MatchSession(make_lobby_config())
    match.add_observer(watcher)
    assert match.observers == (watcher,)
    match.remove_observer(watcher)
    assert len(match.observers) == 0


def test_pending_observers_are_bounded_by_the_session_limit() -> None:
    match = MatchSession(make_lobby_config())
    for _ in range(DEFAULT_MAX_OBSERVERS):
        match.add_observer(Watcher())
    with pytest.raises(TooManyObserversError):
        match.add_observer(Watcher())


def test_the_same_observer_cannot_be_held_twice() -> None:
    match = MatchSession(make_lobby_config())
    watcher = Watcher()
    match.add_observer(watcher)
    with pytest.raises(TooManyObserversError):
        match.add_observer(watcher)


def test_a_closed_lobby_takes_no_watcher() -> None:
    match = MatchSession(make_lobby_config())
    match.close(RejectionCode.SERVER_SHUTDOWN, "stopping")
    with pytest.raises(ObserverRefusedError):
        match.add_observer(Watcher())


def test_an_observer_is_told_when_the_started_match_closes() -> None:
    watcher = Watcher()
    match = started_match(arrange=lambda session: session.add_observer(watcher))
    match.close(RejectionCode.SERVER_SHUTDOWN, "stopping")
    assert watcher.closing is not None
    assert watcher.closing.code is RejectionCode.SERVER_SHUTDOWN


def test_an_observer_takes_no_seat_in_the_lobby() -> None:
    watcher = Watcher()
    match = started_match(arrange=lambda session: session.add_observer(watcher))
    assert match.joined_slots() == ()
    assert match.slot_of(99) is None
    assert match.lobby.occupied_slots() == (1, 2)
