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
  symmetric bound either side, so the mean delay stays equal to the quoted figure and
  the jitter axis stays independent of the latency axis.
* **Reading**: the horizontal pixel the renderer would blit this client's tank at, once
  per rendered frame, over the moving window only.

Regenerate the table with:

```
uv run --locked python tests/networking/report.py
```

## Baseline (`smoothing_ticks = 0`)

| condition    | fps | still frames    | skipped-tick frames | response ms | batches sent | collapsed | command-less ticks | still ticks in the run |
| ------------ | --- | --------------- | ------------------- | ----------- | ------------ | --------- | ------------------ | ---------------------- |
| 0 ms         | 60  | 0/112 (0.0%)    | 0                   | 16          | 181          | 0         | 0                  | 0 |
| 0 ms         | 120 | 111/223 (49.7%) | 0                   | 16          | 181          | 0         | 0                  | 0 |
| 50 ms        | 60  | 0/112 (0.0%)    | 0                   | 100         | 175          | 0         | 0                  | 0 |
| 50 ms        | 120 | 111/223 (49.7%) | 0                   | 100         | 175          | 0         | 0                  | 0 |
| 100 ms       | 60  | 0/112 (0.0%)    | 0                   | 200         | 169          | 0         | 0                  | 0 |
| 100 ms       | 120 | 111/223 (49.7%) | 0                   | 200         | 169          | 0         | 0                  | 0 |
| 200 ms       | 60  | 0/112 (0.0%)    | 0                   | 400         | 157          | 0         | 0                  | 0 |
| 200 ms       | 120 | 111/223 (49.7%) | 0                   | 400         | 157          | 0         | 0                  | 0 |
| 50 ms +/-10  | 60  | 77/167 (46.1%)  | 13                  | 117         | 127          | 19        | 65                 | 66 |
| 50 ms +/-10  | 120 | 226/336 (67.2%) | 2                   | 92          | 163          | 44        | 56                 | 56 |
| 100 ms +/-25 | 60  | 90/153 (58.8%)  | 8                   | 234         | 102          | 24        | 81                 | 86 |
| 100 ms +/-25 | 120 | 232/302 (76.8%) | 5                   | 242         | 122          | 39        | 79                 | 82 |

## Improved (`smoothing_ticks = 2`)

| condition    | fps | still frames    | skipped-tick frames | response ms | batches sent | collapsed | command-less ticks | still ticks in the run |
| ------------ | --- | --------------- | ------------------- | ----------- | ------------ | --------- | ------------------ | ---------------------- |
| 0 ms         | 60  | 0/112 (0.0%)    | 0                   | 50          | 181          | 0         | 0                  | 0 |
| 0 ms         | 120 | 33/225 (14.6%)  | 0                   | 41          | 181          | 0         | 0                  | 0 |
| 50 ms        | 60  | 0/112 (0.0%)    | 0                   | 133         | 175          | 0         | 0                  | 0 |
| 50 ms        | 120 | 33/225 (14.6%)  | 0                   | 125         | 175          | 0         | 0                  | 0 |
| 100 ms       | 60  | 0/112 (0.0%)    | 0                   | 233         | 169          | 0         | 0                  | 0 |
| 100 ms       | 120 | 33/225 (14.6%)  | 0                   | 225         | 169          | 0         | 0                  | 0 |
| 200 ms       | 60  | 0/112 (0.0%)    | 0                   | 433         | 157          | 0         | 0                  | 0 |
| 200 ms       | 120 | 33/225 (14.6%)  | 0                   | 425         | 157          | 0         | 0                  | 0 |
| 50 ms +/-10  | 60  | 30/165 (18.1%)  | 0                   | 150         | 127          | 19        | 65                 | 66 |
| 50 ms +/-10  | 120 | 110/332 (33.1%) | 0                   | 125         | 163          | 44        | 56                 | 56 |
| 100 ms +/-25 | 60  | 62/153 (40.5%)  | 0                   | 250         | 102          | 24        | 81                 | 86 |
| 100 ms +/-25 | 120 | 156/305 (51.1%) | 0                   | 250         | 122          | 39        | 79                 | 82 |

*still frames* are frames that redrew the tank exactly where the previous frame left it.
*skipped-tick frames* moved it a whole extra tick's worth of pixels, which means a frame
drew one snapshot and threw another away. *response* is the time from offering the first
input to seeing the tank move. *still ticks in the run* is how many ticks the **server**
did not move the tank once it had set off, which is the floor under any stutter reading:
no renderer should or can hide a tick the player's input never reached.

## What the baseline says

**Constant latency does not stutter.** At every one of 0, 50, 100 and 200 ms the 60 fps
client draws 112 frames of motion with not one duplicate and not one double step. A
constant delay shifts the whole snapshot stream later without changing its spacing, so
it costs response time -- 16, 100, 200 and 400 ms, which is the round trip plus a tick --
and costs the picture nothing. Nothing in this issue can improve that reading: drawing
the tank somewhere the server has not put it yet is prediction, which the networking
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
66 to 86 still ticks in the authoritative run come from. Separately, when the uplink
bunches two batches into one server tick interval the second replaces the first in the
slot's queue -- 19 to 44 collapsed batches. No condition in the table produces a single
refusal, so none of this is the client hitting a server bound.

## What was done about it

`battle_city_client.interpolation` renders the playfield two ticks behind the newest
snapshot, on a render clock of its own, placing moving entities on the straight line
between the two authoritative positions that bracket it. It is on by default. Nothing
else changed: `OnlineSession.board` -- what the HUD reads, what the state hash on screen
comes from, what input is offered against -- is the newest snapshot exactly as before,
and `tests/networking/test_smoothing.py` runs every condition twice to prove the server
recorded the same run, from the same batches, hash for hash.

* **Duplicated frames at 120 fps: 49.7% -> 14.6%**, identically at every constant delay,
  because this was never a latency problem.
* **Skipped-tick frames under jitter: 13, 8, 5, 2 -> 0.** Every snapshot is held and
  played through, so the largest step the renderer makes is the largest step the
  simulation made. The baseline drew only the newest of a bunched pair and threw the
  other away.
* **Still frames under jitter: 46-77% -> 18-51%**, and what remains is below the
  authoritative still-tick count on every row, which is the measurement saying the rest
  is the input problem rather than the renderer's.
* **At 60 fps on a steady link, nothing changed at all**: 0 duplicated frames before and
  after, no step larger than one tick of travel. Smoothing does not introduce a wobble
  into motion that was already right.
* **The cost is 33 ms of response time**, two ticks at 60 Hz, on every row. That is the
  whole price and it is paid by every player whatever their link.

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
  stream by a sixteenth-of-a-tick-scale nudge each frame and is hard-bounded to the
  newest tick above and six ticks behind it below. A client whose clock ran far enough
  out would re-anchor visibly rather than drift; the measurements do not exercise that,
  because a 1-tick-per-half-hour quartz difference takes longer than a match.
* **One moving entity, one axis, one stage.** The traces measure a player tank crossing
  open ground. Interpolation is applied to tanks and projectiles alike and is unit
  tested for identity matching, spawns, deaths and respawn jumps in
  `test_interpolation.py`, but the stutter percentages themselves come from one shape of
  motion.
* **Measured, not played.** These are numbers from a deterministic harness, not a player
  at a window. They say the picture moves when it should; they do not say it feels good.
