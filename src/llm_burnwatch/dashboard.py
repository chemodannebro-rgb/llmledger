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
dependency, no external file/CDN reference, and no network call -- the
zero-dependency/no-network guarantee that applies to the rest of the core
CLI applies here too. The one relaxation (1.0.2): a small amount of inline
vanilla JavaScript powers table sorting, filtering, and copy-to-clipboard
-- still no external library, no CDN, no network call, so it's not
literally zero-script, but the "never leaves your machine" guarantee is
unchanged.
"""

from __future__ import annotations

import html
from typing import NamedTuple

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


class _FxConfig(NamedTuple):
    """Bundles the (mutually exclusive) currency-conversion options so
    every money-rendering helper below can take one argument instead of
    three, and so a dual-currency parenthetical is applied consistently
    everywhere a cost appears -- not just the top summary card, which is
    all the pre-1.0.2 code did.
    """

    rub_rate: float | None
    fx_rate: float | None
    currency: str | None


def _format_usd(amount: float) -> str:
    """Human-readable USD amount: thousands separator + 2 decimals for
    anything that still rounds to a nonzero value at that precision.
    Falls back to the old 6-decimal form *only* when 2 decimals would
    silently render a real, nonzero micro-cost as "$0.00" -- this tool's
    whole point is surfacing small per-call costs, so precision is never
    dropped, only hidden by default when it isn't needed to see it.
    """
    if amount == 0 or round(amount, 2) != 0:
        return f"${amount:,.2f}"
    return f"${amount:.6f}"


def _money_span(amount: float, fx: _FxConfig) -> str:
    """Inline money markup: `_format_usd()`'s readable amount, an optional
    dual-currency parenthetical when a rate is configured, and a
    copy-to-clipboard button carrying the exact (6-decimal) value -- so
    the full precision is always one click away even though it isn't the
    default on-screen text. Used standalone (summary cards, budget text,
    journal day summary) and inside table cells (see `_render_money_cell`).
    """
    display = _format_usd(amount)
    if fx.rub_rate is not None:
        display += f" (\u20bd{amount * fx.rub_rate:,.2f})"
    elif fx.fx_rate is not None:
        display += f" ({amount * fx.fx_rate:,.2f} {html.escape(str(fx.currency))})"
    full = f"{amount:.6f}"
    return (
        f'<span class="money">{display}'
        f'<button type="button" class="copy-btn" data-copy="{full}" '
        f'aria-label="Copy exact value" title="Copy exact value">\u29c9</button></span>'
    )


def _render_money_cell(amount: float, fx: _FxConfig) -> str:
    """`<td>` wrapper for `_money_span`, carrying the raw numeric amount in
    `data-sort-value` -- the sort JS needs to order rows numerically, and
    the formatted display string (thousands separator, currency symbol,
    RUB parenthetical) is not itself sortable as text.
    """
    return f'<td data-sort-value="{amount:.6f}">{_money_span(amount, fx)}</td>'


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
    from every detector (baseline included), for the per-day alert
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


def _top_label(by_label_micros: dict[str, int], fx: _FxConfig) -> str | None:
    if not by_label_micros:
        return None
    label, micros = max(by_label_micros.items(), key=lambda kv: kv[1])
    return f"{html.escape(label)} ({_money_span(micros / 1_000_000, fx)})"


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


def _render_table(rows: dict[str, int], fx: _FxConfig) -> str:
    if not rows:
        return "<p>No data.</p>"
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td>{_render_money_cell(micros / 1_000_000, fx)}</tr>"
        for key, micros in sorted(rows.items())
    )
    return (
        '<table class="data-table"><thead><tr>'
        '<th data-sort="text" aria-sort="none">Name</th>'
        '<th data-sort="num" aria-sort="none">Cost</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _render_table_with_sparklines(
    totals: dict[str, int],
    cost_series: dict[str, list[int]],
    call_series: dict[str, list[int]],
    fx: _FxConfig,
    table_id: str,
) -> str:
    if not totals:
        return "<p>No data.</p>"
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td>"
        f"{_render_money_cell(micros / 1_000_000, fx)}"
        f"<td>{_render_sparkline(cost_series.get(key, []))}</td>"
        f"<td>{_render_sparkline(call_series.get(key, []))}</td>"
        "</tr>"
        for key, micros in sorted(totals.items())
    )
    return (
        f'<input type="search" class="filter-input" data-filter-target="{table_id}" '
        f'placeholder="Filter by name\u2026" aria-label="Filter {html.escape(table_id)}">'
        f'<table class="data-table" id="{table_id}"><thead><tr>'
        '<th data-sort="text" aria-sort="none">Name</th>'
        '<th data-sort="num" aria-sort="none">Cost</th>'
        "<th>Cost trend</th><th>Calls trend</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _render_journal(daily: dict[str, dict], fx: _FxConfig) -> str:
    if not daily:
        return "<p>No dated records in this period.</p>"

    max_micros = max(day["cost_micros"] for day in daily.values())
    entries = []
    for date, day in sorted(daily.items(), reverse=True):
        top_label = _top_label(day["by_label_micros"], fx) or "\u2014"
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
            f'<span class="day-cost">{_money_span(day["cost_micros"] / 1_000_000, fx)}</span>'
            f'<span class="day-bar">{_render_day_bar(day["cost_micros"], max_micros)}</span>'
            f'<span class="day-top-label">{top_label}</span>'
            f'<span class="anomaly-badge {badge_class}">{anomaly_text}</span>'
            f'<span class="severity-badge {top_severity}">{sev_text}</span>'
            f"</summary>"
            f'<div class="day-detail">'
            f"<h4>By label</h4>{_render_table(day['by_label_micros'], fx)}"
            f"<h4>By model</h4>{_render_table(day['by_model_micros'], fx)}"
            f"{alerts_html}"
            f"</div>"
            f"</details>"
        )
    entries_html = "".join(entries)
    return (
        '<input type="search" class="filter-input" data-filter-target="journal-list" '
        'placeholder="Filter by date/label/model\u2026" aria-label="Filter daily journal">'
        f'<div id="journal-list">{entries_html}</div>'
    )


def _render_budget_block(
    budget_config: dict | None, budget_status: dict | None, fx: _FxConfig
) -> str:
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
            '<h2 id="budget">Budget</h2><p>'
            f"budget: configured ({_money_span(budget_config['monthly_usd'], fx)}/month) "
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
        '<h2 id="budget">Budget</h2>'
        f'<p>month: {html.escape(str(budget_status["month"]))} \u2014 '
        f'<span class="budget-status {status_class}">{status_text}</span></p>'
        f'<div class="budget-bar"><div class="budget-bar-fill {status_class}" '
        f'style="width:{fill_pct:.1f}%">{fill_pct:.0f}%</div></div>'
        f"<p>month-to-date: {_money_span(month_to_date, fx)} / projected month-end: "
        f"{_money_span(forecast, fx)} / budget: {_money_span(monthly_usd, fx)}</p>"
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
        f"<td>{html.escape(threshold)}</td><td data-sort-value=\"{count}\">{count}</td></tr>"
        for name, status, threshold, count in rows
    )
    return (
        '<h2 id="active-detectors">Active detectors</h2>'
        '<table class="data-table"><thead><tr>'
        '<th data-sort="text" aria-sort="none">Detector</th><th>Status</th>'
        '<th>Threshold</th><th data-sort="num" aria-sort="none">Alerts</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


_DASHBOARD_SCRIPT = """
(function () {
  "use strict";

  function sortValue(cell) {
    if (cell.hasAttribute("data-sort-value")) {
      return parseFloat(cell.getAttribute("data-sort-value"));
    }
    return cell.textContent.trim().toLowerCase();
  }

  document.addEventListener("click", function (e) {
    var th = e.target.closest("th[data-sort]");
    if (th) {
      var table = th.closest("table");
      var tbody = table && table.querySelector("tbody");
      if (!tbody) return;
      var headerRow = th.parentElement;
      var index = Array.prototype.indexOf.call(headerRow.children, th);
      var ascending = th.getAttribute("aria-sort") !== "ascending";
      Array.prototype.forEach.call(headerRow.children, function (other) {
        if (other !== th) other.setAttribute("aria-sort", "none");
      });
      th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
      var rows = Array.prototype.slice.call(tbody.children);
      rows.sort(function (a, b) {
        var av = sortValue(a.children[index]);
        var bv = sortValue(b.children[index]);
        if (av < bv) return ascending ? -1 : 1;
        if (av > bv) return ascending ? 1 : -1;
        return 0;
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
      return;
    }

    var copyBtn = e.target.closest(".copy-btn");
    if (copyBtn && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(copyBtn.getAttribute("data-copy")).then(function () {
        copyBtn.classList.add("copied");
        setTimeout(function () { copyBtn.classList.remove("copied"); }, 1500);
      }, function () {});
    }
  });

  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-filter-target]");
    if (!input) return;
    var target = document.getElementById(input.getAttribute("data-filter-target"));
    if (!target) return;
    var query = input.value.trim().toLowerCase();
    var items = target.tagName === "TABLE"
      ? target.querySelectorAll("tbody > tr")
      : target.children;
    Array.prototype.forEach.call(items, function (item) {
      var match = !query || item.textContent.toLowerCase().indexOf(query) !== -1;
      item.style.display = match ? "" : "none";
    });
  });
})();
"""


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
    supplied values. As of 1.0.2, whichever rate is given is shown next to
    *every* rendered cost, not just the top summary card.

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

    fx = _FxConfig(rub_rate=rub_rate, fx_rate=fx_rate, currency=currency)

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

    total_cost_span = _money_span(report["total_cost_usd"], fx)

    last_updated = pricing.get("last_updated")
    last_updated_line = (
        f"<p>pricing data last updated: {html.escape(str(last_updated))}</p>"
        if last_updated
        else ""
    )

    title = f"llm-burnwatch dashboard \u2014 {period_line[len('Period: '):]}"

    budget_block = _render_budget_block(budget_config, budget_status, fx)
    active_detectors_block = _render_active_detectors(
        alerts,
        frequency_enabled=frequency_enabled,
        seasonal_available=seasonal_available,
        budget_config=budget_config,
    )

    nav_links = []
    if budget_config is not None:
        nav_links.append('<a href="#budget">Budget</a>')
    nav_links.append('<a href="#totals">Totals</a>')
    nav_links.append('<a href="#active-detectors">Active detectors</a>')
    nav_links.append('<a href="#daily-journal">Daily journal</a>')
    section_nav = f'<nav class="section-nav">{"".join(nav_links)}</nav>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
  --accent: #4f46e5;
  --accent-soft: #eef2ff;
  --border: #e2e2e6;
  --surface: #ffffff;
  --surface-alt: #f7f7fa;
  --text: #1a1a1a;
  --text-muted: #666;
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0;
  padding: 1.5rem 2rem 3rem;
  color: var(--text);
  background: var(--surface);
  line-height: 1.5;
}}
h1 {{ font-size: 1.6rem; margin: 0.25rem 0 1rem; }}
h2 {{ font-size: 1.2rem; margin: 2rem 0 0.75rem; scroll-margin-top: 3.5rem; }}
h3 {{ font-size: 1rem; margin: 1.25rem 0 0.5rem; color: var(--text-muted); }}
h4 {{ font-size: 0.9rem; margin: 0.75rem 0 0.35rem; }}
.section-nav {{
  position: sticky; top: 0; z-index: 20;
  display: flex; flex-wrap: wrap; gap: 1.25rem;
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0.6rem 0; margin-bottom: 0.5rem;
}}
.section-nav a {{ color: var(--accent); text-decoration: none; font-size: 0.9rem; font-weight: 600; }}
.section-nav a:hover {{ text-decoration: underline; }}
.disclaimer {{ background: #fff8e1; border: 1px solid #e0c46c; padding: 0.75rem 1rem; border-radius: 8px; }}
.period {{ color: var(--text-muted); font-weight: 600; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.85rem; margin: 1rem 0; }}
.card {{
  background: var(--surface-alt); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.25rem;
}}
.card .value {{ font-size: 1.4rem; font-weight: 700; margin-top: 0.15rem; }}
.filter-input {{
  display: block; width: 100%; max-width: 320px; margin: 0.5rem 0 0.75rem;
  padding: 0.45rem 0.7rem; border: 1px solid var(--border); border-radius: 8px;
  font-size: 0.9rem; color: var(--text); background: var(--surface);
}}
table.data-table {{ border-collapse: separate; border-spacing: 0; margin: 0.25rem 0 1rem; width: 100%; max-width: 640px; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 0.5rem 0.9rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--surface-alt); font-weight: 600; position: sticky; top: 2.9rem; }}
th[data-sort] {{ cursor: pointer; user-select: none; }}
th[data-sort]::after {{ content: "\\2195"; opacity: 0.35; margin-left: 0.35rem; font-size: 0.8em; }}
th[aria-sort="ascending"]::after {{ content: "\\2191"; opacity: 1; color: var(--accent); }}
th[aria-sort="descending"]::after {{ content: "\\2193"; opacity: 1; color: var(--accent); }}
tbody tr:nth-child(even) {{ background: var(--surface-alt); }}
tbody tr:hover {{ background: var(--accent-soft); }}
tbody tr:last-child td {{ border-bottom: none; }}
.money {{ white-space: nowrap; }}
.copy-btn {{
  border: none; background: none; cursor: pointer; opacity: 0.45;
  font-size: 0.85em; margin-left: 0.3rem; padding: 0 0.15rem; color: inherit;
}}
.money:hover .copy-btn, .copy-btn:hover, .copy-btn:focus {{ opacity: 1; }}
.copy-btn.copied {{ opacity: 1; color: #16a34a; }}
.copy-btn.copied::after {{ content: " Copied"; font-size: 0.8em; }}
.bar {{ fill: var(--accent); }}
.spark-line {{ stroke: var(--accent); stroke-width: 1.5; }}
.day {{ border: 1px solid var(--border); border-radius: 10px; margin-bottom: 0.6rem; padding: 0 0.9rem; }}
.day summary {{
  display: grid;
  grid-template-columns: 110px 90px 130px 110px 1fr 90px 90px;
  gap: 0.75rem;
  align-items: center;
  cursor: pointer;
  padding: 0.65rem 0;
  list-style: none;
}}
.day summary::-webkit-details-marker {{ display: none; }}
.day summary::before {{
  content: "\\25B8"; display: inline-block; transition: transform 0.15s ease;
  color: var(--text-muted); width: 0.75rem;
}}
.day[open] summary::before {{ transform: rotate(90deg); }}
.day summary:hover {{ background: var(--accent-soft); border-radius: 6px; }}
.day-detail {{ padding: 0 0 0.85rem 0; }}
.alert-list {{ margin: 0.25rem 0; padding-left: 1.25rem; }}
.anomaly-badge, .severity-badge {{
  display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.82rem;
}}
.anomaly-badge.flagged {{ color: #b5341a; background: #fdeae4; font-weight: 600; }}
.anomaly-badge.clean {{ color: #888; background: var(--surface-alt); }}
.severity-badge.critical {{ color: #b5341a; background: #fdeae4; font-weight: 600; }}
.severity-badge.warning {{ color: #92660a; background: #fdf0d5; font-weight: 600; }}
.severity-badge.info {{ color: var(--accent); background: var(--accent-soft); }}
.severity-badge.clean {{ color: #888; background: var(--surface-alt); }}
.budget-bar {{ background: var(--surface-alt); border-radius: 999px; height: 18px; width: 100%; max-width: 400px; overflow: hidden; border: 1px solid var(--border); }}
.budget-bar-fill {{ height: 100%; font-size: 0.7rem; color: #fff; text-align: right; padding-right: 0.4rem; line-height: 18px; white-space: nowrap; }}
.budget-bar-fill.ok {{ background: #3a9a5c; }}
.budget-bar-fill.warn {{ background: #d1a52c; }}
.budget-bar-fill.over {{ background: #b5341a; }}
.budget-status.ok {{ color: #3a9a5c; }}
.budget-status.warn {{ color: #d1a52c; }}
.budget-status.over {{ color: #b5341a; font-weight: 600; }}
.budget-note {{ color: #888; font-size: 0.9rem; }}
@media (prefers-color-scheme: dark) {{
  :root {{
    --accent: #818cf8; --accent-soft: #23253a; --border: #333;
    --surface: #14161a; --surface-alt: #22252b; --text: #e4e4e4; --text-muted: #aaa;
  }}
  .disclaimer {{ background: #2a2410; border-color: #6b5a1e; }}
  .anomaly-badge.flagged, .severity-badge.critical {{ background: #3a1f18; }}
  .severity-badge.warning {{ background: #362c10; }}
}}
@media (max-width: 600px) {{
  body {{ padding: 1rem 1rem 2rem; }}
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
{section_nav}
<p class="disclaimer">{html.escape(_DASHBOARD_DISCLAIMER)}</p>
<p class="period">{html.escape(period_line)}</p>
{last_updated_line}
<div class="cards">
<div class="card"><div>calls</div><div class="value">{report['call_count']}</div></div>
<div class="card"><div>total cost</div><div class="value">{total_cost_span}</div></div>
<div class="card"><div>baseline anomalies flagged</div><div class="value">{anomaly_count}</div></div>
</div>
{budget_block}
<h2 id="totals">Totals for this period</h2>
<h3>By label</h3>
{_render_table_with_sparklines(report['by_label_micros'], cost_by_label, calls_by_label, fx, "by-label-table")}
<h3>By model</h3>
{_render_table_with_sparklines(report['by_model_micros'], cost_by_model, calls_by_model, fx, "by-model-table")}
{active_detectors_block}
<h2 id="daily-journal">Daily journal</h2>
{_render_journal(daily, fx)}
<script>{_DASHBOARD_SCRIPT}</script>
</body>
</html>
"""
