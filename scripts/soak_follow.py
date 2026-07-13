"""Dev-only `detect --follow` memory-stability soak (v1.0.3).

Not part of the installed package, not run in CI. The v1.0 release plan
calls for confirming memory stays flat over ~10,000 polls on a real,
multi-hour trace; a literal multi-hour run isn't something this tool can
do inside one sitting. This script is the automatable stand-in: it drives
many thousands of poll-equivalent iterations back-to-back (seconds, not
hours) and reports RSS at the start/middle/end so a clear upward trend
(a real leak) is still visible. Running this script for hours instead of
minutes -- by raising `--iterations` -- is the real soak; that longer run
is a manual step left to whoever is cutting the release, same as the
release plan's other real-hardware/real-traffic manual steps.

Each iteration mirrors what `_detect_follow_poll()` does to the *window*
on every poll: evict the oldest record, append one new one, and re-run the
full detector registry over the resulting fixed-size `FOLLOW_WINDOW_SIZE`
window -- the same re-analyze-the-whole-window cost `bench.py`'s
follow-poll number measures, just repeated many times in a row instead of
once, since a single iteration says nothing about a *trend* in memory
(the bench script's is the "how much does one poll cost" question, this
script's is the "does polling forever cost more and more" question).

Usage:
    .venv/bin/python3 scripts/soak_follow.py [--iterations 10000]
"""

from __future__ import annotations

import argparse
import resource
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_burnwatch.anomaly.constants import FOLLOW_WINDOW_SIZE  # noqa: E402
from llm_burnwatch.demo_data import generate_demo_calls  # noqa: E402
from llm_burnwatch.detectors.engine import DEFAULT_REGISTRY, run_detectors  # noqa: E402


def _rss_mb() -> float:
    # `ru_maxrss` is bytes on macOS, KiB on Linux -- normalize to a
    # platform-appropriate divisor. This script is dev-only and doesn't
    # need to run on Windows (`resource` isn't available there anyway).
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return maxrss / divisor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10_000)
    args = parser.parse_args()

    calls = generate_demo_calls(n_normal=args.iterations + FOLLOW_WINDOW_SIZE, n_anomalies=0, seed=2)
    window: deque = deque(
        (
            {
                "schema_version": 1,
                "timestamp": "2026-01-01T00:00:00Z",
                "label": c.label,
                "model": c.model,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cached_input_tokens": 0,
                "cost_micros": 0,
            }
            for c in calls[:FOLLOW_WINDOW_SIZE]
        ),
        maxlen=FOLLOW_WINDOW_SIZE,
    )
    remaining = calls[FOLLOW_WINDOW_SIZE:]

    samples: list[tuple[int, float]] = []
    start = time.perf_counter()
    for i, call in enumerate(remaining):
        window.append(
            {
                "schema_version": 1,
                "timestamp": "2026-01-01T00:00:00Z",
                "label": call.label,
                "model": call.model,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "cached_input_tokens": 0,
                "cost_micros": 0,
            }
        )
        run_detectors(list(window), registry=DEFAULT_REGISTRY)
        if i in (0, len(remaining) // 2, len(remaining) - 1) or i % max(1, len(remaining) // 10) == 0:
            samples.append((i, _rss_mb()))

    elapsed = time.perf_counter() - start
    print(f"{len(remaining):,} poll-equivalent iterations in {elapsed:.1f}s")
    print(f"{'iteration':>10}  {'RSS (MB)':>10}")
    for i, rss in samples:
        print(f"{i:>10,}  {rss:>10.1f}")

    first_rss = samples[0][1]
    last_rss = samples[-1][1]
    growth_pct = ((last_rss - first_rss) / first_rss * 100) if first_rss else 0.0
    print(f"\nRSS at first sample: {first_rss:.1f} MB, at last sample: {last_rss:.1f} MB "
          f"({growth_pct:+.1f}%)")
    if growth_pct > 20:
        print("WARNING: RSS grew more than 20% over the run -- investigate before "
              "trusting this as a stable soak.")
    else:
        print("RSS stayed within a 20% band -- no evidence of a leak over this run.")


if __name__ == "__main__":
    main()
