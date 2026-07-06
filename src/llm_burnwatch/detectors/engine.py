"""Orchestrates detectors: filters by enabled state, runs each, merges and
sorts the resulting alerts.

`enabled_overrides` lets a caller turn a specific detector on/off for a
single run (e.g. the seasonal-baseline auto-enable planned for the frequency
detector) without touching `enabled_by_default`, which stays each detector's
hard-coded packaged default. The engine, not individual detectors, owns this
decision -- a detector never needs to know why it was or wasn't run.

This also fixes the contract `detect --follow` will rely on: the engine
decides what window of records to pass in, detectors themselves stay
window-agnostic and always analyze whatever sequence they're given.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from .baseline_detector import BaselineDetector
from .cusum_detector import CusumDetector
from .frequency_detector import FrequencyDetector
from .protocol import Alert, Detector

# `FrequencyDetector.enabled_by_default` is `False`, so registering it here
# doesn't change `run_detectors()`'s output for any existing caller -- it
# only becomes reachable via an explicit `enabled_overrides={"frequency":
# True}` (planned to be wired up automatically once seasonal baselines are
# available for a given log). `CusumDetector.enabled_by_default` is `True`,
# but `detect`'s CLI still builds its own explicit registry rather than
# using `DEFAULT_REGISTRY` (see `cli.py`'s `cmd_detect`), so adding it here
# doesn't change `detect`'s current output either -- this registry is for
# future callers (e.g. `detect --follow`) that use it directly.
DEFAULT_REGISTRY: list[Detector] = [
    BaselineDetector(),
    FrequencyDetector(),
    CusumDetector(),
]

# Sort key only -- not a claim that "info" alerts matter less, just a stable,
# predictable order for output (most actionable first within the same call).
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def run_detectors(
    records: Sequence[dict],
    registry: Sequence[Detector] = DEFAULT_REGISTRY,
    *,
    enabled_overrides: Mapping[str, bool] | None = None,
) -> list[Alert]:
    """Run every enabled detector in `registry` over `records` and return
    their merged alerts, sorted by `(record_ref, severity)`.

    A detector is enabled unless `enabled_overrides` (keyed by detector
    `name`) says otherwise; detectors not mentioned in `enabled_overrides`
    fall back to their own `enabled_by_default`.
    """
    overrides = enabled_overrides or {}
    alerts: list[Alert] = []
    for detector in registry:
        if not overrides.get(detector.name, detector.enabled_by_default):
            continue
        alerts.extend(detector.analyze(records))

    alerts.sort(
        key=lambda a: (
            a.record_ref if a.record_ref is not None else float("inf"),
            _SEVERITY_ORDER.get(a.severity, 99),
        )
    )
    return alerts
