"""The fixed-tick accumulator that keeps simulation time off the frame clock.

The simulation advances in whole ticks and never reads a clock. The client does read a
clock, so something has to convert elapsed wall time into a tick count, and that
something is this module. Keeping it separate from the pygame loop is what makes the
conversion testable without a display and what stops a slow or fast frame from changing
how the run plays.

The accumulator carries its remainder in exact integers. ``elapsed_ms * tick_rate`` is
banked and whole thousandths are withdrawn as ticks, so 60 frames of 16 ms and one frame
of 960 ms both release exactly 57 ticks, and no rounding error accumulates over a long
session. A float accumulator drifts; this one cannot.

``max_ticks_per_advance`` bounds the catch-up burst after a stall -- a breakpoint, a
dragged window, a suspended laptop. Ticks beyond the bound are dropped rather than
queued, because queueing them means the next frames are spent replaying a stall the
player already lived through, and each dropped tick is counted so a caller can report the
gap instead of discovering it as a silent desync. This is a presentation-side catch-up
policy for a locally driven run; a client replaying a recorded input stream must consume
every tick instead and must not rely on this class to pace it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

NOMINAL_TICK_RATE: Final[int] = 60
"""Simulation ticks per second.

The historical game ran its update loop at 60 frames per second and the simulation's
tuning -- tank speed, projectile speed, powerup durations -- is expressed in ticks at
that cadence. Changing this number changes how fast a run plays without changing a single
rule, so it is stated once here rather than spelled inline at the call site.
"""

MAX_TICKS_PER_ADVANCE: Final[int] = 5
"""Default catch-up bound: at most five ticks are released for one frame."""

_MILLISECONDS_PER_SECOND: Final[int] = 1000


@dataclass(slots=True)
class FixedTickAccumulator:
    """Convert elapsed milliseconds into whole simulation ticks."""

    tick_rate: int = NOMINAL_TICK_RATE
    max_ticks_per_advance: int = MAX_TICKS_PER_ADVANCE
    _banked: int = field(default=0, init=False, repr=False)
    _dropped_ticks: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tick_rate < 1:
            raise ValueError(f"tick_rate must be at least 1, found {self.tick_rate}")
        if self.max_ticks_per_advance < 1:
            raise ValueError(
                f"max_ticks_per_advance must be at least 1, found {self.max_ticks_per_advance}"
            )

    @property
    def dropped_ticks(self) -> int:
        """Ticks discarded by the catch-up bound since the last :meth:`reset`."""
        return self._dropped_ticks

    @property
    def pending_milliseconds(self) -> int:
        """Banked time not yet released as a tick, in milliseconds, rounded down."""
        return self._banked // self.tick_rate

    def advance(self, elapsed_ms: int) -> int:
        """Bank ``elapsed_ms`` and return the whole ticks that fall due."""
        if elapsed_ms < 0:
            raise ValueError(f"elapsed_ms must not be negative, found {elapsed_ms}")
        self._banked += elapsed_ms * self.tick_rate
        ticks = self._banked // _MILLISECONDS_PER_SECOND
        self._banked -= ticks * _MILLISECONDS_PER_SECOND
        if ticks > self.max_ticks_per_advance:
            self._dropped_ticks += ticks - self.max_ticks_per_advance
            ticks = self.max_ticks_per_advance
        return ticks

    def reset(self) -> None:
        """Discard banked time and the dropped-tick count.

        Called when the run stops consuming ticks -- a pause, a focus loss, a return to
        the menu -- so that resuming does not release the whole paused interval at once.
        """
        self._banked = 0
        self._dropped_ticks = 0
