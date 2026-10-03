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
    MILLISECONDS_PER_SECOND,
    TANK_SPEED,
    TICK_RATE,
    TRAVEL_TICKS,
    LinkProfile,
    TrialResult,
    run_trial,
    write_pack,
)

TICK_MS: int = MILLISECONDS_PER_SECOND // TICK_RATE
"""One authoritative tick in whole milliseconds, which is what a wait is measured in."""

LATENCIES: tuple[LinkProfile, ...] = (
    LinkProfile(name="lan", delay_ms=0),
    LinkProfile(name="near", delay_ms=50),
    LinkProfile(name="far", delay_ms=100),
    LinkProfile(name="distant", delay_ms=200),
)
"""The four conditions the issue names, as one-way delays: 0, 3, 6 and 12 ticks.

All four are whole numbers of tick intervals, which is a coincidence of the numbers the
issue chose and not a property of a network. :data:`SKEWED` is here so the table is not
read as if it were.
"""

SKEWED = LinkProfile(name="skewed", delay_ms=75)
"""A steady delay that is *not* a whole number of ticks: 4.5 of them at 60 Hz.

Every delay the issue names happens to land a message in the same millisecond as the
tick that will apply it, which is the best case for response time and makes the four
rows above read as "exactly the round trip". A real link has no reason to oblige, so one
profile deliberately does not.
"""

CONSTANT: tuple[LinkProfile, ...] = (*LATENCIES, SKEWED)
"""Every steady condition. Nothing here wobbles, so nothing here should stutter."""

JITTER = LinkProfile(name="jitter", delay_ms=50, jitter_ms=10, seed=20260115)
"""A 50 ms link that wobbles by up to 10 ms either way: a 20 ms spread across a 16 ms frame."""

BURST = LinkProfile(name="burst", delay_ms=100, jitter_ms=25, seed=20260116)
"""A harsher wobble, 50 ms of spread, which is three ticks of bunching at 60 Hz."""

FAR_BURST = LinkProfile(name="far-burst", delay_ms=200, jitter_ms=25, seed=20260117)
"""The worst condition measured: the longest delay the issue names, wobbling hard.

Its own row, because distance and wobble together are not the sum of their readings --
the round trip costs the input stream so much that most of what is left to look at is
the run standing still rather than anything a renderer decides.
"""

WOBBLY: tuple[LinkProfile, ...] = (JITTER, BURST, FAR_BURST)
"""Every jittery condition."""

EVERY_PROFILE: tuple[LinkProfile, ...] = (*CONSTANT, *WOBBLY)


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Pack:
    root: Path = tmp_path_factory.mktemp("latency-pack")
    return write_pack(root)


# -- what the link actually did ------------------------------------------------


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
def test_a_steady_link_delivers_exactly_the_delay_it_was_asked_for(
    pack: Pack, profile: LinkProfile
) -> None:
    """Without jitter there is nothing for the ordering clamp to do."""
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.downlink_mean_delay_tenths == profile.delay_ms * 10
    assert result.uplink_mean_delay_tenths == profile.delay_ms * 10


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", (60, 120))
def test_a_jittery_downlink_runs_slower_than_its_nominal_delay(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """A jittery profile does not run at the delay on its label, and says so.

    The delay line keeps arrivals in order, as reliable TCP does, by holding a message
    that drew an early arrival until its predecessor has landed. That clamp can only
    move a delivery later, so although the *draw* is symmetric and has mean zero, what
    arrives does not. The downlink is where it shows: it carries a snapshot every single
    tick, so a draw that would have overtaken its predecessor usually has one to
    overtake, and the realized mean lands two to nine milliseconds above nominal.

    It is measured rather than argued about, and it is the reason every jittery
    condition in the table has a steady sibling at the same nominal delay: a reader
    comparing the two rows can see how much of the difference is the extra milliseconds
    and how much is the wobble.
    """
    result = run_trial(pack, profile=profile, frame_rate=frame_rate)
    nominal = profile.delay_ms * 10
    assert result.downlink_mean_delay_tenths > nominal
    assert result.downlink_mean_delay_tenths <= nominal + profile.jitter_ms * 10


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", (60, 120))
def test_the_sparse_uplink_is_barely_biased_at_all(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """The same clamp costs the uplink almost nothing, which is the other half of it.

    The client sends one batch per authoritative tick it has *seen*, and under jitter it
    sees fewer, so the uplink is sparse: a draw rarely has a predecessor close enough to
    be held behind. Its realized mean therefore sits within a millisecond or two of
    nominal, on either side -- below it is the finite-sample noise of the draw, because
    the clamp itself can only ever add.

    Asserted separately from the downlink so that neither reading can be taken for the
    other. The bias is a property of how densely a direction is used, not of the profile.
    """
    result = run_trial(pack, profile=profile, frame_rate=frame_rate)
    nominal = profile.delay_ms * 10
    assert abs(result.uplink_mean_delay_tenths - nominal) <= 2 * 10


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", (60, 120))
def test_the_bias_stays_under_a_tick(pack: Pack, profile: LinkProfile, frame_rate: int) -> None:
    """The clamp costs milliseconds, not ticks, so a jittery row is still about wobble.

    Stated as a threshold because it is the assumption every side-by-side reading in the
    table rests on. If the bias ever grew past a tick, a jittery row would be measuring a
    slower link as well as a less steady one, and comparing it with its steady sibling
    would stop meaning anything.
    """
    result = run_trial(pack, profile=profile, frame_rate=frame_rate)
    excess = result.realized_round_trip_tenths - profile.round_trip_ms * 10
    assert 0 < excess < TICK_MS * 10


# -- the latency axis ----------------------------------------------------------


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
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


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
def test_response_time_tracks_the_round_trip(pack: Pack, profile: LinkProfile) -> None:
    """Time from offering an input to seeing it is a round trip plus at most two ticks.

    The two ticks are the waits either end: a batch waits for the tick that applies it
    and the answering snapshot waits for a frame that draws it.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    response = result.response_ms
    assert response is not None
    assert profile.round_trip_ms <= response <= profile.round_trip_ms + 2 * TICK_MS


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
def test_a_delay_off_the_tick_boundary_costs_an_extra_tick(
    pack: Pack, profile: LinkProfile
) -> None:
    """The four delays the issue names sit on a best case the table should not hide.

    A session ticks at ``k * 1000 // 60`` and drains arrivals before running the tick
    they feed. A one-way delay that is a whole number of tick intervals therefore lands
    a batch in the same millisecond as the tick that applies it, and the snapshot
    answering it in the same millisecond as a frame: the response is the round trip and
    nothing more. 50, 100 and 200 ms all happen to be whole numbers of ticks. 75 ms is
    not, and pays for it.

    Zero is the one aligned delay that still pays a tick, because a batch is offered
    during a frame, which is after that millisecond's delivery step has already run.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    response = result.response_ms
    assert response is not None
    if profile.tick_aligned and profile.delay_ms > 0:
        assert response == profile.round_trip_ms
    else:
        assert profile.round_trip_ms < response <= profile.round_trip_ms + 2 * TICK_MS


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
        for profile in CONSTANT
    }
    assert all(value >= 450 for value in duplicated.values()), duplicated


# -- the jitter axis -----------------------------------------------------------


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
def test_jitter_both_stalls_and_doubles_frames(pack: Pack, profile: LinkProfile) -> None:
    """Wobble bunches snapshots, so some frames show nothing and others show two ticks.

    A frame that receives no snapshot redraws the last one; the next frame receives two
    and jumps twice as far. Both halves are measured, because a reading that counted
    only the still frames would be satisfied by a client that stuttered in one direction.
    """
    result = run_trial(pack, profile=profile, frame_rate=60)
    assert result.trace.still_frames > 0
    assert result.trace.max_step >= 2 * TANK_SPEED


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
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


@pytest.mark.parametrize("profile", EVERY_PROFILE, ids=lambda profile: profile.name)
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


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
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
