"""The project-owned seeded generator.

The golden vectors pin SplitMix64 itself: if the algorithm or the version changes, every
recorded replay changes with it, so the change has to be deliberate.
"""

from __future__ import annotations

import pytest
from battle_city_sim import RNG_ALGORITHM, RNG_VERSION, Rng

SEED_ZERO_STREAM = (
    16294208416658607535,
    7960286522194355700,
    487617019471545679,
    17909611376780542444,
    1961750202426094747,
)


def _take(rng: Rng, count: int) -> tuple[int, ...]:
    values: list[int] = []
    current = rng
    for _ in range(count):
        value, current = current.next_u64()
        values.append(value)
    return tuple(values)


def test_the_algorithm_is_named_and_versioned() -> None:
    assert RNG_ALGORITHM == "splitmix64"
    assert RNG_VERSION == 1


def test_seed_zero_matches_the_published_splitmix64_stream() -> None:
    assert _take(Rng.from_seed(0), 5) == SEED_ZERO_STREAM


def test_the_same_seed_gives_the_same_stream() -> None:
    assert _take(Rng.from_seed(4242), 16) == _take(Rng.from_seed(4242), 16)


def test_different_seeds_give_different_streams() -> None:
    assert _take(Rng.from_seed(1), 8) != _take(Rng.from_seed(2), 8)


def test_a_draw_does_not_mutate_the_generator() -> None:
    rng = Rng.from_seed(99)
    first, _ = rng.next_u64()
    second, _ = rng.next_u64()
    assert first == second, "Rng must be a value; dropping the successor replays the draw"


def test_every_draw_fits_in_64_unsigned_bits() -> None:
    for value in _take(Rng.from_seed(7), 64):
        assert 0 <= value < 1 << 64


def test_a_seed_wider_than_64_bits_is_folded() -> None:
    assert Rng.from_seed((1 << 64) + 5).state == 5


def test_below_stays_in_range_and_is_reproducible() -> None:
    rng = Rng.from_seed(11)
    first: list[int] = []
    current = rng
    for _ in range(50):
        value, current = current.below(6)
        assert 0 <= value < 6
        first.append(value)

    second: list[int] = []
    current = rng
    for _ in range(50):
        value, current = current.below(6)
        second.append(value)
    assert first == second


def test_below_rejects_a_non_positive_bound() -> None:
    with pytest.raises(ValueError, match="bound must be positive"):
        Rng.from_seed(1).below(0)


def test_choice_picks_from_the_sequence() -> None:
    options = ("a", "b", "c")
    value, successor = Rng.from_seed(3).choice(options)
    assert value in options
    assert successor != Rng.from_seed(3)


def test_choice_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="non-empty sequence"):
        Rng.from_seed(1).choice(())


def test_a_generator_state_must_fit_in_64_bits() -> None:
    with pytest.raises(ValueError, match="64 unsigned bits"):
        Rng(state=1 << 64)
