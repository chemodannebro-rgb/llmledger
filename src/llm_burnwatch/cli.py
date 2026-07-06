"""Command-line interface for llm-burnwatch.

Eight subcommands:
  report          -- cost summary read back from a log
  demo-data       -- write a synthetic log with a known number of injected anomalies
  detect          -- baseline (+ optional ML cross-check) anomaly detection over a log
  train           -- train an IsolationForest model (requires `llm-burnwatch[anomaly]`)
  schema          -- print the packaged JSONL log schema
  validate        -- check a log's records against the packaged JSON schema
  dashboard       -- write a static single-file HTML cost report with a daily journal
  pricing import  -- import pricing data from a local file or http(s):// URL

`report`/`demo-data`/`schema`/`validate`/`dashboard`/`detect`/`train` never
make a network call. `detect` only imports scikit-learn indirectly, via
`registry.load_model` deserializing (via `skops.io`) an existing model -- if
none exists yet, `detect` runs baseline-only and never touches scikit-learn
either. `train` imports `anomaly.train` (which imports scikit-learn at module
level) lazily, inside a try/except, so the zero-dependency core guarantee
holds for every other command even when scikit-learn is not installed.
`pricing import <url>` is the sole, explicit, opt-in exception to the
no-network-by-default rule -- see "Network boundaries" in ARCHITECTURE.md.

Exit codes (a stable contract for cron/alerting integration -- the only
integration surface llm-burnwatch offers; it never sends notifications itself):
  0 -- ran cleanly, no anomalies found (or the command has no anomaly concept)
  1 -- ran cleanly, `detect` found at least one anomalous call
  2 -- execution error (bad path, bad arguments, missing dependency, ...)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Iterator

from . import __version__
from ._messages import error, warn
from .anomaly.constants import (
    CONTAMINATION,
    FOLLOW_WINDOW_SIZE,
    KEEP_LAST_DEFAULT,
    Z_SCORE_THRESHOLD,
)
from .anomaly.features import (
    check_label_cardinality,
    compute_reference_stats,
    detect_drift,
    extract_features,
)
from .anomaly.registry import latest_version_dir, load_model
from .anomaly.seasonal import has_seasonal_coverage, seasonal_coverage_message
from .dashboard import render_dashboard
from .demo_data import DEFAULT_SEED, write_demo_log
from .detectors.baseline_detector import BaselineDetector
from .detectors.cusum_detector import CusumDetector
from .detectors.engine import run_detectors
from .detectors.frequency_detector import FrequencyDetector
from .detectors.protocol import ALERT_SCHEMA_VERSION
from .detectors.rules_detector import RulesDetector
from .follow_state import load_follow_state, save_follow_state, state_path_for
from .logreader import (
    check_scale,
    filter_by_period,
    iter_log_records,
    parse_date,
    read_new_records,
)
from .pricing_import import PricingImportError, import_pricing
from .tracker import build_report, resolve_pricing, user_pricing_path

DISCLAIMER = (
    "llm-burnwatch is a diagnostic aid, not a guarantee: it flags statistically "
    "unusual calls, it does not confirm they are errors, and it may miss "
    "real ones. Always use your own judgement before acting on its output."
)


def _print_header(pricing: dict) -> None:
    print(DISCLAIMER)
    last_updated = pricing.get("last_updated")
    if last_updated:
        print(f"pricing data last updated: {last_updated}")


def _print_report_csv(result: dict) -> None:
    """Print `result` as a normalized 3-column CSV: one `total` row (empty
    key), then one row per label, then one row per model, each carrying its
    own cost in USD. Deliberately ignores `--rub-rate`/`--fx-rate` (documented
    limitation, not a bug) and skips the human-readable disclaimer/pricing-date header --
    this output is meant to be piped into a spreadsheet or another program,
    not read directly, so it stays exactly three columns with no preamble.
    """
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["dimension", "key", "cost_usd"])
    writer.writerow(["total", "", f"{result['total_cost_usd']:.6f}"])
    for label, micros in sorted(result["by_label_micros"].items()):
        writer.writerow(["label", label, f"{micros / 1_000_000:.6f}"])
    for model, micros in sorted(result["by_model_micros"].items()):
        writer.writerow(["model", model, f"{micros / 1_000_000:.6f}"])


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number, got {value!r}")
    return parsed


def _date_arg(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got {value!r}")
    return value


class _FxError(Exception):
    """Raised by `_resolve_fx` for an invalid/ambiguous combination of
    --rub-rate (deprecated) and --fx-rate/--currency."""


def _resolve_fx(args: argparse.Namespace):
    """Resolve --rub-rate (deprecated) and --fx-rate/--currency into a single
    `(rate, currency, legacy)` tuple, or raise `_FxError` for an invalid
    combination.

    `legacy=True` means the deprecated --rub-rate path was used and callers
    must keep its output byte-identical (RUB-only, ``rub_rate``/
    ``total_cost_rub`` JSON keys) for backward compatibility. `legacy=False`
    means the generic --fx-rate/--currency path was used (``fx_rate``/
    ``currency``/``total_cost_fx`` JSON keys). Returns `(None, None, False)`
    if no conversion was requested at all.
    """
    if args.rub_rate is not None and (args.fx_rate is not None or args.currency is not None):
        raise _FxError(
            "--rub-rate cannot be combined with --fx-rate/--currency; use --fx-rate/--currency alone"
        )
    if args.rub_rate is not None:
        warn(
            '--rub-rate is deprecated and will be removed before v1.0; use '
            '"--fx-rate <rate> --currency RUB" instead'
        )
        return args.rub_rate, "RUB", True
    if args.fx_rate is not None and args.currency is None:
        raise _FxError("--fx-rate requires --currency")
    if args.currency is not None and args.fx_rate is None:
        raise _FxError("--currency requires --fx-rate")
    if args.fx_rate is not None:
        return args.fx_rate, args.currency, False
    return None, None, False


def _filter_report_records(records, args, counts: dict) -> Iterator[dict]:
    """Yield records matching `--since`/`--until`/`--trace-id`, counting the
    total number seen and how many were dropped by the period filter along
    the way -- `counts` is filled in as a side effect so the caller can still
    run `check_scale()`/warn about period drops after this generator has been
    fully consumed by `build_report()`, without ever materializing the whole
    log into a list (unlike `detect`/`dashboard`, which need every record's
    full group history in memory at once and are out of scope for this fix).
    """
    period_active = bool(args.since or args.until)
    for record in records:
        counts["total"] += 1
        if period_active:
            record_date = parse_date(record.get("timestamp"))
            if (
                record_date is None
                or (args.since and record_date < args.since)
                or (args.until and record_date > args.until)
            ):
                counts["dropped_period"] += 1
                continue
        if args.trace_id is not None and record.get("trace_id") != args.trace_id:
            continue
        yield record


def cmd_report(args: argparse.Namespace) -> int:
    if args.json and args.format == "csv":
        error("--json and --format csv are mutually exclusive")
        return 2

    try:
        fx_rate, currency, fx_legacy = _resolve_fx(args)
    except _FxError as exc:
        error(str(exc))
        return 2

    try:
        raw_records = iter_log_records(args.log_file)
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    counts = {"total": 0, "dropped_period": 0}
    pricing = resolve_pricing(args.pricing_file)
    result = build_report(_filter_report_records(raw_records, args, counts), pricing)

    check_scale(args.log_file, counts["total"])
    if counts["dropped_period"]:
        warn(
            f"{counts['dropped_period']} record(s) fell outside --since/--until or lacked a "
            "usable timestamp and were excluded from this period"
        )

    if args.format == "csv":
        _print_report_csv(result)
        return 0

    if result["call_count"] == 0:
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        _print_header(pricing)
        if args.trace_id is not None:
            print(f"no records found for trace_id {args.trace_id!r}")
        elif args.since or args.until:
            print("no records found in the given period")
        else:
            print("no records found in log")
        return 0

    if args.json:
        payload = dict(result)
        if fx_rate is not None:
            if fx_legacy:
                payload["rub_rate"] = fx_rate
                payload["total_cost_rub"] = result["total_cost_usd"] * fx_rate
            else:
                payload["fx_rate"] = fx_rate
                payload["currency"] = currency
                payload["total_cost_fx"] = result["total_cost_usd"] * fx_rate
        print(json.dumps(payload, indent=2))
        return 0

    _print_header(pricing)
    print(f"calls: {result['call_count']}")
    total_cost_line = f"total cost: ${result['total_cost_usd']:.6f}"
    if fx_rate is not None:
        fx_total = result["total_cost_usd"] * fx_rate
        if fx_legacy:
            total_cost_line += f" (~₽{fx_total:.2f} at {fx_rate:.2f} ₽/$)"
        else:
            total_cost_line += f" (~{fx_total:.2f} {currency} at {fx_rate:.2f} {currency}/$)"
    print(total_cost_line)
    print("by label:")
    for label, micros in sorted(result["by_label_micros"].items()):
        print(f"  {label}: ${micros / 1_000_000:.6f}")
    print("by model:")
    for model, micros in sorted(result["by_model_micros"].items()):
        print(f"  {model}: ${micros / 1_000_000:.6f}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        fx_rate, currency, fx_legacy = _resolve_fx(args)
    except _FxError as exc:
        error(str(exc))
        return 2

    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    check_scale(args.log_file, len(records))
    records = filter_by_period(records, args.since, args.until)
    pricing = resolve_pricing(args.pricing_file)
    if fx_legacy:
        html = render_dashboard(
            records, pricing, rub_rate=fx_rate, since=args.since, until=args.until
        )
    else:
        html = render_dashboard(
            records, pricing, fx_rate=fx_rate, currency=currency, since=args.since, until=args.until
        )

    try:
        Path(args.out).write_text(html, encoding="utf-8")
    except OSError as exc:
        error(str(exc))
        return 2
    # The dashboard carries the same cost/usage data as the source log, so it
    # gets the same 0600 treatment as the log file (tracker.py) and the model
    # registry (registry.py) -- not more world-readable than its source.
    os.chmod(args.out, 0o600)

    print(f"dashboard written to {args.out}")
    return 0


def cmd_demo_data(args: argparse.Namespace) -> int:
    try:
        results = write_demo_log(
            args.out,
            n_normal=args.n_normal,
            n_anomalies=args.n_anomalies,
            seed=args.seed,
        )
    except OSError as exc:
        error(str(exc))
        return 2

    print(f"wrote {len(results)} demo call(s) to {args.out}")
    return 0


_ML_CROSS_CHECK_LOAD_ATTEMPTS = 3


def _run_ml_cross_check(records: list[dict], model_dir: str) -> dict | None:
    """Return an ML cross-check summary, or `None` if no trained model
    exists yet at `model_dir`. Never raises: missing scikit-learn, a
    corrupted/tampered `model.skops` (sha256 mismatch), or a missing/corrupted
    `metadata.json` (e.g. an interrupted `train()`, or manual tampering) are
    all reported through `warn()`/`error()` and reflected in the returned
    dict's `available` flag instead of aborting `detect` entirely -- the
    baseline result is still valid and should still be printed even when the
    ML side of the registry is unusable.

    `latest_version_dir()` + `load_model()` are two separate steps, so a
    concurrent `train()` can prune the exact version just resolved as
    "latest" in between them (e.g. `keep_last=1` and a new version finishes
    training right after `latest_version_dir()` returns). That race would
    otherwise surface as an avoidable `FileNotFoundError` even though a
    perfectly good (newer) model exists on disk a moment later -- so on
    that specific error only, re-resolve "latest" and retry a bounded
    number of times before giving up.
    """
    version_dir = None
    for attempt in range(_ML_CROSS_CHECK_LOAD_ATTEMPTS):
        version_dir = latest_version_dir(model_dir)
        if version_dir is None:
            return None

        try:
            model, metadata = load_model(version_dir)
            break
        except ImportError:
            warn(
                "a trained model exists but scikit-learn is not installed; "
                "skipping ML cross-check. Install with: "
                'pip install "llm-burnwatch[anomaly]"'
            )
            return {"available": False, "reason": "scikit-learn not installed"}
        except ValueError as exc:
            error(str(exc))
            return {"available": False, "reason": str(exc)}
        except FileNotFoundError as exc:
            if attempt == _ML_CROSS_CHECK_LOAD_ATTEMPTS - 1:
                error(
                    f"could not load model registry at {version_dir}: {exc}. "
                    "It was likely pruned by a concurrent `llm-burnwatch train` "
                    "run. Skipping ML cross-check for this run."
                )
                return {"available": False, "reason": str(exc)}
            continue
        except (OSError, json.JSONDecodeError) as exc:
            error(
                f"could not load model registry at {version_dir}: {exc}. "
                "Skipping ML cross-check; re-run `llm-burnwatch train` to regenerate it."
            )
            return {"available": False, "reason": str(exc)}

    X, kept_indices = extract_features(records)
    ml_anomaly_indices = []
    if X:
        predictions = model.predict(X)
        ml_anomaly_indices = [
            kept_indices[i] for i, pred in enumerate(predictions) if pred == -1
        ]

    current_stats = compute_reference_stats(records)
    reference_stats = metadata.get("reference_stats") or {}
    drift_messages = detect_drift(current_stats, reference_stats)
    for message in drift_messages:
        warn(message)

    return {
        "available": True,
        "model_version": metadata.get("version"),
        "anomaly_count": len(ml_anomaly_indices),
        "anomaly_indices": ml_anomaly_indices,
        "drift_warnings": drift_messages,
    }


def _detect_registry(args: argparse.Namespace) -> list:
    """The detector registry `detect`/`detect --follow` both build from the
    same CLI flags -- kept in one place so the two entry points can't drift.
    """
    return [
        BaselineDetector(threshold=args.threshold),
        FrequencyDetector(),
        CusumDetector(),
        RulesDetector(
            allowed_models=args.allowed_models,
            max_call_cost_usd=args.max_call_cost,
            max_trace_cost_usd=args.max_trace_cost,
        ),
    ]


def _frequency_enabled_for(records: list[dict], args: argparse.Namespace) -> tuple[bool, bool]:
    """Returns `(frequency_enabled, seasonal_available)` for this batch of
    `records`, applying `--frequency-detector`'s auto/on/off decision.
    """
    seasonal_available = has_seasonal_coverage(records)
    if args.frequency_detector == "auto":
        frequency_enabled = seasonal_available
    else:
        frequency_enabled = args.frequency_detector == "on"
    return frequency_enabled, seasonal_available


def _cusum_enabled_for(args: argparse.Namespace) -> bool:
    """Returns whether `CusumDetector` should run, from `--cusum-detector`.

    Unlike `_frequency_enabled_for`, there's no log-dependent "auto" case:
    `CusumDetector.enabled_by_default` is already `True` -- a sustained
    cost/token level shift isn't subject to the day-of-week false-positive
    risk that justifies the frequency detector's seasonal gating (see
    `CusumDetector`'s docstring) -- so `--cusum-detector` is a plain on/off
    switch.
    """
    return args.cusum_detector == "on"


def cmd_detect(args: argparse.Namespace) -> int:
    if args.follow:
        if args.json:
            warn(
                "--follow always streams newline-delimited JSON alerts to "
                "stdout; --json is ignored in follow mode"
            )
        return _run_detect_follow(args)

    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    check_scale(args.log_file, len(records))
    pricing = resolve_pricing(args.pricing_file)

    if not records:
        if args.json:
            print(
                json.dumps(
                    {
                        "alert_schema_version": ALERT_SCHEMA_VERSION,
                        "call_count": 0,
                        "anomaly_count": 0,
                        "anomalies": [],
                    },
                    indent=2,
                )
            )
        else:
            _print_header(pricing)
            print("no records found in log; nothing to analyze")
        return 0

    check_label_cardinality(records)
    # "auto" (the default) enables the frequency detector only once this log
    # has enough calendar span for its seasonal (weekday x hour) comparison
    # to be meaningful -- otherwise a routine "every Monday morning" burst
    # looks statistically identical to a runaway agent (see
    # `FrequencyDetector`'s docstring). `--frequency-detector on/off`
    # overrides that decision explicitly in either direction, the same
    # override pattern already used for `RulesDetector`'s CLI flags.
    frequency_enabled, seasonal_available = _frequency_enabled_for(records, args)
    cusum_enabled = _cusum_enabled_for(args)

    alerts = run_detectors(
        records,
        registry=_detect_registry(args),
        enabled_overrides={"frequency": frequency_enabled, "cusum": cusum_enabled},
    )

    anomalous = [(a.record_ref, a) for a in alerts if a.kind == "zscore_outlier"]
    insufficient_count = sum(1 for a in alerts if a.kind == "insufficient_data")
    # Hard-limit violations from RulesDetector -- a distinct, additive
    # concern from the baseline z-score's statistical "anomalies" above,
    # so they get their own count/section rather than being folded into it.
    rule_violations = [a for a in alerts if a.detector == "rules"]
    # Frequency spikes -- likewise additive; only present at all when
    # `frequency_enabled` is True for this run.
    frequency_spikes = [a for a in alerts if a.detector == "frequency"]
    # Level shifts from CusumDetector -- likewise additive; only present at
    # all when `cusum_enabled` is True for this run (on by default).
    level_shifts = [a for a in alerts if a.detector == "cusum"]

    ml_info = _run_ml_cross_check(records, args.model_dir)

    if args.json:
        payload = {
            "alert_schema_version": ALERT_SCHEMA_VERSION,
            "call_count": len(records),
            "threshold": args.threshold,
            "anomaly_count": len(anomalous),
            "insufficient_data_count": insufficient_count,
            "anomalies": [
                {
                    "index": i,
                    "label": records[i].get("label"),
                    "model": records[i].get("model"),
                    "timestamp": records[i].get("timestamp"),
                    # Same human-readable explanation `detect`'s non-JSON
                    # output prints, already computed by the detector so a
                    # JSON consumer doesn't have to recompute it from raw
                    # numbers.
                    "features": a.evidence["scores"],
                }
                for i, a in anomalous
            ],
            "rule_violation_count": len(rule_violations),
            "rule_violations": [
                {
                    "index": a.record_ref,
                    "kind": a.kind,
                    "message": a.message,
                    "evidence": a.evidence,
                }
                for a in rule_violations
            ],
            "seasonal_baseline": {
                "available": seasonal_available,
                "message": seasonal_coverage_message(records),
            },
            "frequency_detector_enabled": frequency_enabled,
            "frequency_spike_count": len(frequency_spikes),
            "frequency_spikes": [
                {
                    "index": a.record_ref,
                    "group_key": a.group_key,
                    "message": a.message,
                    "evidence": a.evidence,
                }
                for a in frequency_spikes
            ],
            "cusum_detector_enabled": cusum_enabled,
            "level_shift_count": len(level_shifts),
            "level_shifts": [
                {
                    "index": a.record_ref,
                    "group_key": a.group_key,
                    "message": a.message,
                    "evidence": a.evidence,
                }
                for a in level_shifts
            ],
            "ml": ml_info,
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_header(pricing)
        print(f"analyzed {len(records)} call(s) (threshold={args.threshold})")
        if insufficient_count:
            print(f"{insufficient_count} call(s) had insufficient history and were skipped")
        if not anomalous:
            print("no anomalies found")
        for i, a in anomalous:
            r = records[i]
            print(f"- [{i}] {r.get('label')} / {r.get('model')} @ {r.get('timestamp')}")
            for s in a.evidence["scores"]:
                print(f"    {s['reason']}")
        if rule_violations:
            print(f"{len(rule_violations)} rule violation(s) found:")
            for a in rule_violations:
                print(f"- [{a.record_ref}] {a.kind}: {a.message}")
        if frequency_enabled and frequency_spikes:
            print(f"{len(frequency_spikes)} frequency spike(s) found:")
            for a in frequency_spikes:
                print(f"- [{a.record_ref}] {a.group_key}: {a.message}")
        if cusum_enabled and level_shifts:
            print(f"{len(level_shifts)} level shift(s) found:")
            for a in level_shifts:
                print(f"- [{a.record_ref}] {a.group_key}: {a.message}")
        if ml_info is not None and ml_info.get("available"):
            print(
                f"ML cross-check (model v{ml_info['model_version']}): "
                f"{ml_info['anomaly_count']} call(s) flagged"
            )

    return 1 if (anomalous or rule_violations or frequency_spikes or level_shifts) else 0


def _detect_follow_poll(
    log_path: Path,
    offsets: dict[str, int],
    window: deque,
    args: argparse.Namespace,
) -> tuple[list, dict[str, int], bool]:
    """Run a single `--follow` poll: read whatever's new in `log_path` since
    `offsets`, fold it into `window` (mutated in place, evicting the oldest
    records past `FOLLOW_WINDOW_SIZE`), and re-run the detector registry over
    the resulting window.

    Returns `(new_alerts, updated_offsets, had_new_records)`. `new_alerts` is
    restricted to alerts whose `record_ref` falls at or after the index the
    newly arrived records start at -- since a `deque`'s `maxlen` only ever
    evicts from the *left*, that index cleanly separates "already surfaced
    in an earlier poll" from "triggered by data that arrived just now"
    without needing a stable identity per record across polls. `had_new_records`
    tells the caller whether state actually changed and needs saving, even
    when this poll happened to produce zero alerts.

    Known limitation: an alert whose `record_ref` points at a record from a
    *previous* poll (e.g. `FrequencyDetector` reporting a window's first
    record) is filtered out even if the detection itself only became true
    because of newly arrived data -- accepted as a deliberate, documented
    trade-off of re-running stateless, batch detectors over a sliding window
    rather than each detector tracking its own incremental state.
    """
    new_records, offsets, corrupt_count = read_new_records(log_path, offsets)
    if corrupt_count:
        warn(f"skipped {corrupt_count} corrupt log line(s) this poll")

    if not new_records:
        return [], offsets, False

    window.extend(new_records)
    records = list(window)
    new_start_index = len(records) - len(new_records)

    frequency_enabled, _ = _frequency_enabled_for(records, args)
    cusum_enabled = _cusum_enabled_for(args)
    alerts = run_detectors(
        records,
        registry=_detect_registry(args),
        enabled_overrides={"frequency": frequency_enabled, "cusum": cusum_enabled},
    )
    new_alerts = [
        a for a in alerts if a.record_ref is not None and a.record_ref >= new_start_index
    ]
    return new_alerts, offsets, True


def _print_follow_alert(a) -> None:
    print(
        json.dumps(
            {
                "detector": a.detector,
                "severity": a.severity,
                "kind": a.kind,
                "group_key": a.group_key,
                "record_ref": a.record_ref,
                "message": a.message,
                "evidence": a.evidence,
            }
        )
    )


def _run_detect_follow(args: argparse.Namespace) -> int:
    """`detect --follow`: poll `args.log_file` every `args.poll_interval`
    seconds, re-running the same detector registry `detect` uses over a
    fixed-size rolling window (`FOLLOW_WINDOW_SIZE`) of the most recently
    seen records, and print each newly triggered alert as one JSON object
    per line to stdout as soon as it's found (see `_detect_follow_poll`).

    State (per-file byte offsets already consumed, and the current window)
    persists to `follow_state.state_path_for(args.log_file)` between runs,
    so stopping and restarting `--follow` resumes rather than re-scanning
    the whole log or missing what arrived while it wasn't running.

    Deliberately out of scope for this streaming mode (unlike one-shot
    `detect`): the ML cross-check (`_run_ml_cross_check` loads a model
    fresh from disk, too expensive to repeat every poll) and
    `check_label_cardinality`'s log-wide cardinality warning (would repeat
    identically almost every poll). Runs until interrupted (Ctrl+C), then
    exits `0`.
    """
    log_path = Path(args.log_file)
    state_path = state_path_for(log_path)
    state = load_follow_state(state_path)
    window: deque = deque(state["window"], maxlen=FOLLOW_WINDOW_SIZE)
    offsets: dict[str, int] = state["offsets"]

    warn(
        f"following {log_path} every {args.poll_interval}s, "
        f"window={FOLLOW_WINDOW_SIZE} record(s); state file: {state_path} "
        "(Ctrl+C to stop)"
    )

    try:
        while True:
            new_alerts, offsets, had_new_records = _detect_follow_poll(
                log_path, offsets, window, args
            )
            if new_alerts:
                for a in new_alerts:
                    _print_follow_alert(a)
                sys.stdout.flush()
            if had_new_records:
                save_follow_state(state_path, {"offsets": offsets, "window": list(window)})

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        return 0


def _contamination_type(value: str):
    if value == "auto":
        return "auto"
    return float(value)


def cmd_train(args: argparse.Namespace) -> int:
    try:
        from .anomaly.train import train as train_model
    except ImportError:
        error(
            "scikit-learn is required for training. Install with: "
            'pip install "llm-burnwatch[anomaly]"'
        )
        return 2

    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    check_scale(args.log_file, len(records))

    if not records:
        error("no records found in log; nothing to train on")
        return 2

    try:
        version_dir, eval_metrics = train_model(
            records,
            model_dir=args.model_dir,
            keep_last=args.keep_last,
            contamination=args.contamination,
        )
    except ValueError as exc:
        error(str(exc))
        return 2
    except ImportError as exc:
        # scikit-learn is imported eagerly above, so a bare ImportError here
        # almost always means the optional "skops" dependency (used lazily by
        # anomaly/registry.py to persist the trained model) is missing. Only
        # translate it into the friendly extras-install message when the
        # missing module is actually one of our optional deps; otherwise
        # re-raise so unrelated import bugs aren't masked by a misleading
        # "install llm-burnwatch[anomaly]" message.
        missing = exc.name or ""
        if missing == "skops" or missing.startswith("skops.") or missing == "sklearn" or missing.startswith("sklearn."):
            error(
                "scikit-learn/skops are required for training. Install with: "
                'pip install "llm-burnwatch[anomaly]"'
            )
            return 2
        raise

    print(f"trained model saved to {version_dir}")
    if eval_metrics["holdout_used"]:
        print(
            f"held-out eval: {eval_metrics['flagged_count']}/{eval_metrics['n_holdout_examples']} "
            f"({eval_metrics['flagged_fraction']:.1%}) held-out example(s) flagged anomalous by "
            "a model trained without them"
        )
    else:
        print(f"held-out eval skipped: {eval_metrics['reason']}")
    return 0


def cmd_pricing_import(args: argparse.Namespace) -> int:
    dest = user_pricing_path()
    try:
        pricing = import_pricing(args.source, dest)
    except PricingImportError as exc:
        error(str(exc))
        return 2
    print(f"imported {len(pricing['models'])} model(s) to {dest}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from importlib import resources

    text = resources.files("llm_burnwatch").joinpath("schema.json").read_text(encoding="utf-8")
    print(text)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from importlib import resources

    from .validation import validate_record

    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    schema_text = resources.files("llm_burnwatch").joinpath("schema.json").read_text(encoding="utf-8")
    schema = json.loads(schema_text)

    invalid = []
    for i, record in enumerate(records):
        errors = validate_record(record, schema)
        if errors:
            invalid.append((i, errors))

    if args.json:
        payload = {
            "record_count": len(records),
            "invalid_count": len(invalid),
            "invalid": [{"index": i, "errors": errs} for i, errs in invalid],
        }
        print(json.dumps(payload, indent=2))
        return 1 if invalid else 0

    print(f"validated {len(records)} record(s) against schema.json")
    if not invalid:
        print("all records valid")
    for i, errs in invalid:
        print(f"- [{i}]")
        for e in errs:
            print(f"    {e}")

    return 1 if invalid else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-burnwatch", description=__doc__)
    parser.add_argument("--version", action="version", version=f"llm-burnwatch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_p = subparsers.add_parser("report", help="Summarize cost from a log file")
    report_p.add_argument("--log-file", required=True)
    report_p.add_argument(
        "--pricing-file", default=None, help="Override pricing.json with a custom file"
    )
    report_p.add_argument(
        "--rub-rate",
        type=_positive_float,
        default=None,
        help="Deprecated, use --fx-rate/--currency instead. Also show total cost converted "
        "to RUB at this fixed, manually-supplied rate (RUB per USD). No exchange rate is "
        "ever fetched over the network.",
    )
    report_p.add_argument(
        "--fx-rate",
        type=_positive_float,
        default=None,
        help="Also show total cost converted to --currency at this fixed, manually-supplied "
        "rate (units of --currency per USD). Requires --currency. No exchange rate is ever "
        "fetched over the network.",
    )
    report_p.add_argument(
        "--currency",
        default=None,
        help="Currency code to display alongside --fx-rate (e.g. RUB, EUR). Requires --fx-rate.",
    )
    report_p.add_argument(
        "--since",
        type=_date_arg,
        default=None,
        help="Only include records on or after this UTC calendar date (YYYY-MM-DD, inclusive)",
    )
    report_p.add_argument(
        "--until",
        type=_date_arg,
        default=None,
        help="Only include records on or before this UTC calendar date (YYYY-MM-DD, inclusive)",
    )
    report_p.add_argument(
        "--trace-id",
        default=None,
        help="Only include records with this exact trace_id (e.g. to find the cost of one "
        "specific request across retries/sub-calls)",
    )
    report_p.add_argument(
        "--json", action="store_true", help="Print a machine-readable JSON summary"
    )
    report_p.add_argument(
        "--format",
        choices=["text", "csv"],
        default="text",
        help="Output format. 'csv' prints a normalized dimension,key,cost_usd table "
        "(total/label/model rows) instead of the human-readable summary; ignores "
        "--rub-rate/--fx-rate and cannot be combined with --json.",
    )
    report_p.set_defaults(handler=cmd_report)

    dashboard_p = subparsers.add_parser(
        "dashboard", help="Write a static HTML cost dashboard from a log file"
    )
    dashboard_p.add_argument("--log-file", required=True)
    dashboard_p.add_argument("--out", required=True)
    dashboard_p.add_argument(
        "--pricing-file", default=None, help="Override pricing.json with a custom file"
    )
    dashboard_p.add_argument(
        "--rub-rate",
        type=_positive_float,
        default=None,
        help="Deprecated, use --fx-rate/--currency instead. Also show total cost converted "
        "to RUB at this fixed, manually-supplied rate (RUB per USD). No exchange rate is "
        "ever fetched over the network.",
    )
    dashboard_p.add_argument(
        "--fx-rate",
        type=_positive_float,
        default=None,
        help="Also show total cost converted to --currency at this fixed, manually-supplied "
        "rate (units of --currency per USD). Requires --currency. No exchange rate is ever "
        "fetched over the network.",
    )
    dashboard_p.add_argument(
        "--currency",
        default=None,
        help="Currency code to display alongside --fx-rate (e.g. RUB, EUR). Requires --fx-rate.",
    )
    dashboard_p.add_argument(
        "--since",
        type=_date_arg,
        default=None,
        help="Only include records on or after this UTC calendar date (YYYY-MM-DD, inclusive)",
    )
    dashboard_p.add_argument(
        "--until",
        type=_date_arg,
        default=None,
        help="Only include records on or before this UTC calendar date (YYYY-MM-DD, inclusive)",
    )
    dashboard_p.set_defaults(handler=cmd_dashboard)

    demo_p = subparsers.add_parser("demo-data", help="Write a synthetic demo log")
    demo_p.add_argument("--out", required=True)
    demo_p.add_argument("--n-normal", type=int, default=200)
    demo_p.add_argument("--n-anomalies", type=int, default=10)
    demo_p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    demo_p.set_defaults(handler=cmd_demo_data)

    detect_p = subparsers.add_parser("detect", help="Detect anomalous calls in a log")
    detect_p.add_argument("--log-file", required=True)
    detect_p.add_argument("--threshold", type=float, default=Z_SCORE_THRESHOLD)
    detect_p.add_argument("--model-dir", default="models")
    detect_p.add_argument(
        "--pricing-file", default=None, help="Override pricing.json with a custom file"
    )
    detect_p.add_argument(
        "--json", action="store_true", help="Print a machine-readable JSON summary"
    )
    detect_p.add_argument(
        "--allowed-models",
        nargs="+",
        default=None,
        help="Only these models are allowed; any other model triggers a critical rule violation",
    )
    detect_p.add_argument(
        "--max-call-cost",
        type=_positive_float,
        default=None,
        help="Maximum cost (USD) for a single call before it's flagged as a rule violation",
    )
    detect_p.add_argument(
        "--max-trace-cost",
        type=_positive_float,
        default=None,
        help="Maximum total cost (USD) for a single trace_id before it's flagged as a rule violation",
    )
    detect_p.add_argument(
        "--frequency-detector",
        choices=["auto", "on", "off"],
        default="auto",
        help=(
            "Runaway-agent frequency detector: 'auto' (default) enables it only once "
            "the log has enough calendar span for a seasonal (weekday/hour) baseline; "
            "'on'/'off' override that decision explicitly"
        ),
    )
    detect_p.add_argument(
        "--cusum-detector",
        choices=["on", "off"],
        default="on",
        help=(
            "Sustained level-shift (CUSUM) detector over output_tokens/cost_micros: "
            "'on' (default) runs it, 'off' disables it. Unlike --frequency-detector, "
            "there's no 'auto' -- a sustained cost/token shift isn't subject to the "
            "day-of-week false-positive risk that justifies frequency's seasonal gating"
        ),
    )
    detect_p.add_argument(
        "--follow",
        action="store_true",
        help=(
            "Keep polling --log-file for new records and stream newly triggered "
            "alerts as one JSON object per line to stdout, instead of a single "
            "one-shot report. Ignores --json (follow mode has its own streaming "
            "output format)."
        ),
    )
    detect_p.add_argument(
        "--poll-interval",
        type=_positive_float,
        default=5.0,
        help="Seconds between polls in --follow mode (default: 5.0)",
    )
    detect_p.set_defaults(handler=cmd_detect)

    train_p = subparsers.add_parser(
        "train", help="Train an anomaly-detection model (requires scikit-learn)"
    )
    train_p.add_argument("--log-file", required=True)
    train_p.add_argument("--model-dir", default="models")
    train_p.add_argument("--keep-last", type=int, default=KEEP_LAST_DEFAULT)
    train_p.add_argument("--contamination", type=_contamination_type, default=CONTAMINATION)
    train_p.set_defaults(handler=cmd_train)

    schema_p = subparsers.add_parser("schema", help="Print the JSONL log schema")
    schema_p.set_defaults(handler=cmd_schema)

    validate_p = subparsers.add_parser(
        "validate", help="Check a log's records against the packaged JSON schema"
    )
    validate_p.add_argument("--log-file", required=True)
    validate_p.add_argument(
        "--json", action="store_true", help="Print a machine-readable JSON summary"
    )
    validate_p.set_defaults(handler=cmd_validate)

    pricing_p = subparsers.add_parser("pricing", help="Manage local pricing data")
    pricing_sub = pricing_p.add_subparsers(dest="pricing_command", required=True)
    pricing_import_p = pricing_sub.add_parser(
        "import",
        help="Import pricing from a local file or http(s):// URL in LiteLLM's "
        "model_prices_and_context_window.json format, saved to a user config file that "
        "takes priority over the packaged pricing.json for report/dashboard/detect",
    )
    pricing_import_p.add_argument(
        "source",
        help="Local file path or http(s):// URL to import pricing from, e.g. "
        "LiteLLM's community-maintained "
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json (third-party source -- only "
        "import from a URL you trust, see SECURITY.md)",
    )
    pricing_import_p.set_defaults(handler=cmd_pricing_import)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:  # unexpected failure -> exit code 2, not a raw traceback
        error(f"unexpected error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
