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
from typing import Protocol


class TickClock(Protocol):
    """Decides when the next tick runs."""

    async def wait_for_tick(self, tick: int) -> None:
        """Return once ``tick`` is due."""


class RealTimeClock:
    """A fixed-rate clock anchored to the first tick it is asked about.

    Sleeping for the remaining time to an absolute deadline, rather than for one tick
    period, stops the cadence drifting when a tick takes longer than its budget: the
    session catches up instead of falling steadily further behind.
    """

    __slots__ = ("_anchor", "_anchor_tick", "_period")

    def __init__(self, tick_rate: int) -> None:
        if tick_rate <= 0:
            raise ValueError("tick_rate must be positive")
        self._period = 1.0 / tick_rate
        self._anchor: float | None = None
        self._anchor_tick = 0

    async def wait_for_tick(self, tick: int) -> None:
        loop = asyncio.get_running_loop()
        if self._anchor is None:
            self._anchor = loop.time()
            self._anchor_tick = tick
            return
        deadline = self._anchor + (tick - self._anchor_tick) * self._period
        delay = deadline - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)


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
