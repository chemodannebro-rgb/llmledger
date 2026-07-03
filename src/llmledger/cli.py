"""Command-line interface for llmledger.

Five subcommands:
  report     -- cost summary read back from a log
  demo-data  -- write a synthetic log with a known number of injected anomalies
  detect     -- baseline (+ optional ML cross-check) anomaly detection over a log
  train      -- train an IsolationForest model (requires `llmledger[anomaly]`)
  schema     -- print the packaged JSONL log schema

`report`/`demo-data`/`schema` never import scikit-learn. `detect` only
imports it indirectly, via `registry.load_model` deserializing (via
`skops.io`) an existing model -- if none exists yet, `detect` runs
baseline-only and never touches scikit-learn either. `train` imports
`anomaly.train` (which imports
scikit-learn at module level) lazily, inside a try/except, so the
zero-dependency core guarantee holds for every other command even when
scikit-learn is not installed.

Exit codes (a stable contract for cron/alerting integration -- the only
integration surface llmledger offers; it never sends notifications itself):
  0 -- ran cleanly, no anomalies found (or the command has no anomaly concept)
  1 -- ran cleanly, `detect` found at least one anomalous call
  2 -- execution error (bad path, bad arguments, missing dependency, ...)
"""

from __future__ import annotations

import argparse
import json

from . import __version__
from ._messages import error, warn
from .anomaly.baseline import analyze, format_score
from .anomaly.constants import CONTAMINATION, KEEP_LAST_DEFAULT, Z_SCORE_THRESHOLD
from .anomaly.features import (
    check_label_cardinality,
    compute_reference_stats,
    detect_drift,
    extract_features,
)
from .anomaly.registry import latest_version_dir, load_model
from .demo_data import DEFAULT_SEED, write_demo_log
from .logreader import check_scale, iter_log_records
from .tracker import build_report, load_default_pricing

DISCLAIMER = (
    "llmledger is a diagnostic aid, not a guarantee: it flags statistically "
    "unusual calls, it does not confirm they are errors, and it may miss "
    "real ones. Always use your own judgement before acting on its output."
)


def _print_header(pricing: dict) -> None:
    print(DISCLAIMER)
    last_updated = pricing.get("last_updated")
    if last_updated:
        print(f"pricing data last updated: {last_updated}")


def _load_pricing_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number, got {value!r}")
    return parsed


def cmd_report(args: argparse.Namespace) -> int:
    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    check_scale(args.log_file, len(records))
    pricing = (
        _load_pricing_file(args.pricing_file) if args.pricing_file else load_default_pricing()
    )
    result = build_report(records, pricing)

    _print_header(pricing)
    if result["call_count"] == 0:
        print("no records found in log")
        return 0

    print(f"calls: {result['call_count']}")
    total_cost_line = f"total cost: ${result['total_cost_usd']:.6f}"
    if args.rub_rate is not None:
        rub_total = result["total_cost_usd"] * args.rub_rate
        total_cost_line += f" (~₽{rub_total:.2f} at {args.rub_rate:.2f} ₽/$)"
    print(total_cost_line)
    print("by label:")
    for label, micros in sorted(result["by_label_micros"].items()):
        print(f"  {label}: ${micros / 1_000_000:.6f}")
    print("by model:")
    for model, micros in sorted(result["by_model_micros"].items()):
        print(f"  {model}: ${micros / 1_000_000:.6f}")
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


def _run_ml_cross_check(records: list[dict], model_dir: str) -> dict | None:
    """Return an ML cross-check summary, or `None` if no trained model
    exists yet at `model_dir`. Never raises: missing scikit-learn, a
    corrupted/tampered `model.skops` (sha256 mismatch), or a missing/corrupted
    `metadata.json` (e.g. an interrupted `train()`, or manual tampering) are
    all reported through `warn()`/`error()` and reflected in the returned
    dict's `available` flag instead of aborting `detect` entirely -- the
    baseline result is still valid and should still be printed even when the
    ML side of the registry is unusable.
    """
    version_dir = latest_version_dir(model_dir)
    if version_dir is None:
        return None

    try:
        model, metadata = load_model(version_dir)
    except ImportError:
        warn(
            "a trained model exists but scikit-learn is not installed; "
            "skipping ML cross-check. Install with: "
            'pip install "llmledger[anomaly]"'
        )
        return {"available": False, "reason": "scikit-learn not installed"}
    except ValueError as exc:
        error(str(exc))
        return {"available": False, "reason": str(exc)}
    except (OSError, json.JSONDecodeError) as exc:
        error(
            f"could not load model registry at {version_dir}: {exc}. "
            "Skipping ML cross-check; re-run `llmledger train` to regenerate it."
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


def cmd_detect(args: argparse.Namespace) -> int:
    try:
        records = list(iter_log_records(args.log_file))
    except FileNotFoundError as exc:
        error(str(exc))
        return 2

    check_scale(args.log_file, len(records))
    pricing = load_default_pricing()

    if not records:
        if args.json:
            print(json.dumps({"call_count": 0, "anomaly_count": 0, "anomalies": []}, indent=2))
        else:
            _print_header(pricing)
            print("no records found in log; nothing to analyze")
        return 0

    check_label_cardinality(records)
    analyses = analyze(records, threshold=args.threshold)

    anomalous = []
    insufficient_count = 0
    for i, a in enumerate(analyses):
        if a.status == "insufficient_data":
            insufficient_count += 1
        elif a.status == "anomaly":
            anomalous.append((i, a))

    ml_info = _run_ml_cross_check(records, args.model_dir)

    if args.json:
        payload = {
            "call_count": len(records),
            "threshold": args.threshold,
            "anomaly_count": len(anomalous),
            "insufficient_data_count": insufficient_count,
            "anomalies": [
                {
                    "index": i,
                    "label": a.record.get("label"),
                    "model": a.record.get("model"),
                    "timestamp": a.record.get("timestamp"),
                    "features": [
                        {
                            "feature": s.feature,
                            "value": s.value,
                            "median": s.median,
                            "mad": s.mad,
                            "z_score": s.z_score,
                            "is_extreme": s.is_extreme,
                        }
                        for s in a.scores
                        if s.is_anomalous
                    ],
                }
                for i, a in anomalous
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
            print(f"- [{i}] {a.record.get('label')} / {a.record.get('model')} @ {a.record.get('timestamp')}")
            for s in a.scores:
                if s.is_anomalous:
                    print(f"    {format_score(s)}")
        if ml_info is not None and ml_info.get("available"):
            print(
                f"ML cross-check (model v{ml_info['model_version']}): "
                f"{ml_info['anomaly_count']} call(s) flagged"
            )

    return 1 if anomalous else 0


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
            'pip install "llmledger[anomaly]"'
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
        version_dir = train_model(
            records,
            model_dir=args.model_dir,
            keep_last=args.keep_last,
            contamination=args.contamination,
        )
    except ValueError as exc:
        error(str(exc))
        return 2

    print(f"trained model saved to {version_dir}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from importlib import resources

    text = resources.files("llmledger").joinpath("schema.json").read_text(encoding="utf-8")
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmledger", description=__doc__)
    parser.add_argument("--version", action="version", version=f"llmledger {__version__}")
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
        help="Also show total cost converted to RUB at this fixed, manually-supplied rate "
        "(RUB per USD). No exchange rate is ever fetched over the network.",
    )
    report_p.set_defaults(handler=cmd_report)

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
        "--json", action="store_true", help="Print a machine-readable JSON summary"
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

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:  # unexpected failure -> exit code 2, not a raw traceback
        error(f"unexpected error: {exc}")
        return 2
