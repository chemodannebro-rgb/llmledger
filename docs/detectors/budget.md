# Budget detector

**Catches:** month-to-date spend that has already exceeded your configured
monthly budget, or is on pace to exceed it by month-end. Like the
[rules detector](rules.md), this is your own explicit policy, not a
statistical threshold.

**Always available, no dependencies. Disabled by default** — stays a
deliberate no-op until you've actually run `budget set`; `detect` only
turns it on once `budget.json` exists.

## Setting a budget

```bash
llm-burnwatch budget set --monthly 100 --warn-at 0.8
llm-burnwatch budget show
```

This persists a monthly USD budget and an early-warning fraction to
`~/.config/llm-burnwatch/budget.json`. Once set:

- `report` prints a `budget:` section — month-to-date spend, a
  linear-pace forecast for month-end, and a status of "within budget" /
  "on pace to exceed" / "budget exceeded".
- `detect` gains `budget_pace_warning` (severity `warning`) and
  `budget_exceeded` (severity `critical`) alert kinds alongside the
  statistical detectors.

Neither section appears at all until `budget set` has been run — no
"budget: not configured" noise for scripts parsing this output.

## The forecast

Deliberately simple: sum `cost_micros` for every record whose timestamp
falls in the current UTC calendar month, then linearly extrapolate
`month-to-date total / days elapsed so far × days in month` to a
projected month-end total. This does **not** reuse the frequency
detector's seasonal (weekday × hour) baselines — that answers a different
question ("is this hour unusual?"), not "will this month exceed budget at
the current pace?".

Below **3 elapsed days** in the current month
(`LOW_CONFIDENCE_DAY_THRESHOLD`), the forecast is flagged `low_confidence`
in both the alert message and `report`'s output — too little data for the
extrapolation to mean much, surfaced rather than hidden.

If a budget is configured but the log has no records yet in the current
UTC calendar month, `report`'s text output prints a single line instead of
the full section: `budget: configured ($100.00/month) — no records this
month yet` — so "not configured" and "configured, nothing to report yet"
stay distinguishable. `--json` output is unaffected either way: the
`"budget"` key is only ever present when there's an actual month-to-date
status to report.

## Detection vs. enforcement

This is **detection, not enforcement** — `budget`/`report`/`detect` only
tell you the month is trending over budget; nothing here stops a call from
happening or throws partway through a request. If you want an in-process
loop to actually stop the instant it goes over a limit, see
[`CostTracker.guard()`](../budget-vs-guard.md) — a different, complementary
tool for a different trade-off (real-time, per-trace enforcement vs.
month-long, cross-process detection).

## Known limitations

- Month boundaries are UTC calendar months, not a rolling 30-day window or
  your local timezone's month.
- The forecast is linear — it doesn't account for known future spend
  patterns (e.g. a planned batch job later in the month).
- Cross-process by nature (it reads the whole log), so it can't stop an
  individual call the way `guard()` can — by the time `detect`/`report`
  sees a record, the money's already been spent.
