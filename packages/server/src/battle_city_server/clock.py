"""When a tick happens.

Tick cadence is injected rather than assumed, for the same reason the simulation refuses
to read a clock: a test that has to wait for wall time is a test that is slow when it
passes and flaky when it fails. :class:`ManualClock` releases ticks on demand, so a
scenario can state exactly how many ticks it ran, and :class:`RealTimeClock` is the one
a deployment uses.

Neither clock reaches the simulation. The server asks a clock when to call
``advance_tick``; the tick itself is a pure function of the state and the queued input,
so a session advanced by hand and the same session advanced in real time produce the
same canonical state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Final, Protocol


class TickClock(Protocol):
    """Decides when the next tick runs."""

    async def wait_for_tick(self, tick: int) -> None:
        """Return once ``tick`` is due."""


DEFAULT_MAX_CATCHUP_TICKS: Final[int] = 8
"""How far behind its schedule a session may be before it stops trying to catch up."""


class RealTimeClock:
    """A fixed-rate clock anchored to the first tick it is asked about.

    Sleeping until an absolute deadline, rather than for one tick period, stops the
    cadence drifting when a tick takes longer than its budget: a session that overran
    by a little catches up instead of falling steadily further behind.

    Catch-up is bounded, which matters more than it sounds. A process that was descheduled
    for a second owes sixty ticks at sixty hertz, and running them back to back is the
    worst thing the server could do with that second: it bursts sixty snapshots into
    every client's bounded outbound queue, which overflows the queue and disconnects
    exactly the clients that did nothing wrong. Past :attr:`max_catchup_ticks` the clock
    re-anchors to now and the owed ticks are abandoned — a run that skips wall-clock time
    is a run that is late, while a run that floods its clients is a run that is over.
    Simulation time is unaffected either way: ticks are still consecutive integers.
    """

    __slots__ = ("_anchor", "_anchor_tick", "_dropped", "_max_catchup", "_now", "_period")

    def __init__(
        self,
        tick_rate: int,
        *,
        max_catchup_ticks: int = DEFAULT_MAX_CATCHUP_TICKS,
        now: Callable[[], float] | None = None,
    ) -> None:
        if tick_rate <= 0:
            raise ValueError("tick_rate must be positive")
        if max_catchup_ticks <= 0:
            raise ValueError("max_catchup_ticks must be positive")
        self._period = 1.0 / tick_rate
        self._max_catchup = max_catchup_ticks
        self._now = now
        self._anchor: float | None = None
        self._anchor_tick = 0
        self._dropped = 0

    @property
    def dropped_ticks(self) -> int:
        """How many times the clock gave up catching up. A health signal, not a rule."""
        return self._dropped

    def _time(self) -> float:
        if self._now is not None:
            return self._now()
        return asyncio.get_running_loop().time()

    async def wait_for_tick(self, tick: int) -> None:
        now = self._time()
        if self._anchor is None:
            self._anchor = now
            self._anchor_tick = tick
            return
        deadline = self._anchor + (tick - self._anchor_tick) * self._period
        delay = deadline - now
        if delay > 0:
            await asyncio.sleep(delay)
            return
        if -delay > self._max_catchup * self._period:
            self._anchor = now
            self._anchor_tick = tick
            self._dropped += 1
        # Behind schedule, so there is nothing to wait for, but yielding is not
        # optional: the writer tasks that drain client queues run on this loop too.
        await asyncio.sleep(0)


class ManualClock:
    """A clock that only advances when a caller says so.

    ``release(count)`` permits that many further ticks. A session driven by this clock
    runs exactly as many ticks as a test asked for and never waits on wall time.
    """

    __slots__ = ("_permits",)

    def __init__(self, permits: int = 0) -> None:
        self._permits = asyncio.Semaphore(permits)

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._permits.release()

    async def wait_for_tick(self, tick: int) -> None:
        await self._permits.acquire()
