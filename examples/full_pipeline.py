"""End-to-end walkthrough: CostTracker -> demo data -> baseline detection ->
dashboard -> ML training -> ML-assisted detection.

Everything through step 3 (writing the dashboard) works with only the core
package installed. Steps 4-5 require the optional ML extra:

    pip install "llm-burnwatch[anomaly]"

If scikit-learn isn't installed, this script still runs to completion --
it just skips the ML steps and explains how to enable them, the same way
`llm-burnwatch detect` degrades gracefully when no trained model is available.

    python examples/full_pipeline.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from llm_burnwatch.anomaly.baseline import analyze, format_score
from llm_burnwatch.dashboard import render_dashboard
from llm_burnwatch.demo_data import write_demo_log
from llm_burnwatch.logreader import filter_by_period, iter_log_records, parse_date
from llm_burnwatch.tracker import load_default_pricing


def main() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="llm-burnwatch-pipeline-"))
    log_file = work_dir / "calls.jsonl"
    model_dir = work_dir / "models"

    # 1. Generate a synthetic log via a real CostTracker (same code path as
    #    `llm-burnwatch demo-data`): 200 normal calls plus 10 injected outliers.
    write_demo_log(log_file, n_normal=200, n_anomalies=10)
    records = list(iter_log_records(log_file))
    print(f"logged {len(records)} demo call(s) to {log_file}")

    # 2. Baseline detection: robust (median/MAD) z-score against each call's
    #    own (label, model) history. No dependencies beyond the standard
    #    library -- this always works.
    analyses = analyze(records)
    anomalous = [(i, a) for i, a in enumerate(analyses) if a.is_anomaly]
    print(f"\nbaseline: {len(anomalous)} anomalous call(s) found")
    for i, a in anomalous[:3]:
        print(f"  - [{i}] {a.record['label']} / {a.record['model']}")
        for score in a.scores:
            if score.is_anomalous:
                print(f"      {format_score(score)}")

    # 3. Write a static HTML dashboard, the same output `llm-burnwatch dashboard`
    #    produces. All the demo calls were logged just now, so they share one
    #    UTC calendar date -- `--since`/`--until` (here via
    #    `filter_by_period`/`parse_date` directly, the same helpers the CLI
    #    uses) narrows the dashboard to that one date. On a real log spanning
    #    many days this is how you'd scope a report to a billing period, an
    #    incident window, etc.
    today = parse_date(records[0]["timestamp"])
    scoped_records = filter_by_period(records, since=today, until=today)
    dashboard_path = work_dir / "dashboard.html"
    html = render_dashboard(scoped_records, load_default_pricing(), since=today, until=today)
    dashboard_path.write_text(html, encoding="utf-8")
    print(f"\nwrote dashboard for {today} ({len(scoped_records)} call(s)) to {dashboard_path}")

    # 4. Train an IsolationForest as a second, ML-based opinion. This is the
    #    one part of llm-burnwatch that needs scikit-learn, so the import is
    #    deliberately deferred to here and guarded -- the rest of this
    #    script (and the rest of the package) never imports it at module
    #    level.
    try:
        from llm_burnwatch.anomaly.train import train
    except ImportError:
        print(
            '\nscikit-learn not installed; skipping ML training/detection. '
            'Install with: pip install "llm-burnwatch[anomaly]"'
        )
        return

    version_dir, eval_metrics = train(records, model_dir=model_dir)
    print(f"\ntrained model saved to {version_dir}")
    if eval_metrics["holdout_used"]:
        print(
            f"held-out eval: {eval_metrics['flagged_count']}/{eval_metrics['n_holdout_examples']} "
            f"({eval_metrics['flagged_fraction']:.1%}) held-out example(s) flagged anomalous"
        )
    else:
        print(f"held-out eval skipped: {eval_metrics['reason']}")

    # 5. Load the trained model back and cross-check the same log with it.
    from llm_burnwatch.anomaly.features import extract_features
    from llm_burnwatch.anomaly.registry import load_model

    model, metadata = load_model(version_dir)
    X, kept_indices = extract_features(records)
    predictions = model.predict(X)
    ml_flagged = [kept_indices[i] for i, pred in enumerate(predictions) if pred == -1]

    print(
        f"ML cross-check (model v{metadata['version']}, "
        f"trained on {metadata['n_examples']} example(s)): "
        f"{len(ml_flagged)} call(s) flagged"
    )


if __name__ == "__main__":
    main()
