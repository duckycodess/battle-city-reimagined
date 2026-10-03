"""Print the measurement table in ``README.md``, so the table is never hand-maintained.

Run it from the repository root::

    uv run --locked python tests/networking/report.py

It writes Markdown to standard output and touches nothing. Every number it prints comes
from the same :func:`networking_helpers.run_trial` the tests assert on, over the same
seeded profiles, so a figure in the README and a threshold in a test cannot drift apart
without one of them failing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from battle_city_client.interpolation import SMOOTHING_TICKS
from battle_city_content import Pack
from networking_helpers import LinkProfile, TrialResult, run_trial, write_pack

PROFILES: tuple[LinkProfile, ...] = (
    LinkProfile(name="0 ms", delay_ms=0),
    LinkProfile(name="50 ms", delay_ms=50),
    LinkProfile(name="75 ms", delay_ms=75),
    LinkProfile(name="100 ms", delay_ms=100),
    LinkProfile(name="200 ms", delay_ms=200),
    LinkProfile(name="50 ms +/-10", delay_ms=50, jitter_ms=10, seed=20260115),
    LinkProfile(name="100 ms +/-25", delay_ms=100, jitter_ms=25, seed=20260116),
    LinkProfile(name="200 ms +/-25", delay_ms=200, jitter_ms=25, seed=20260117),
)
"""The conditions in the table.

75 ms is here because every delay the issue names is a whole number of ticks, which is
the best case for response time; a profile that is not keeps the table honest about it.
The seeds match the ones ``test_latency_baseline.py`` asserts against.
"""

FRAME_RATES: tuple[int, ...] = (60, 120)

COLUMNS: tuple[str, ...] = (
    "condition",
    "fps",
    "realized rtt ms",
    "still frames",
    "skipped-tick frames",
    "response ms",
    "batches sent",
    "collapsed",
    "command-less ticks",
    "still ticks in the run",
)


def rows(pack: Pack, *, smoothing_ticks: int) -> list[tuple[str, ...]]:
    collected: list[tuple[str, ...]] = []
    for profile in PROFILES:
        for frame_rate in FRAME_RATES:
            collected.append(
                _row(
                    run_trial(
                        pack,
                        profile=profile,
                        frame_rate=frame_rate,
                        smoothing_ticks=smoothing_ticks,
                    )
                )
            )
    return collected


def _row(result: TrialResult) -> tuple[str, ...]:
    trace = result.trace
    return (
        result.profile.name,
        str(result.frame_rate),
        f"{result.realized_round_trip_tenths / 10:.1f}",
        f"{trace.still_frames}/{trace.frames} ({trace.still_permille / 10:.1f}%)",
        str(trace.double_steps),
        "-" if result.response_ms is None else str(result.response_ms),
        str(result.batches_sent),
        str(result.collapsed),
        str(result.commandless_ticks),
        str(result.authoritative_still_ticks),
    )


def render(table: list[tuple[str, ...]]) -> str:
    widths = [
        max(len(COLUMNS[index]), *(len(row[index]) for row in table))
        for index in range(len(COLUMNS))
    ]
    lines = [
        "| " + " | ".join(name.ljust(width) for name, width in zip(COLUMNS, widths, strict=True)),
        "| " + " | ".join("-" * width for width in widths),
    ]
    for row in table:
        lines.append(
            "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        )
    return "\n".join(line.rstrip() + " |" for line in lines)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        pack = write_pack(Path(directory))
        print("## Baseline (`smoothing_ticks = 0`)\n")
        print(render(rows(pack, smoothing_ticks=0)))
        print(f"\n## Improved (`smoothing_ticks = {SMOOTHING_TICKS}`)\n")
        print(render(rows(pack, smoothing_ticks=SMOOTHING_TICKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
