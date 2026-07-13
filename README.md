# llm-burnwatch

[![CI](https://github.com/chemodannebro-rgb/llm-burnwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/chemodannebro-rgb/llm-burnwatch/actions/workflows/ci.yml)

**A watchdog for what your LLM calls actually cost.**

It logs every call to a plain file on your own disk, learns what "normal"
looks like for your app, and tells you in plain language when something is
off — a runaway agent loop, a prompt change that quietly doubled your bill,
a model swap that shouldn't have happened. Nothing ever leaves your machine
unless you explicitly turn on an alert (Slack, Telegram, webhook).

[Русская версия](README.ru.md) · [Full documentation](docs/index.md)

![llm-burnwatch dashboard](docs/dashboard.png)

## What it does

- **Tracks cost** — every call, with cost, tokens, and your own label
  (`"summarize"`, `"chat"`, whatever makes sense for your app).
- **Learns your normal** — no setup, no training data to provide. It watches
  your log and figures out what a typical call for each part of your app
  looks like.
- **Flags what's off, in plain English** — "this call cost 20x more than
  usual", not a wall of statistics. Every alert says what happened and what
  to do next.
- **Can stop a runaway loop** — set a budget for a request and it raises an
  error the moment it's exceeded, instead of quietly burning money.
- **Stays on your machine** — one file, zero required dependencies, no
  account, no server. You can open the log file yourself and read every line.

## Is this for you?

**Yes**, if you're shipping an app or agent that calls an LLM and you want
to know what it costs and get warned when something's wrong — without
setting up a whole observability platform.

**Probably not**, if you need full prompt/response tracing and evals (try
[Langfuse](https://langfuse.com/)), or a request-routing proxy in front of
multiple providers (try [LiteLLM](https://www.litellm.ai/)). See
[docs/comparison.md](docs/comparison.md) for the honest breakdown.

## Install

```bash
pip install llm-burnwatch
```

## Five minutes to your first alert

**1. Log your calls.** Add one line after each LLM call:

```python
from llm_burnwatch import CostTracker

tracker = CostTracker()
tracker.log_call(
    label="summarize",
    model="gpt-4o-mini",
    input_tokens=812,
    output_tokens=143,
)
```

Already using the OpenAI, Anthropic, Gemini, or LangChain SDK? There's a
one-line adapter for each — see [docs/connecting.md](docs/connecting.md).

**2. Check how it's doing.**

```bash
llm-burnwatch status
```

This tells you, in plain words, what's being watched and what's still
warming up — nothing to configure first.

**3. See what you're spending.**

```bash
llm-burnwatch report
```

**4. Look for anomalies.**

```bash
llm-burnwatch detect
```

No log yet? Try it on synthetic data first:

```bash
llm-burnwatch demo-data --out demo.jsonl
llm-burnwatch detect --log-file demo.jsonl
```

**5. Want a visual view?**

```bash
llm-burnwatch dashboard --out dashboard.html
```

Opens as a single HTML file — no server, nothing to install.

## Going further

Once the basics are working, here's where to look:

| I want to... | Read this |
|---|---|
| Set a monthly budget and get warned before I go over | [docs/budget-vs-guard.md](docs/budget-vs-guard.md) |
| Stop a runaway agent loop in real time | [docs/budget-vs-guard.md](docs/budget-vs-guard.md) |
| Get alerts in Slack, Telegram, or my own webhook | [docs/connecting.md](docs/connecting.md) |
| Understand exactly how each detector decides something is anomalous | [docs/detectors/](docs/detectors) |
| Import cost data I already have (OpenTelemetry traces) | [docs/connecting.md](docs/connecting.md) |
| Know exactly what data ever leaves my machine, and when | [docs/security.md](docs/security.md) |
| See every command and flag | [docs/api.md](docs/api.md) |
| Compare this to Langfuse / LiteLLM / Helicone | [docs/comparison.md](docs/comparison.md) |
| Common questions (why didn't an alert fire, etc.) | [docs/faq.md](docs/faq.md) |

## The guarantee

The core of llm-burnwatch never makes a network call. Everything happens
on your disk. The only exceptions are things *you* explicitly turn on:
importing a pricing file from a URL, or sending an alert to a webhook/Slack/
Telegram/local command in `detect --follow`. Full details, including how
this is tested, in [docs/security.md](docs/security.md).

## Contributing

```bash
pip install -e ".[anomaly,dev]"
pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for what a PR needs.

See [CHANGELOG.md](CHANGELOG.md) for version history.
