"""Frequency (runaway-agent) detector: flags time windows whose call count
is anomalously high relative to that group's own history of window counts.

Ships **disabled by default** (`enabled_by_default = False`, decided up
front rather than added as an afterthought) at the package level -- it's
`cmd_detect` that decides whether to actually run it for a given log, based
on whether that log has enough calendar span for a seasonal comparison (see
`anomaly.seasonal.has_seasonal_coverage`); `FrequencyDetector` itself never
flips its own default.

Independently of whether the detector runs at all, `analyze()` checks that
same span condition itself and, if it holds, additionally compares each
window against its own (weekday, hour) bucket's history rather than only
the group's flat, pooled history: a window's bucket history is built only
from *other calendar dates* that share its (weekday, hour) -- a window is
never allowed to use itself, or other windows from the same calendar
date/hour, as its own baseline, which would otherwise make a first-ever
hour-long burst invisible to itself (it would "learn" its own burst as
normal in the same instant it happens). A bucket that doesn't yet have
`MIN_GROUP_SAMPLES` worth of history from other dates falls back to the
flat, group-wide comparison from v0.8.1. This is what lets a routine
"every Monday morning" burst stop being flagged once it has recurred often
enough to become the expected pattern for that specific time slot, while a
burst that's still new -- or one that's unusually large even for a
normally-busy Monday morning -- is still caught.

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
from ..anomaly.seasonal import has_seasonal_coverage
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


def _window_start_dt(window_index: int) -> datetime:
    epoch_seconds = window_index * FREQUENCY_WINDOW_SECONDS
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


def _window_start_iso(window_index: int) -> str:
    return _window_start_dt(window_index).isoformat()


def _seasonal_bucket(window_index: int) -> tuple:
    dt = _window_start_dt(window_index)
    return (dt.weekday(), dt.hour)


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
        seasonal = has_seasonal_coverage(records)
        alerts = self._analyze_keyed(records, _group_key, seasonal)
        alerts += self._analyze_keyed(records, lambda r: GLOBAL_GROUP_KEY, seasonal)
        return alerts

    def _analyze_keyed(self, records: Sequence[dict], key_fn, seasonal: bool) -> list[Alert]:
        buckets = _bucket_by_window(records, key_fn)
        alerts: list[Alert] = []
        for group_key, windows in buckets.items():
            alerts += self._analyze_group(group_key, windows, seasonal)
        return alerts

    def _analyze_group(self, group_key: tuple, windows: dict, seasonal: bool) -> list[Alert]:
        call_counts = [len(idxs) for idxs in windows.values()]
        enough_flat_history = len(call_counts) >= MIN_GROUP_SAMPLES
        flat_median = flat_mad = None
        if enough_flat_history:
            flat_median, flat_mad = _median_mad(call_counts)

        # {(weekday, hour): {calendar_date: [window_calls, ...]}} -- lets a
        # window's seasonal history exclude windows from its own calendar
        # date (see class docstring for why that exclusion matters).
        by_seasonal_bucket: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
        if seasonal:
            for window_index in windows:
                bucket = _seasonal_bucket(window_index)
                date = _window_start_dt(window_index).date()
                by_seasonal_bucket[bucket][date].append(len(windows[window_index]))

        alerts = []
        for window_index in sorted(windows):
            idxs = windows[window_index]
            n_calls = len(idxs)

            median, mad = flat_median, flat_mad
            if seasonal:
                bucket = _seasonal_bucket(window_index)
                date = _window_start_dt(window_index).date()
                other_counts = [
                    c
                    for d, counts in by_seasonal_bucket[bucket].items()
                    if d != date
                    for c in counts
                ]
                if len(other_counts) >= MIN_GROUP_SAMPLES:
                    median, mad = _median_mad(other_counts)
                # else: not enough same-bucket history from other calendar
                # dates yet -- keep the flat fallback assigned above.

            z = None
            is_spike = False
            if median is not None:
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
                    # The window's *last* record, not its first: `detect
                    # --follow` (see `cli._detect_follow_poll`) decides
                    # whether an alert is "new this poll" by comparing
                    # `record_ref` against the index newly arrived records
                    # start at. Pointing at the window's first call would
                    # make that comparison see an old, already-surfaced
                    # record even when the spike itself was only confirmed
                    # by calls that arrived just now -- silently dropping a
                    # genuinely new detection.
                    record_ref=idxs[-1],
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
