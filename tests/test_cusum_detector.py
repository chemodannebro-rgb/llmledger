from __future__ import annotations

from llm_burnwatch.anomaly.constants import MIN_GROUP_SAMPLES
from llm_burnwatch.detectors.cusum_detector import CusumDetector
from llm_burnwatch.detectors.engine import DEFAULT_REGISTRY, run_detectors


def _record(label, model, output_tokens):
    return {"label": label, "model": model, "output_tokens": output_tokens}


def test_cusum_detector_is_enabled_by_default():
    detector = CusumDetector()
    assert detector.name == "cusum"
    assert detector.enabled_by_default is True


def test_cusum_detector_is_registered_in_default_registry():
    names = [d.name for d in DEFAULT_REGISTRY]
    assert "cusum" in names


def test_cusum_detector_flags_sustained_level_shift_not_caught_by_single_call_zscore():
    # Stable history cycling 90/100/110 (median=100, MAD=10).
    pre = ([90, 100, 110] * 7)[:20]
    # From here, output_tokens sustainably rises ~35% (cycling 121/135/148).
    # Each individual value's baseline modified z-score against the
    # *pre-shift-only* stats stays under Z_SCORE_THRESHOLD (3.5):
    #   0.6745 * (148 - 100) / 10 == 3.24
    # so no single call would be flagged as anomalous on its own -- only
    # the cumulative sum of the sustained rise crosses the CUSUM threshold.
    post = ([121, 135, 148] * 5)[:15]
    records = [_record("chat", "gpt-4o", v) for v in pre + post]

    alerts = run_detectors(
        records, registry=[CusumDetector()], enabled_overrides={"cusum": True}
    )
    group_alerts = [a for a in alerts if a.group_key == ("chat", "gpt-4o")]

    assert len(group_alerts) == 1
    alert = group_alerts[0]
    assert alert.kind == "level_shift"
    assert alert.detector == "cusum"
    assert alert.evidence["feature"] == "output_tokens"
    assert alert.evidence["shift_started_at_record"] == len(pre)
    # Flagged within a bounded number of records after the shift starts,
    # not immediately and not only after exhausting the whole post-shift run.
    assert len(pre) < alert.record_ref < len(records)


def test_cusum_detector_does_not_flag_stable_data():
    records = [
        _record("chat", "gpt-4o", v) for v in ([90, 100, 110] * 20)[:50]
    ]
    alerts = run_detectors(
        records, registry=[CusumDetector()], enabled_overrides={"cusum": True}
    )
    assert alerts == []


def test_cusum_detector_does_not_flag_a_drop():
    # A sustained *decrease* is not a cost-risk "level shift" worth
    # alerting on -- only sustained rises are flagged.
    pre = ([90, 100, 110] * 7)[:20]
    drop = ([59, 65, 72] * 5)[:15]
    records = [_record("chat", "gpt-4o", v) for v in pre + drop]

    alerts = run_detectors(
        records, registry=[CusumDetector()], enabled_overrides={"cusum": True}
    )
    assert not any(a.group_key == ("chat", "gpt-4o") for a in alerts)


def test_cusum_detector_skips_groups_with_insufficient_history():
    records = [_record("chat", "gpt-4o", v) for v in [90, 100, 110]]
    assert len(records) < MIN_GROUP_SAMPLES

    alerts = CusumDetector().analyze(records)
    assert alerts == []


def test_cusum_detector_skips_zero_spread_groups():
    records = [_record("chat", "gpt-4o", 100) for _ in range(MIN_GROUP_SAMPLES + 5)]
    alerts = CusumDetector().analyze(records)
    assert alerts == []
