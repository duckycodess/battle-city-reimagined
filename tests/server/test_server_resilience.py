"""Failures that must end a session cleanly rather than leave it hanging.

A tick loop that dies silently is worse than a session that ends: every connected client
sits waiting for a snapshot that will never arrive, with no reason code and nothing in
the log. These tests pin the containment, the hazard that motivates it, and the clock
behaviour that stops a late server punishing the clients that did nothing wrong.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import cast

import pytest
from battle_city_protocol import (
    MAX_PROJECTILES_PER_SNAPSHOT,
    MessageError,
    RejectionCode,
    SessionClosed,
)
from battle_city_server import (
    GameSession,
    ManualClock,
    RealTimeClock,
    SessionServer,
    UntranslatableEventError,
    protocol_event,
    snapshot_of,
)
from battle_city_sim import (
    CANONICAL_STATE_VERSION,
    Direction,
    Event,
    Faction,
    Projectile,
    Vec2,
)
from server_helpers import SESSION_ID, joined, make_config


def _snapshot(session: GameSession, projectiles: int) -> object:
    state = dataclasses.replace(
        session.state,
        projectiles=tuple(
            Projectile(
                entity_id=1000 + index,
                owner_id=1,
                faction=Faction.PLAYER,
                position=Vec2(8, 8),
                direction=Direction.UP,
            )
            for index in range(projectiles)
        ),
    )
    return snapshot_of(
        state,
        session_id=SESSION_ID,
        tick_rate=60,
        state_version=CANONICAL_STATE_VERSION,
        keyframe=False,
    )


def test_a_snapshot_at_the_projectile_bound_still_encodes() -> None:
    session = GameSession(make_config())
    assert _snapshot(session, MAX_PROJECTILES_PER_SNAPSHOT) is not None


def test_a_state_past_the_projectile_bound_is_refused_not_truncated() -> None:
    """Gatling fire can outrun the snapshot bound; the refusal must be visible.

    Silently dropping projectiles would hand clients an authoritative snapshot that is
    not the authoritative state, which is a worse outcome than a loud failure.
    """
    session = GameSession(make_config())
    with pytest.raises(MessageError) as error:
        _snapshot(session, MAX_PROJECTILES_PER_SNAPSHOT + 1)
    assert error.value.code is RejectionCode.INVALID_FIELD
    assert "projectiles" in error.value.detail


def test_an_unmapped_simulation_event_is_refused_rather_than_dropped() -> None:
    @dataclasses.dataclass(frozen=True, slots=True)
    class InventedEvent:
        tank_id: int

    with pytest.raises(UntranslatableEventError) as error:
        protocol_event(cast(Event, InventedEvent(tank_id=1)))
    assert "InventedEvent" in str(error.value)


def test_a_failing_tick_ends_the_session_instead_of_killing_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())
        one = await joined(server, 1)
        two = await joined(server, 2)

        def boom() -> tuple[object, ...]:
            raise MessageError(RejectionCode.INVALID_FIELD, "projectiles carries too many entries")

        monkeypatch.setattr(server.session, "advance_tick", boom)

        # The driver must survive: run() returns rather than raising into its task.
        clock = ManualClock()
        runner = asyncio.create_task(server.run(clock, ticks=3))
        clock.release(3)
        await asyncio.wait_for(runner, 1.0)

        for client in (one, two):
            closed = await client.receive()
            assert isinstance(closed, SessionClosed)
            assert closed.code is RejectionCode.INTERNAL_ERROR

        assert server.session.closed
        assert server.tick == 0
        await asyncio.wait_for(one.task, 1.0)
        await asyncio.wait_for(two.task, 1.0)

    asyncio.run(scenario())


def test_a_failing_tick_is_logged_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        server = SessionServer(make_config())

        def boom() -> tuple[object, ...]:
            raise RuntimeError("translation broke")

        monkeypatch.setattr(server.session, "advance_tick", boom)
        await server.advance_tick()

    with caplog.at_level(logging.DEBUG, logger="battle_city_server"):
        asyncio.run(scenario())

    failures = [
        record for record in caplog.records if getattr(record, "event", None) == "tick_failed"
    ]
    assert len(failures) == 1
    assert getattr(failures[0], "reason", None) == RejectionCode.INTERNAL_ERROR.value
    assert getattr(failures[0], "detail", None) == "RuntimeError"
    assert failures[0].levelno == logging.ERROR


def test_the_clock_abandons_a_backlog_it_could_never_pay_off() -> None:
    """A descheduled process owes ticks. Running them back to back floods every client.

    Sixty owed ticks at sixty hertz is sixty snapshots into a bounded outbound queue,
    which overflows it and disconnects clients that were keeping up fine. Being late is
    recoverable; being disconnected is not.
    """

    async def scenario() -> None:
        now = [0.0]
        clock = RealTimeClock(60, max_catchup_ticks=4, now=lambda: now[0])

        await clock.wait_for_tick(0)
        assert clock.dropped_ticks == 0

        # A small overrun is caught up, not abandoned.
        now[0] = 0.03
        await clock.wait_for_tick(1)
        assert clock.dropped_ticks == 0

        # A long stall is abandoned: the clock re-anchors to now.
        now[0] = 10.0
        await clock.wait_for_tick(2)
        assert clock.dropped_ticks == 1

        # And having re-anchored, it is back on schedule rather than still owing ticks.
        now[0] = 10.0
        await clock.wait_for_tick(3)
        assert clock.dropped_ticks == 1

    asyncio.run(scenario())


def test_the_clock_still_yields_while_it_is_behind() -> None:
    """Writer tasks drain client queues on this loop; a catch-up must not starve them."""

    async def scenario() -> None:
        now = [0.0]
        clock = RealTimeClock(60, max_catchup_ticks=4, now=lambda: now[0])
        await clock.wait_for_tick(0)
        now[0] = 1.0

        ran = False

        async def other() -> None:
            nonlocal ran
            ran = True

        task = asyncio.create_task(other())
        await clock.wait_for_tick(1)
        assert ran
        await task

    asyncio.run(scenario())
