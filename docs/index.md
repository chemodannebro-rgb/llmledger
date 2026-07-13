# Quickstart

llm-burnwatch tracks the cost of every LLM/agent call your code makes,
writes it to a plain JSONL file on your own disk, and flags anomalies
(runaway loops, cost spikes, model swaps) — with **zero required
dependencies** and **no network calls** in its core. See
[Security model](security.md) for exactly what that guarantee covers.

## Install

```bash
pip install llm-burnwatch
```

## Log your first call

```python
from llm_burnwatch import CostTracker

tracker = CostTracker("calls.jsonl")
tracker.log_call(
    label="summarize",
    model="gpt-4o-mini",
    input_tokens=800,
    output_tokens=150,
)
```

If you're already calling an OpenAI/Anthropic/Gemini/Ollama SDK, use the
matching adapter instead of computing tokens yourself — see
[Connecting to an existing app](connecting.md).

## See what it cost

```bash
llm-burnwatch report --log-file calls.jsonl
```

## Get your first alert

You don't need real traffic to see the anomaly detection work — generate a
synthetic log with a few injected anomalies and detect them:

```bash
llm-burnwatch demo-data --out demo.jsonl
llm-burnwatch detect --log-file demo.jsonl
```

`detect` exits `1` if it found anything (anomalies, rule violations,
frequency spikes, level shifts, budget alerts) and `0` otherwise — script
it into CI or a cron job. For a live-tailing version that keeps running
and can push alerts to Slack/Telegram/a webhook/a local command as they
happen, see `detect --follow` in the main README.

## See it as a dashboard

```bash
llm-burnwatch dashboard --log-file demo.jsonl --out dashboard.html
open dashboard.html
```

One self-contained HTML file — no server, no build step, works from
`file://`. Sortable/filterable tables and copy-to-clipboard are powered by
a small amount of inline vanilla JS (no CDN, no network call — see
[Security model](security.md)).

## Where to go next

- Already have an app making LLM calls? [Connecting to an existing app](connecting.md).
- Want to know what each detector actually catches and how to tune it? [Detectors](detectors/baseline.md).
- Deciding between `budget` and `guard()`? [budget vs guard()](budget-vs-guard.md).
- Wondering if this is even the right tool? [Comparison](comparison.md) and the [FAQ](faq.md).
