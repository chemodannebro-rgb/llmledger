from __future__ import annotations

import json

import pytest

from llm_burnwatch.anomaly.constants import SCALE_WARNING_THRESHOLD
from llm_burnwatch.logreader import check_scale, filter_by_period, iter_log_records


def _write_lines(path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_missing_path_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(iter_log_records(tmp_path / "does-not-exist.jsonl"))


def test_reads_single_file(tmp_path):
    path = tmp_path / "calls.jsonl"
    _write_lines(path, [{"a": 1}, {"a": 2}])
    records = list(iter_log_records(path))
    assert records == [{"a": 1}, {"a": 2}]


def test_empty_file_yields_no_records(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.touch()
    assert list(iter_log_records(path)) == []


def test_corrupt_lines_are_skipped_with_warning(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"a": 1}) + "\n")
        fh.write("{not valid json\n")
        fh.write(json.dumps({"a": 2}) + "\n")

    records = list(iter_log_records(path))
    assert records == [{"a": 1}, {"a": 2}]

    captured = capsys.readouterr()
    assert "skipping corrupt JSONL line" in captured.err
    assert "skipped 1 corrupt log line(s) total" in captured.err


def test_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "calls.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"a": 1}) + "\n")
        fh.write("\n")
        fh.write("   \n")
        fh.write(json.dumps({"a": 2}) + "\n")
    assert list(iter_log_records(path)) == [{"a": 1}, {"a": 2}]


def test_directory_mode_merges_all_jsonl_files(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_lines(log_dir / "proc-a.jsonl", [{"a": 1}])
    _write_lines(log_dir / "proc-b.jsonl", [{"a": 2}])
    (log_dir / "not-a-log.txt").write_text("ignore me")

    records = list(iter_log_records(log_dir))
    assert sorted(r["a"] for r in records) == [1, 2]


def test_directory_mode_empty_directory_yields_no_records(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    assert list(iter_log_records(log_dir)) == []


def test_rotated_backups_are_read_in_chronological_order(tmp_path):
    path = tmp_path / "calls.jsonl"
    # RotatingFileHandler convention: .1 is the most recently rotated-out
    # file, higher numbers are progressively older. Chronological (oldest
    # first) order is therefore: .2, .1, current.
    _write_lines(path.with_name("calls.jsonl.2"), [{"seq": 1}])
    _write_lines(path.with_name("calls.jsonl.1"), [{"seq": 2}])
    _write_lines(path, [{"seq": 3}])

    records = list(iter_log_records(path))
    assert [r["seq"] for r in records] == [1, 2, 3]


def test_check_scale_warns_when_over_threshold_without_mitigation(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    path.touch()
    check_scale(path, SCALE_WARNING_THRESHOLD + 1)
    captured = capsys.readouterr()
    assert "Consider enabling rotation" in captured.err


def test_check_scale_silent_when_under_threshold(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    path.touch()
    check_scale(path, SCALE_WARNING_THRESHOLD)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_scale_silent_for_directory_mode(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    check_scale(log_dir, SCALE_WARNING_THRESHOLD + 1)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_scale_silent_when_rotation_backups_present(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    path.touch()
    path.with_name("calls.jsonl.1").touch()
    check_scale(path, SCALE_WARNING_THRESHOLD + 1)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_filter_by_period_noop_when_both_bounds_none():
    records = [{"timestamp": "2026-01-01T00:00:00+00:00"}, {"timestamp": "bad"}]
    assert filter_by_period(records, None, None) is records


def test_filter_by_period_inclusive_bounds():
    records = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "seq": 1},
        {"timestamp": "2026-01-15T00:00:00+00:00", "seq": 2},
        {"timestamp": "2026-01-31T00:00:00+00:00", "seq": 3},
        {"timestamp": "2026-02-01T00:00:00+00:00", "seq": 4},
    ]
    kept = filter_by_period(records, "2026-01-01", "2026-01-31")
    assert [r["seq"] for r in kept] == [1, 2, 3]


def test_filter_by_period_drops_records_without_valid_timestamp_only_when_active(capsys):
    records = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "seq": 1},
        {"timestamp": "not-a-timestamp", "seq": 2},
        {"seq": 3},
    ]

    kept_noop = filter_by_period(records, None, None)
    assert [r["seq"] for r in kept_noop] == [1, 2, 3]

    kept_active = filter_by_period(records, "2026-01-01", None)
    assert [r["seq"] for r in kept_active] == [1]


def test_filter_by_period_warns_once_for_whole_skip(capsys):
    records = [
        {"timestamp": "2026-01-01T00:00:00+00:00"},
        {"timestamp": "not-a-timestamp"},
        {"timestamp": "also-not-a-timestamp"},
    ]

    filter_by_period(records, "2026-01-01", None)
    captured = capsys.readouterr()

    assert captured.err.count("fell outside --since/--until") == 1
    assert "2 record(s)" in captured.err
