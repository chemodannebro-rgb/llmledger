from __future__ import annotations

import json
import re
import stat
from datetime import datetime, timezone

import pytest

from llm_burnwatch.budget import save_budget
from llm_burnwatch.cli import main
from llm_burnwatch.dashboard import _format_usd, render_dashboard
from llm_burnwatch.demo_data import write_demo_log
from llm_burnwatch.tracker import build_report, load_default_pricing, user_budget_path


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path, monkeypatch):
    # `render_dashboard()` now unconditionally calls `load_budget(...)`, which
    # reads $XDG_CONFIG_HOME/llm-burnwatch/budget.json -- point every test in
    # this file at a throwaway directory so they never read (or are affected
    # by) the real developer's ~/.config/llm-burnwatch/budget.json, the same
    # isolation `test_cli_budget.py` already applies to budget-related CLI
    # tests.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    # `dashboard --log-file` is no longer required -- an omitted --log-file
    # now falls back to default_log_path() ($XDG_DATA_HOME/llm-burnwatch/
    # log.jsonl). Isolate that too, so a test that omits --log-file never
    # reads (or is affected by) the real developer's own log.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


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

    assert "₽" not in without_rate

    # 1.0.2: dual-currency uses the same `_money_span()` markup everywhere --
    # the summary card and the by-label/by-model table row for this record's
    # $1.00 cost must both carry the identical RUB parenthetical (and "₽" not
    # in without_rate, above, already confirms it's absent everywhere when no
    # rate is given).
    assert with_rate.count('<span class="money">$1.00 (₽90.00)') >= 2


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
    assert "[llm-burnwatch] error:" in captured.err
    assert not out_path.exists()


def test_dashboard_cli_requires_out_argument(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["dashboard", "--log-file", str(tmp_path / "demo.jsonl")])
    assert exc_info.value.code == 2


def test_dashboard_cli_without_log_file_falls_back_to_default_log_path(tmp_path, capsys):
    # A2/D4: --log-file is no longer required -- omitting it resolves to
    # default_log_path(), which doesn't exist in this test's isolated
    # $XDG_DATA_HOME, so this returns (not raises) exit code 2 via the
    # same FileNotFoundError-with-a-suggestion path as an explicit missing
    # --log-file, not an argparse "required" SystemExit.
    exit_code = main(["dashboard", "--out", str(tmp_path / "dash.html")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "[llm-burnwatch] error:" in captured.err
    assert "log path does not exist" in captured.err


# --- 1.0.1: Dashboard 2.0 -----------------------------------------------------


def test_render_dashboard_active_detectors_baseline_count_matches_summary_card():
    # `render_dashboard()` now runs the full detector registry (1.0.1-a),
    # not just baseline z-score. The "baseline anomalies flagged" summary
    # card and the "Active detectors" table's Baseline row both derive from
    # the same `alerts` list (`kind == "zscore_outlier"` vs `detector ==
    # "baseline"`) -- for this detector every alert is both, so the two
    # counts must always agree.
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
    records = day_a_normal + [day_a_outlier]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    card_match = re.search(
        r'baseline anomalies flagged</div><div class="value">(\d+)', result
    )
    table_match = re.search(
        r'<td>Baseline \(z-score\)</td><td>enabled</td><td>[^<]*</td>'
        r'<td data-sort-value="(\d+)">\d+</td>',
        result,
    )
    assert card_match is not None
    assert table_match is not None
    assert card_match.group(1) == table_match.group(1) == "1"


def test_render_dashboard_alert_timeline_shows_non_baseline_alert():
    # 1.0.1-b: the per-day alert timeline is not limited to baseline
    # z-score findings -- a budget alert (a different detector entirely)
    # must also show up in that day's `<ul class="alert-list">`.
    save_budget(user_budget_path(), monthly_usd=1.0, warn_at_fraction=0.8)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 2_000_000,  # $2, over the $1 monthly budget
            "timestamp": today,
        }
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    assert '<h4>Alerts</h4><ul class="alert-list">' in result
    assert "budget: budget_exceeded" in result
    assert "exceeds monthly budget" in result


def test_render_dashboard_budget_block_three_states(tmp_path):
    # 1.0.1-c: not configured -> no section; configured, no records this
    # month -> one-line message; configured with a status -> progress bar.
    pricing = load_default_pricing()

    not_configured = render_dashboard([], pricing)
    assert "<h2>Budget</h2>" not in not_configured

    save_budget(user_budget_path(), monthly_usd=100.0, warn_at_fraction=0.8)
    old_month_record = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 500_000,
            "timestamp": "2020-01-01T00:00:00+00:00",
        }
    ]
    no_records_yet = render_dashboard(old_month_record, pricing)
    assert "no records this month yet" in no_records_yet
    assert "configured ($100.00/month)" in no_records_yet

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    current_month_record = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 1_000_000,
            "timestamp": today,
        }
    ]
    with_status = render_dashboard(current_month_record, pricing)
    assert '<div class="budget-bar">' in with_status
    assert "within budget" in with_status
    assert "month-to-date:" in with_status
    assert '<span class="money">$1.00' in with_status
    assert "budget:" in with_status
    assert '<span class="money">$100.00' in with_status


def test_render_dashboard_sparklines_differ_between_labels_with_different_trends():
    # 1.0.1-d: sparklines are normalized per-row (own max), so two labels
    # with opposite day-to-day trends must render different point lists.
    records = [
        {
            "label": "a",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "label": "a",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 400_000,
            "timestamp": "2026-01-02T00:00:00+00:00",
        },
        {
            "label": "b",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 400_000,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "label": "b",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100_000,
            "timestamp": "2026-01-02T00:00:00+00:00",
        },
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    idx_a = result.index("<td>a</td>")
    idx_b = result.index("<td>b</td>")
    row_a = result[idx_a : result.index("</tr>", idx_a)]
    row_b = result[idx_b : result.index("</tr>", idx_b)]

    assert '<polyline points="' in row_a
    assert '<polyline points="' in row_b
    assert row_a != row_b


def test_render_dashboard_active_detectors_section_lists_all_five():
    # 1.0.1-e: transparency section -- every detector shows up, even ones
    # that never fired and even when budget tracking isn't configured.
    pricing = load_default_pricing()

    result = render_dashboard([], pricing)

    assert '<h2 id="active-detectors">Active detectors</h2>' in result
    assert "Baseline (z-score)" in result
    assert "Frequency" in result
    assert "Level-shift (CUSUM)" in result
    assert "Rules (hard limits)" in result
    assert "not configured" in result  # Budget row, unconfigured


def test_render_dashboard_active_detectors_shows_budget_configured():
    save_budget(user_budget_path(), monthly_usd=50.0, warn_at_fraction=0.8)
    pricing = load_default_pricing()

    result = render_dashboard([], pricing)

    assert "configured ($50.00/month)" in result


def test_render_dashboard_stays_under_300kb_on_demo_scale_log(tmp_path):
    # 1.0.1-f: explicit size regression barrier -- sparklines/alert
    # timeline must not make the single-file HTML balloon past the
    # documented budget on demo-data-scale logs.
    log_path = tmp_path / "demo.jsonl"
    write_demo_log(log_path, n_normal=200, n_anomalies=10)
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    assert len(result.encode("utf-8")) < 300_000


# --- 1.0.2: Dashboard 3.0 -- design, sort/filter/copy, dual-currency ---------


def test_format_usd_thousands_separator_and_small_value_fallback():
    # Normal amounts: 2 decimals + thousands separator (the readability fix).
    assert _format_usd(1234.567891) == "$1,234.57"
    assert _format_usd(0) == "$0.00"

    # A real, nonzero micro-cost that would round away to "$0.00" at 2
    # decimals must fall back to the old 6-decimal form instead of lying
    # about it being zero.
    assert _format_usd(0.0000015) == "$0.000002"
    assert _format_usd(0.0000015) != "$0.00"


def test_render_dashboard_money_cells_carry_full_precision_copy_button():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 1_234_567,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    # Readable 2-decimal form is what's shown...
    assert "$1.23" in result
    # ...but the exact 6-decimal value is still one click away via the
    # copy button, never silently dropped.
    assert 'data-copy="1.234567"' in result
    assert 'class="copy-btn"' in result


def test_render_dashboard_sort_and_filter_markup_present():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    # Sortable column headers, numeric sort key on data-sort-value.
    assert 'data-sort="text"' in result
    assert 'data-sort="num"' in result
    assert 'data-sort-value="0.000100"' in result

    # Filter inputs above the by-label/by-model tables and the journal.
    assert 'data-filter-target="by-label-table"' in result
    assert 'data-filter-target="by-model-table"' in result
    assert 'data-filter-target="journal-list"' in result
    assert 'id="by-label-table"' in result
    assert 'id="by-model-table"' in result
    assert 'id="journal-list"' in result


def test_render_dashboard_script_makes_no_network_calls():
    pricing = load_default_pricing()
    result = render_dashboard([], pricing)

    script_start = result.index("<script>")
    script_end = result.index("</script>", script_start)
    script = result[script_start:script_end]

    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "http://" not in script
    assert "https://" not in script


def test_render_dashboard_has_anchor_navigation_and_aria_sort():
    save_budget(user_budget_path(), monthly_usd=100.0, warn_at_fraction=0.8)
    pricing = load_default_pricing()

    result = render_dashboard([], pricing)

    assert '<nav class="section-nav">' in result
    assert '<a href="#budget">Budget</a>' in result
    assert '<a href="#totals">Totals</a>' in result
    assert '<a href="#active-detectors">Active detectors</a>' in result
    assert '<a href="#daily-journal">Daily journal</a>' in result
    assert 'aria-sort="none"' in result


def test_render_dashboard_copy_button_has_aria_label():
    records = [
        {
            "label": "x",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_micros": 100,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    ]
    pricing = load_default_pricing()

    result = render_dashboard(records, pricing)

    assert 'aria-label="Copy exact value"' in result
