"""Baseline (non-ML) anomaly detection via a robust z-score.

For each call, compares `input_tokens`, `output_tokens`, and `cost_micros`
against the history of the same (label, model) pair, using the modified
z-score of Iglewicz & Hoaglin (1993): `M_i = 0.6745 * (x_i - median) / MAD`.
This is robust to outliers already present in the history (unlike a
mean/stdev z-score, which such outliers would inflate and mask), and does
not assume a normal distribution. `|M_i| > Z_SCORE_THRESHOLD` (3.5, the
standard cutoff from the same paper, not tuned by eye) flags a call as
anomalous on that feature.

Degrades gracefully when there isn't enough history for a group: if the
exact (label, model) pair has fewer than `MIN_GROUP_SAMPLES` records, falls
back to statistics for the model across all labels; if that's still too
small, the call is marked `insufficient_data` rather than silently treated
as normal or silently dropped.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Sequence

from .constants import MIN_GROUP_SAMPLES, Z_SCORE_THRESHOLD

FEATURES = ("input_tokens", "output_tokens", "cost_micros")


@dataclass
class FeatureScore:
    feature: str
    value: float
    median: float
    mad: float
    z_score: float | None  # None when MAD == 0 (see is_extreme)
    is_extreme: bool  # MAD == 0 and value deviates from the (zero-spread) median
    is_anomalous: bool


@dataclass
class CallAnalysis:
    record: dict
    status: str  # "ok" | "anomaly" | "insufficient_data"
    group_key: tuple
    scores: list[FeatureScore] = field(default_factory=list)

    @property
    def is_anomaly(self) -> bool:
        return self.status == "anomaly"


def _median_mad(values: Sequence[float]) -> tuple[float, float]:
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return med, mad


def _score_feature(
    feature: str, value: float, history: Sequence[float], threshold: float
) -> FeatureScore:
    med, mad = _median_mad(history)
    if mad == 0:
        is_extreme = value != med
        return FeatureScore(feature, value, med, mad, None, is_extreme, is_extreme)
    z = 0.6745 * (value - med) / mad
    return FeatureScore(feature, value, med, mad, z, False, abs(z) > threshold)


def _group_key(record: dict) -> tuple:
    return (record.get("label"), record.get("model"))


def _model_key(record: dict) -> tuple:
    return (None, record.get("model"))


def analyze(
    records: Sequence[dict], *, threshold: float = Z_SCORE_THRESHOLD
) -> list[CallAnalysis]:
    """Analyze each record against the (label, model) group it belongs to,
    degrading to model-only history when the exact group is too small, and
    marking `insufficient_data` when even that is too small.

    This is a batch/offline analysis over one log (the comparison
    population for a group includes all of that group's records, not just
    ones preceding it), not a streaming/online detector.

    `threshold` overrides `Z_SCORE_THRESHOLD` for this call only (exposed
    as `detect --threshold` on the CLI).
    """
    by_group: dict[tuple, list[dict]] = {}
    by_model: dict[tuple, list[dict]] = {}
    for r in records:
        by_group.setdefault(_group_key(r), []).append(r)
        by_model.setdefault(_model_key(r), []).append(r)

    results = []
    for r in records:
        gkey = _group_key(r)
        group_records = by_group[gkey]
        if len(group_records) >= MIN_GROUP_SAMPLES:
            history_records = group_records
            used_key = gkey
        else:
            mkey = _model_key(r)
            model_records = by_model[mkey]
            if len(model_records) >= MIN_GROUP_SAMPLES:
                history_records = model_records
                used_key = mkey
            else:
                results.append(
                    CallAnalysis(record=r, status="insufficient_data", group_key=gkey)
                )
                continue

        scores = []
        for feature in FEATURES:
            history = [h[feature] for h in history_records if feature in h]
            value = r.get(feature)
            if value is None or not history:
                continue
            scores.append(_score_feature(feature, value, history, threshold))

        status = "anomaly" if any(s.is_anomalous for s in scores) else "ok"
        results.append(
            CallAnalysis(record=r, status=status, group_key=used_key, scores=scores)
        )

    return results


def format_score(score: FeatureScore) -> str:
    """Human-readable rendering of a feature score.

    Avoids printing a literal `inf` when MAD == 0 (a group with zero
    spread whose value still deviates from it) in favor of a clear
    "extreme deviation" label.
    """
    if score.is_extreme:
        return (
            f"{score.feature}: extreme deviation (value={score.value}, "
            f"group median={score.median}, MAD=0)"
        )
    return (
        f"{score.feature}: z={score.z_score:.2f} "
        f"(value={score.value}, median={score.median}, MAD={score.mad:.2f})"
    )
