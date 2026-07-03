"""Internal sanity check for both anomaly detectors (baseline and ML)
against a deterministic synthetic log with a known number of injected
anomalies (`demo_data.py`, fixed seed).

This is intentionally not a public `evaluate` command: recall/precision
can only be computed here because we know the ground truth for our own
synthetic data. On a real customer log there is no ground truth, so this
kind of metric would be meaningless/dishonest as a user-facing feature.
Here it only exists to catch a regression that silently breaks detection.
"""

from __future__ import annotations

import pytest

from llmledger.anomaly.baseline import analyze
from llmledger.demo_data import write_demo_log

N_ANOMALIES = 10
N_NORMAL = 200


def _generate(tmp_path):
    log_path = tmp_path / "demo.jsonl"
    results = write_demo_log(log_path, n_normal=N_NORMAL, n_anomalies=N_ANOMALIES)
    records = [r for r, _ in results]
    is_anomaly = [flag for _, flag in results]
    return records, is_anomaly


def test_baseline_detector_finds_all_injected_anomalies_with_few_false_positives(
    tmp_path,
):
    records, is_anomaly = _generate(tmp_path)
    analyses = analyze(records)

    injected_idx = {i for i, flag in enumerate(is_anomaly) if flag}
    detected_idx = {i for i, a in enumerate(analyses) if a.status == "anomaly"}

    true_positives = injected_idx & detected_idx
    false_negatives = injected_idx - detected_idx
    false_positives = detected_idx - injected_idx

    assert not false_negatives, "baseline detector missed an injected anomaly"
    assert len(true_positives) == N_ANOMALIES
    # A handful of false positives on 200 normal calls is expected for a
    # z-score threshold tuned for statistical soundness, not for a
    # zero-false-positive demo; this bounds it isn't unreasonably noisy.
    assert len(false_positives) <= 5


def test_ml_detector_finds_all_injected_anomalies(tmp_path):
    pytest.importorskip("sklearn")

    from llmledger.anomaly.features import extract_features
    from llmledger.anomaly.registry import load_model
    from llmledger.anomaly.train import train

    records, is_anomaly = _generate(tmp_path)
    version_dir = train(records, model_dir=tmp_path / "models")
    model, _metadata = load_model(version_dir)

    X, kept_indices = extract_features(records)
    predictions = model.predict(X)  # -1 == anomaly, 1 == normal

    injected_idx = {i for i, flag in enumerate(is_anomaly) if flag}
    detected_idx = {
        kept_indices[i] for i, pred in enumerate(predictions) if pred == -1
    }

    false_negatives = injected_idx - detected_idx
    assert not false_negatives, "ML detector missed an injected anomaly"
