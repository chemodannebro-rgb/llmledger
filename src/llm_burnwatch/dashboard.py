"""Static, single-file HTML cost dashboard.

`render_dashboard()` turns a list of log records into a self-contained HTML
document: a period-scoped summary (total/by-label/by-model, reusing
`tracker.build_report`) plus a "daily journal" -- one expandable entry per
UTC calendar day showing that day's calls/cost/top label/anomaly count/
alert timeline, with a per-day by-label/by-model breakdown (with cost/calls
sparklines) inside, a budget progress bar (same numbers as `report`'s
"budget:" section, via `detectors.budget_detector.compute_budget_status`),
and an "Active detectors" table showing what ran and what it found. Alerts
reuse the same `detectors.engine.run_detectors()` full registry that
`detect` uses -- not just the baseline z-score. No new third-party
dependency, no external file/CDN reference, no network call, and no
JavaScript (day entries use native `<details>`/`<summary>`) -- the whole
zero-dependency/no-network guarantee that applies to the rest of the core
CLI applies here too.
"""

from __future__ import annotations

import html

from ._messages import warn
from .anomaly.constants import (
    CUSUM_H_MULTIPLIER,
    CUSUM_SLACK_MULTIPLIER,
    FREQUENCY_ABS_CALLS_PER_WINDOW,
    FREQUENCY_Z_THRESHOLD,
    MIN_SEASONAL_SPAN_DAYS,
    Z_SCORE_THRESHOLD,
)
from .anomaly.seasonal import has_seasonal_coverage
from .budget import load_budget
from .detectors.baseline_detector import BaselineDetector
from .detectors.budget_detector import BudgetDetector, compute_budget_status
from .detectors.cusum_detector import CusumDetector
from .detectors.engine import run_detectors
from .detectors.frequency_detector import FrequencyDetector
from .detectors.protocol import Alert
from .detectors.rules_detector import RulesDetector
from .logreader import parse_date
from .tracker import build_report, user_budget_path

_DAY_BAR_WIDTH = 100
_DAY_BAR_HEIGHT = 14
_SPARK_WIDTH = 60
_SPARK_HEIGHT = 16

# Duplicated from cli.DISCLAIMER rather than imported from it, to avoid a
# circular import (cli.py imports this module for cmd_dashboard).
_DASHBOARD_DISCLAIMER = (
    "llm-burnwatch is a diagnostic aid, not a guarantee: it flags statistically "
    "unusual calls, it does not confirm they are errors, and it may miss "
    "real ones. Always use your own judgement before acting on its output."
)


def _dashboard_registry(budget_config: dict | None) -> list:
    """Same shape as `cli._detect_registry()`, minus CLI-flag thresholds --
    `dashboard` has no `--threshold`/`--allowed-models`/`--max-call-cost`/
    `--max-trace-cost` flags, so every detector runs with its packaged
    default. `RulesDetector()` with no limits configured is a deliberate
    no-op (see its docstring), same as `BudgetDetector()` with no
    `monthly_usd` -- both are safe to always include.
    """
    return [
        BaselineDetector(),
        FrequencyDetector(),
        CusumDetector(),
        RulesDetector(),
        BudgetDetector(
            monthly_usd=budget_config["monthly_usd"] if budget_config else None,
            warn_at_fraction=budget_config["warn_at_fraction"] if budget_config else None,
        ),
    ]


def _daily_breakdown(records: list[dict], alerts: list[Alert]) -> dict[str, dict]:
    """Group records by UTC calendar date. Returns a date-sorted (ascending)
    dict: `"YYYY-MM-DD" -> {call_count, cost_micros, by_label_micros,
    by_model_micros, by_label_calls, by_model_calls, anomaly_count,
    severity_counts, alerts}`.

    Records with a missing/invalid `timestamp` are excluded from the journal
    (but still counted in the overall totals via `build_report`), with a
    single `warn()` for the whole skip, not one per record.

    `anomaly_count` only counts baseline z-score alerts (`kind ==
    "zscore_outlier"`) -- unchanged from before this module ran the full
    detector registry, so the existing `anomaly-badge` contract stays
    intact. `severity_counts`/`alerts` are additive: they cover every alert
    from every detector (baseline included), for the new per-day alert
    timeline.
    """
    by_date: dict[str, dict] = {}
    skipped = 0

    def _day(date: str) -> dict:
        return by_date.setdefault(
            date,
            {
                "call_count": 0,
                "cost_micros": 0,
                "by_label_micros": {},
                "by_model_micros": {},
                "by_label_calls": {},
                "by_model_calls": {},
                "anomaly_count": 0,
                "severity_counts": {"critical": 0, "warning": 0, "info": 0},
                "alerts": [],
            },
        )

    for record in records:
        date = parse_date(record.get("timestamp"))
        if date is None:
            skipped += 1
            continue
        day = _day(date)
        label = record.get("label", "?")
        model = record.get("model", "?")
        cost_micros = record.get("cost_micros", 0)
        day["call_count"] += 1
        day["cost_micros"] += cost_micros
        day["by_label_micros"][label] = day["by_label_micros"].get(label, 0) + cost_micros
        day["by_model_micros"][model] = day["by_model_micros"].get(model, 0) + cost_micros
        day["by_label_calls"][label] = day["by_label_calls"].get(label, 0) + 1
        day["by_model_calls"][model] = day["by_model_calls"].get(model, 0) + 1

    for a in alerts:
        if a.record_ref is None or a.record_ref >= len(records):
            continue
        date = parse_date(records[a.record_ref].get("timestamp"))
        if date is None or date not in by_date:
            continue
        day = by_date[date]
        if a.kind == "zscore_outlier":
            day["anomaly_count"] += 1
        day["severity_counts"][a.severity] = day["severity_counts"].get(a.severity, 0) + 1
        day["alerts"].append(a)

    if skipped:
        warn(
            f"{skipped} record(s) had a missing or unparseable timestamp and "
            "were left out of the dashboard's daily journal (they are still "
            "included in the total/by-label/by-model figures)"
        )

    return dict(sorted(by_date.items()))


def _top_label(by_label_micros: dict[str, int]) -> str | None:
    if not by_label_micros:
        return None
    label, micros = max(by_label_micros.items(), key=lambda kv: kv[1])
    return f"{html.escape(label)} (${micros / 1_000_000:.2f})"


def _render_day_bar(cost_micros: int, max_micros: int) -> str:
    width = round(_DAY_BAR_WIDTH * cost_micros / max_micros) if max_micros else 0
    width = max(1, width)
    return (
        f'<svg viewBox="0 0 {_DAY_BAR_WIDTH} {_DAY_BAR_HEIGHT}" '
        f'width="{_DAY_BAR_WIDTH}" height="{_DAY_BAR_HEIGHT}" '
        'xmlns="http://www.w3.org/2000/svg" class="day-bar-svg">'
        f'<rect x="0" y="0" width="{width}" height="{_DAY_BAR_HEIGHT}" class="bar"/>'
        "</svg>"
    )


def _render_sparkline(values: list[int]) -> str:
    """Static inline-SVG polyline, normalized to THIS series' own max (each
    row shows its own shape/trend -- the standard sparkline convention;
    values are not meant to be compared across rows, only within one).
    Returns "" (no element) for an all-zero/empty series.
    """
    if not values or max(values) <= 0:
        return ""
    vmax = max(values)
    n = len(values)
    step = _SPARK_WIDTH / max(n - 1, 1)
    points = " ".join(
        f"{i * step:.1f},{_SPARK_HEIGHT - (v / vmax) * _SPARK_HEIGHT:.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg viewBox="0 0 {_SPARK_WIDTH} {_SPARK_HEIGHT}" '
        f'width="{_SPARK_WIDTH}" height="{_SPARK_HEIGHT}" '
        'xmlns="http://www.w3.org/2000/svg" class="spark-svg">'
        f'<polyline points="{points}" class="spark-line" fill="none"/>'
        "</svg>"
    )


def _series_by_name(daily: dict[str, dict], key: str) -> dict[str, list[int]]:
    """Per-day series for every name seen under `daily[date][key]`, in
    ascending date order -- the raw material for a sparkline per row."""
    dates = sorted(daily)
    names = {name for day in daily.values() for name in day[key]}
    return {name: [daily[d][key].get(name, 0) for d in dates] for name in names}


def _render_table(rows: dict[str, int]) -> str:
    if not rows:
        return "<p>No data.</p>"
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>${micros / 1_000_000:.6f}</td></tr>"
        for key, micros in sorted(rows.items())
    )
    return f"<table><thead><tr><th>Name</th><th>Cost</th></tr></thead><tbody>{body}</tbody></table>"


def _render_table_with_sparklines(
    totals: dict[str, int],
    cost_series: dict[str, list[int]],
    call_series: dict[str, list[int]],
) -> str:
    if not totals:
        return "<p>No data.</p>"
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td>"
        f"<td>${micros / 1_000_000:.6f}</td>"
        f"<td>{_render_sparkline(cost_series.get(key, []))}</td>"
        f"<td>{_render_sparkline(call_series.get(key, []))}</td>"
        "</tr>"
        for key, micros in sorted(totals.items())
    )
    return (
        "<table><thead><tr><th>Name</th><th>Cost</th>"
        "<th>Cost trend</th><th>Calls trend</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _render_journal(daily: dict[str, dict]) -> str:
    if not daily:
        return "<p>No dated records in this period.</p>"

    max_micros = max(day["cost_micros"] for day in daily.values())
    entries = []
    for date, day in sorted(daily.items(), reverse=True):
        top_label = _top_label(day["by_label_micros"]) or "\u2014"
        anomaly_count = day["anomaly_count"]
        badge_class = "flagged" if anomaly_count else "clean"
        anomaly_text = str(anomaly_count) if anomaly_count else "\u2014"

        severity_counts = day["severity_counts"]
        if severity_counts["critical"]:
            top_severity, sev_text = "critical", str(severity_counts["critical"])
        elif severity_counts["warning"]:
            top_severity, sev_text = "warning", str(severity_counts["warning"])
        elif severity_counts["info"]:
            top_severity, sev_text = "info", str(severity_counts["info"])
        else:
            top_severity, sev_text = "clean", "\u2014"

        alerts_html = ""
        if day["alerts"]:
            items = "".join(
                f"<li>{html.escape(a.detector)}: {html.escape(a.kind)}"
                f" \u2014 {html.escape(a.message)}</li>"
                for a in day["alerts"]
            )
            alerts_html = f"<h4>Alerts</h4><ul class=\"alert-list\">{items}</ul>"

        entries.append(
            f'<details class="day">'
            f'<summary>'
            f'<span class="day-date">{html.escape(date)}</span>'
            f'<span class="day-calls">{day["call_count"]} calls</span>'
            f'<span class="day-cost">${day["cost_micros"] / 1_000_000:.6f}</span>'
            f'<span class="day-bar">{_render_day_bar(day["cost_micros"], max_micros)}</span>'
            f'<span class="day-top-label">{top_label}</span>'
            f'<span class="anomaly-badge {badge_class}">{anomaly_text}</span>'
            f'<span class="severity-badge {top_severity}">{sev_text}</span>'
            f"</summary>"
            f'<div class="day-detail">'
            f"<h4>By label</h4>{_render_table(day['by_label_micros'])}"
            f"<h4>By model</h4>{_render_table(day['by_model_micros'])}"
            f"{alerts_html}"
            f"</div>"
            f"</details>"
        )
    return "".join(entries)


def _render_budget_block(budget_config: dict | None, budget_status: dict | None) -> str:
    """Mirrors `cli._print_budget_status()`'s three-state UX (1.0.0-c):
    not configured -> nothing; configured, no records this month yet -> one
    line; configured with a status -> a progress bar plus the same numbers
    `report` prints. `budget_status` comes from `compute_budget_status()`,
    the single source of truth for all these numbers.
    """
    if budget_config is None:
        return ""
    if budget_status is None:
        return (
            "<h2>Budget</h2><p>"
            f"budget: configured (${budget_config['monthly_usd']:.2f}/month) "
            "\u2014 no records this month yet</p>"
        )

    monthly_usd = budget_status["monthly_usd"]
    month_to_date = budget_status["month_to_date_usd"]
    forecast = budget_status["forecast_usd"]
    fill_pct = min(100.0, (month_to_date / monthly_usd * 100) if monthly_usd else 0.0)

    if budget_status["over_budget"]:
        status_class, status_text = "over", "budget exceeded"
    elif budget_status["pace_warning"]:
        status_class, status_text = "warn", (
            f"on pace to exceed {budget_status['warn_at_fraction']:.0%} of budget"
        )
    else:
        status_class, status_text = "ok", "within budget"

    low_confidence_html = ""
    if budget_status["low_confidence"]:
        low_confidence_html = (
            f"<p class=\"budget-note\">only {budget_status['days_elapsed']} day(s) "
            "elapsed this month \u2014 projection is low-confidence</p>"
        )

    return (
        "<h2>Budget</h2>"
        f'<p>month: {html.escape(str(budget_status["month"]))} \u2014 '
        f'<span class="budget-status {status_class}">{status_text}</span></p>'
        f'<div class="budget-bar"><div class="budget-bar-fill {status_class}" '
        f'style="width:{fill_pct:.1f}%"></div></div>'
        f"<p>month-to-date: ${month_to_date:.2f} / projected month-end: "
        f"${forecast:.2f} / budget: ${monthly_usd:.2f}</p>"
        f"{low_confidence_html}"
    )


def _render_active_detectors(
    alerts: list[Alert],
    *,
    frequency_enabled: bool,
    seasonal_available: bool,
    budget_config: dict | None,
) -> str:
    """Transparency section: what ran, at what threshold, and what it found
    -- "what's actually being watched", per the milestone's explicit ask.
    """

    def _count(name: str) -> int:
        return sum(1 for a in alerts if a.detector == name)

    frequency_status = (
        f"enabled (auto, \u2265{MIN_SEASONAL_SPAN_DAYS} days span)"
        if frequency_enabled
        else f"disabled (auto, <{MIN_SEASONAL_SPAN_DAYS} days span)"
        if not seasonal_available
        else "disabled"
    )
    budget_status_cell = (
        f"configured (${budget_config['monthly_usd']:.2f}/month)"
        if budget_config
        else "not configured"
    )

    rows = [
        ("Baseline (z-score)", "enabled", f"{Z_SCORE_THRESHOLD}", _count("baseline")),
        (
            "Frequency",
            frequency_status,
            f"z={FREQUENCY_Z_THRESHOLD}, abs={FREQUENCY_ABS_CALLS_PER_WINDOW}/window",
            _count("frequency"),
        ),
        (
            "Level-shift (CUSUM)",
            "enabled",
            f"h={CUSUM_H_MULTIPLIER}\u00d7, slack={CUSUM_SLACK_MULTIPLIER}\u00d7",
            _count("cusum"),
        ),
        ("Rules (hard limits)", "enabled, no limits configured", "\u2014", _count("rules")),
        ("Budget", budget_status_cell, "\u2014", _count("budget")),
    ]
    body = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{html.escape(status)}</td>"
        f"<td>{html.escape(threshold)}</td><td>{count}</td></tr>"
        for name, status, threshold, count in rows
    )
    return (
        "<h2>Active detectors</h2>"
        "<table><thead><tr><th>Detector</th><th>Status</th>"
        "<th>Threshold</th><th>Alerts</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_dashboard(
    records: list[dict],
    pricing: dict,
    *,
    rub_rate: float | None = None,
    fx_rate: float | None = None,
    currency: str | None = None,
    since: str | None = None,
    until: str | None = None,
    budget_records: list[dict] | None = None,
) -> str:
    """Render a self-contained HTML dashboard for `records`, priced with
    `pricing`.

    `rub_rate` is the deprecated RUB-only conversion path, kept for backward
    compatibility with existing direct callers -- its output is unchanged.
    `fx_rate`/`currency` is the generic replacement (any currency, shown by
    its ISO code, e.g. "90.00 RUB"); pass at most one of the two. Neither
    rate is ever fetched over the network -- both are fixed, manually
    supplied values.

    `since`/`until` are assumed to have already been applied to `records` by
    the caller (see `logreader.filter_by_period`) -- they are only used here
    to display the period the dashboard covers.

    `budget_records` is the *unfiltered* log, used only for the budget
    block: like `report`'s budget section, budget tracking is about this
    calendar month's actual spend, not whatever `since`/`until` period the
    rest of the dashboard summarizes. Defaults to `records` when omitted
    (correct whenever the caller didn't filter by period).
    """
    if budget_records is None:
        budget_records = records

    report = build_report(records, pricing)

    budget_config = load_budget(user_budget_path())
    seasonal_available = has_seasonal_coverage(records) if records else False
    frequency_enabled = seasonal_available
    alerts = (
        run_detectors(
            records,
            registry=_dashboard_registry(budget_config),
            enabled_overrides={
                "frequency": frequency_enabled,
                "budget": budget_config is not None,
            },
        )
        if records
        else []
    )
    anomaly_count = sum(1 for a in alerts if a.kind == "zscore_outlier")

    daily = _daily_breakdown(records, alerts)
    cost_by_label = _series_by_name(daily, "by_label_micros")
    cost_by_model = _series_by_name(daily, "by_model_micros")
    calls_by_label = _series_by_name(daily, "by_label_calls")
    calls_by_model = _series_by_name(daily, "by_model_calls")

    budget_status = (
        compute_budget_status(
            budget_records, budget_config["monthly_usd"], budget_config["warn_at_fraction"]
        )
        if budget_config
        else None
    )

    if since or until:
        since_display = since or "\u2026"
        until_display = until or "\u2026"
        period_line = f"Period: {since_display} \u2013 {until_display}"
    else:
        period_line = "Period: all time"

    total_cost_line = f"${report['total_cost_usd']:.6f}"
    if rub_rate is not None:
        rub_total = report["total_cost_usd"] * rub_rate
        total_cost_line += f" (~\u20bd{rub_total:.2f} at {rub_rate:.2f} \u20bd/$)"
    elif fx_rate is not None:
        fx_total = report["total_cost_usd"] * fx_rate
        total_cost_line += f" (~{fx_total:.2f} {currency} at {fx_rate:.2f} {currency}/$)"

    last_updated = pricing.get("last_updated")
    last_updated_line = (
        f"<p>pricing data last updated: {html.escape(str(last_updated))}</p>"
        if last_updated
        else ""
    )

    title = f"llm-burnwatch dashboard \u2014 {period_line[len('Period: '):]}"

    budget_block = _render_budget_block(budget_config, budget_status)
    active_detectors_block = _render_active_detectors(
        alerts,
        frequency_enabled=frequency_enabled,
        seasonal_available=seasonal_available,
        budget_config=budget_config,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; color: #1a1a1a; }}
.disclaimer {{ background: #fff8e1; border: 1px solid #e0c46c; padding: 0.75rem 1rem; border-radius: 6px; }}
.period {{ color: #555; font-weight: 600; }}
.card {{ display: inline-block; background: #f5f5f5; border-radius: 8px; padding: 1rem 1.5rem; margin: 0.5rem 1rem 0.5rem 0; }}
.card .value {{ font-size: 1.5rem; font-weight: bold; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.8rem; text-align: left; }}
th {{ background: #f0f0f0; }}
.bar {{ fill: #4c72b0; }}
.spark-line {{ stroke: #4c72b0; stroke-width: 1.5; }}
.day {{ border: 1px solid #ddd; border-radius: 6px; margin-bottom: 0.5rem; padding: 0 0.75rem; }}
.day summary {{
  display: grid;
  grid-template-columns: 110px 90px 90px 110px 1fr 90px 90px;
  gap: 0.75rem;
  align-items: center;
  cursor: pointer;
  padding: 0.6rem 0;
}}
.day-detail {{ padding: 0 0 0.75rem 0; }}
.alert-list {{ margin: 0.25rem 0; padding-left: 1.25rem; }}
.anomaly-badge.flagged {{ color: #b5341a; font-weight: 600; }}
.anomaly-badge.clean {{ color: #999; }}
.severity-badge.critical {{ color: #b5341a; font-weight: 600; }}
.severity-badge.warning {{ color: #b3811a; font-weight: 600; }}
.severity-badge.info {{ color: #4c72b0; }}
.severity-badge.clean {{ color: #999; }}
.budget-bar {{ background: #eee; border-radius: 6px; height: 14px; width: 100%; max-width: 400px; overflow: hidden; }}
.budget-bar-fill {{ height: 100%; }}
.budget-bar-fill.ok {{ background: #4c9a4c; }}
.budget-bar-fill.warn {{ background: #d1a52c; }}
.budget-bar-fill.over {{ background: #b5341a; }}
.budget-status.ok {{ color: #4c9a4c; }}
.budget-status.warn {{ color: #d1a52c; }}
.budget-status.over {{ color: #b5341a; font-weight: 600; }}
.budget-note {{ color: #888; font-size: 0.9rem; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #14161a; color: #e4e4e4; }}
  .card {{ background: #22252b; }}
  .day {{ border-color: #333; }}
  th {{ background: #2a2d33; }}
  th, td {{ border-color: #333; }}
  .period {{ color: #aaa; }}
  .budget-bar {{ background: #2a2d33; }}
}}
@media (max-width: 600px) {{
  .day summary {{
    display: flex;
    flex-wrap: wrap;
    column-gap: 0.75rem;
    row-gap: 0.15rem;
    font-size: 0.85rem;
  }}
  .day-date {{ order: 1; }}
  .day-cost {{ order: 2; margin-left: auto; }}
  .day-calls {{ order: 3; }}
  .day-bar {{ order: 4; }}
  .day-top-label {{ order: 5; }}
  .anomaly-badge {{ order: 6; margin-left: auto; }}
  .severity-badge {{ order: 7; margin-left: auto; }}
}}
</style>
</head>
<body>
<h1>llm-burnwatch dashboard</h1>
<p class="disclaimer">{html.escape(_DASHBOARD_DISCLAIMER)}</p>
<p class="period">{html.escape(period_line)}</p>
{last_updated_line}
<div class="card"><div>calls</div><div class="value">{report['call_count']}</div></div>
<div class="card"><div>total cost</div><div class="value">{total_cost_line}</div></div>
<div class="card"><div>baseline anomalies flagged</div><div class="value">{anomaly_count}</div></div>
{budget_block}
<h2>Totals for this period</h2>
<h3>By label</h3>
{_render_table_with_sparklines(report['by_label_micros'], cost_by_label, calls_by_label)}
<h3>By model</h3>
{_render_table_with_sparklines(report['by_model_micros'], cost_by_model, calls_by_model)}
{active_detectors_block}
<h2>Daily journal</h2>
{_render_journal(daily)}
</body>
</html>
"""
