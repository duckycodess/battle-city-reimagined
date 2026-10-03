"""The same trials as the baseline, with interpolation on, and what it changed.

Every test here runs one scenario twice — once with ``smoothing_ticks=0``, which is the
client exactly as Phase 7 shipped it, and once with the shipped
:data:`~battle_city_client.interpolation.SMOOTHING_TICKS` — over the same stage, the same
seed, the same jitter draw and the same frames at the same milliseconds. The only thing
that differs between the two numbers in any assertion below is that setting.

The three claims, in the order the baseline raised them:

1. Rendering above the tick rate no longer duplicates half the frames.
2. A jittery link no longer skips whole ticks of motion in a single frame.
3. The run the server recorded is identical, byte for byte of its state hash, and so is
   every input message that reached it.

And the one cost, measured rather than asserted away: the picture is two ticks older
than the newest snapshot. On a steady link that is the whole of what response time
grows by; on a jittery one it is less, because the baseline there was already waiting on
a snapshot that had not arrived.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from battle_city_client.interpolation import SMOOTHING_TICKS
from battle_city_content import Pack
from networking_helpers import (
    TANK_SPEED,
    TICK_RATE,
    LinkProfile,
    TrialResult,
    run_trial,
    write_pack,
)
from test_latency_baseline import CONSTANT, EVERY_PROFILE, WOBBLY

FRAME_RATES: tuple[int, ...] = (60, 120)

DUPLICATE_FRAME_FLOOR: int = 450
"""Per mille of duplicated frames the baseline shows at 120 fps. Measured, not assumed."""

DUPLICATE_FRAME_CEILING: int = 200
"""Per mille the interpolated client must stay under at 120 fps. Measured at 146."""

JITTER_IMPROVEMENT: int = 150
"""Per mille of stutter interpolation must remove under jitter, where there is room.

Measured at 183 to 341 on every condition but one. The exception is the worst of them --
200 ms wobbling by 25 -- where at 60 fps the baseline picture is *already* stiller than
the authoritative run, because the round trip costs the input stream so many ticks that
the tank spends most of the trial genuinely stopped. There is nothing there for a
renderer to remove, so the test asks for whatever room the run actually leaves; see
:func:`test_jitter_stutter_drops_as_far_as_there_is_room`.
"""


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Pack:
    root: Path = tmp_path_factory.mktemp("smoothing-pack")
    return write_pack(root)


def _pair(pack: Pack, profile: LinkProfile, frame_rate: int) -> tuple[TrialResult, TrialResult]:
    """The same trial without and with the shipped smoothing."""
    return (
        run_trial(pack, profile=profile, frame_rate=frame_rate, smoothing_ticks=0),
        run_trial(pack, profile=profile, frame_rate=frame_rate, smoothing_ticks=SMOOTHING_TICKS),
    )


# -- the frame-rate axis -------------------------------------------------------


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
def test_a_120fps_client_stops_drawing_every_snapshot_twice(
    pack: Pack, profile: LinkProfile
) -> None:
    """The largest reading in the baseline table, at every constant delay.

    Half the frames a 120 fps display showed were a copy of the previous one. With
    interpolation they are frames of their own, each a pixel further on, and the figure
    is the same at 0 ms as it is at 200 ms because this was never a latency problem.
    """
    baseline, smoothed = _pair(pack, profile, 120)
    assert baseline.trace.still_permille >= DUPLICATE_FRAME_FLOOR
    assert smoothed.trace.still_permille <= DUPLICATE_FRAME_CEILING


@pytest.mark.parametrize("profile", CONSTANT, ids=lambda profile: profile.name)
def test_a_60fps_client_is_left_exactly_as_it_was(pack: Pack, profile: LinkProfile) -> None:
    """Where there was nothing to fix, nothing changed.

    A client rendering at the tick rate on a steady link already drew one new snapshot
    per frame. Interpolation must not introduce a wobble into motion that was already
    perfect, so the reading stays at no duplicated frames and no step larger than the
    one tick of travel the simulation makes.
    """
    baseline, smoothed = _pair(pack, profile, 60)
    assert baseline.trace.still_frames == 0
    assert smoothed.trace.still_frames == 0
    assert smoothed.trace.max_step == TANK_SPEED


# -- the jitter axis -----------------------------------------------------------


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", FRAME_RATES)
def test_no_frame_ever_skips_a_tick_of_motion(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """A jittery baseline jumps the tank two ticks at once. An interpolated one cannot.

    When two snapshots land on one frame the baseline draws only the newer, so the tank
    teleports four pixels where the simulation never moves it more than two. Every one
    of those snapshots is held and played through instead, so the largest step the
    renderer can make is the largest step the simulation made.
    """
    baseline, smoothed = _pair(pack, profile, frame_rate)
    assert baseline.trace.double_steps > 0
    assert smoothed.trace.double_steps == 0


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", FRAME_RATES)
def test_jitter_stutter_drops_as_far_as_there_is_room(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """Interpolation removes a large share of the stalled frames jitter causes.

    "A large share" is bounded by what is there to remove. A tick the player's input
    never reached is a tick the tank genuinely did not move, and the share of the window
    those ticks occupy is a floor no renderer may go under. The claim is therefore the
    measured 150 per mille, or the whole of the room the run leaves if that is less --
    which at 200 ms wobbling by 25, at 60 fps, is none at all: the baseline there is
    already stiller than the authoritative run, and the honest reading is that the
    picture is the input problem rather than a rendering one.
    """
    baseline, smoothed = _pair(pack, profile, frame_rate)
    floor_permille = _floor_permille(smoothed)
    room = baseline.trace.still_permille - floor_permille
    assert smoothed.trace.still_permille <= baseline.trace.still_permille
    assert smoothed.trace.still_permille + min(JITTER_IMPROVEMENT, room) <= (
        baseline.trace.still_permille
    )


def _floor_permille(result: TrialResult) -> int:
    """Per mille of the measured window that the authoritative run spent standing still."""
    frames = result.trace.frames
    return 0 if frames == 0 else result.authoritative_still_ticks * 1000 // frames


@pytest.mark.parametrize("profile", WOBBLY, ids=lambda profile: profile.name)
def test_the_stillness_that_remains_is_the_run_standing_still(
    pack: Pack, profile: LinkProfile
) -> None:
    """What is left under jitter is the input problem, not a rendering one.

    Jitter costs this client whole ticks of input, and a tick the server ran with
    nothing from the player is a tick the tank genuinely did not move. The baseline
    table records 56 to 81 of those per trial. Interpolation shows strictly fewer still
    frames than the authoritative run had still ticks, which is the measurement that
    says the remaining stutter is not the renderer's to remove — and which would fail if
    this module ever started smoothing over a stop the server really made.
    """
    _, smoothed = _pair(pack, profile, 60)
    assert smoothed.authoritative_still_ticks > 0
    assert smoothed.trace.still_frames < smoothed.authoritative_still_ticks


# -- the cost ------------------------------------------------------------------


@pytest.mark.parametrize("profile", EVERY_PROFILE, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", FRAME_RATES)
def test_the_only_cost_is_the_playback_delay(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """Response time grows by at most the smoothing delay, and never by more.

    Two ticks is 33 ms at 60 Hz; the allowance on top is one frame, because the input is
    offered on a frame boundary and the movement is seen on one. It is an upper bound
    rather than an equality because a jittery link pays less: the baseline there was
    already waiting on a snapshot that had not arrived, and playing back a little behind
    costs nothing on a frame that had nothing new to draw anyway.
    """
    baseline, smoothed = _pair(pack, profile, frame_rate)
    assert baseline.response_ms is not None
    assert smoothed.response_ms is not None
    added = smoothed.response_ms - baseline.response_ms
    budget = SMOOTHING_TICKS * 1000 // TICK_RATE + 1000 // frame_rate
    assert 0 <= added <= budget


# -- what must not have changed ------------------------------------------------


@pytest.mark.parametrize("profile", EVERY_PROFILE, ids=lambda profile: profile.name)
@pytest.mark.parametrize("frame_rate", FRAME_RATES)
def test_the_authoritative_run_is_untouched(
    pack: Pack, profile: LinkProfile, frame_rate: int
) -> None:
    """The server recorded the same run, from the same input, under every condition.

    This is the assertion that makes the change safe to ship. Interpolation is in the
    draw path and nowhere else: the same batches were sent, the same ones were accepted
    for the same ticks, the same number collapsed, nothing new was refused, and the
    canonical state hash at the end of the trial is identical.
    """
    baseline, smoothed = _pair(pack, profile, frame_rate)
    assert smoothed.state_hash == baseline.state_hash
    assert smoothed.final_tick == baseline.final_tick
    assert smoothed.batches_sent == baseline.batches_sent
    assert smoothed.accepted_ticks == baseline.accepted_ticks
    assert smoothed.collapsed == baseline.collapsed
    assert smoothed.commandless_ticks == baseline.commandless_ticks
    assert smoothed.rejections == baseline.rejections
