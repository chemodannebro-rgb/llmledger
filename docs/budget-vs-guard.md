# `budget` vs `guard()`

llm-burnwatch has two ways to think about "am I spending too much", solving
different problems. Neither replaces the other, and they compose freely.

| | `budget`/`BudgetDetector` | `CostTracker.guard()` |
|---|---|---|
| **Trade-off** | Detection | Enforcement |
| **Scope** | Cross-process — reads the whole log | In-process — only calls made in the current Python process |
| **Timescale** | Monthly (UTC calendar month) | Per `with` block (a single trace) |
| **Memory** | Persisted (`budget.json`, on disk) | In-memory only, forgotten when the block exits |
| **What happens on breach** | An alert (`budget_pace_warning`/`budget_exceeded`); nothing stops | `BudgetExceededError` raised from the `log_call()`/adapter call that crossed the limit |
| **Configured via** | `llm-burnwatch budget set --monthly <usd> --warn-at <fraction>` | `tracker.guard(max_usd_per_trace=..., max_calls_per_trace=...)` |

## `budget` — post-hoc, cross-process detection

```bash
llm-burnwatch budget set --monthly 100 --warn-at 0.8
```

Once set, `report` prints month-to-date spend and a linear-pace forecast,
and `detect` gains a `budget` alert kind. This tells you the *month* is
trending over budget across every process that writes to the log — but it
only ever tells you after the fact, on whatever cadence you run
`report`/`detect` (or `detect --follow`). Nothing here stops a call from
happening. See the [Budget detector](detectors/budget.md) page for the
forecast math.

## `guard()` — real-time, in-process enforcement

```python
tracker = CostTracker("calls.jsonl")

with tracker.guard(max_usd_per_trace=1.0, max_calls_per_trace=20) as trace_id:
    for step in agent_loop():
        response = openai_client.chat.completions.create(...)
        tracker.log_openai_response(response, label="agent-step", trace_id=trace_id)
        # BudgetExceededError is raised from the log_*() call that pushes this
        # trace over $1.00 or 20 calls, whichever comes first — the call that
        # tripped it is still logged (it already happened and already cost
        # money by the time log_call() runs); the exception is your signal to
        # stop the loop, not a way to undo that last call.
```

`guard()` stops a runaway agent loop the moment it goes over a limit — but
it's invisible across processes (two `CostTracker` instances sharing a
`trace_id`, even on the same machine, don't see each other) and it forgets
everything the instant the `with` block exits. It is not a daily/monthly
budget.

Only calls logged with a `trace_id` matching the active `guard()` block
count against it — pass the `trace_id` `guard()` yields you (or your own,
via `guard(trace_id=...)`) to every `log_call()`/adapter call inside the
block. Two `guard()` blocks with different `trace_id`s, even nested or
concurrent, track completely independent totals. At least one of
`max_usd_per_trace`/`max_calls_per_trace` must be given — calling `guard()`
with neither would silently enforce nothing.

## Using both together

A typical setup: `guard()` around each individual agent run to stop any
single run from spending past a per-run limit, plus `budget set` so you
also know when the *month* as a whole is trending over budget across every
run. Neither one substitutes for the other — `guard()` can't tell you
you're on pace to blow the monthly budget across many small, well-behaved
runs, and `budget`/`BudgetDetector` can't stop a single runaway loop before
it finishes.
