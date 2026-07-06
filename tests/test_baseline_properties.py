"""Property-based/fuzz tests for the robust z-score statistics in
`anomaly/baseline.py` (BACKLOG #17).

These complement the example-based tests in `test_baseline.py` by throwing
a much wider, randomly-generated range of inputs at `_median_mad`,
`_score_feature`, and `analyze()` itself -- in particular the edge cases
that are easy to miss by hand: all-identical values (MAD == 0), single-
sample groups, negative/zero/huge magnitudes, and mixed-size random
(label, model) groupings feeding `analyze()`'s group/model/insufficient-data
fallback logic.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from llm_burnwatch.anomaly.baseline import _median_mad, _score_feature, analyze
from llm_burnwatch.anomaly.constants import MIN_GROUP_SAMPLES, Z_SCORE_THRESHOLD

_finite_floats = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9)


@given(st.lists(_finite_floats, min_size=1, max_size=200))
def test_median_mad_never_negative(values):
    _med, mad = _median_mad(values)
    assert mad >= 0


@given(st.lists(_finite_floats, min_size=1, max_size=200))
def test_median_mad_median_is_within_value_range(values):
    med, _mad = _median_mad(values)
    assert min(values) <= med <= max(values)


@given(_finite_floats, st.integers(min_value=1, max_value=50))
def test_median_mad_of_identical_values_has_zero_mad(value, n):
    med, mad = _median_mad([value] * n)
    assert mad == 0
    assert med == value


@given(_finite_floats, st.integers(min_value=1, max_value=50))
def test_score_feature_at_zero_mad_matches_value_equals_median(value, n):
    med, mad = _median_mad([value] * n)

    score = _score_feature("f", value, med, mad, Z_SCORE_THRESHOLD)

    assert mad == 0
    assert score.z_score is None
    assert score.is_extreme is False
    assert score.is_anomalous is False


@given(_finite_floats, _finite_floats)
def test_score_feature_at_zero_mad_flags_any_deviation_as_extreme(med, value):
    if value == med:
        return
    score = _score_feature("f", value, med, 0, Z_SCORE_THRESHOLD)

    assert score.z_score is None
    assert score.is_extreme is True
    assert score.is_anomalous is True


@given(
    med=_finite_floats,
    mad=st.floats(min_value=1e-6, max_value=1e9, allow_nan=False, allow_infinity=False),
    value=_finite_floats,
)
def test_score_feature_anomalous_iff_z_beyond_threshold(med, mad, value):
    score = _score_feature("f", value, med, mad, Z_SCORE_THRESHOLD)

    assert score.is_extreme is False
    assert score.z_score is not None
    assert score.is_anomalous == (abs(score.z_score) > Z_SCORE_THRESHOLD)


@given(
    med=_finite_floats,
    mad=st.floats(min_value=1e-6, max_value=1e9, allow_nan=False, allow_infinity=False),
    value=_finite_floats,
    threshold=st.floats(min_value=0.1, max_value=10, allow_nan=False, allow_infinity=False),
)
def test_score_feature_respects_custom_threshold(med, mad, value, threshold):
    score = _score_feature("f", value, med, mad, threshold)
    assert score.is_anomalous == (abs(score.z_score) > threshold)


def _record(label, model, input_tokens, output_tokens, cost_micros):
    return {
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_micros": cost_micros,
    }


_small_int = st.integers(min_value=0, max_value=1_000_000)
_label = st.sampled_from(["a", "b", "c"])
_model = st.sampled_from(["m1", "m2"])

_records_strategy = st.lists(
    st.tuples(_label, _model, _small_int, _small_int, _small_int),
    min_size=0,
    max_size=60,
).map(lambda rows: [_record(*row) for row in rows])


@settings(max_examples=100)
@given(_records_strategy)
def test_analyze_never_crashes_and_returns_one_result_per_record_in_order(records):
    results = analyze(records)

    assert len(results) == len(records)
    for record, result in zip(records, results):
        assert result.record is record
        assert result.status in ("ok", "anomaly", "insufficient_data")
        if result.status == "insufficient_data":
            assert result.scores == []


@given(
    st.integers(min_value=0, max_value=MIN_GROUP_SAMPLES - 1),
)
def test_analyze_marks_isolated_small_group_as_insufficient_data(n):
    # A single (label, model) pair with fewer than MIN_GROUP_SAMPLES records
    # and no other records to fall back to at the model level.
    records = [_record("only-label", "only-model", 800 + i, 150 + i, 2000 + i) for i in range(n)]

    results = analyze(records)

    assert all(r.status == "insufficient_data" for r in results)
