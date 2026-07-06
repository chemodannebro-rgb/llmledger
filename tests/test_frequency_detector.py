from __future__ import annotations

from datetime import datetime, timedelta, timezone

from llm_burnwatch.anomaly.constants import (
    FREQUENCY_ABS_CALLS_PER_WINDOW,
    FREQUENCY_WINDOW_SECONDS,
    MIN_GROUP_SAMPLES,
)
from llm_burnwatch.detectors.engine import DEFAULT_REGISTRY, run_detectors
from llm_burnwatch.detectors.frequency_detector import FrequencyDetector, GLOBAL_GROUP_KEY

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(label, model, window_offset, second_offset=0):
    ts = BASE + timedelta(seconds=window_offset * FREQUENCY_WINDOW_SECONDS + second_offset)
    return {"label": label, "model": model, "timestamp": ts.isoformat()}


def test_frequency_detector_is_disabled_by_default():
    detector = FrequencyDetector()
    assert detector.name == "frequency"
    assert detector.enabled_by_default is False


def test_frequency_detector_is_registered_but_inert_in_default_registry():
    names = [d.name for d in DEFAULT_REGISTRY]
    assert "frequency" in names

    # A normal, unremarkable log -- default registry (frequency disabled)
    # should produce no frequency_spike alerts at all.
    records = [_record("chat", "gpt-4o", w, s) for w in range(10) for s in range(3)]
    alerts = run_detectors(records)
    assert not any(a.kind == "frequency_spike" for a in alerts)


def test_frequency_detector_flags_spike_window_relative_to_group_history():
    # MIN_GROUP_SAMPLES+ windows of 2 calls each (zero-spread history),
    # followed by one window with a clear burst.
    normal = [
        _record("chat", "gpt-4o", w, s)
        for w in range(MIN_GROUP_SAMPLES + 2)
        for s in range(2)
    ]
    burst_window = MIN_GROUP_SAMPLES + 2
    burst = [_record("chat", "gpt-4o", burst_window, s) for s in range(20)]
    records = normal + burst

    alerts = run_detectors(
        records, registry=[FrequencyDetector()], enabled_overrides={"frequency": True}
    )
    group_alerts = [a for a in alerts if a.group_key == ("chat", "gpt-4o")]

    assert len(group_alerts) == 1
    alert = group_alerts[0]
    assert alert.kind == "frequency_spike"
    assert alert.severity == "warning"
    assert alert.detector == "frequency"
    assert alert.evidence["window_calls"] == 20
    assert alert.evidence["expected_calls"] == 2
    assert alert.record_ref == len(normal)


def test_frequency_detector_does_not_flag_a_quiet_window():
    # A lull (fewer calls than usual) is not a "runaway agent" -- only
    # flag increases, never decreases.
    normal = [
        _record("chat", "gpt-4o", w, s)
        for w in range(MIN_GROUP_SAMPLES + 2)
        for s in range(5)
    ]
    quiet_window = MIN_GROUP_SAMPLES + 2
    quiet = [_record("chat", "gpt-4o", quiet_window, 0)]
    records = normal + quiet

    alerts = run_detectors(
        records, registry=[FrequencyDetector()], enabled_overrides={"frequency": True}
    )
    assert not any(a.group_key == ("chat", "gpt-4o") for a in alerts)


def test_frequency_detector_absolute_fail_safe_fires_without_history():
    # A single window, on the very first records ever seen for this group --
    # no history exists to compute a z-score, but the absolute fail-safe
    # should still catch it. All within the same 60s window (second_offset
    # capped at < FREQUENCY_WINDOW_SECONDS via modulo).
    records = [
        _record("chat", "gpt-4o", 0, s % FREQUENCY_WINDOW_SECONDS)
        for s in range(FREQUENCY_ABS_CALLS_PER_WINDOW)
    ]

    alerts = run_detectors(
        records, registry=[FrequencyDetector()], enabled_overrides={"frequency": True}
    )
    group_alerts = [a for a in alerts if a.group_key == ("chat", "gpt-4o")]

    assert len(group_alerts) == 1
    assert group_alerts[0].evidence["expected_calls"] is None
    assert group_alerts[0].evidence["z"] is None
    assert group_alerts[0].evidence["window_calls"] == FREQUENCY_ABS_CALLS_PER_WINDOW


def test_frequency_detector_global_check_flags_cross_label_burst():
    # No single (label, model) group spikes on its own, but the combined
    # volume across many different groups in one window is a clear burst
    # relative to the log's overall per-window history.
    normal = [
        _record(f"label-{w}", "gpt-4o", w, 0)
        for w in range(MIN_GROUP_SAMPLES + 2)
    ]
    burst_window = MIN_GROUP_SAMPLES + 2
    burst = [
        _record(f"burst-label-{i}", "gpt-4o", burst_window, i)
        for i in range(30)
    ]
    records = normal + burst

    alerts = run_detectors(
        records, registry=[FrequencyDetector()], enabled_overrides={"frequency": True}
    )
    global_alerts = [a for a in alerts if a.group_key == GLOBAL_GROUP_KEY]

    assert len(global_alerts) == 1
    assert global_alerts[0].evidence["window_calls"] == 30
    # None of the individual burst labels have enough history of their own
    # to trigger a per-group alert -- only the global check should fire.
    assert not any(a.group_key not in (GLOBAL_GROUP_KEY,) for a in alerts)


def test_frequency_detector_ignores_records_without_parseable_timestamp():
    records = [{"label": "chat", "model": "gpt-4o", "timestamp": "not-a-timestamp"}]
    alerts = FrequencyDetector().analyze(records)
    assert alerts == []
