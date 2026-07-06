from __future__ import annotations

import math

from llm_burnwatch.anomaly.baseline import FEATURES
from llm_burnwatch.anomaly.constants import DRIFT_MULTIPLIER, EXTREME_Z_SENTINEL, MIN_GROUP_SAMPLES
from llm_burnwatch.anomaly.features import (
    check_label_cardinality,
    compute_reference_stats,
    detect_drift,
    extract_features,
)


def _record(label, model, input_tokens, output_tokens, cost_micros):
    return {
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_micros": cost_micros,
    }


def test_insufficient_data_records_are_excluded_from_feature_matrix():
    assert MIN_GROUP_SAMPLES > 2
    sufficient = [
        _record("summarize", "gpt-4o", 800 + i, 150 + i, 2000 + i) for i in range(10)
    ]
    insufficient = [_record("rare-label", "rare-model", 10, 10, 10) for _ in range(2)]
    records = sufficient + insufficient

    X, kept_indices = extract_features(records)

    assert len(X) == len(sufficient)
    assert kept_indices == list(range(len(sufficient)))
    assert all(len(row) == len(FEATURES) for row in X)


def test_feature_matrix_has_one_row_per_kept_record():
    records = [_record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(10)]
    X, kept_indices = extract_features(records)
    assert len(X) == 10
    assert kept_indices == list(range(10))


def test_extreme_deviation_uses_finite_sentinel_not_infinity():
    identical = [_record("x", "gpt-4o", 100, 100, 100) for _ in range(10)]
    deviating = _record("x", "gpt-4o", 999, 100, 100)
    records = identical + [deviating]

    X, kept_indices = extract_features(records)
    deviating_row = X[kept_indices.index(len(records) - 1)]

    assert deviating_row[0] == EXTREME_Z_SENTINEL
    assert all(math.isfinite(v) for v in deviating_row)


def test_check_label_cardinality_warns_when_most_groups_are_tiny(capsys):
    # Many distinct (label, model) pairs, each with only 1-2 calls.
    records = [_record(f"label-{i}", "gpt-4o", 100, 10, 100) for i in range(20)]
    check_label_cardinality(records)
    captured = capsys.readouterr()
    assert "group-relative statistics will be unreliable" in captured.err


def test_check_label_cardinality_silent_when_groups_are_well_populated(capsys):
    records = [
        _record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(20)
    ]
    check_label_cardinality(records)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_label_cardinality_silent_for_empty_input(capsys):
    check_label_cardinality([])
    captured = capsys.readouterr()
    assert captured.err == ""


def test_compute_reference_stats_has_group_and_model_entries():
    records = [_record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(10)]
    stats = compute_reference_stats(records)
    assert stats["by_group"][0]["key"] == ["summarize", "gpt-4o"]
    assert stats["by_model"][0]["key"] == [None, "gpt-4o"]
    assert "input_tokens" in stats["by_group"][0]["features"]


def test_detect_drift_flags_group_whose_mad_grew_past_multiplier():
    reference_records = [
        _record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(10)
    ]
    reference_stats = compute_reference_stats(reference_records)

    # Same group, but now with much larger spread (MAD) in input_tokens.
    drifted_records = [
        _record("summarize", "gpt-4o", 800 + i * (DRIFT_MULTIPLIER * 20), 150, 2000)
        for i in range(10)
    ]
    current_stats = compute_reference_stats(drifted_records)

    messages = detect_drift(current_stats, reference_stats)
    assert any("summarize" in m and "input_tokens" in m for m in messages)


def test_detect_drift_silent_when_stats_are_stable():
    records = [_record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(10)]
    reference_stats = compute_reference_stats(records)
    current_stats = compute_reference_stats(records)

    assert detect_drift(current_stats, reference_stats) == []


def test_detect_drift_ignores_groups_absent_from_reference():
    reference_stats = compute_reference_stats(
        [_record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(10)]
    )
    current_stats = compute_reference_stats(
        [_record("new-label", "gpt-4o", 100000 + i, 150, 2000) for i in range(10)]
    )

    assert detect_drift(current_stats, reference_stats) == []
