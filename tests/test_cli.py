from __future__ import annotations

import json
import shutil
import socket
import sys
from types import SimpleNamespace

import pytest

from llmledger.cli import DISCLAIMER, _filter_report_records, main
from llmledger.demo_data import write_demo_log
from llmledger.tracker import CostTracker


def _demo_log(tmp_path, **kwargs):
    log_path = tmp_path / "demo.jsonl"
    write_demo_log(log_path, **kwargs)
    return log_path


def test_schema_command_prints_valid_json_matching_packaged_schema(capsys):
    exit_code = main(["schema"])
    captured = capsys.readouterr()
    schema = json.loads(captured.out)

    assert exit_code == 0
    assert schema["title"] == "llmledger JSONL log record"


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
    assert "[llmledger] error:" in captured.err


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

    assert exit_code == 0
    assert "no records found" in captured.out


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

    assert exit_code == 0
    assert "no records found" in captured.out
    assert "₽" not in captured.out


def test_report_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["report", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llmledger] error:" in captured.err


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


def test_report_since_until_rejects_bad_date_format(tmp_path, capsys):
    log_path = tmp_path / "dated.jsonl"
    _write_dated_records(log_path, ["2026-01-01"])

    with pytest.raises(SystemExit) as exc_info:
        main(["report", "--log-file", str(log_path), "--since", "01/01/2026"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "must be YYYY-MM-DD" in captured.err


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

    exit_code = main(["report", "--log-file", str(log_path), "--trace-id", "req-1"])
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
    assert payload["call_count"] == 210
    assert payload["anomaly_count"] >= 10
    assert "anomalies" in payload
    assert payload["ml"] is None  # no trained model yet


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


def test_detect_command_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    exit_code = main(["detect", "--log-file", str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llmledger] error:" in captured.err


def test_train_command_empty_log_returns_exit_code_2(tmp_path, capsys):
    pytest.importorskip("sklearn")
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llmledger] error:" in captured.err


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

    import llmledger.cli as cli_module

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
    assert "[llmledger] error:" in captured.err


def test_train_command_missing_sklearn_returns_exit_code_2(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "llmledger.anomaly.train", None)
    log_path = _demo_log(tmp_path, n_normal=5, n_anomalies=0)

    exit_code = main(["train", "--log-file", str(log_path), "--model-dir", str(tmp_path / "models")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert 'pip install "llmledger[anomaly]"' in captured.err


def test_detect_command_skips_ml_cross_check_when_sklearn_missing(tmp_path, capsys, monkeypatch):
    pytest.importorskip("sklearn")
    log_path = _demo_log(tmp_path, n_normal=200, n_anomalies=10)
    model_dir = tmp_path / "models"
    main(["train", "--log-file", str(log_path), "--model-dir", str(model_dir)])
    capsys.readouterr()

    def _raise_import_error(version_dir):
        raise ImportError("No module named 'sklearn'")

    monkeypatch.setattr("llmledger.cli.load_model", _raise_import_error)

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
    monkeypatch.setattr("llmledger.cli.iter_log_records", _boom)
    exit_code = main(["report", "--log-file", str(tmp_path / "x.jsonl")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llmledger] error: unexpected error: boom" in captured.err


def test_core_commands_make_no_network_attempts(tmp_path, monkeypatch, capsys):
    # Patching socket.socket itself catches every stdlib-level network path
    # (urllib, requests, http.client all eventually construct socket.socket),
    # without needing to mock each HTTP library separately.
    def _no_sockets(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", _no_sockets)

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
