from __future__ import annotations

from llmledger.anomaly.baseline import analyze, format_score
from llmledger.anomaly.constants import MIN_GROUP_SAMPLES


def _record(label, model, input_tokens, output_tokens, cost_micros):
    return {
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_micros": cost_micros,
    }


def test_clear_outlier_in_well_populated_group_is_flagged_anomaly():
    normal = [
        _record("summarize", "gpt-4o", 800 + i, 150 + i, 2000 + i)
        for i in range(20)
    ]
    outlier = _record("summarize", "gpt-4o", 50_000, 10_000, 200_000)
    records = normal + [outlier]

    results = analyze(records)
    outlier_result = results[-1]

    assert outlier_result.record is outlier
    assert outlier_result.status == "anomaly"
    assert outlier_result.group_key == ("summarize", "gpt-4o")


def test_typical_calls_in_well_populated_group_are_not_flagged():
    normal = [
        _record("summarize", "gpt-4o", 800 + i, 150 + i, 2000 + i)
        for i in range(20)
    ]
    results = analyze(normal)
    assert all(r.status == "ok" for r in results)


def test_small_group_degrades_to_model_level_statistics():
    # Only 2 records for ("summarize", "gpt-4o") -- below MIN_GROUP_SAMPLES --
    # but plenty of records for model "gpt-4o" once other labels are counted.
    assert MIN_GROUP_SAMPLES > 2
    group_records = [_record("summarize", "gpt-4o", 800, 150, 2000) for _ in range(2)]
    other_label_records = [
        _record("retrieval", "gpt-4o", 800 + i, 150 + i, 2000 + i) for i in range(10)
    ]
    records = group_records + other_label_records

    results = analyze(records)
    summarize_results = [r for r in results if r.record["label"] == "summarize"]
    assert all(r.status != "insufficient_data" for r in summarize_results)
    assert all(r.group_key == (None, "gpt-4o") for r in summarize_results)


def test_insufficient_data_when_both_group_and_model_are_too_small():
    assert MIN_GROUP_SAMPLES > 2
    records = [_record("summarize", "rare-model", 800, 150, 2000) for _ in range(2)]

    results = analyze(records)
    assert all(r.status == "insufficient_data" for r in results)
    assert all(r.scores == [] for r in results)


def test_zero_mad_with_deviating_value_is_extreme_not_infinite():
    identical = [_record("x", "gpt-4o", 100, 100, 100) for _ in range(10)]
    deviating = _record("x", "gpt-4o", 999, 100, 100)
    records = identical + [deviating]

    results = analyze(records)
    deviating_result = results[-1]

    assert deviating_result.status == "anomaly"
    input_score = next(s for s in deviating_result.scores if s.feature == "input_tokens")
    assert input_score.mad == 0
    assert input_score.is_extreme is True
    assert input_score.z_score is None

    rendered = format_score(input_score)
    assert "inf" not in rendered.lower()
    assert "extreme deviation" in rendered


def test_zero_mad_with_matching_value_is_not_extreme():
    identical = [_record("x", "gpt-4o", 100, 100, 100) for _ in range(10)]
    matching = _record("x", "gpt-4o", 100, 100, 100)
    records = identical + [matching]

    results = analyze(records)
    matching_result = results[-1]
    assert matching_result.status == "ok"
    input_score = next(s for s in matching_result.scores if s.feature == "input_tokens")
    assert input_score.is_extreme is False


def test_format_score_normal_case_shows_z_value():
    normal = [_record("summarize", "gpt-4o", 800 + i, 150, 2000) for i in range(20)]
    results = analyze(normal)
    score = results[0].scores[0]
    rendered = format_score(score)
    assert "z=" in rendered
