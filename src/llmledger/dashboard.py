"""Static, single-file HTML cost dashboard.

`render_dashboard()` turns a list of log records into a self-contained HTML
document: a period-scoped summary (total/by-label/by-model, reusing
`tracker.build_report`) plus a "daily journal" -- one expandable entry per
UTC calendar day showing that day's calls/cost/top label/anomaly count, with
a per-day by-label/by-model breakdown inside. Anomaly counts reuse
`anomaly.baseline.analyze`, which never imports scikit-learn. No new
third-party dependency, no external file/CDN reference, no network call, and
no JavaScript (day entries use native `<details>`/`<summary>`) -- the whole
zero-dependency/no-network guarantee that applies to the rest of the core
CLI applies here too.
"""

from __future__ import annotations

import html

from ._messages import warn
from .anomaly.baseline import analyze
from .logreader import parse_date
from .tracker import build_report

_DAY_BAR_WIDTH = 100
_DAY_BAR_HEIGHT = 14

# Duplicated from cli.DISCLAIMER rather than imported from it, to avoid a
# circular import (cli.py imports this module for cmd_dashboard).
_DASHBOARD_DISCLAIMER = (
    "llmledger is a diagnostic aid, not a guarantee: it flags statistically "
    "unusual calls, it does not confirm they are errors, and it may miss "
    "real ones. Always use your own judgement before acting on its output."
)


def _daily_breakdown(records: list[dict], analyses: list) -> dict[str, dict]:
    """Group records by UTC calendar date. Returns a date-sorted (ascending)
    dict: `"YYYY-MM-DD" -> {call_count, cost_micros, by_label_micros,
    by_model_micros, anomaly_count}`.

    Records with a missing/invalid `timestamp` are excluded from the journal
    (but still counted in the overall totals via `build_report`), with a
    single `warn()` for the whole skip, not one per record.
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
                "anomaly_count": 0,
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

    for a in analyses:
        if a.status != "anomaly":
            continue
        date = parse_date(a.record.get("timestamp"))
        if date is not None and date in by_date:
            by_date[date]["anomaly_count"] += 1

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


def _render_table(rows: dict[str, int]) -> str:
    if not rows:
        return "<p>No data.</p>"
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>${micros / 1_000_000:.6f}</td></tr>"
        for key, micros in sorted(rows.items())
    )
    return f"<table><thead><tr><th>Name</th><th>Cost</th></tr></thead><tbody>{body}</tbody></table>"


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
        entries.append(
            f'<details class="day">'
            f'<summary>'
            f'<span class="day-date">{html.escape(date)}</span>'
            f'<span class="day-calls">{day["call_count"]} calls</span>'
            f'<span class="day-cost">${day["cost_micros"] / 1_000_000:.6f}</span>'
            f'<span class="day-bar">{_render_day_bar(day["cost_micros"], max_micros)}</span>'
            f'<span class="day-top-label">{top_label}</span>'
            f'<span class="anomaly-badge {badge_class}">{anomaly_text}</span>'
            f"</summary>"
            f'<div class="day-detail">'
            f"<h4>By label</h4>{_render_table(day['by_label_micros'])}"
            f"<h4>By model</h4>{_render_table(day['by_model_micros'])}"
            f"</div>"
            f"</details>"
        )
    return "".join(entries)


def render_dashboard(
    records: list[dict],
    pricing: dict,
    *,
    rub_rate: float | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Render a self-contained HTML dashboard for `records`, priced with
    `pricing`. If `rub_rate` is given, also show the total converted to RUB
    at that fixed, manually-supplied rate (never fetched over the network).

    `since`/`until` are assumed to have already been applied to `records` by
    the caller (see `logreader.filter_by_period`) -- they are only used here
    to display the period the dashboard covers.
    """
    report = build_report(records, pricing)
    analyses = analyze(records) if records else []
    daily = _daily_breakdown(records, analyses)
    anomaly_count = sum(1 for a in analyses if a.status == "anomaly")

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

    last_updated = pricing.get("last_updated")
    last_updated_line = (
        f"<p>pricing data last updated: {html.escape(str(last_updated))}</p>"
        if last_updated
        else ""
    )

    title = f"llmledger dashboard \u2014 {period_line[len('Period: '):]}"

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
.day {{ border: 1px solid #ddd; border-radius: 6px; margin-bottom: 0.5rem; padding: 0 0.75rem; }}
.day summary {{
  display: grid;
  grid-template-columns: 110px 90px 90px 110px 1fr 90px;
  gap: 0.75rem;
  align-items: center;
  cursor: pointer;
  padding: 0.6rem 0;
}}
.day-detail {{ padding: 0 0 0.75rem 0; }}
.anomaly-badge.flagged {{ color: #b5341a; font-weight: 600; }}
.anomaly-badge.clean {{ color: #999; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #14161a; color: #e4e4e4; }}
  .card {{ background: #22252b; }}
  .day {{ border-color: #333; }}
  th {{ background: #2a2d33; }}
  th, td {{ border-color: #333; }}
  .period {{ color: #aaa; }}
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
}}
</style>
</head>
<body>
<h1>llmledger dashboard</h1>
<p class="disclaimer">{html.escape(_DASHBOARD_DISCLAIMER)}</p>
<p class="period">{html.escape(period_line)}</p>
{last_updated_line}
<div class="card"><div>calls</div><div class="value">{report['call_count']}</div></div>
<div class="card"><div>total cost</div><div class="value">{total_cost_line}</div></div>
<div class="card"><div>baseline anomalies flagged</div><div class="value">{anomaly_count}</div></div>
<h2>Totals for this period</h2>
<h3>By label</h3>
{_render_table(report['by_label_micros'])}
<h3>By model</h3>
{_render_table(report['by_model_micros'])}
<h2>Daily journal</h2>
{_render_journal(daily)}
</body>
</html>
"""
