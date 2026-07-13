"""Dev-only performance benchmark for llm-burnwatch (v1.0.3).

Not part of the installed package (lives outside `src/`), not imported by
any shipped code, and not run in CI -- this is a manual tool a contributor
runs on their own machine to get real numbers for `docs/performance.md`
before/after a change that could affect hot paths.

Measures the four operations called out in the v1.0 release plan:

1. `build_report()` (tracker.py) over 1,000,000 log records.
2. `run_detectors()` (detectors/engine.py) -- the full detector registry --
   over the same 1,000,000 records (one-shot `detect`, not `--follow`).
3. One `--follow` poll's detector re-analysis: `run_detectors()` over a
   single full `FOLLOW_WINDOW_SIZE` window. `_detect_follow_poll()` itself
   also does a byte-offset tail-read and (optionally) sink delivery, but
   the detector re-analysis over the full window is the dominant,
   O(window_size)-per-poll cost the release plan's threshold targets --
   see docs/performance.md for the full reasoning.
4. `parse_otel_spans()` (otel_import.py) over a synthetic 100,000-span
   OTLP JSON export.

Usage:
    .venv/bin/python3 scripts/bench.py
"""

from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_burnwatch.anomaly.constants import FOLLOW_WINDOW_SIZE  # noqa: E402
from llm_burnwatch.demo_data import generate_demo_calls  # noqa: E402
from llm_burnwatch.detectors.engine import DEFAULT_REGISTRY, run_detectors  # noqa: E402
from llm_burnwatch.logreader import iter_log_records  # noqa: E402
from llm_burnwatch.otel_import import parse_otel_spans  # noqa: E402
from llm_burnwatch.tracker import CostTracker, build_report, load_default_pricing  # noqa: E402

REPORT_RECORD_COUNT = 1_000_000
FOLLOW_WINDOW_RECORD_COUNT = FOLLOW_WINDOW_SIZE
OTEL_SPAN_COUNT = 100_000


def _timed(label: str, fn) -> float:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(f"{label}: {elapsed:.3f}s")
    return elapsed, result


def _make_synthetic_otel_export(span_count: int) -> str:
    """Build a minimal-but-valid OTLP JSON export with `span_count`
    GenAI-shaped spans, matching the attribute names `parse_otel_spans()`
    recognizes (see otel_import.py's `_MODEL_ATTRS`/`_INPUT_TOKEN_ATTRS`)."""
    spans = []
    for i in range(span_count):
        spans.append(
            {
                "traceId": f"{i:032x}",
                "name": "chat",
                "startTimeUnixNano": str(1_700_000_000_000_000_000 + i),
                "attributes": [
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o-mini"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 500}},
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": 150}},
                ],
            }
        )
    export = {
        "resourceSpans": [
            {"scopeSpans": [{"spans": spans}]},
        ]
    }
    return json.dumps(export)


def main() -> None:
    print(f"platform: {platform.platform()}")
    print(f"python: {sys.version.split()[0]}")
    print()

    pricing = load_default_pricing()

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "bench.jsonl"

        print(f"generating {REPORT_RECORD_COUNT:,} demo records...")

        def _generate() -> None:
            # `write_demo_log()`'s default `CostTracker` rotation settings
            # (10MB/5 backups) would silently drop most of a 1M-record log
            # before we ever measure anything -- rotation is the right
            # default for real usage, but wrong for a benchmark that needs
            # every record in one place. `max_bytes` large enough for the
            # whole run + `backup_count=0` disables rotation entirely.
            tracker = CostTracker(log_path, max_bytes=1024 * 1024 * 1024, backup_count=0)
            for call in generate_demo_calls(n_normal=REPORT_RECORD_COUNT, n_anomalies=0, seed=1):
                tracker.log_call(
                    label=call.label,
                    model=call.model,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                )

        gen_elapsed, _ = _timed(
            "  generation (not a measured threshold, for context only)", _generate
        )
        print()

        records = list(iter_log_records(str(log_path)))
        assert len(records) == REPORT_RECORD_COUNT

        print(f"--- build_report() over {REPORT_RECORD_COUNT:,} records ---")
        report_elapsed, _ = _timed(
            "report", lambda: build_report(records, pricing)
        )
        print()

        print(f"--- run_detectors() (full registry) over {REPORT_RECORD_COUNT:,} records ---")
        detect_elapsed, _ = _timed(
            "detect", lambda: run_detectors(records, registry=DEFAULT_REGISTRY)
        )
        print()

        window = records[-FOLLOW_WINDOW_RECORD_COUNT:]
        print(
            f"--- one --follow poll's detector re-analysis "
            f"(full {FOLLOW_WINDOW_RECORD_COUNT:,}-record window) ---"
        )
        poll_elapsed, _ = _timed(
            "follow-poll", lambda: run_detectors(window, registry=DEFAULT_REGISTRY)
        )
        print()

        print(f"--- parse_otel_spans() over {OTEL_SPAN_COUNT:,} synthetic spans ---")
        otel_export = _make_synthetic_otel_export(OTEL_SPAN_COUNT)
        otel_elapsed, otel_records = _timed(
            "otel-import", lambda: parse_otel_spans(otel_export, pricing=pricing)
        )
        assert len(otel_records) == OTEL_SPAN_COUNT
        print()

    print("=== summary ===")
    print(f"report        (1M records):     {report_elapsed:.3f}s  (threshold: < 5.0s)")
    print(f"follow-poll   (5000-window):     {poll_elapsed:.3f}s  (threshold: < 1.0s)")
    print(f"detect        (1M records):      {detect_elapsed:.3f}s  (no fixed threshold)")
    print(f"otel-import   (100k spans):       {otel_elapsed:.3f}s  (no fixed threshold)")
    print(f"[fixture generation took {gen_elapsed:.1f}s, not itself a measured operation]")

    if report_elapsed >= 5.0:
        print("\nWARNING: report threshold (< 5.0s @ 1M) NOT met.")
    if poll_elapsed >= 1.0:
        print("\nWARNING: follow-poll threshold (< 1.0s @ 5000-window) NOT met.")


if __name__ == "__main__":
    main()
