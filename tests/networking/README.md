# Networking measurements

Phase 8 of the roadmap asks a question before it asks for code: *what does an online
match actually look like at 0, 50, 100 and 200 ms, and is anything worth doing about
it?* This directory is the answer, and the measurement that produced it.

## How the measurement works

There are no sockets here. `networking_helpers.py` runs the real
`battle_city_server.GameSession` and the real `battle_city_client.online.OnlineSession`
against each other, message by message, on one integer millisecond clock. Between them
sit two delay lines that are lossless and strictly in order — the shipped transport is
reliable TCP, so a measurement that dropped or reordered messages would be measuring a
transport this build does not have. Jitter comes from a seeded `random.Random`.

Everything is therefore reproducible to the pixel: same stage, same seed, same jitter
draw, same frames at the same milliseconds, on any machine.

* **Session**: 60 Hz, one player slot, keyframe interval 30, shipped `SessionLimits`.
* **Stage**: a 16x16 open arena written as a real content pack and loaded through
  `battle_city_content.load_pack`, so the simulation's stage contract is satisfied.
* **Input**: hold right, never fire, for the whole trial. The tank crosses 224 px of
  clear ground at 2 px/tick, which is 112 ticks of steady, measurable motion.
* **Delay**: the quoted figure is **one way**; a round trip costs twice it. Jitter is a
  symmetric bound either side, so each *draw* has mean zero.
* **Realized delay**: what each direction actually ran at, measured per trial. It is not
  the same as the nominal, and the difference is the one place these numbers are not
  what the labels say — see *What the delay lines really deliver* below.
* **Reading**: the horizontal pixel the renderer would blit this client's tank at, once
  per rendered frame, over the moving window only.

Regenerate the table with:

```
uv run --locked python tests/networking/report.py
```

### What the delay lines really deliver

Keeping a stream in order means a message that drew an early arrival waits for its
predecessor. That clamp can only move a delivery **later**, so a jittery profile does not
run at the delay on its label, however symmetric the draw is. The table therefore carries
the realized round trip rather than asking anyone to assume it:

| nominal rtt | realized rtt | where the bias comes from |
| ----------- | ------------ | ------------------------- |
| 100 ms (50 ±10)  | 102.2–102.9 ms | downlink +2.2 to +2.4; uplink +0.0 to +0.5 |
| 200 ms (100 ±25) | 209.1–209.5 ms | downlink +8.5 to +9.5; uplink +0.0 to +0.6 |
| 400 ms (200 ±25) | 406.9–408.8 ms | downlink +7.5 to +7.8; uplink −0.9 to +1.3 |

It lands almost entirely on the **downlink**, which carries a snapshot every single tick,
so a draw that would overtake its predecessor usually has one to overtake. The uplink is
sparse — one batch per tick the client has *seen*, and under jitter it sees fewer — so
the clamp barely binds there and its mean sits within a millisecond or two of nominal on
either side; below nominal is the finite-sample noise of the draw, since the clamp itself
can only add.

The bias stays under one tick in every condition, which is what keeps a jittery row
comparable with its steady sibling at the same nominal delay: the few extra milliseconds
are visible in the table, and the rest of the difference is the wobble.
`test_latency_baseline.py` asserts all three claims — steady links exact, jittery
downlink strictly slower and bounded by the jitter, round-trip excess under a tick.

### Why the response times sit on a best case

A session ticks at `k * 1000 // 60`, and a server drains its socket before running the
tick that socket feeds — as this harness does. So a one-way delay that is a **whole
number of tick intervals** lands a batch in the same millisecond as the tick that applies
it, and lands the snapshot answering it in the same millisecond as a frame. Nothing waits,
and the response is exactly the round trip.

Every delay the issue names happens to be one: 50, 100 and 200 ms are 3, 6 and 12 ticks.
A real link has no reason to oblige, so the table carries a **75 ms** profile that is 4.5
ticks and pays 16 ms more than its 150 ms round trip at 60 fps, and 8 ms more at 120. Zero is the one aligned delay that
still pays a tick, because a batch is offered during a frame — after that millisecond's
delivery step has already run.

The quantization is at most a tick at each end, so it never changes which problem a row
is about. It is spelled out because "the response is the round trip" is a property of the
numbers chosen, not of the client.

## Baseline (`smoothing_ticks = 0`)

| condition    | fps | realized rtt ms | still frames    | skipped-tick frames | response ms | batches sent | collapsed | command-less ticks | still ticks in the run |
| ------------ | --- | --------------- | --------------- | ------------------- | ----------- | ------------ | --------- | ------------------ | ---------------------- |
| 0 ms         | 60  | 0.0             | 0/112 (0.0%)    | 0                   | 16          | 181          | 0         | 0                  | 0 |
| 0 ms         | 120 | 0.0             | 111/223 (49.7%) | 0                   | 16          | 181          | 0         | 0                  | 0 |
| 50 ms        | 60  | 100.0           | 0/112 (0.0%)    | 0                   | 100         | 175          | 0         | 0                  | 0 |
| 50 ms        | 120 | 100.0           | 111/223 (49.7%) | 0                   | 100         | 175          | 0         | 0                  | 0 |
| 75 ms        | 60  | 150.0           | 0/112 (0.0%)    | 0                   | 166         | 172          | 0         | 0                  | 0 |
| 75 ms        | 120 | 150.0           | 111/223 (49.7%) | 0                   | 158         | 172          | 1         | 0                  | 0 |
| 100 ms       | 60  | 200.0           | 0/112 (0.0%)    | 0                   | 200         | 169          | 0         | 0                  | 0 |
| 100 ms       | 120 | 200.0           | 111/223 (49.7%) | 0                   | 200         | 169          | 0         | 0                  | 0 |
| 200 ms       | 60  | 400.0           | 0/112 (0.0%)    | 0                   | 400         | 157          | 0         | 0                  | 0 |
| 200 ms       | 120 | 400.0           | 111/223 (49.7%) | 0                   | 400         | 157          | 0         | 0                  | 0 |
| 50 ms +/-10  | 60  | 102.2           | 77/167 (46.1%)  | 13                  | 117         | 127          | 19        | 65                 | 66 |
| 50 ms +/-10  | 120 | 102.9           | 226/336 (67.2%) | 2                   | 92          | 163          | 44        | 56                 | 56 |
| 100 ms +/-25 | 60  | 209.1           | 90/153 (58.8%)  | 8                   | 234         | 102          | 24        | 81                 | 86 |
| 100 ms +/-25 | 120 | 209.5           | 232/302 (76.8%) | 5                   | 242         | 122          | 39        | 79                 | 82 |
| 200 ms +/-25 | 60  | 406.9           | 77/129 (59.6%)  | 6                   | 433         | 95           | 21        | 73                 | 80 |
| 200 ms +/-25 | 120 | 408.8           | 202/260 (77.6%) | 11                  | 416         | 113          | 27        | 64                 | 70 |

## Improved (`smoothing_ticks = 2`)

| condition    | fps | realized rtt ms | still frames    | skipped-tick frames | response ms | batches sent | collapsed | command-less ticks | still ticks in the run |
| ------------ | --- | --------------- | --------------- | ------------------- | ----------- | ------------ | --------- | ------------------ | ---------------------- |
| 0 ms         | 60  | 0.0             | 0/112 (0.0%)    | 0                   | 50          | 181          | 0         | 0                  | 0 |
| 0 ms         | 120 | 0.0             | 33/225 (14.6%)  | 0                   | 41          | 181          | 0         | 0                  | 0 |
| 50 ms        | 60  | 100.0           | 0/112 (0.0%)    | 0                   | 133         | 175          | 0         | 0                  | 0 |
| 50 ms        | 120 | 100.0           | 33/225 (14.6%)  | 0                   | 125         | 175          | 0         | 0                  | 0 |
| 75 ms        | 60  | 150.0           | 0/112 (0.0%)    | 0                   | 200         | 172          | 0         | 0                  | 0 |
| 75 ms        | 120 | 150.0           | 0/224 (0.0%)    | 0                   | 191         | 172          | 1         | 0                  | 0 |
| 100 ms       | 60  | 200.0           | 0/112 (0.0%)    | 0                   | 233         | 169          | 0         | 0                  | 0 |
| 100 ms       | 120 | 200.0           | 33/225 (14.6%)  | 0                   | 225         | 169          | 0         | 0                  | 0 |
| 200 ms       | 60  | 400.0           | 0/112 (0.0%)    | 0                   | 433         | 157          | 0         | 0                  | 0 |
| 200 ms       | 120 | 400.0           | 33/225 (14.6%)  | 0                   | 425         | 157          | 0         | 0                  | 0 |
| 50 ms +/-10  | 60  | 102.2           | 30/165 (18.1%)  | 0                   | 150         | 127          | 19        | 65                 | 66 |
| 50 ms +/-10  | 120 | 102.9           | 110/332 (33.1%) | 0                   | 125         | 163          | 44        | 56                 | 56 |
| 100 ms +/-25 | 60  | 209.1           | 62/153 (40.5%)  | 0                   | 250         | 102          | 24        | 81                 | 86 |
| 100 ms +/-25 | 120 | 209.5           | 156/305 (51.1%) | 0                   | 250         | 122          | 39        | 79                 | 82 |
| 200 ms +/-25 | 60  | 406.9           | 69/126 (54.7%)  | 0                   | 450         | 95           | 21        | 73                 | 80 |
| 200 ms +/-25 | 120 | 408.8           | 124/258 (48.0%) | 0                   | 433         | 113          | 27        | 64                 | 70 |

*realized rtt* is the round trip the trial actually ran at, measured, not the label.
*still frames* are frames that redrew the tank exactly where the previous frame left it.
*skipped-tick frames* moved it a whole extra tick's worth of pixels, which means a frame
drew one snapshot and threw another away. *response* is the time from offering the first
input to seeing the tank move. *still ticks in the run* is how many ticks the **server**
did not move the tank once it had set off, which is the floor under any stutter reading:
no renderer should or can hide a tick the player's input never reached.

## What the baseline says

**Constant latency does not stutter.** At every one of 0, 50, 75, 100 and 200 ms the
60 fps client draws 112 frames of motion with not one duplicate and not one double step.
A constant delay shifts the whole snapshot stream later without changing its spacing, so
it costs response time -- 16, 100, 166, 200 and 400 ms, which is the round trip plus
whatever the batch and the answering snapshot wait for a tick and a frame -- and costs
the picture nothing. Nothing in this issue can improve that reading: drawing the tank
somewhere the server has not put it yet is prediction, which the networking
specification reserves for measured need, protocol versioning and acceptance tests.

**Stutter comes from spacing, and has two causes.**

1. *The frame rate.* A 120 fps client on a 60 Hz session shows every snapshot twice:
   49.7% of frames are duplicates on a **perfect** link. This is the largest single
   reading in the table and it has no network in it at all.
2. *Jitter.* Wobble bunches snapshots, so some frames receive none and redraw the last
   one while the next receives two and jumps 4 px instead of 2.

The two compound: 120 fps under jitter reaches 67-77% still frames.

**Jitter also costs the input stream, and that is a separate problem.** The client
offers one batch per authoritative tick it has *seen*. When two snapshots land on one
frame, the tick in between never gets a batch, and the server runs it with nothing from
this player: 56 to 81 command-less ticks in a 180-tick trial, which is exactly where the
56 to 86 still ticks in the authoritative run come from. Separately, when the uplink
bunches two batches into one server tick interval the second replaces the first in the
slot's queue -- 19 to 44 collapsed batches. No condition in the table produces a single
refusal, so none of this is the client hitting a server bound.

**At the worst condition the input problem is the whole of it.** At 200 ms wobbling by
25, at 60 fps, the baseline draws 77 still frames in a 129-frame window while the
authoritative run stood still for 80 ticks. The picture is already stiller than the run,
which means there is nothing there for a renderer to take away: what a player at that
link is looking at is their own input not arriving, not their client drawing badly.

## What was done about it

`battle_city_client.interpolation` renders the playfield two ticks behind the newest
snapshot, on a render clock of its own, placing moving entities on the straight line
between the two authoritative positions that bracket it. It is on by default. Nothing
else changed: `OnlineSession.board` -- what the HUD reads, what the state hash on screen
comes from, what input is offered against -- is the newest snapshot exactly as before,
and `tests/networking/test_smoothing.py` runs every condition twice to prove the server
recorded the same run, from the same batches, hash for hash.

* **Duplicated frames at 120 fps: 49.7% -> 14.6%** (0% at 75 ms), the same at every
  constant delay, because this was never a latency problem.
* **Skipped-tick frames under jitter: 13, 8, 5, 2, 6, 11 -> 0 on every row.** Every
  snapshot is held and played through, so the largest step the renderer makes is the
  largest step the simulation made. The baseline drew only the newest of a bunched pair
  and threw the other away.
* **Still frames under jitter: 46-78% -> 18-55%**, and what remains is below the
  authoritative still-tick count on every row, which is the measurement saying the rest
  is the input problem rather than the renderer's. The smallest gain is at 200 ms ±25
  and 60 fps, where the baseline had already fallen below that floor and there was
  essentially nothing to win; the largest at the same link is at 120 fps, 77.6% -> 48.0%,
  where duplication rather than the input stream dominated.
* **At 60 fps on a steady link, nothing changed at all**: 0 duplicated frames before and
  after, no step larger than one tick of travel. Smoothing does not introduce a wobble
  into motion that was already right.
* **The cost is at most 33 ms of response time**, two ticks at 60 Hz. Steady rows pay
  25 to 34 ms -- the two ticks, give or take the frame the movement is first seen on --
  and jittery rows pay *less*, 8 to 33 ms, because a frame that was already waiting on a
  snapshot that had not arrived loses nothing by being drawn a little behind.

## Remaining risks and what was deliberately not done

* **Residual input delay.** The picture is two ticks older than the authoritative state.
  A player at 200 ms now sees their input land 433 ms after offering it rather than
  400 ms. Shortening that is prediction, not interpolation.
* **The input stream is untouched.** Command-less ticks (56-81 per trial) and collapsed
  batches (19-44) under jitter are recorded and not fixed. Closing the first means
  sending input for a tick the client has not been told about yet, or scheduling a batch
  ahead of the server's current tick; closing the second means the client asserting a
  `target_tick` rather than letting the server place the batch. Each changes what the
  client claims about authoritative time -- the prediction and reconciliation
  conversation the networking specification reserves for measured need, protocol
  versioning and acceptance tests, with the protocol-version decision owned by an
  integration issue rather than this one. Raised as a follow-up.
* **No protocol change, no bandwidth cost.** Interpolation reads snapshots the server
  already sends. No message, field, version or rate moved, so an interpolating client
  and a Phase 7 client are the same peer on the wire.
* **Clock drift is bounded, not eliminated.** The render clock is corrected towards the
  stream by a nudge of a sixty-fourth of its error each frame, itself capped at an
  eighth of the frame's own playback, and is hard-bounded to the newest tick above and
  six ticks behind it below. A client whose clock ran far enough
  out would re-anchor visibly rather than drift; the measurements do not exercise that,
  because a 1-tick-per-half-hour quartz difference takes longer than a match.
* **One moving entity, one axis, one stage.** The traces measure a player tank crossing
  open ground rightwards. Interpolation is applied to tanks and projectiles alike and is unit
  tested for identity matching, spawns, deaths and respawn jumps in
  `test_interpolation.py`, but the stutter percentages themselves come from one shape of
  motion.
* **Measured, not played.** These are numbers from a deterministic harness, not a player
  at a window. They say the picture moves when it should; they do not say it feels good.
