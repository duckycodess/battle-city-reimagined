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

## Baseline

| condition    | fps | still frames    | max step px | response ms | batches sent | collapsed | command-less ticks |
| ------------ | --- | --------------- | ----------- | ----------- | ------------ | --------- | ------------------ |
| 0 ms         | 60  | 0/112 (0.0%)    | 2           | 16          | 181          | 0         | 0 |
| 0 ms         | 120 | 111/223 (49.7%) | 2           | 16          | 181          | 0         | 0 |
| 50 ms        | 60  | 0/112 (0.0%)    | 2           | 100         | 175          | 0         | 0 |
| 50 ms        | 120 | 111/223 (49.7%) | 2           | 100         | 175          | 0         | 0 |
| 100 ms       | 60  | 0/112 (0.0%)    | 2           | 200         | 169          | 0         | 0 |
| 100 ms       | 120 | 111/223 (49.7%) | 2           | 200         | 169          | 0         | 0 |
| 200 ms       | 60  | 0/112 (0.0%)    | 2           | 400         | 157          | 0         | 0 |
| 200 ms       | 120 | 111/223 (49.7%) | 2           | 400         | 157          | 0         | 0 |
| 50 ms +/-10  | 60  | 77/167 (46.1%)  | 4           | 117         | 127          | 19        | 65 |
| 50 ms +/-10  | 120 | 226/336 (67.2%) | 4           | 92          | 163          | 44        | 56 |
| 100 ms +/-25 | 60  | 90/153 (58.8%)  | 4           | 234         | 102          | 24        | 81 |
| 100 ms +/-25 | 120 | 232/302 (76.8%) | 4           | 242         | 122          | 39        | 79 |

*still frames* are frames that redrew the tank exactly where the previous frame left it
while the server was reporting movement. *max step* is the largest single-frame jump in
pixels; the nominal figure is 2, one tick of tank speed. *response* is the time from
offering the first input to seeing the tank move.

## What the baseline says

**Constant latency does not stutter.** At every one of 0, 50, 100 and 200 ms the 60 fps
client draws 112 frames of motion with not one duplicate and not one double step. A
constant delay shifts the whole snapshot stream later without changing its spacing, so
it costs response time — 16, 100, 200 and 400 ms, which is the round trip plus a tick —
and costs the picture nothing. Nothing in this issue can improve that reading: drawing
the tank somewhere the server has not put it yet is prediction, which the networking
specification reserves for measured need, protocol versioning and acceptance tests.

**Stutter comes from spacing, and has two causes.**

1. *The frame rate.* A 120 fps client on a 60 Hz session shows every snapshot twice:
   49.7% of frames are duplicates on a **perfect** link. This is the largest single
   reading in the table and it has no network in it at all.
2. *Jitter.* Wobble bunches snapshots, so some frames receive none and redraw the last
   one while the next receives two and jumps 4 px instead of 2. At 50 ms +/-10 that is
   46.1% still frames at 60 fps.

The two compound: 120 fps under jitter reaches 67–77% still frames.

**Jitter also costs the input stream, and that is a separate problem.** The client
offers one batch per authoritative tick it has *seen*. When two snapshots land on one
frame, the tick in between never gets a batch, and the server runs it with nothing from
this player: 56–81 command-less ticks in a 180-tick trial. Separately, when the uplink
bunches two batches into one server tick interval the second replaces the first in the
slot's queue — 19–44 collapsed batches. No condition in the table produces a single
refusal, so none of this is the client hitting a server bound.

## What was done about it, and what was not

See `test_interpolation.py` and the improved table below once Phase 8's change lands.
The input-stream findings are **not** addressed here: closing the command-less ticks
means sending input for a tick the client has not been told about yet, or scheduling a
batch ahead of the server's current tick, and closing the collapses means the client
asserting a `target_tick` rather than letting the server place the batch. Each is a
change to what the client claims about authoritative time — the prediction and
reconciliation conversation the networking specification reserves — and each needs its
own proposal, protocol-version decision and acceptance tests. Recorded as a follow-up.
