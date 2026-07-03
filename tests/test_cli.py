from __future__ import annotations

import json
import sys

import pytest

from llmledger.cli import DISCLAIMER, main
from llmledger.demo_data import write_demo_log


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
    assert payload["ml"]["anomaly_count"] > 0


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
