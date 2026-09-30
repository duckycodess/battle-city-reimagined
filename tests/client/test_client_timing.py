"""The fixed-tick accumulator: frame cadence must not change how a run plays."""

from __future__ import annotations

import pytest
from battle_city_client.timing import NOMINAL_TICK_RATE, FixedTickAccumulator

ONE_SECOND_MS = 1000


def _total_ticks(chunks: list[int], *, max_ticks: int = 10_000) -> int:
    accumulator = FixedTickAccumulator(tick_rate=NOMINAL_TICK_RATE, max_ticks_per_advance=max_ticks)
    return sum(accumulator.advance(chunk) for chunk in chunks)


def test_one_second_of_wall_time_is_one_second_of_ticks() -> None:
    assert _total_ticks([ONE_SECOND_MS]) == NOMINAL_TICK_RATE


def test_the_same_elapsed_time_yields_the_same_ticks_at_any_cadence() -> None:
    """A 144Hz display, a 30Hz display and one long stall all play the same run."""
    total = 6 * ONE_SECOND_MS
    cadences = {
        "144hz": [7] * (total // 7) + [total % 7],
        "60hz": [16] * (total // 16) + [total % 16],
        "30hz": [33] * (total // 33) + [total % 33],
        "one stall": [total],
    }
    results = {name: _total_ticks(chunks) for name, chunks in cadences.items()}
    assert set(results.values()) == {total * NOMINAL_TICK_RATE // ONE_SECOND_MS}


def test_the_remainder_never_drifts() -> None:
    """16 ms is not a whole tick at 60Hz; the shortfall must be banked, not lost."""
    accumulator = FixedTickAccumulator(max_ticks_per_advance=10_000)
    ticks = sum(accumulator.advance(16) for _ in range(1000))
    assert ticks == 16 * 1000 * NOMINAL_TICK_RATE // ONE_SECOND_MS


def test_a_frame_shorter_than_a_tick_releases_nothing_then_catches_up() -> None:
    accumulator = FixedTickAccumulator()
    assert accumulator.advance(8) == 0
    assert accumulator.pending_milliseconds == 8
    assert accumulator.advance(9) == 1


def test_the_catch_up_bound_drops_ticks_and_counts_them() -> None:
    """A stall is not replayed frame by frame; the gap is reported instead."""
    accumulator = FixedTickAccumulator(max_ticks_per_advance=5)
    assert accumulator.advance(ONE_SECOND_MS) == 5
    assert accumulator.dropped_ticks == NOMINAL_TICK_RATE - 5


def test_reset_discards_banked_time_and_the_dropped_count() -> None:
    accumulator = FixedTickAccumulator(max_ticks_per_advance=1)
    accumulator.advance(ONE_SECOND_MS)
    accumulator.reset()
    assert accumulator.dropped_ticks == 0
    assert accumulator.pending_milliseconds == 0
    assert accumulator.advance(0) == 0


def test_zero_elapsed_time_releases_nothing() -> None:
    assert FixedTickAccumulator().advance(0) == 0


def test_negative_elapsed_time_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        FixedTickAccumulator().advance(-1)


@pytest.mark.parametrize(
    ("rate", "bound"),
    [(0, 5), (-1, 5), (60, 0), (60, -3)],
)
def test_an_unusable_configuration_is_refused(rate: int, bound: int) -> None:
    with pytest.raises(ValueError):
        FixedTickAccumulator(tick_rate=rate, max_ticks_per_advance=bound)
