"""Spectators: authoritative output in, and nothing at all out."""

from __future__ import annotations

import inspect
import typing

import pytest
from battle_city_client import OnlineSession, SpectatorView
from battle_city_protocol import (
    ClientMessage,
    DirectionCode,
    RejectionCode,
    SessionClosed,
    SessionInfo,
    StateSnapshot,
    TickEvents,
)
from battle_city_server import (
    DEFAULT_MAX_OBSERVERS,
    GameSession,
    ObserverRefusedError,
    ObserverRegistry,
    ReplayRecorder,
    SessionObserver,
    TooManyObserversError,
    replay_metadata,
)
from battle_city_sim import SpawnEnemyCommand, TankVariant, state_hash
from replay_helpers import (
    ENEMY_CELL,
    SESSION_ID,
    TOKENS,
    fire,
    join_request,
    make_config,
    move,
    recording_session,
)


class Watcher:
    """A minimal observer that keeps what it was handed, for the server-side tests."""

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


class BrokenWatcher(Watcher):
    """An observer that raises on every tick. It must not be able to stop one."""

    def observed(self, snapshot: StateSnapshot, events: TickEvents | None) -> None:
        raise RuntimeError("this watcher is broken")


def playing_session() -> tuple[GameSession, int]:
    session = GameSession(make_config())
    connection = session.connect()
    session.handle(connection, join_request(1))
    return session, connection


# -- the server seam -----------------------------------------------------------------


def test_an_observer_is_handed_a_keyframe_the_moment_it_registers() -> None:
    session, _ = playing_session()
    watcher = Watcher()
    session.add_observer(watcher)
    assert watcher.opened_with is not None
    info, snapshot = watcher.opened_with
    assert info == session.info
    assert snapshot.grid is not None
    assert snapshot.state_hash == state_hash(session.state)


def test_an_observer_registering_mid_run_starts_from_a_complete_board() -> None:
    session, connection = playing_session()
    session.handle(connection, move(1, sequence=1, direction=DirectionCode.UP))
    session.advance_tick()
    session.advance_tick()
    watcher = Watcher()
    session.add_observer(watcher)
    assert watcher.opened_with is not None
    assert watcher.opened_with[1].grid is not None
    assert watcher.opened_with[1].tick == session.tick


def test_an_observer_receives_every_tick_with_its_events() -> None:
    session, connection = playing_session()
    watcher = Watcher()
    session.add_observer(watcher)
    session.handle(connection, fire(1, sequence=1))
    session.advance_tick()
    session.advance_tick()
    assert [snapshot.tick for snapshot, _ in watcher.ticks] == [1, 2]
    assert any(events is not None for _, events in watcher.ticks)


def test_an_observer_is_handed_the_same_snapshot_the_players_are() -> None:
    session, connection = playing_session()
    watcher = Watcher()
    session.add_observer(watcher)
    replies = session.advance_tick()
    sent = [reply.message for reply in replies if isinstance(reply.message, StateSnapshot)]
    assert sent
    assert watcher.ticks[0][0] is sent[0]


def test_an_observer_is_told_when_the_session_closes() -> None:
    session, _ = playing_session()
    watcher = Watcher()
    session.add_observer(watcher)
    session.close(RejectionCode.SERVER_SHUTDOWN, "stopping")
    assert watcher.closing is not None
    assert watcher.closing.code is RejectionCode.SERVER_SHUTDOWN


def test_a_removed_observer_stops_receiving() -> None:
    session, _ = playing_session()
    watcher = Watcher()
    session.add_observer(watcher)
    session.remove_observer(watcher)
    session.advance_tick()
    assert watcher.ticks == []


def test_removing_an_observer_that_was_never_added_is_not_an_error() -> None:
    session, _ = playing_session()
    session.remove_observer(Watcher())


# -- an observer is not a player -----------------------------------------------------


def test_an_observer_takes_no_slot_and_no_connection() -> None:
    session, _ = playing_session()
    before = session.joined_slots()
    session.add_observer(Watcher())
    assert session.joined_slots() == before
    assert len(session.observers) == 1


def test_the_spectator_view_has_no_method_that_speaks_to_a_server() -> None:
    """The whole public surface is the three the server calls. Nothing goes the other way."""
    callables = {
        name
        for name, value in vars(SpectatorView).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert callables == {"opened", "observed", "closed"}


def test_the_spectator_view_returns_nothing_a_server_would_read() -> None:
    view = SpectatorView()
    session, _ = playing_session()
    session.add_observer(view)
    view.observed(session.snapshot(keyframe=False), None)
    view.closed(session.closing_notice(RejectionCode.SESSION_CLOSED))
    for name in ("opened", "observed", "closed"):
        assert inspect.signature(getattr(SpectatorView, name)).return_annotation == "None"
    held = [getattr(view, name) for name in SpectatorView.__slots__]
    client_messages = typing.get_args(ClientMessage.__value__)
    assert client_messages
    assert not any(isinstance(value, client_messages) for value in held)


def test_the_online_session_is_the_one_that_can_speak() -> None:
    """A contrast, so "a spectator cannot play" is a measured difference, not a claim."""
    builders = {
        name
        for name, value in vars(OnlineSession).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert builders - {"opened", "observed", "closed"}
    assert "input_batch" in builders


def test_a_spectator_holds_no_credential() -> None:
    view = SpectatorView()
    session, _ = playing_session()
    session.add_observer(view)
    session.advance_tick()
    rendered = repr([getattr(view, name) for name in SpectatorView.__slots__])
    for token in TOKENS.values():
        assert token not in rendered


# -- bounds and failure --------------------------------------------------------------


def test_the_observer_roster_is_bounded() -> None:
    registry = ObserverRegistry(2)
    registry.add(Watcher())
    registry.add(Watcher())
    with pytest.raises(TooManyObserversError):
        registry.add(Watcher())


def test_registering_the_same_observer_twice_is_refused() -> None:
    registry = ObserverRegistry()
    watcher = Watcher()
    registry.add(watcher)
    with pytest.raises(TooManyObserversError):
        registry.add(watcher)


def test_the_session_limit_bounds_a_session_s_observers() -> None:
    session, _ = playing_session()
    for _ in range(DEFAULT_MAX_OBSERVERS):
        session.add_observer(Watcher())
    with pytest.raises(TooManyObserversError):
        session.add_observer(Watcher())


def test_a_broken_observer_is_dropped_and_the_tick_still_runs() -> None:
    session, connection = playing_session()
    broken = BrokenWatcher()
    good = Watcher()
    session.add_observer(broken)
    session.add_observer(good)
    replies = session.advance_tick()
    assert session.tick == 1
    assert replies
    assert broken not in session.observers
    assert good.ticks


def test_a_watcher_satisfies_the_observer_protocol_structurally() -> None:
    """The client package implements this without importing the server package."""
    assert isinstance(SpectatorView(), SessionObserver)
    assert isinstance(Watcher(), SessionObserver)


# -- the client view -----------------------------------------------------------------


def test_the_client_view_draws_the_board_the_server_broadcast() -> None:
    session, connection = playing_session()
    view = SpectatorView()
    session.add_observer(view)
    assert view.board is not None
    assert view.session is not None
    assert view.watching

    session.handle(connection, move(1, sequence=1, direction=DirectionCode.UP))
    session.advance_tick()
    assert view.board is not None
    assert view.board.tick == session.tick
    assert view.board.state_hash == state_hash(session.state)


def test_the_client_view_follows_a_whole_run() -> None:
    session, recorder = recording_session()
    connection = session.connect()
    session.handle(connection, join_request(1))
    view = SpectatorView()
    session.add_observer(view)
    session.schedule_commands(
        session.tick, [SpawnEnemyCommand(cell=ENEMY_CELL, variant=TankVariant.ENEMY_NORMAL)]
    )
    for index in range(5):
        session.handle(connection, move(1, sequence=index + 1, direction=DirectionCode.UP))
        session.advance_tick()
    assert view.board is not None
    assert view.board.state_hash == state_hash(session.state)
    # Two player tanks and the enemy the server spawned: a spectator sees the whole board.
    assert len(view.board.tanks) == 3


def test_the_client_view_keeps_the_last_board_after_the_session_ends() -> None:
    session, _ = playing_session()
    view = SpectatorView()
    session.add_observer(view)
    session.advance_tick()
    last = view.board
    session.close(RejectionCode.SESSION_CLOSED, "run finished")
    assert view.closing is not None
    assert view.closing.session_id == SESSION_ID
    assert not view.watching
    assert view.board is last


def test_the_client_view_ignores_frames_after_the_session_closed() -> None:
    session, _ = playing_session()
    view = SpectatorView()
    session.add_observer(view)
    session.advance_tick()
    before = view.board
    view.closed(session.closing_notice(RejectionCode.SESSION_CLOSED))
    view.observed(session.snapshot(keyframe=True), None)
    assert view.board is before


# -- identity, not equality ----------------------------------------------------------


class EqualWatcher(Watcher):
    """A watcher that compares equal to every other one. Two of them are still two."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, EqualWatcher)

    def __hash__(self) -> int:
        return 0


def test_two_distinct_observers_that_compare_equal_are_two_observers() -> None:
    session, _ = playing_session()
    first = EqualWatcher()
    second = EqualWatcher()
    session.add_observer(first)
    session.add_observer(second)
    session.advance_tick()
    assert len(session.observers) == 2
    assert len(first.ticks) == 1
    assert len(second.ticks) == 1


def test_removing_one_observer_leaves_an_equal_one_registered() -> None:
    session, _ = playing_session()
    first = EqualWatcher()
    second = EqualWatcher()
    session.add_observer(first)
    session.add_observer(second)
    session.remove_observer(first)
    session.advance_tick()
    assert [observer is second for observer in session.observers] == [True]
    assert first.ticks == []
    assert len(second.ticks) == 1


# -- a closed session is not watchable ------------------------------------------------


def test_a_closed_session_takes_no_new_observer() -> None:
    """It would be opened on a run that has ended and never told that it ended."""
    session, _ = playing_session()
    session.close(RejectionCode.SESSION_CLOSED, "run finished")
    with pytest.raises(ObserverRefusedError):
        session.add_observer(Watcher())


def test_a_closed_session_takes_no_recorder() -> None:
    session, _ = playing_session()
    session.close(RejectionCode.SESSION_CLOSED, "run finished")
    with pytest.raises(ValueError, match="closed"):
        session.attach_recorder(ReplayRecorder(replay_metadata(make_config())))


def test_too_many_observers_is_an_observer_refusal() -> None:
    assert issubclass(TooManyObserversError, ObserverRefusedError)
