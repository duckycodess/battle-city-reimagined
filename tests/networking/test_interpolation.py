"""The interpolator on its own: what it draws between two snapshots, and what it will not.

These tests do not run a session. They hand :class:`SnapshotInterpolator` boards it could
have been sent and ask what it would draw, which is the only way to pin the cases a
latency trial reaches by accident if at all -- an entity that appears, one that is
destroyed, a respawn that crosses the stage.

The claim every one of them is defending is the same: **nothing here invents state.**
Every pixel lies on the straight line between two positions the server reported, nothing
is drawn before the tick it was reported for, nothing is removed before the tick it
stopped being reported, and the picture always converges on the newest snapshot.
"""

from __future__ import annotations

from battle_city_client.interpolation import (
    CATCHUP_SLACK_TICKS,
    MAX_CORRECTION_FRACTION,
    MILLITICKS_PER_TICK,
    SMOOTHING_TICKS,
    TELEPORT_PIXELS,
    SnapshotInterpolator,
)
from battle_city_client.timing import NOMINAL_TICK_RATE
from networking_helpers import SLOT, TANK_SPEED, board_at, shot_at, tank_at

FRAME_MS = 17
"""One frame at 60 fps, as the client's integer-millisecond clock reports it."""


def _loaded(*ticks: int, smoothing: int = SMOOTHING_TICKS) -> SnapshotInterpolator:
    """An interpolator holding a tank crossing the stage at the simulation's speed."""
    view = SnapshotInterpolator(tick_rate=NOMINAL_TICK_RATE, smoothing_ticks=smoothing)
    for tick in ticks:
        view.record(board_at(tick, tanks=(tank_at(1, 16 + TANK_SPEED * tick, 128),)))
    return view


def _at(view: SnapshotInterpolator, milliticks: int) -> SnapshotInterpolator:
    """Put the render clock exactly on ``milliticks``, for a test that needs a position.

    Reaching in is deliberate. The clock is driven by frame time and corrected towards
    the stream, so asking "what is drawn a third of the way between tick 4 and tick 5"
    through :meth:`SnapshotInterpolator.advance` would be asking two questions at once.
    """
    view._render = milliticks  # noqa: SLF001 - the clock is the thing under test
    return view


# -- matching entities by identity ---------------------------------------------


def test_a_tank_is_drawn_between_the_two_ticks_it_was_reported_at() -> None:
    """Halfway between two snapshots, a tank is drawn halfway between its positions."""
    view = _at(_loaded(0, 1, 2, 3, 4), 2 * MILLITICKS_PER_TICK + MILLITICKS_PER_TICK // 2)
    drawn = view.view()
    assert drawn is not None
    tank = drawn.tank_of(SLOT)
    assert tank is not None
    assert tank.x == 16 + TANK_SPEED * 2 + TANK_SPEED // 2


def test_entities_are_matched_by_identity_and_not_by_position() -> None:
    """Two tanks that swapped places are each drawn moving to their own new position."""
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=(tank_at(1, 0, 0, slot=1), tank_at(2, 4, 0, slot=2))))
    view.record(board_at(1, tanks=(tank_at(2, 2, 0, slot=2), tank_at(1, 2, 0, slot=1))))
    drawn = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert drawn is not None
    places = {tank.entity_id: tank.x for tank in drawn.tanks}
    assert places == {1: 1, 2: 3}


def test_a_projectile_is_interpolated_like_a_tank() -> None:
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, shots=(shot_at(9, 40, 40),)))
    view.record(board_at(1, shots=(shot_at(9, 43, 40),)))
    drawn = _at(view, MILLITICKS_PER_TICK // 3).view()
    assert drawn is not None
    assert drawn.shots[0].x == 41


def test_the_same_motion_mirrored_is_drawn_mirrored() -> None:
    """A tank driving left is drawn where the mirror of one driving right would be.

    The blend rounds to whole pixels, and rounding a signed value sends a halfway case
    towards negative infinity -- away from the start in one direction and towards it in
    the other. Two clients watching the same pair of tanks would disagree by a pixel
    about which had travelled further, over motion the simulation treats identically.
    """
    rightward = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    rightward.record(board_at(0, tanks=(tank_at(1, 100, 0),)))
    rightward.record(board_at(1, tanks=(tank_at(1, 100 + TANK_SPEED, 0),)))
    leftward = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    leftward.record(board_at(0, tanks=(tank_at(2, 100, 0),)))
    leftward.record(board_at(1, tanks=(tank_at(2, 100 - TANK_SPEED, 0),)))

    for numerator in range(0, MILLITICKS_PER_TICK, 37):
        forward = _at(rightward, numerator).view()
        backward = _at(leftward, numerator).view()
        assert forward is not None
        assert backward is not None
        assert forward.tanks[0].x - 100 == 100 - backward.tanks[0].x, numerator


# -- entities that appear and disappear ----------------------------------------


def test_an_entity_is_not_drawn_before_the_tick_it_first_appeared_in() -> None:
    """A tank the server first reported at tick 1 is not on screen during tick 0.

    Drawing it early would be showing a spawn the server had not decided yet, which is
    the one thing an interpolating client must never do, however small.
    """
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=()))
    view.record(board_at(1, tanks=(tank_at(7, 32, 32),)))
    drawn = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert drawn is not None
    assert drawn.tanks == ()


def test_an_entity_stays_drawn_until_the_tick_it_was_destroyed_in() -> None:
    """A tank the server stopped reporting at tick 1 is still on screen during tick 0.

    The mirror of the rule above, and for the same reason: it existed for the whole of
    the tick being drawn, so removing it early would show a kill before it happened.
    """
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=(tank_at(7, 32, 32),)))
    view.record(board_at(1, tanks=()))
    drawn = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert drawn is not None
    assert len(drawn.tanks) == 1
    assert drawn.tanks[0].x == 32


def test_the_entity_appears_once_the_clock_reaches_its_tick() -> None:
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=()))
    view.record(board_at(1, tanks=(tank_at(7, 32, 32),)))
    view.record(board_at(2, tanks=(tank_at(7, 34, 32),)))
    drawn = _at(view, MILLITICKS_PER_TICK).view()
    assert drawn is not None
    assert [tank.entity_id for tank in drawn.tanks] == [7]


# -- jumps ---------------------------------------------------------------------


def test_a_respawn_is_not_slid_across_the_stage() -> None:
    """A tank that crossed the board between two ticks is held, then is simply there.

    Sliding it would draw it through walls it was never inside. The rule is distance,
    not a flag on the wire: anything faster than half a tile a tick is not travel.
    """
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=(tank_at(1, 240, 16),)))
    view.record(board_at(1, tanks=(tank_at(1, 16, 160),)))
    held = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert held is not None
    assert (held.tanks[0].x, held.tanks[0].y) == (240, 16)
    arrived = _at(view, MILLITICKS_PER_TICK).view()
    assert arrived is not None
    assert (arrived.tanks[0].x, arrived.tanks[0].y) == (16, 160)


def test_the_fastest_thing_the_simulation_moves_is_still_interpolated() -> None:
    """A projectile at its full speed is travel, not a jump. The bound has headroom."""
    assert TELEPORT_PIXELS > 3
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, shots=(shot_at(9, 100, 40),)))
    view.record(board_at(1, shots=(shot_at(9, 100 + TELEPORT_PIXELS, 40),)))
    drawn = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert drawn is not None
    assert drawn.shots[0].x == 100 + TELEPORT_PIXELS // 2


# -- convergence ---------------------------------------------------------------


def test_the_picture_converges_on_the_newest_snapshot() -> None:
    """When the stream stops, the clock runs up to the last tick and stays there.

    This is the property that makes interpolation safe to have on. Whatever the clock
    was doing, the board the player ends up looking at is the newest one the server
    sent, unaltered -- not a blend, not an extrapolation, and not a frame behind.
    """
    view = _loaded(*range(8))
    for _ in range(60):
        view.advance(FRAME_MS)
    drawn = view.view()
    assert drawn == view.latest
    assert view.render_tick == 7


def test_a_long_stall_re_anchors_instead_of_replaying_the_backlog() -> None:
    """After a stall the clock jumps to the target rather than crawling up to it.

    A client that came back from a dragged window and then played the missed second of
    the match back in order would be a second behind for the rest of the session.
    """
    view = _loaded(*range(8))
    for _ in range(4):
        view.advance(FRAME_MS)
    for tick in range(8, 40):
        view.record(board_at(tick, tanks=(tank_at(1, 16 + TANK_SPEED * tick, 128),)))
    view.advance(FRAME_MS)
    assert view.render_tick is not None
    assert view.render_tick >= 39 - SMOOTHING_TICKS - CATCHUP_SLACK_TICKS


def test_playback_never_runs_more_than_an_eighth_faster_than_real_time() -> None:
    """However far behind the clock is, it catches up without the picture racing.

    The lag bounds make this hard to reach in ordinary play; it is asserted anyway, so
    the guarantee about playback speed does not depend on them staying where they are.
    """
    view = _loaded(*range(8))
    view.advance(FRAME_MS)
    assert view.render_tick is not None
    for tick in range(8, 12):
        view.record(board_at(tick, tanks=(tank_at(1, 16 + TANK_SPEED * tick, 128),)))
    start = (view.render_tick - 1) * MILLITICKS_PER_TICK
    _at(view, start)  # put the clock a whole tick behind on purpose
    view.advance(FRAME_MS)
    reached = view._render  # noqa: SLF001 - the clock is the thing under test
    assert reached is not None
    nominal = FRAME_MS * NOMINAL_TICK_RATE
    assert reached - start <= nominal + nominal // MAX_CORRECTION_FRACTION


# -- what it refuses to do ------------------------------------------------------


def test_zero_smoothing_draws_the_newest_snapshot_and_nothing_else() -> None:
    """With smoothing off the interpolator is a pass-through, board for board."""
    view = _loaded(*range(6), smoothing=0)
    view.advance(FRAME_MS)
    assert view.view() == view.latest


def test_a_board_from_before_the_newest_starts_the_history_again() -> None:
    """A tick that went backwards is a different run, not a reordering to be merged."""
    view = _loaded(*range(6))
    view.advance(FRAME_MS)
    view.record(board_at(0, tanks=(tank_at(1, 16, 128),)))
    assert view.latest is not None
    assert view.latest.tick == 0
    view.advance(FRAME_MS)
    assert view.view() == view.latest


def test_terrain_and_the_hash_are_taken_whole_and_never_blended() -> None:
    """Everything that is not a position comes from one snapshot, unaltered."""
    view = SnapshotInterpolator(smoothing_ticks=SMOOTHING_TICKS)
    view.record(board_at(0, tanks=(tank_at(1, 0, 0),)))
    view.record(board_at(1, tanks=(tank_at(1, 2, 0),), base_destroyed=True, outcome=2))
    drawn = _at(view, MILLITICKS_PER_TICK // 2).view()
    assert drawn is not None
    assert drawn.tick == 0
    assert drawn.state_hash == board_at(0).state_hash
    assert drawn.base_destroyed is False
    assert drawn.outcome is None
