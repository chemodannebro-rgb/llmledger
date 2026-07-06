from __future__ import annotations

import json

import pytest

pytest.importorskip("sklearn")

from llm_burnwatch.anomaly.constants import EVAL_HOLDOUT_MIN_EXAMPLES, MIN_GROUP_SAMPLES
from llm_burnwatch.anomaly.train import train
from llm_burnwatch.demo_data import write_demo_log
from llm_burnwatch.tracker import CostTracker


def _records(tmp_path, n_normal, n_anomalies=0):
    log_path = tmp_path / "demo.jsonl"
    results = write_demo_log(log_path, n_normal=n_normal, n_anomalies=n_anomalies)
    return [r for r, _ in results]


def _uniform_records(tmp_path, n, label="only-label", model="only-model"):
    """`n` records all sharing a single `(label, model)` pair, so they land
    in one group regardless of `n` -- unlike `write_demo_log`, which spreads
    calls randomly across 5 fixed pairs and so cannot guarantee any single
    group reaches `MIN_GROUP_SAMPLES` for small `n`.
    """
    tracker = CostTracker(tmp_path / "uniform.jsonl")
    return [
        tracker.log_call(
            label=label, model=model, input_tokens=800, output_tokens=150, cost=0.01
        )
        for _ in range(n)
    ]


def test_train_returns_version_dir_and_eval_metrics(tmp_path):
    records = _records(tmp_path, n_normal=200, n_anomalies=10)

    version_dir, eval_metrics = train(records, model_dir=tmp_path / "models")

    assert version_dir.exists()
    assert isinstance(eval_metrics, dict)
    assert "holdout_used" in eval_metrics


def test_train_skips_holdout_when_too_few_examples(tmp_path):
    assert EVAL_HOLDOUT_MIN_EXAMPLES > MIN_GROUP_SAMPLES
    # Just enough for one (label, model) group to clear MIN_GROUP_SAMPLES,
    # but nowhere near EVAL_HOLDOUT_MIN_EXAMPLES.
    records = _uniform_records(tmp_path, MIN_GROUP_SAMPLES + 1)

    _version_dir, eval_metrics = train(records, model_dir=tmp_path / "models")

    assert eval_metrics["holdout_used"] is False
    assert "reason" in eval_metrics


def test_train_uses_holdout_when_enough_examples(tmp_path):
    records = _records(tmp_path, n_normal=200, n_anomalies=10)

    _version_dir, eval_metrics = train(records, model_dir=tmp_path / "models")

    assert eval_metrics["holdout_used"] is True
    assert eval_metrics["n_holdout_examples"] > 0
    assert eval_metrics["n_train_examples"] > 0
    assert 0.0 <= eval_metrics["flagged_fraction"] <= 1.0
    assert eval_metrics["flagged_count"] <= eval_metrics["n_holdout_examples"]


def test_train_holdout_is_deterministic_across_runs(tmp_path):
    records = _records(tmp_path, n_normal=200, n_anomalies=10)

    _v1, eval_metrics_1 = train(records, model_dir=tmp_path / "models-a")
    _v2, eval_metrics_2 = train(records, model_dir=tmp_path / "models-b")

    assert eval_metrics_1 == eval_metrics_2


def test_train_final_model_is_trained_on_full_dataset_not_just_holdout_split(tmp_path):
    from llm_burnwatch.anomaly.features import extract_features

    records = _records(tmp_path, n_normal=200, n_anomalies=10)
    X, _kept = extract_features(records)

    version_dir, _eval_metrics = train(records, model_dir=tmp_path / "models")

    metadata = json.loads((version_dir / "metadata.json").read_text())
    assert metadata["n_examples"] == len(X)


def test_eval_metrics_are_persisted_in_registry_metadata(tmp_path):
    records = _records(tmp_path, n_normal=200, n_anomalies=10)

    version_dir, eval_metrics = train(records, model_dir=tmp_path / "models")

    metadata = json.loads((version_dir / "metadata.json").read_text())
    assert metadata["eval_metrics"] == eval_metrics
