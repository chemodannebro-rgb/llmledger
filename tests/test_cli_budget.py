from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import llm_burnwatch.detectors.budget_detector as budget_detector_module
from llm_burnwatch.cli import main
from llm_burnwatch.tracker import user_budget_path


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path, monkeypatch):
    # Every budget-related command reads/writes budget.json under
    # $XDG_CONFIG_HOME/llm-burnwatch/ -- point it at a throwaway directory so
    # these tests never touch the real developer's ~/.config/llm-burnwatch/.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


def _write_records(log_path, cost_micros_list, timestamp=None):
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with log_path.open("w", encoding="utf-8") as fh:
        for cost in cost_micros_list:
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
                        "timestamp": ts,
                    }
                )
                + "\n"
            )


# --- budget set / show -------------------------------------------------------


def test_budget_set_persists_and_prints_confirmation(capsys):
    exit_code = main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget saved to" in captured.out
    assert "monthly=$100.00" in captured.out
    assert "warn-at=80%" in captured.out
    assert user_budget_path().exists()


def test_budget_show_without_configuration_prints_message(capsys):
    exit_code = main(["budget", "show"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no budget configured" in captured.out
    assert "budget set" in captured.out


def test_budget_show_after_set_prints_values(capsys):
    main(["budget", "set", "--monthly", "250", "--warn-at", "0.5"])
    capsys.readouterr()

    exit_code = main(["budget", "show"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "monthly budget: $250.00" in captured.out
    assert "warn-at fraction: 50%" in captured.out


def test_budget_set_rejects_non_positive_monthly(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["budget", "set", "--monthly", "0", "--warn-at", "0.8"])
    assert exc_info.value.code == 2
    assert "positive" in capsys.readouterr().err


def test_budget_set_rejects_warn_at_above_one(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["budget", "set", "--monthly", "100", "--warn-at", "1.5"])
    assert exc_info.value.code == 2


def test_budget_set_rejects_warn_at_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["budget", "set", "--monthly", "100", "--warn-at", "0"])
    assert exc_info.value.code == 2


def test_budget_set_overwrites_previous_value(capsys):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()
    main(["budget", "set", "--monthly", "300", "--warn-at", "0.9"])
    capsys.readouterr()

    main(["budget", "show"])
    captured = capsys.readouterr()
    assert "monthly budget: $300.00" in captured.out
    assert "warn-at fraction: 90%" in captured.out


# --- report: Budget section --------------------------------------------------


def test_report_omits_budget_section_when_not_configured(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget:" not in captured.out


def test_report_json_omits_budget_key_when_not_configured(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert "budget" not in payload


def test_report_includes_budget_section_when_configured(tmp_path, capsys):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])  # $1.00

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget:" in captured.out
    assert "monthly budget: $100.00" in captured.out
    assert "status: within budget" in captured.out


def test_report_json_includes_budget_when_configured(tmp_path, capsys):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["budget"]["monthly_usd"] == 100.0
    assert payload["budget"]["month_to_date_usd"] == pytest.approx(1.0)


def test_report_csv_format_ignores_budget_even_when_configured(tmp_path, capsys):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["report", "--log-file", str(log_path), "--format", "csv"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget" not in captured.out.lower()


def test_report_budget_section_shows_exceeded_status(tmp_path, capsys):
    main(["budget", "set", "--monthly", "0.5", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])  # $1.00 > $0.50 budget

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status: budget exceeded" in captured.out


def test_report_shows_configured_no_records_this_month_line(tmp_path, capsys):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    # A record from well outside the current UTC calendar month -- nothing
    # falls into `compute_budget_status`'s month-to-date window.
    _write_records(log_path, [1_000_000], timestamp="2020-01-01T00:00:00+00:00")

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget: configured ($100.00/month) — no records this month yet" in captured.out
    assert "status:" not in captured.out  # no full _print_budget_status section


def test_report_json_omits_budget_key_when_configured_but_no_records_this_month(
    tmp_path, capsys
):
    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000], timestamp="2020-01-01T00:00:00+00:00")

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert "budget" not in payload


def test_report_omits_budget_section_when_budget_json_corrupt(tmp_path, capsys):
    path = user_budget_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "budget:" not in captured.out
    assert "could not read budget file" in captured.err


# --- detect: budget alerts ----------------------------------------------------


def test_detect_without_budget_configured_leaves_budget_disabled(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    main(["detect", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["budget_detector_enabled"] is False
    assert payload["budget_alert_count"] == 0
    assert payload["budget_alerts"] == []


def test_detect_flags_budget_exceeded_when_configured(tmp_path, capsys):
    main(["budget", "set", "--monthly", "0.5", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])  # $1.00 > $0.50 budget

    exit_code = main(["detect", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["budget_detector_enabled"] is True
    assert payload["budget_alert_count"] == 1
    assert payload["budget_alerts"][0]["kind"] == "budget_exceeded"


def test_detect_text_output_prints_budget_alert_line(tmp_path, capsys):
    main(["budget", "set", "--monthly", "0.5", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])

    exit_code = main(["detect", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "budget alert(s) found" in captured.out
    # B2: the console prints the plain-language incident type ("budget
    # exceeded"), not the raw `Alert.kind` ("budget_exceeded") -- the raw
    # kind is still available verbatim via `--json` (see
    # test_detect_flags_budget_exceeded_when_configured above).
    assert "budget exceeded" in captured.out
    assert "budget_exceeded" not in captured.out


def test_detect_stays_silent_on_budget_when_within_budget_and_pace(tmp_path, capsys):
    main(["budget", "set", "--monthly", "1000", "--warn-at", "0.99"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [1_000_000])  # $1.00, nowhere near $1000

    exit_code = main(["detect", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["budget_detector_enabled"] is True
    assert payload["budget_alert_count"] == 0


# --- low-confidence forecast note, surfaced through report/detect -----------


def test_report_flags_low_confidence_early_in_month(tmp_path, capsys, monkeypatch):
    early_now = datetime(2026, 7, 2, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return early_now

    monkeypatch.setattr(budget_detector_module, "datetime", _FrozenDatetime)

    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [50_000_000], timestamp=early_now.isoformat())  # $50 on day 2

    exit_code = main(["report", "--log-file", str(log_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["budget"]["low_confidence"] is True
    assert payload["budget"]["days_elapsed"] == 2


def test_report_text_prints_low_confidence_note(tmp_path, capsys, monkeypatch):
    early_now = datetime(2026, 7, 2, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return early_now

    monkeypatch.setattr(budget_detector_module, "datetime", _FrozenDatetime)

    main(["budget", "set", "--monthly", "100", "--warn-at", "0.8"])
    capsys.readouterr()

    log_path = tmp_path / "calls.jsonl"
    _write_records(log_path, [50_000_000], timestamp=early_now.isoformat())

    exit_code = main(["report", "--log-file", str(log_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "low-confidence" in captured.out
    assert "2 day(s) elapsed this month" in captured.out
