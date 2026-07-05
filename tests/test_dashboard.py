from __future__ import annotations

import json
import stat

import pytest

from llmledger.cli import main
from llmledger.dashboard import render_dashboard
from llmledger.demo_data import write_demo_log
from llmledger.tracker import build_report, load_default_pricing


def _write_records(log_path, records):
    with log_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cached_input_tokens": 0,
                        **r,
                    }
                )
                + "\n"
            )


def test_render_dashboard_on_demo_log_contains_totals_and_names(tmp_path):
    log_path = tmp_path / "demo.jsonl"
    write_demo_log(log_path, n_normal=5, n_anomalies=1)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    report = build_report(records, pricing)
    assert f"{report['total_cost_usd']:.6f}" in result
    for label in report["by_label_micros"]:
        assert label in result
    for model in report["by_model_micros"]:
        assert model in result


def test_render_dashboard_on_empty_log_does_not_crash():
    pricing = load_default_pricing()
    result = render_dashboard([], pricing)

    assert "<html" in result.lower()
    assert "No data." in result


def test_render_dashboard_escapes_script_tags_in_label_and_model():
    records = [
        {
            "label": "<script>alert(1)</script>",
            "model": "<img src=x onerror=alert(1)>",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    assert "<script>alert" not in result
    assert "<img src=x" not in result
    assert "&lt;script&gt;" in result


def test_render_dashboard_multiday_timeseries_includes_all_dates():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": f"2026-01-0{d}T00:00:00+00:00",
        }
        for d in range(1, 4)
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    for d in range(1, 4):
        assert f"01-0{d}" in result


def test_render_dashboard_skips_invalid_timestamp_from_journal_but_keeps_totals(capsys):
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": "not-a-timestamp",
        },
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 200,
            # missing timestamp entirely
        },
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)
    captured = capsys.readouterr()

    report = build_report(records, pricing)
    assert f"{report['total_cost_usd']:.6f}" in result
    assert "No dated records in this period." in result
    assert "left out of the dashboard" in captured.err


def test_render_dashboard_rub_rate_shown_when_given():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 1_000_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    ]
    pricing = load_default_pricing()

    with_rate = render_dashboard(records, pricing, rub_rate=90)
    without_rate = render_dashboard(records, pricing)

    assert "~₽90.00 at 90.00 ₽/$" in with_rate
    assert "₽" not in without_rate


def test_render_dashboard_includes_viewport_meta_tag():
    pricing = load_default_pricing()
    result = render_dashboard([], pricing)

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in result


def test_render_dashboard_daily_journal_has_one_entry_per_day_desc_order():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": f"2026-01-0{d}T00:00:00+00:00",
        }
        for d in range(1, 4)
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    assert result.count('<details class="day">') == 3
    first = result.index("2026-01-03")
    second = result.index("2026-01-02")
    third = result.index("2026-01-01")
    assert first < second < third


def test_render_dashboard_day_bar_width_is_constant_regardless_of_period_length():
    def _make(n_days):
        return [
            {
                "label": "x",
                "model": "gpt-4o",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_micros": 100 * (i + 1),
                "timestamp": f"2026-01-{i + 1:02d}T00:00:00+00:00",
            }
            for i in range(n_days)
        ]

    pricing = load_default_pricing()
    short_result = render_dashboard(_make(3), pricing)
    long_result = render_dashboard(_make(28), pricing)

    assert 'width="100" height="14"' in short_result
    assert 'width="100" height="14"' in long_result


def test_render_dashboard_day_detail_breakdown_is_scoped_to_that_day():
    records = [
        {
            "label": "label-a",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 111_111,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "label": "label-b",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 222_222,
            "timestamp": "2026-01-02T00:00:00+00:00",
        },
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    # Journal entries are most-recent-first, so 2026-01-02 appears before
    # 2026-01-01 in the rendered HTML.
    idx_b = result.index("2026-01-02")
    idx_a = result.index("2026-01-01")
    day_b = result[idx_b:idx_a]
    day_a = result[idx_a:]
    assert "label-a" in day_a
    assert "label-b" not in day_a
    assert "label-b" in day_b
    assert "label-a" not in day_b


def test_render_dashboard_per_day_anomaly_count():
    day_a_normal = [
        {
            "label": "summarize",
            "model": "gpt-4o",
            "input_tokens": 800 + i,
            "output_tokens": 150 + i,
            "cost_micros": 2000 + i,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        for i in range(20)
    ]
    day_a_outlier = {
        "label": "summarize",
        "model": "gpt-4o",
        "input_tokens": 50_000,
        "output_tokens": 10_000,
        "cost_micros": 200_000,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    day_b_normal = [
        {
            "label": "chat",
            "model": "gpt-4o-mini",
            "input_tokens": 100 + i,
            "output_tokens": 20 + i,
            "cost_micros": 500 + i,
            "timestamp": "2026-01-02T00:00:00+00:00",
        }
        for i in range(20)
    ]
    records = day_a_normal + [day_a_outlier] + day_b_normal
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    # Journal entries are most-recent-first, so 2026-01-02 appears before
    # 2026-01-01 in the rendered HTML.
    idx_b = result.index("2026-01-02")
    idx_a = result.index("2026-01-01")
    day_b = result[idx_b:idx_a]
    day_a = result[idx_a:]
    assert '<span class="anomaly-badge flagged">1</span>' in day_a
    assert '<span class="anomaly-badge clean">' in day_b


def test_render_dashboard_period_line_reflects_since_until_or_all_time():
    pricing = load_default_pricing()

    all_time = render_dashboard([], pricing)
    with_period = render_dashboard([], pricing, since="2026-01-01", until="2026-01-31")

    assert "Period: all time" in all_time
    assert "Period: 2026-01-01" in with_period
    assert "2026-01-31" in with_period


def test_render_dashboard_has_mobile_media_query():
    pricing = load_default_pricing()
    result = render_dashboard([], pricing)

    assert "@media (max-width: 600px)" in result


def test_dashboard_cli_writes_file_with_0600_permissions(tmp_path, capsys):
    log_path = tmp_path / "demo.jsonl"
    write_demo_log(log_path, n_normal=5, n_anomalies=1)
    out_path = tmp_path / "dash.html"

    exit_code = main(["dashboard", "--log-file", str(log_path), "--out", str(out_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dashboard written to" in captured.out
    assert out_path.exists()
    mode = stat.S_IMODE(out_path.stat().st_mode)
    assert mode == 0o600


def test_dashboard_cli_missing_log_file_returns_exit_code_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    out_path = tmp_path / "dash.html"

    exit_code = main(["dashboard", "--log-file", str(missing), "--out", str(out_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llmledger] error:" in captured.err
    assert not out_path.exists()


def test_dashboard_cli_requires_log_file_and_out_arguments(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["dashboard", "--out", str(tmp_path / "dash.html")])
    assert exc_info.value.code == 2

    with pytest.raises(SystemExit) as exc_info:
        main(["dashboard", "--log-file", str(tmp_path / "demo.jsonl")])
    assert exc_info.value.code == 2
