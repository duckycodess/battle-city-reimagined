"""What an online match looks like before anything is done about it.

These are the Phase 8 baseline measurements. Each test states one thing the harness
observes and pins it with an integer threshold, so the table in ``README.md`` is not a
paragraph somebody wrote down once but the output of a check that fails when it stops
being true.

The headline, stated here because every threshold below depends on it: **constant
latency and jitter are different problems.** A constant one-way delay moves the whole
snapshot stream later without changing its spacing, so the client still gets exactly one
new snapshot per frame and draws perfectly smooth motion — it just draws it late.
Stutter comes from the *spacing* changing, which has two causes in this build: jitter,
and a frame rate that is not the tick rate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from battle_city_content import Pack
from networking_helpers import (
    TANK_SPEED,
    TRAVEL_TICKS,
    LinkProfile,
    TrialResult,
    run_trial,
    write_pack,
)

LATENCIES: tuple[LinkProfile, ...] = (
    LinkProfile(name="lan", delay_ms=0),
    LinkProfile(name="near", delay_ms=50),
    LinkProfile(name="far", delay_ms=100),
    LinkProfile(name="distant", delay_ms=200),
)
"""The four conditions the issue names, as one-way delays: 0, 3, 6 and 12 ticks."""

JITTER = LinkProfile(name="jitter", delay_ms=50, jitter_ms=10, seed=20260115)
"""A 50 ms link that wobbles by up to 10 ms either way: a 20 ms spread across a 16 ms frame."""

BURST = LinkProfile(name="burst", delay_ms=100, jitter_ms=25, seed=20260116)
"""A harsher wobble, 50 ms of spread, which is three ticks of bunching at 60 Hz."""


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Pack:
    root: Path = tmp_path_factory.mktemp("latency-pack")
    return write_pack(root)


# -- the latency axis ----------------------------------------------------------


@pytest.mark.parametrize("profile", LATENCIES, ids=lambda profile: profile.name)
def test_constant_latency_does_not_stutter(pack: Pack, profile: LinkProfile) -> None:
    """At 60 fps on a 60 Hz session, a constant delay costs nothing but time.

    Every frame still receives exactly one snapshot, so the tank advances its two pixels
    on every single frame of the moving window whatever the delay is. This is the result
    that decides what the improvement in this issue may be: there is no stutter here to
    remove, and anything that made the picture arrive *sooner* than the server decided it
    would be prediction, which the networking specification reserves.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.trace.still_frames == 0
    assert result.trace.max_step == TANK_SPEED
    assert result.trace.frames >= TRAVEL_TICKS - 2


@pytest.mark.parametrize("profile", LATENCIES, ids=lambda profile: profile.name)
def test_response_time_tracks_the_round_trip(pack: Pack, profile: LinkProfile) -> None:
    """Time from offering an input to seeing it is a round trip plus about a tick.

    The allowance is a frame either side: the input is offered on a frame boundary and
    the movement is seen on one, and at 60 fps a frame is 17 ms.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    response = result.response_ms
    assert response is not None
    assert profile.round_trip_ms <= response <= profile.round_trip_ms + 50


# -- the frame-rate axis -------------------------------------------------------


def test_rendering_above_the_tick_rate_duplicates_half_the_frames(pack: Pack) -> None:
    """A 120 fps client on a 60 Hz session draws every snapshot twice.

    This is stutter with no network in it at all: the link here is perfect. Half the
    frames the player's display showed them were a copy of the previous one, which is
    the cost of rendering raw snapshots rather than interpolating between them.
    """
    result = run_trial(pack, profile=LATENCIES[0], frame_rate=120)
    assert result.trace.still_permille >= 450
    assert result.trace.max_step == TANK_SPEED


def test_the_frame_rate_axis_is_independent_of_the_latency_axis(pack: Pack) -> None:
    """Duplicated frames at 120 fps are the same count at every constant delay."""
    duplicated = {
        profile.name: run_trial(pack, profile=profile, frame_rate=120).trace.still_permille
        for profile in LATENCIES
    }
    assert all(value >= 450 for value in duplicated.values()), duplicated


# -- the jitter axis -----------------------------------------------------------


@pytest.mark.parametrize("profile", (JITTER, BURST), ids=lambda profile: profile.name)
def test_jitter_both_stalls_and_doubles_frames(pack: Pack, profile: LinkProfile) -> None:
    """Wobble bunches snapshots, so some frames show nothing and others show two ticks.

    A frame that receives no snapshot redraws the last one; the next frame receives two
    and jumps twice as far. Both halves are measured, because a reading that counted
    only the still frames would be satisfied by a client that stuttered in one direction.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.trace.still_frames > 0
    assert result.trace.max_step >= 2 * TANK_SPEED


@pytest.mark.parametrize("profile", (JITTER, BURST), ids=lambda profile: profile.name)
def test_jitter_costs_the_input_stream_whole_ticks(pack: Pack, profile: LinkProfile) -> None:
    """Under jitter the server runs ticks this client supplied no input for.

    Two things happen, and both are counted because they have different causes. The
    client offers one batch per authoritative tick it has *seen*, so when two snapshots
    land on one frame the older tick never gets a batch of its own — a command-less
    tick, where the server ran with nothing from this player and the tank coasted. And
    when the uplink bunches two batches into one server tick interval, the second
    replaces the first in the slot's queue: a collapsed batch, whose actions never
    reached the simulation at all.

    Both are recorded here and neither is fixed by this issue. Closing the first means
    sending input for a tick the client has not been told about yet, or scheduling input
    ahead of the server's current tick; closing the second means the client asserting a
    target tick rather than letting the server place the batch. Each is a change to what
    the client claims about authoritative time, which is the prediction conversation the
    networking specification reserves for measured need, protocol versioning and
    acceptance tests of its own. See ``README.md`` for the follow-up.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.commandless_ticks > 0
    assert result.collapsed > 0
    assert result.accepted_ticks


# -- what the link never costs -------------------------------------------------


@pytest.mark.parametrize("profile", (*LATENCIES, JITTER, BURST), ids=lambda profile: profile.name)
def test_no_link_condition_is_refused(pack: Pack, profile: LinkProfile) -> None:
    """Latency and jitter never push this client into a server bound.

    One batch per authoritative tick is well inside the four-per-tick allowance, the
    sequence is monotonic by construction, and no condition here makes the client speak
    faster than it would on a perfect link. So the whole rejection tally is empty, and
    the stutter above is a presentation problem rather than a client being throttled.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.rejections == ()
    assert result.rate_limited == 0


@pytest.mark.parametrize("profile", LATENCIES, ids=lambda profile: profile.name)
def test_constant_latency_delivers_every_batch_to_its_own_tick(
    pack: Pack, profile: LinkProfile
) -> None:
    """On a steady link every batch this client sent reached a tick of its own."""
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.collapsed == 0
    assert result.commandless_ticks == 0


@pytest.mark.parametrize("frame_rate", (60, 120))
def test_the_frame_rate_cannot_change_the_run(pack: Pack, frame_rate: int) -> None:
    """A faster display draws the same authoritative run, hash for hash.

    The client steps nothing, so its frame rate reaches the simulation only through when
    it offers input. It offers one batch per authoritative tick either way, so the run
    the server recorded is identical.
    """
    results: list[TrialResult] = [
        run_trial(pack, profile=LATENCIES[0], frame_rate=rate) for rate in (60, frame_rate)
    ]
    assert results[0].state_hash == results[1].state_hash
    assert results[0].final_tick == results[1].final_tick
