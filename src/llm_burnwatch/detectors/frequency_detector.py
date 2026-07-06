"""Frequency (runaway-agent) detector: flags time windows whose call count
is anomalously high relative to that group's own history of window counts.

Ships **disabled by default** (`enabled_by_default = False`, decided
up front rather than added as an afterthought): without a notion of
expected daily/weekly call patterns (seasonal baselines, a later v0.8 task),
a routine "every Monday morning" burst of calls looks statistically
identical to a runaway agent looping out of control -- see
`FREQUENCY_Z_THRESHOLD`'s docstring in `anomaly/constants.py`. Once seasonal
baselines are available for a given log, the caller is expected to pass
`enabled_overrides={"frequency": True}` to `run_detectors()`;
`FrequencyDetector` itself never flips its own default.

Batch/offline like `BaselineDetector`: windows are built from the whole
input sequence at once, not accumulated incrementally across calls. This is
consistent with `detect --follow`'s planned design of re-running detectors
over a small, fixed-size window of records on each poll rather than
maintaining streaming state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Sequence

from ..anomaly.baseline import _median_mad
from ..anomaly.constants import (
    FREQUENCY_ABS_CALLS_PER_WINDOW,
    FREQUENCY_WINDOW_SECONDS,
    FREQUENCY_Z_THRESHOLD,
    MIN_GROUP_SAMPLES,
)
from ..logreader import parse_timestamp
from .protocol import Alert

# Sentinel group key for the log-wide (not per label/model) frequency check
# -- catches a fan-out burst spread across many different labels/models that
# no single (label, model) group's own history would flag.
GLOBAL_GROUP_KEY = ("__global__",)


def _group_key(record: dict) -> tuple:
    return (record.get("label"), record.get("model"))


def _bucket_by_window(records: Sequence[dict], key_fn) -> dict[tuple, dict[int, list[int]]]:
    """`{group_key: {window_index: [record_index, ...]}}`, built from each
    record's parsed timestamp.

    Records with a missing/unparseable timestamp can't be placed in a
    window and are silently excluded -- the same tolerance for bad
    timestamps already applied by `logreader.filter_by_period`.
    """
    buckets: dict[tuple, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, r in enumerate(records):
        ts = parse_timestamp(r.get("timestamp"))
        if ts is None:
            continue
        window_index = int(ts.timestamp() // FREQUENCY_WINDOW_SECONDS)
        buckets[key_fn(r)][window_index].append(i)
    return buckets


def _window_start_iso(window_index: int) -> str:
    epoch_seconds = window_index * FREQUENCY_WINDOW_SECONDS
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


class FrequencyDetector:
    name = "frequency"
    enabled_by_default = False

    def __init__(
        self,
        z_threshold: float = FREQUENCY_Z_THRESHOLD,
        abs_threshold: int = FREQUENCY_ABS_CALLS_PER_WINDOW,
    ) -> None:
        self.z_threshold = z_threshold
        self.abs_threshold = abs_threshold

    def analyze(self, records: Sequence[dict]) -> list[Alert]:
        """Runs the per-(label, model) check and the log-wide (global)
        check independently -- a burst can trip either, both, or neither.
        """
        alerts = self._analyze_keyed(records, _group_key)
        alerts += self._analyze_keyed(records, lambda r: GLOBAL_GROUP_KEY)
        return alerts

    def _analyze_keyed(self, records: Sequence[dict], key_fn) -> list[Alert]:
        buckets = _bucket_by_window(records, key_fn)
        alerts: list[Alert] = []

        for group_key, windows in buckets.items():
            call_counts = [len(idxs) for idxs in windows.values()]
            enough_history = len(call_counts) >= MIN_GROUP_SAMPLES
            median = mad = None
            if enough_history:
                median, mad = _median_mad(call_counts)

            for window_index in sorted(windows):
                idxs = windows[window_index]
                n_calls = len(idxs)

                z = None
                is_spike = False
                if enough_history:
                    if mad == 0:
                        is_spike = n_calls > median
                    else:
                        z = 0.6745 * (n_calls - median) / mad
                        is_spike = z > self.z_threshold
                if n_calls >= self.abs_threshold:
                    is_spike = True

                if not is_spike:
                    continue

                alerts.append(
                    Alert(
                        detector=self.name,
                        severity="warning",
                        kind="frequency_spike",
                        group_key=group_key,
                        record_ref=idxs[0],
                        evidence={
                            "window_start": _window_start_iso(window_index),
                            "window_calls": n_calls,
                            "expected_calls": median,
                            "z": z,
                        },
                        message=(
                            f"{n_calls} call(s) in a single "
                            f"{FREQUENCY_WINDOW_SECONDS}s window"
                            + (f" (expected ~{median})" if median is not None else "")
                        ),
                    )
                )

        return alerts
