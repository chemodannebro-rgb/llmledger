"""Feature engineering for the ML anomaly detector.

Reuses the same group-relative statistic as `baseline.py` (modified
z-score of `input_tokens`/`output_tokens`/`cost_micros` against the
history of the same (label, model) pair, degrading to model-only history
for small groups) but exposes it as a plain numeric feature matrix for
`IsolationForest`, rather than a threshold decision.

Group-relative features are used deliberately instead of raw token counts:
different agent steps are naturally different scales (e.g. "retrieval"
vs. "summarize"), so a single global z-score would conflate ordinary scale
differences between steps with genuine anomalies.
"""

from __future__ import annotations

import math
from typing import Sequence

from .._messages import warn
from .baseline import FEATURES, _group_key, _median_mad, _model_key, analyze
from .constants import (
    DRIFT_MULTIPLIER,
    EXTREME_Z_SENTINEL,
    HIGH_CARDINALITY_WARNING_FRACTION,
    MIN_GROUP_SAMPLES,
)


def extract_features(records: Sequence[dict]) -> tuple[list[list[float]], list[int]]:
    """Return `(X, kept_indices)`.

    `X` is a feature matrix (one row per record) built from the same
    group-relative modified z-scores as `baseline.analyze`. `kept_indices`
    maps each row of `X` back to its position in `records`; records marked
    `insufficient_data` by `baseline.analyze` (too little history at both
    the group and model level) are excluded rather than given a fabricated
    feature vector.
    """
    analyses = analyze(records)
    X: list[list[float]] = []
    kept_indices: list[int] = []

    for i, a in enumerate(analyses):
        if a.status == "insufficient_data":
            continue
        row = []
        for feature in FEATURES:
            score = next((s for s in a.scores if s.feature == feature), None)
            if score is None:
                row.append(0.0)
            elif score.is_extreme:
                sign = math.copysign(1.0, score.value - score.median)
                row.append(sign * EXTREME_Z_SENTINEL)
            elif score.z_score is None:
                # MAD == 0 and value == median: zero spread, no deviation.
                row.append(0.0)
            else:
                row.append(score.z_score)
        X.append(row)
        kept_indices.append(i)

    return X, kept_indices


def check_label_cardinality(records: Sequence[dict]) -> None:
    """Warn if a large fraction of (label, model) groups have fewer than
    `MIN_GROUP_SAMPLES` records each -- a sign that too many distinct
    labels are in use for group-relative statistics to be meaningful,
    which would otherwise silently degrade detection accuracy for most of
    the log.
    """
    groups: dict[tuple, int] = {}
    for r in records:
        key = (r.get("label"), r.get("model"))
        groups[key] = groups.get(key, 0) + 1

    if not groups:
        return

    small_groups = sum(1 for n in groups.values() if n < MIN_GROUP_SAMPLES)
    fraction = small_groups / len(groups)
    if fraction > HIGH_CARDINALITY_WARNING_FRACTION:
        warn(
            f"{small_groups}/{len(groups)} (label, model) groups have fewer "
            f"than {MIN_GROUP_SAMPLES} calls each; group-relative statistics "
            "will be unreliable for most of your data. Consider using "
            "fewer, coarser-grained labels."
        )


def _stats_by_key(records: Sequence[dict], key_fn) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault(key_fn(r), []).append(r)

    entries = []
    for key, recs in groups.items():
        feature_stats = {}
        for feature in FEATURES:
            values = [r[feature] for r in recs if feature in r]
            if not values:
                continue
            med, mad = _median_mad(values)
            feature_stats[feature] = {"median": med, "mad": mad, "n": len(values)}
        entries.append({"key": list(key), "features": feature_stats})
    return entries


def compute_reference_stats(records: Sequence[dict]) -> dict:
    """Reference median/MAD per (label, model) group and per model-only
    fallback group.

    Deliberately kept free of any ML dependency (unlike `train.py`, which
    imports scikit-learn at module level): this is saved into a trained
    model's metadata for later drift comparison, but also needs to be
    computable by `detect` on a fresh log even when scikit-learn isn't
    installed, so it can't live in `train.py`.
    """
    return {
        "by_group": _stats_by_key(records, _group_key),
        "by_model": _stats_by_key(records, _model_key),
    }


def detect_drift(current_stats: dict, reference_stats: dict) -> list[str]:
    """Compare `current_stats` (from `compute_reference_stats` on a fresh
    log) against `reference_stats` (saved at train time) and return a list
    of human-readable warning messages for any (label, model) group whose
    current MAD has grown by more than `DRIFT_MULTIPLIER` relative to the
    reference MAD -- a sign the trained model may no longer reflect the
    data and `llmledger train` should be re-run.

    Only groups present in both are compared; new groups (no reference yet)
    are silently skipped rather than flagged as drift.
    """
    reference_by_key = {
        tuple(entry["key"]): entry["features"]
        for entry in reference_stats.get("by_group", [])
    }

    messages = []
    for entry in current_stats.get("by_group", []):
        key = tuple(entry["key"])
        reference_features = reference_by_key.get(key)
        if reference_features is None:
            continue
        for feature, current in entry["features"].items():
            reference = reference_features.get(feature)
            if reference is None or reference["mad"] == 0:
                continue
            if current["mad"] > DRIFT_MULTIPLIER * reference["mad"]:
                messages.append(
                    f"drift detected for group {key!r} feature '{feature}': "
                    f"current MAD={current['mad']:.2f} vs. reference "
                    f"MAD={reference['mad']:.2f} (train a new model with "
                    "'llmledger train' if this is expected)"
                )
    return messages
