"""CUSUM (level-shift) detector: flags a sustained, cumulative rise in a
group's `output_tokens`/`cost_micros` relative to that group's own
reference median -- catching a level shift (e.g. a prompt change that
quietly makes every response longer/pricier) that no single call's own
z-score would necessarily trip, because each individual call can stay
under the baseline detector's threshold while still being part of a real,
sustained shift (see `test_cusum_detector.py` for a worked example).

Ships **enabled by default** (`enabled_by_default = True`), unlike
`FrequencyDetector`: a sustained rise in tokens/cost isn't subject to the
same "every Monday morning" seasonal false-positive risk that keeps the
frequency detector off by default (see `frequency_detector.py`'s
docstring) -- a real cost-per-call level shift looks the same regardless
of day-of-week/time-of-day.

Self-sufficient like `BaselineDetector`/`FrequencyDetector`: reference
median/MAD are computed directly from the group's own records passed into
`analyze()`, not loaded from a separately trained model's saved state, so
this detector never depends on whether `llm-burnwatch train` has been run.
"""

from __future__ import annotations

from typing import Sequence

from ..anomaly.baseline import _group_key, _median_mad, _model_key
from ..anomaly.constants import (
    CUSUM_H_MULTIPLIER,
    CUSUM_SLACK_MULTIPLIER,
    MIN_GROUP_SAMPLES,
)
from .protocol import Alert

# Only these two -- unlike baseline's four FEATURES -- because a level
# shift that matters financially always shows up in output volume and/or
# cost; input_tokens/cached_input_tokens are caller-controlled (the
# prompt itself), not a symptom of a shift the callee (model/prompt
# template) introduced.
CUSUM_FEATURES = ("output_tokens", "cost_micros")


class CusumDetector:
    name = "cusum"
    enabled_by_default = True

    def __init__(
        self,
        h_multiplier: float = CUSUM_H_MULTIPLIER,
        slack_multiplier: float = CUSUM_SLACK_MULTIPLIER,
    ) -> None:
        self.h_multiplier = h_multiplier
        self.slack_multiplier = slack_multiplier

    def analyze(self, records: Sequence[dict]) -> list[Alert]:
        by_group: dict[tuple, list[tuple[int, dict]]] = {}
        by_model: dict[tuple, list[tuple[int, dict]]] = {}
        for i, r in enumerate(records):
            by_group.setdefault(_group_key(r), []).append((i, r))
            by_model.setdefault(_model_key(r), []).append((i, r))

        alerts: list[Alert] = []
        for gkey, indexed in by_group.items():
            if len(indexed) >= MIN_GROUP_SAMPLES:
                history = indexed
            else:
                mkey = _model_key(indexed[0][1])
                model_records = by_model.get(mkey, [])
                if len(model_records) < MIN_GROUP_SAMPLES:
                    continue  # insufficient data at both group and model level
                history = model_records

            for feature in CUSUM_FEATURES:
                ref_values = [r[feature] for _, r in history if feature in r]
                if len(ref_values) < MIN_GROUP_SAMPLES:
                    continue
                median, mad = _median_mad(ref_values)
                if mad == 0:
                    continue  # no spread to normalize against

                values = [(i, r[feature]) for i, r in indexed if feature in r]
                alert = self._scan_feature(gkey, feature, values, median, mad)
                if alert is not None:
                    alerts.append(alert)

        return alerts

    def _scan_feature(
        self,
        group_key: tuple,
        feature: str,
        values: list[tuple[int, float]],
        median: float,
        mad: float,
    ) -> Alert | None:
        """One-sided tabular CUSUM: accumulates `(value - median - slack)`,
        resetting to zero whenever the running sum would go negative, and
        flags the first record where it exceeds `h_multiplier * mad`.

        Only tracks upward shifts -- a *drop* in tokens/cost is never a
        "runaway" symptom worth alerting on, same spike-only reasoning as
        `FrequencyDetector`.
        """
        threshold = self.h_multiplier * mad
        slack = self.slack_multiplier * mad

        cusum = 0.0
        run_start_index: int | None = None
        for record_index, value in values:
            deviation = value - median - slack
            if cusum + deviation <= 0:
                cusum = 0.0
                run_start_index = None
                continue
            if run_start_index is None:
                run_start_index = record_index
            cusum += deviation

            if cusum > threshold:
                return Alert(
                    detector=self.name,
                    severity="warning",
                    kind="level_shift",
                    group_key=group_key,
                    record_ref=record_index,
                    evidence={
                        "feature": feature,
                        "cusum_value": cusum,
                        "reference_median": median,
                        "h_threshold": threshold,
                        "shift_started_at_record": run_start_index,
                    },
                    message=(
                        f"sustained rise in {feature} starting at record "
                        f"{run_start_index} (cusum={cusum:.2f} > "
                        f"threshold={threshold:.2f})"
                    ),
                )
        return None
