from __future__ import annotations

import json
import shutil
import socket
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from llm_burnwatch.anomaly.constants import Z_SCORE_THRESHOLD
from llm_burnwatch.cli import (
    DISCLAIMER,
    _detector_status_lines,
    _filter_report_records,
    _format_baseline_score_for_console,
    _format_cusum_for_console,
    _format_frequency_for_console,
    _incident_type_label,
    main,
)
from llm_burnwatch.detectors.protocol import Alert
from llm_burnwatch.demo_data import model_swap, prompt_regression, runaway_loop, write_demo_log
from llm_burnwatch.tracker import CostTracker


def _demo_log(tmp_path, **kwargs):
    log_path = tmp_path / "demo.jsonl"
    write_demo_log(log_path, **kwargs)
    return log_path


def _scenario_log(tmp_path, results, name="calls.jsonl"):
    log_path = tmp_path / name
    with log_path.open("w", encoding="utf-8") as fh:
        for record, _label in results:
            fh.write(json.dumps(record) + "\n")
    return log_path


def test_schema_command_prints_valid_json_matching_packaged_schema(capsys):
    exit_code = main(["schema"])
    captured = capsys.readouterr()
    schema = json.loads(captured.out)

    assert exit_code == 0
    assert schema["title"] == "llm-burnwatch JSONL log record"


def test_validate_command_on_valid_demo_log_reports_all_valid(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["validate", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "validated 5 record(s)" in captured.out
    assert "all records valid" in captured.out


def test_validate_command_reports_invalid_records_and_exits_1(tmp_path, capsys):
    log_path = tmp_path / "bad.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "label": "",
                    "model": "gpt-4o",
                    "input_tokens": -5,
                    "output_tokens": 10,
                    "cost_micros": 100,
                }
            )
            + "\n"
        )

    exit_code = main(["validate", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "below minLength" in captured.out
    assert "below minimum" in captured.out


def test_validate_command_json_flag_prints_machine_readable_summary(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=3, n_anomalies=0)

    exit_code = main(["validate", "--log-file", str(log_path), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["record_count"] == 3
    assert payload["invalid_count"] == 0
    assert payload["invalid"] == []


def test_validate_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["validate", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def test_validate_command_without_log_file_or_alerts_returns_exit_code_2(capsys):
    exit_code = main(["validate"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--log-file is required unless --alerts is given" in captured.err


def test_validate_alerts_on_valid_alert_json_reports_valid(tmp_path, capsys):
    alerts_path = tmp_path / "alert.json"
    alerts_path.write_text(
        json.dumps(
            {
                "alert_schema_version": 1,
                "call_count": 0,
                "anomaly_count": 0,
                "anomalies": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["validate", "--alerts", "--alerts-file", str(alerts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"validated {alerts_path} against alert_schema.json" in captured.out
    assert "valid" in captured.out


def test_validate_alerts_on_invalid_alert_json_reports_errors_and_exits_1(tmp_path, capsys):
    alerts_path = tmp_path / "alert.json"
    # Missing the required "anomalies" field.
    alerts_path.write_text(
        json.dumps({"alert_schema_version": 1, "call_count": 0, "anomaly_count": 0}),
        encoding="utf-8",
    )

    exit_code = main(["validate", "--alerts", "--alerts-file", str(alerts_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "missing required field 'anomalies'" in captured.out


def test_validate_alerts_json_flag_prints_machine_readable_summary(tmp_path, capsys):
    alerts_path = tmp_path / "alert.json"
    alerts_path.write_text(
        json.dumps(
            {
                "alert_schema_version": 1,
                "call_count": 0,
                "anomaly_count": 0,
                "anomalies": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        ["validate", "--alerts", "--alerts-file", str(alerts_path), "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload == {"valid": True, "errors": []}


def test_validate_alerts_missing_alerts_file_flag_returns_exit_code_2(capsys):
    exit_code = main(["validate", "--alerts"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--alerts-file is required with --alerts" in captured.err


def test_validate_alerts_missing_file_on_disk_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.json"
    exit_code = main(["validate", "--alerts", "--alerts-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def test_validate_alerts_dogfoods_real_detect_json_output(tmp_path, capsys):
    """The dogfooding test the QA review called for: a real `detect --json`
    output, round-tripped through `validate --alerts`, must itself validate
    as `valid` -- proving alert_schema.json actually matches what `detect`
    produces, rather than trusting a hand-written fixture to stay in sync.
    """
    log_path = _demo_log(tmp_path, n_normal=20, n_anomalies=2)

    detect_exit = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json"]
    )
    alert_json = capsys.readouterr().out
    assert detect_exit in (0, 1)  # 1 just means anomalies were found, not a failure

    alerts_path = tmp_path / "alert.json"
    alerts_path.write_text(alert_json, encoding="utf-8")

    exit_code = main(["validate", "--alerts", "--alerts-file", str(alerts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "valid" in captured.out


def test_demo_data_command_writes_log_and_reports_count(tmp_path, capsys):
    out_path = tmp_path / "out.jsonl"
    exit_code = main(["demo-data", "--out", str(out_path), "--n-normal", "5", "--n-anomalies", "1"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "wrote 6 demo call(s)" in captured.out
    assert out_path.exists()


def test_report_command_on_empty_log_says_no_records(tmp_path, capsys):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    # E1: a genuinely empty log (0 records ever) gets onboarding steps
    # instead of a dead-end "no records found" message.
    assert exit_code == 0
    assert "has no records yet" in captured.out
    assert "Log your first call" in captured.out
    assert "no records found" not in captured.out


def test_report_command_prints_disclaimer_and_totals(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert DISCLAIMER in captured.out
    assert "calls: 5" in captured.out
    assert "total cost:" in captured.out


def test_report_command_prints_rub_conversion_when_rate_given(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--rub-rate", "90"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "total cost: $" in captured.out
    assert "~₽" in captured.out
    assert "90.00 ₽/$" in captured.out


def test_report_command_without_rub_rate_omits_rub_conversion(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "₽" not in captured.out


def test_report_rub_rate_rejects_non_positive_value(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    with pytest.raises(SystemExit) as exc_info:
        main(["report", "--log-file", str(log_path), "--rub-rate", "-5"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "must be a positive number" in captured.err


def test_report_empty_log_with_rub_rate_shows_no_records(tmp_path, capsys):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["report", "--log-file", str(log_path), "--rub-rate", "90"])
    captured = capsys.readouterr()

    # E1: --rub-rate doesn't change the fact that this log is genuinely
    # empty -- still onboarding, not "no records found".
    assert exit_code == 0
    assert "has no records yet" in captured.out
    assert "no records found" not in captured.out
    assert "₽" not in captured.out


def test_report_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["report", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def _write_dated_records(log_path, dates):
    with log_path.open("w", encoding="utf-8") as fh:
        for i, d in enumerate(dates):
            fh.write(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "label": "x",
                        "model": "gpt-4o",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": 0,
                        "cost_micros": 100 * (i + 1),
                        "timestamp": f"{d}T00:00:00+00:00",
                    }
                )
                + "\n"
            )


def test_report_since_until_filters_records(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01", "2026-01-15", "2026-02-01"])

    exit_code = main(
        ["report", "--log-file", str(log_path), "--since", "2026-01-01", "--until", "2026-01-31"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "calls: 2" in captured.out


def test_report_since_until_excludes_all_records_shows_period_message_not_onboarding(
    tmp_path, capsys
):
    # E1's onboarding only replaces the message for a genuinely empty log
    # (0 records ever) -- a log that *has* data, just none in the requested
    # --since/--until window, still gets the older "given period" message,
    # not onboarding (there's nothing to onboard: the user already has data,
    # they just filtered it all out).
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01"])

    exit_code = main(
        ["report", "--log-file", str(log_path), "--since", "2027-01-01", "--until", "2027-01-31"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no records found in the given period" in captured.out
    assert "has no records yet" not in captured.out


def test_report_since_until_rejects_bad_date_format(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01"])

    with pytest.raises(SystemExit) as exc_info:
        main(["report", "--log-file", str(log_path), "--since", "01/01/2026"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "must be YYYY-MM-DD" in captured.err


def test_report_defaults_to_last_30_days_excluding_older_records(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    today = datetime.now(timezone.utc).date()
    old_date = (today - timedelta(days=60)).isoformat()
    recent_date = (today - timedelta(days=5)).isoformat()
    _write_dated_records(log_path, [old_date, recent_date])

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "calls: 1" in captured.out


def test_report_all_time_flag_includes_records_older_than_30_days(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    today = datetime.now(timezone.utc).date()
    old_date = (today - timedelta(days=60)).isoformat()
    recent_date = (today - timedelta(days=5)).isoformat()
    _write_dated_records(log_path, [old_date, recent_date])

    exit_code = main(["report", "--log-file", str(log_path), "--all-time"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "calls: 2" in captured.out


def test_report_all_time_rejects_combination_with_since(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01"])

    exit_code = main(
        ["report", "--log-file", str(log_path), "--all-time", "--since", "2026-01-01"]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--all-time cannot be combined with --since/--until" in captured.err


def test_report_explicit_since_overrides_default_30_day_window(tmp_path, capsys):
    """An explicit --since further in the past than 30 days must still work
    (the default only applies when neither --since nor --until is given)."""
    log_path = tmp_path / "dated.jsonl"
    today = datetime.now(timezone.utc).date()
    old_date = (today - timedelta(days=60)).isoformat()
    _write_dated_records(log_path, [old_date])

    exit_code = main(["report", "--log-file", str(log_path), "--since", old_date])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "calls: 1" in captured.out


def test_report_json_period_reflects_default_30_day_window(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["period"]["all_time"] is False
    assert payload["period"]["until"] is None
    expected_since = (
        datetime.now(timezone.utc).date() - timedelta(days=30)
    ).isoformat()
    assert payload["period"]["since"] == expected_since


def test_report_json_flag_prints_machine_readable_summary(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["call_count"] == 5
    assert payload["total_cost_micros"] > 0
    assert "by_label_micros" in payload
    assert "by_model_micros" in payload


def test_report_json_flag_on_empty_log_prints_zero_summary(tmp_path, capsys):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["call_count"] == 0
    assert payload["total_cost_micros"] == 0


def test_report_json_flag_includes_rub_conversion_when_rate_given(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--json", "--rub-rate", "90"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["rub_rate"] == 90
    assert payload["total_cost_rub"] == pytest.approx(payload["total_cost_usd"] * 90)


def test_report_csv_format_prints_total_label_model_rows(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--format", "csv"])
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()

    assert exit_code == 0
    assert lines[0] == "dimension,key,cost_usd"
    assert lines[1].startswith("total,,")
    assert any(line.startswith("label,") for line in lines[1:])
    assert any(line.startswith("model,") for line in lines[1:])
    assert DISCLAIMER not in captured.out


def test_report_csv_format_on_empty_log_prints_zero_total_only(tmp_path, capsys):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["report", "--log-file", str(log_path), "--format", "csv"])
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()

    assert exit_code == 0
    assert lines == ["dimension,key,cost_usd", "total,,0.000000"]


def test_report_csv_format_rejects_json_flag_combo(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(
        ["report", "--log-file", str(log_path), "--format", "csv", "--json"]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "mutually exclusive" in captured.err


def test_report_trace_id_filters_to_matching_records(tmp_path, capsys):
    log_path = tmp_path / "traced.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        for trace_id, cost in [("req-1", 100), ("req-2", 200), ("req-1", 300)]:
            fh.write(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "label": "x",
                        "model": "gpt-4o",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": 0,
                        "cost_micros": cost,
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "trace_id": trace_id,
                    }
                )
                + "\n"
            )

    # --all-time: this test is about --trace-id filtering, not the default
    # 30-day period, and its records use a fixed (now old) calendar date.
    exit_code = main(["report", "--log-file", str(log_path), "--trace-id", "req-1", "--all-time"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "calls: 2" in captured.out
    assert "total cost: $0.000400" in captured.out


def test_report_trace_id_no_match_prints_message(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["report", "--log-file", str(log_path), "--trace-id", "does-not-exist"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no records found for trace_id" in captured.out


def test_filter_report_records_consumes_a_plain_generator_without_materializing(tmp_path):
    # `_filter_report_records` (and therefore `cmd_report`) must work directly
    # off a one-shot generator -- no `list(...)`/indexing/`len()` on the raw
    # records -- so a full log is never held in memory at once (issue #22).
    def records_gen():
        yield {"cost_micros": 1, "label": "a", "model": "m", "timestamp": "2026-01-01T00:00:00+00:00"}
        yield {"cost_micros": 2, "label": "b", "model": "m", "timestamp": "2026-01-02T00:00:00+00:00"}

    args = SimpleNamespace(since=None, until=None, trace_id=None)
    counts = {"total": 0, "dropped_period": 0}

    result = list(_filter_report_records(records_gen(), args, counts))

    assert len(result) == 2
    assert counts["total"] == 2
    assert counts["dropped_period"] == 0


def test_dashboard_since_until_filters_records(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01", "2026-01-15", "2026-02-01"])
    out_path = tmp_path / "dash.html"

    exit_code = main(
        [
            "dashboard",
            "--log-file",
            str(log_path),
            "--out",
            str(out_path),
            "--since",
            "2026-01-01",
            "--until",
            "2026-01-31",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    html = out_path.read_text(encoding="utf-8")
    assert "Period: 2026-01-01" in html
    assert "2026-01-31" in html
    assert "2026-02-01" not in html


def test_detect_command_returns_exit_code_1_when_anomalies_found(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    exit_code = main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "anomalies found" not in captured.out  # only printed when there are none
    assert DISCLAIMER in captured.out


def test_detect_command_returns_exit_code_0_when_no_clear_anomalies(tmp_path, capsys):
    # A handful of identical, well-populated groups with no injected outliers.
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 10,
            "cost_micros": 100,
        }
        for _ in range(20)
    ]
    log_path = tmp_path / "flat.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps({**r, "schema_version": "1.0", "timestamp": "2026-01-01T00:00:00+00:00", "cached_input_tokens": 0}) + "\n")

    exit_code = main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no anomalies found" in captured.out


def test_detect_command_json_output_is_valid_json_with_expected_keys(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    exit_code = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["alert_schema_version"] == 1
    assert payload["call_count"] == 210
    assert payload["anomaly_count"] >= 10
    assert "anomalies" in payload
    assert payload["cusum_detector_enabled"] is True
    assert "level_shift_count" in payload
    assert "level_shifts" in payload
    assert payload["ml"] is None  # no trained model yet


def test_detect_command_flags_cusum_level_shift(tmp_path, capsys):
    # Regression test: CusumDetector was fully implemented (v0.8.2,
    # enabled_by_default=True) but `detect`'s CLI built its own explicit
    # registry that never included it, so the milestone's flagship scenario
    # -- a prompt change that quietly makes every response pricier, with no
    # single call crossing the baseline z-score threshold on its own -- was
    # invisible through the CLI even though a direct `run_detectors()` call
    # on the same records correctly caught it. `--cusum-detector` defaults
    # to "on", matching `CusumDetector.enabled_by_default`.
    log_path = _scenario_log(tmp_path, prompt_regression())

    exit_code = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["cusum_detector_enabled"] is True
    assert payload["level_shift_count"] > 0
    assert any(
        ls["evidence"]["feature"] == "output_tokens" for ls in payload["level_shifts"]
    ), "expected the abrupt output_tokens level shift to be reported via --json"


def test_detect_command_cusum_detector_off_flag_disables_it(tmp_path, capsys):
    log_path = _scenario_log(tmp_path, prompt_regression())

    main(
        [
            "detect",
            "--log-file",
            str(log_path),
            "--model-dir",
            str(tmp_path / "models"),
            "--json",
            "--cusum-detector",
            "off",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["cusum_detector_enabled"] is False
    assert payload["level_shift_count"] == 0
    assert payload["level_shifts"] == []


def test_format_baseline_score_for_console_renders_cost_as_dollars_not_micros():
    # B1: cost_micros must speak in money, never raw micros -- the one
    # feature (of the four baseline FEATURES) that has a currency at all.
    score = {
        "feature": "cost_micros",
        "value": 91632.0,
        "median": 4785.0,
        "mad": 100.0,
        "z_score": 12.3,
        "is_extreme": False,
    }
    rendered = _format_baseline_score_for_console(score)
    assert "$0.0916" in rendered
    assert "$0.0048" in rendered
    assert "91632" not in rendered
    assert "z=" not in rendered
    assert "MAD=" not in rendered


def test_format_baseline_score_for_console_non_cost_feature_has_no_dollar_sign():
    score = {
        "feature": "output_tokens",
        "value": 2571.0,
        "median": 156.0,
        "mad": 10.0,
        "z_score": 16.5,
        "is_extreme": False,
    }
    rendered = _format_baseline_score_for_console(score)
    assert "$" not in rendered
    assert "response length" in rendered
    assert "higher than usual" in rendered


def test_format_baseline_score_for_console_extreme_zero_spread_case():
    score = {
        "feature": "input_tokens",
        "value": 500.0,
        "median": 100.0,
        "mad": 0.0,
        "z_score": None,
        "is_extreme": True,
    }
    rendered = _format_baseline_score_for_console(score)
    assert "z=" not in rendered
    assert "MAD" not in rendered
    assert "exactly" in rendered


def test_format_cusum_for_console_renders_cost_feature_as_dollars():
    evidence = {
        "feature": "cost_micros",
        "cusum_value": 500.0,
        "reference_median": 4785.0,
        "h_threshold": 300.0,
        "shift_started_at_record": 3,
    }
    rendered = _format_cusum_for_console(evidence)
    assert "$0.0048" in rendered
    assert "cusum=" not in rendered
    assert "threshold=" not in rendered
    assert "record 3" in rendered


def test_format_frequency_for_console_mentions_expected_baseline():
    evidence = {"window_start": "2026-01-01T00:00:00+00:00", "window_calls": 40, "expected_calls": 5.0}
    rendered = _format_frequency_for_console(evidence)
    assert "40 calls" in rendered
    assert "normally about 5" in rendered


def test_detect_command_console_output_uses_money_language_and_next_step_hint(tmp_path, capsys):
    log_path = _scenario_log(tmp_path, prompt_regression())

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    # B2: the console surfaces the plain-language incident type
    # ("gradual cost increase"), not the raw detector/kind jargon
    # ("cusum"/"level_shift") -- those remain available via --json.
    assert "gradual cost increase(s) found" in captured.out
    level_shift_section = captured.out.split("gradual cost increase(s) found:")[1]
    # No raw jargon from the pre-B1 message format leaking into the console.
    assert "cusum=" not in level_shift_section
    assert "h_threshold=" not in level_shift_section
    # A concrete next step is always shown alongside the finding.
    assert "-> run `llm-burnwatch report --json`" in captured.out


def test_incident_type_label_translates_known_detector_kind_pairs():
    # B2: a fixed vocabulary of (detector, kind) -> plain incident type,
    # never the raw snake_case `Alert.kind` a `--json` consumer sees.
    cases = [
        (("baseline", "zscore_outlier"), "cost/usage spike"),
        (("cusum", "level_shift"), "gradual cost increase"),
        (("frequency", "frequency_spike"), "unusually frequent calls"),
        (("rules", "model_not_allowed"), "rule violated: model not allowed"),
        (("rules", "call_cost_exceeded"), "rule violated: call cost limit exceeded"),
        (("rules", "trace_cost_exceeded"), "rule violated: trace cost limit exceeded"),
        (("budget", "budget_exceeded"), "budget exceeded"),
        (("budget", "budget_pace_warning"), "budget pace warning"),
    ]
    for (detector, kind), expected_label in cases:
        a = Alert(
            detector=detector,
            severity="warning",
            kind=kind,
            group_key="g",
            record_ref=0,
            evidence={},
            message="irrelevant for this test",
        )
        assert _incident_type_label(a) == expected_label


def test_incident_type_label_falls_back_to_raw_kind_for_unknown_pair():
    # A future detector/kind not yet in the vocabulary must never crash the
    # console renderer -- it just prints un-translated, same as today.
    a = Alert(
        detector="future_detector",
        severity="warning",
        kind="some_new_kind",
        group_key="g",
        record_ref=0,
        evidence={},
        message="irrelevant for this test",
    )
    assert _incident_type_label(a) == "some_new_kind"


def test_detect_command_baseline_section_uses_incident_type_header(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    write_demo_log(log_path, n_normal=60, n_anomalies=5, seed=42)

    exit_code = main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 1
    # B2: baseline anomalies get the same "N <incident type>(s) found:"
    # header shape as the other three sections, instead of jumping
    # straight into the per-record listing with no summary line.
    assert "cost/usage spike(s) found:" in captured.out


def test_detect_command_rule_violation_line_uses_incident_type_not_raw_kind(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    record = {
        "schema_version": "1.0",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "label": "x",
        "model": "not-allowed-model",
        "input_tokens": 10,
        "output_tokens": 10,
        "cost_micros": 0,
    }
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    exit_code = main(
        [
            "detect",
            "--log-file",
            str(log_path),
            "--allowed-models",
            "some-other-model",
            "--model-dir",
            str(tmp_path / "models"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "rule violation(s) found" in captured.out
    assert "rule violated: model not allowed" in captured.out
    assert "model_not_allowed" not in captured.out


# B3: a permanent regression guard against internal statistics/algorithm
# jargon and raw snake_case Alert.detector/Alert.kind values leaking into
# `detect`'s plain-text console branch. `--json` is exempt on purpose --
# these terms remain the correct, unchanged vocabulary for machine-readable
# output (see the B1/B2 status notes in the plan for why `Alert.message`/
# `evidence`/`--json` keys are a frozen contract, not covered by this test).
_BANNED_CONSOLE_JARGON = (
    "z=",
    "MAD",
    "cusum=",
    "quantile",
    "micros",
    "zscore_outlier",
    "level_shift",
    "frequency_spike",
    "budget_exceeded",
    "budget_pace_warning",
    "model_not_allowed",
    "call_cost_exceeded",
    "trace_cost_exceeded",
)


def _assert_no_banned_jargon(text):
    for term in _BANNED_CONSOLE_JARGON:
        assert term not in text, f"banned jargon {term!r} leaked into console output:\n{text}"


def test_detect_console_output_never_leaks_internal_jargon_baseline(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=60, n_anomalies=5, seed=42)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    _assert_no_banned_jargon(capsys.readouterr().out)


def test_detect_console_output_never_leaks_internal_jargon_cusum(tmp_path, capsys):
    log_path = _scenario_log(tmp_path, prompt_regression())

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    _assert_no_banned_jargon(capsys.readouterr().out)


def test_detect_console_output_never_leaks_internal_jargon_frequency(tmp_path, capsys):
    log_path = _scenario_log(tmp_path, runaway_loop())

    main(
        [
            "detect",
            "--log-file",
            str(log_path),
            "--model-dir",
            str(tmp_path / "models"),
            "--frequency-detector",
            "on",
        ]
    )
    _assert_no_banned_jargon(capsys.readouterr().out)


def test_detect_console_output_never_leaks_internal_jargon_rules(tmp_path, capsys):
    log_path = _scenario_log(tmp_path, model_swap())

    main(
        [
            "detect",
            "--log-file",
            str(log_path),
            "--model-dir",
            str(tmp_path / "models"),
            "--allowed-models",
            "gpt-4o-mini",
        ]
    )
    _assert_no_banned_jargon(capsys.readouterr().out)


def test_detect_console_output_never_leaks_internal_jargon_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    main(["budget", "set", "--monthly", "0.5", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    record = {
        "schema_version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": "x",
        "model": "gpt-4o",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_micros": 1_000_000,
    }
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    _assert_no_banned_jargon(capsys.readouterr().out)


def test_detector_status_lines_reports_learning_state_for_short_log(tmp_path):
    # Fewer than MIN_SEASONAL_SPAN_DAYS (14) calendar days -- frequency's
    # "auto" mode can't yet attempt seasonal bucketing, which is a distinct
    # state from a deliberate "off": the user hasn't turned anything off,
    # the detector just doesn't have enough history yet.
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)
    records = list(json.loads(line) for line in log_path.read_text().splitlines())

    lines = _detector_status_lines(records, budget_config=None)
    by_name = {name: (state, message) for name, state, message in lines}

    assert by_name["frequency"][0] == "learning"
    assert "insufficient data" in by_name["frequency"][1]
    assert by_name["cusum"][0] == "on"
    assert by_name["budget"][0] == "off"
    assert "budget set" in by_name["budget"][1]


def test_detector_status_lines_frequency_off_flag_is_off_not_learning(tmp_path):
    # Explicitly turning frequency off is a different state from "learning"
    # even though both mean "not currently running" -- an explicit --off
    # should never be reported as if the detector were still waiting on data.
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)
    records = list(json.loads(line) for line in log_path.read_text().splitlines())

    lines = _detector_status_lines(records, budget_config=None, frequency_detector="off")
    by_name = {name: state for name, state, _message in lines}

    assert by_name["frequency"] == "off"


def test_detector_status_lines_cusum_off_flag_reports_off_state():
    lines = _detector_status_lines([], budget_config=None, cusum_detector="off")
    by_name = {name: (state, message) for name, state, message in lines}

    assert by_name["cusum"][0] == "off"
    assert "off" in by_name["cusum"][1]


def test_detector_status_lines_budget_configured_reports_on_state_with_amount():
    lines = _detector_status_lines(
        [], budget_config={"monthly_usd": 50.0, "warn_at_fraction": 0.8}
    )
    by_name = {name: (state, message) for name, state, message in lines}

    assert by_name["budget"][0] == "on"
    assert "50.00" in by_name["budget"][1]


def test_detect_json_anomaly_features_include_human_readable_reason(tmp_path, capsys):
    # BACKLOG.md #2: the z-score/median/MAD breakdown was already printed in
    # human-readable form via `format_score()` in the non-JSON output, but
    # wasn't exposed to JSON consumers -- they had to recompute the same
    # explanation from the raw numbers themselves.
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    exit_code = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["anomalies"], "expected at least one anomaly in the demo log"
    features = payload["anomalies"][0]["features"]
    assert features, "expected the first anomaly to have at least one flagged feature"
    for f in features:
        assert "reason" in f
        assert f["feature"] in f["reason"]


def test_detect_command_threshold_override_changes_results(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json", "--threshold", "3.5"])
    lenient_payload = json.loads(capsys.readouterr().out)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json", "--threshold", "1000"])
    strict_payload = json.loads(capsys.readouterr().out)

    assert strict_payload["anomaly_count"] < lenient_payload["anomaly_count"]


def test_detect_command_sensitivity_high_flags_more_than_low(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json", "--sensitivity", "low"])
    low_payload = json.loads(capsys.readouterr().out)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json", "--sensitivity", "high"])
    high_payload = json.loads(capsys.readouterr().out)

    assert low_payload["anomaly_count"] <= high_payload["anomaly_count"]
    assert low_payload["sensitivity"] == "low"
    assert high_payload["sensitivity"] == "high"
    assert low_payload["threshold"] > high_payload["threshold"]


def test_detect_command_default_sensitivity_matches_pre_sensitivity_behavior(tmp_path, capsys):
    """No --sensitivity/--threshold given: effective threshold must be exactly
    Z_SCORE_THRESHOLD, matching `detect`'s output before --sensitivity existed
    (backward compatibility)."""
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["sensitivity"] == "normal"
    assert payload["threshold"] == Z_SCORE_THRESHOLD


def test_detect_command_threshold_and_sensitivity_are_mutually_exclusive(tmp_path, capsys):
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "detect",
                "--log-file",
                str(log_path),
                "--threshold",
                "3.5",
                "--sensitivity",
                "high",
            ]
        )
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "not allowed with" in captured.err


def test_detect_command_threshold_override_keeps_sensitivity_normal_for_other_detectors(tmp_path, capsys):
    """Using the advanced --threshold escape hatch should not silently affect
    frequency/cusum -- sensitivity stays "normal" (multiplier 1.0) for them."""
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models"), "--json", "--threshold", "1000"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["sensitivity"] == "normal"
    assert payload["threshold"] == 1000.0


def test_detect_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["detect", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def test_status_command_reports_learning_and_off_states_for_fresh_log(tmp_path, monkeypatch, capsys):
    # No budget.json in this throwaway $XDG_CONFIG_HOME -- budget must read
    # as "off", not pick up a real developer's budget.json.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["status", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "analyzed 5 call(s)" in captured.out
    assert "frequency: LEARNING" in captured.out
    assert "cusum" in captured.out and "ON" in captured.out
    assert "budget" in captured.out and "OFF" in captured.out


def test_status_command_json_matches_detector_status_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["status", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["call_count"] == 5
    by_name = {d["name"]: d["state"] for d in payload["detectors"]}
    assert by_name == {"frequency": "learning", "cusum": "on", "budget": "off"}


def test_status_command_reflects_configured_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    budget_exit = main(["budget", "set", "--monthly", "50", "--warn-at", "0.8"])
    assert budget_exit == 0
    capsys.readouterr()

    exit_code = main(["status", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    by_name = {d["name"]: d for d in payload["detectors"]}
    assert by_name["budget"]["state"] == "on"
    assert "50.00" in by_name["budget"]["message"]


def test_status_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["status", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def test_status_command_on_empty_log_prints_onboarding(tmp_path, monkeypatch, capsys):
    # E1: an existing-but-empty log gets the same onboarding steps as
    # `report`, not a "analyzed 0 call(s)" + meaningless detector states.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["status", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "has no records yet" in captured.out
    assert "Log your first call" in captured.out
    assert "analyzed 0 call(s)" not in captured.out


def test_status_command_json_on_empty_log_is_unaffected_by_onboarding(tmp_path, monkeypatch, capsys):
    # `--json` is a machine contract -- E1's onboarding text is console-only.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["status", "--log-file", str(log_path), "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["call_count"] == 0
    assert len(payload["detectors"]) == 3


def test_train_command_empty_log_returns_exit_code_2(tmp_path, capsys):
    pytest.importorskip("sklearn")
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err


def test_train_then_detect_uses_trained_model(tmp_path, capsys):
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"

    train_exit = main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    train_out = capsys.readouterr().out
    assert train_exit == 0
    assert "trained model saved to" in train_out
    assert (model_dir / "v1" / "model.skops").exists()

    detect_exit = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(model_dir), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert detect_exit == 1
    assert payload["ml"]["available"] is True


def test_train_command_prints_held_out_eval_metric(tmp_path, capsys):
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    captured_out = capsys.readouterr().out

    assert exit_code == 0
    assert "held-out eval:" in captured_out
    assert "held-out example(s) flagged anomalous" in captured_out


def test_train_command_reports_holdout_skipped_on_tiny_log(tmp_path, capsys):
    pytest.importorskip("sklearn")
    # All calls share one (label, model) pair so the single group clears
    # MIN_GROUP_SAMPLES, but the total stays well under
    # EVAL_HOLDOUT_MIN_EXAMPLES -- unlike `_demo_log`, which spreads calls
    # randomly across 5 fixed pairs and so can't guarantee that for a small
    # n_normal.
    log_path = tmp_path / "tiny.jsonl"
    tracker = CostTracker(log_path)
    for _ in range(6):
        tracker.log_call(
            label="only-label",
            model="only-model",
            input_tokens=800,
            output_tokens=150,
            cost=0.01,
        )
    model_dir = tmp_path / "models"

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    captured_out = capsys.readouterr().out

    assert exit_code == 0
    assert "held-out eval skipped:" in captured_out


def test_detect_retries_ml_cross_check_when_latest_version_pruned_mid_resolve(
    tmp_path, capsys, monkeypatch
):
    # Simulates the race from BACKLOG.md #21: `latest_version_dir()` resolves
    # a version, then a concurrent `train()` prunes it before `load_model()`
    # runs. `_run_ml_cross_check` should re-resolve and retry instead of just
    # reporting the ML cross-check unavailable.
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"

    main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    capsys.readouterr()
    stale_dir = model_dir / "v1"

    main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    capsys.readouterr()
    real_latest = model_dir / "v2"
    assert real_latest.exists()

    # Simulate v1 having just been pruned by a concurrent `train()` run,
    # happening in the window between `latest_version_dir()` resolving it
    # and `load_model()` reading it.
    shutil.rmtree(stale_dir)

    import llm_burnwatch.cli as cli_module

    real_latest_version_dir = cli_module.latest_version_dir
    calls = {"n": 0}

    def _stale_then_real(model_dir_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            return stale_dir
        return real_latest_version_dir(model_dir_arg)

    monkeypatch.setattr(cli_module, "latest_version_dir", _stale_then_real)

    exit_code = main(
        ["detect", "--log-file", str(log_path), "--model-dir", str(model_dir), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ml"]["available"] is True
    assert payload["ml"]["model_version"] == 2
    assert calls["n"] == 2


def test_detect_survives_missing_metadata_json_and_still_reports_baseline(tmp_path, capsys):
    # A corrupted/missing metadata.json (as opposed to a corrupted model.skops,
    # already covered by test_registry.py's sha256 check) must not take down
    # the whole `detect` command -- baseline results are still valid and
    # should still be printed, with ML simply marked unavailable.
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"
    main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    capsys.readouterr()

    (model_dir / "v1" / "metadata.json").unlink()

    exit_code = main(["detect", "--log-file", str(log_path), "--model-dir", str(model_dir), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["anomaly_count"] > 0  # baseline result still computed and reported
    assert payload["ml"]["available"] is False
    assert "[llm-burnwatch] error:" in captured.err


def test_train_command_missing_sklearn_returns_exit_code_2(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "llm_burnwatch.anomaly.train", None)
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert 'pip install "llm-burnwatch[anomaly]"' in captured.err


def test_detect_command_skips_ml_cross_check_when_sklearn_missing(tmp_path, capsys, monkeypatch):
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"
    main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    capsys.readouterr()

    def _raise_import_error(version_dir):
        raise ImportError("No module named 'sklearn'")

    monkeypatch.setattr("llm_burnwatch.cli.load_model", _raise_import_error)

    exit_code = main(["detect", "--log-file", str(log_path), "--model-dir", str(model_dir), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["ml"]["available"] is False
    assert "skipping ML cross-check" in captured.err


def test_unexpected_exception_in_handler_returns_exit_code_2(tmp_path, monkeypatch, capsys):
    def _boom(path):
        raise RuntimeError("boom")

    # cmd_report only catches FileNotFoundError explicitly; any other
    # exception should be caught generically by main()'s top-level handler
    # rather than crash with a raw traceback.
    monkeypatch.setattr("llm_burnwatch.cli.iter_log_records", _boom)
    exit_code = main(["report", "--log-file", str(tmp_path / "x.jsonl")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error: unexpected error: boom" in captured.err


def test_core_commands_make_no_network_attempts(tmp_path, monkeypatch, capsys):
    # Patching socket.socket itself catches every stdlib-level network path
    # (urllib, requests, http.client all eventually construct socket.socket),
    # without needing to mock each HTTP library separately.
    def _no_sockets(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", _no_sockets)
    # `report`/`dashboard` both read $XDG_CONFIG_HOME/llm-burnwatch/budget.json
    # (via `load_budget`) regardless of whether a budget was ever configured
    # -- point it at a throwaway directory so this test never depends on (or
    # is affected by) the real developer's own budget.json.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)

    schema_exit = main(["schema"])
    captured = capsys.readouterr()
    assert schema_exit == 0
    assert "unexpected error" not in captured.err

    demo_out = tmp_path / "demo2.jsonl"
    demo_exit = main(["demo-data", "--out", str(demo_out), "--n-normal", "5", "--n-anomalies", "1"])
    captured = capsys.readouterr()
    assert demo_exit == 0
    assert "unexpected error" not in captured.err

    report_exit = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()
    assert report_exit == 0
    assert "unexpected error" not in captured.err

    status_exit = main(["status", "--log-file", str(log_path)])
    captured = capsys.readouterr()
    assert status_exit == 0
    assert "unexpected error" not in captured.err

    # No --model-dir with a trained model: latest_version_dir() returns None,
    # so detect stays on the baseline-only path and never touches skops/sklearn.
    # demo-data's default n_anomalies=10 means anomalies ARE expected here --
    # exit code 1 is the correct, non-error outcome, not a failure.
    detect_exit = main(["detect", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()
    assert detect_exit == 1
    assert "unexpected error" not in captured.err

    validate_exit = main(["validate", "--log-file", str(log_path)])
    captured = capsys.readouterr()
    assert validate_exit == 0
    assert "unexpected error" not in captured.err

    # `dashboard` now runs the full detector registry and reads budget.json
    # (1.0.1) -- neither should ever touch the network either.
    dashboard_exit = main(
        ["dashboard", "--log-file", str(log_path), "--out", str(tmp_path / "dash.html")]
    )
    captured = capsys.readouterr()
    assert dashboard_exit == 0
    assert "unexpected error" not in captured.err
