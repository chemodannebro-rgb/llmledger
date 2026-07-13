# Performance

llm-burnwatch reads a plain JSONL log on disk; nothing about its design is
fundamentally slow, but no one had actually measured it against the
thresholds that matter for real usage until this page. Numbers below come
from `scripts/bench.py` and `scripts/soak_follow.py` (dev-only, not part of
the installed package) run against this release.

## Methodology

- Hardware/software: `macOS-26.3-arm64-arm-64bit`, Python 3.9.6, Apple
  Silicon (arm64), 2026-07-13.
- `report`: `build_report()` (`tracker.py`) over 1,000,000 synthetic log
  records (`demo_data.generate_demo_calls`, no injected anomalies).
- `detect` (one-shot): the full detector registry
  (`detectors/engine.py`'s `DEFAULT_REGISTRY` -- baseline, frequency, cusum,
  rules, budget) run via `run_detectors()` over the same 1,000,000 records.
- `--follow` poll: `_detect_follow_poll()`'s dominant per-poll cost is
  re-running the full detector registry over the fixed-size
  `FOLLOW_WINDOW_SIZE` (5,000-record) window -- that's what's measured here
  as "follow-poll". The tail-read (byte-offset diff since the last poll)
  and optional sink delivery are comparatively cheap and not separately
  benchmarked.
- `import otel`: `parse_otel_spans()` over a synthetic 100,000-span OTLP
  JSON export (all spans GenAI-shaped, so none are skipped).
- Soak: `scripts/soak_follow.py` drives 10,000 poll-equivalent iterations
  back-to-back (evict oldest record, append one new one, re-run the full
  registry over the resulting window), sampling RSS (`ru_maxrss`)
  throughout. This is an automated stand-in for a real multi-hour/live-traffic
  soak -- see "What this soak does and doesn't prove" below.

Reproduce with:

```
.venv/bin/python3 scripts/bench.py
.venv/bin/python3 scripts/soak_follow.py --iterations 10000
```

## Results

| Operation | Records | Time | Threshold | Result |
|---|---|---|---|---|
| `report` | 1,000,000 | 0.82s | < 5s | **pass** (6x margin) |
| `--follow` poll (full window) | 5,000 | 0.036s | < 1s | **pass** (27x margin) |
| `detect` (one-shot, full registry) | 1,000,000 | 44.0s | none set | for context only |
| `import otel` | 100,000 spans | 0.93s | none set | for context only |

Soak (10,000 poll-equivalent iterations, ~5.4 minutes wall-clock):

| Sample point | RSS |
|---|---|
| iteration 0 | 26.5 MB |
| iteration 5,000 | 26.6 MB |
| iteration 9,999 | 26.6 MB |

RSS grew **+0.4%** over the full run -- flat, no evidence of a leak.

## What this means for the two hard thresholds

- `report` on 1,000,000 records comfortably clears the 5-second target with
  room to spare. **No caching was added** -- the v1.0 release plan is explicit
  that a sidecar aggregate cache is only justified if the threshold is
  actually missed, and it wasn't. `build_report()` stays the simple,
  single-pass function it already was.
- A single `--follow` poll's full-window re-analysis is **~28x faster** than
  the default 5-second poll interval, and well inside the 1-second target
  even measured in isolation. **No memoization was added** to the frequency
  detector's seasonal bucketing (`has_seasonal_coverage()`) -- profiling
  would only be worth doing if the poll number were close to the threshold,
  and it isn't.

## `detect` (one-shot) on 1,000,000 records: no threshold, but worth noting

The full detector registry over 1M records took 44 seconds -- much slower
than `report`'s single pass over the same data, because several detectors
do per-group statistical work (robust z-scores, CUSUM state per group,
seasonal bucketing) rather than one linear aggregation pass. The release
plan sets no fixed threshold for this case (`detect` is meant to run once
against however much log history you have, not on every poll), so this
isn't a pass/fail number -- it's here so a future change that regresses it
has something to compare against.

## What this soak does and doesn't prove

A real multi-hour soak against live, unpredictable traffic is the
release-plan's actual bar, and it's a manual step left to whoever cuts the
release (same as the plan's other real-hardware/live-traffic steps --
external testers, external design/editor passes). What the automated
10,000-iteration run above *does* show: no monotonic RSS growth trend
across many thousands of polls, which is the shape a real leak (e.g. an
ever-growing cache, or something outside the `deque(maxlen=...)` window
accumulating state) would produce. `--iterations` can be raised (e.g. to
run for hours instead of minutes) to get closer to the real target.
