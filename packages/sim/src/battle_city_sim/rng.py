"""Project-owned, versioned, seeded pseudo-random number generator.

The simulation may never call :mod:`random` or any other shared global generator: a
global stream would couple unrelated call sites and break replay. Instead the generator
is an immutable value carried inside the state, and every draw returns both the drawn
value and the successor generator.

The algorithm is SplitMix64 (Steele, Lea and Flood, 2014) chosen because it is a fixed
sequence of 64-bit integer operations with no platform-dependent behaviour. The
algorithm name and version are part of the canonical state encoding, so swapping the
algorithm is a replay-compatibility change and needs a proposal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

RNG_ALGORITHM: Final[str] = "splitmix64"
RNG_ALGORITHM_CODE: Final[int] = 1
RNG_VERSION: Final[int] = 1

_MASK64: Final[int] = (1 << 64) - 1
_GOLDEN_GAMMA: Final[int] = 0x9E3779B97F4A7C15
_MIX_A: Final[int] = 0xBF58476D1CE4E5B9
_MIX_B: Final[int] = 0x94D049BB133111EB


@dataclass(frozen=True, slots=True)
class Rng:
    """An immutable SplitMix64 stream position.

    Draws never mutate: ``value, rng = rng.next_u64()``. Dropping the returned successor
    silently replays the same value, which tests assert against.
    """

    state: int

    def __post_init__(self) -> None:
        if not 0 <= self.state <= _MASK64:
            raise ValueError("rng state must fit in 64 unsigned bits")

    @classmethod
    def from_seed(cls, seed: int) -> Rng:
        """Build a stream from any integer seed, folded into 64 unsigned bits."""
        return cls(state=seed & _MASK64)

    def next_u64(self) -> tuple[int, Rng]:
        """Return the next 64-bit draw and the successor stream."""
        state = (self.state + _GOLDEN_GAMMA) & _MASK64
        mixed = state
        mixed = ((mixed ^ (mixed >> 30)) * _MIX_A) & _MASK64
        mixed = ((mixed ^ (mixed >> 27)) * _MIX_B) & _MASK64
        mixed = mixed ^ (mixed >> 31)
        return mixed, Rng(state=state)

    def below(self, bound: int) -> tuple[int, Rng]:
        """Return a uniform draw in ``[0, bound)`` and the successor stream.

        Rejection sampling removes modulo bias; the rejection loop is deterministic
        because it consumes the same stream every time.
        """
        if bound <= 0:
            raise ValueError("rng bound must be positive")
        limit = (1 << 64) - ((1 << 64) % bound)
        rng = self
        while True:
            draw, rng = rng.next_u64()
            if draw < limit:
                return draw % bound, rng

    def choice[T](self, items: Sequence[T]) -> tuple[T, Rng]:
        """Return a uniform element of ``items`` and the successor stream."""
        if not items:
            raise ValueError("rng choice needs a non-empty sequence")
        index, rng = self.below(len(items))
        return items[index], rng
