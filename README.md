# llm-burnwatch

[![CI](https://github.com/chemodannebro-rgb/llm-burnwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/chemodannebro-rgb/llm-burnwatch/actions/workflows/ci.yml)

> Early stage — API may change before v1.0.

Local, zero-dependency cost tracking and statistical anomaly detection for
LLM/agent calls. Logs go to a plain JSONL file on your own disk; nothing
leaves the machine.

**Built for:** a solo developer shipping their own LLM-powered feature who
wants cost/anomaly visibility without standing up a full observability
platform — whether that's a single JSONL file on one machine, or a small
team's calls merged from multiple processes via directory mode.

A `dashboard` command also turns that log into a single static HTML file
(no JS, no external services) — see the CLI table below.

![llm-burnwatch dashboard: summary cards, cost totals by label/model, and an
expandable daily journal with anomaly badges](docs/dashboard.png)

## Quickstart

```bash
pip install llm-burnwatch
```

Or from a local clone (e.g. to hack on it):

```bash
git clone <this-repo> && cd llm-burnwatch
pip install -e .
```

```python
from llm_burnwatch.tracker import CostTracker

tracker = CostTracker("calls.jsonl")
tracker.log_call(
    label="summarize",
    model="gpt-4o-mini",
    input_tokens=812,
    output_tokens=143,
)
print(tracker.report())
```

```bash
llm-burnwatch report --log-file calls.jsonl
```

That's the whole core: no scikit-learn, no database, one JSONL file you can
read yourself.

## When NOT to use llm-burnwatch

- **You need full request/response traces or LLM-specific evals** (prompt
  diffing, golden datasets, human/LLM-graded scoring) — llm-burnwatch only
  records cost/token metadata per call, not the prompt or completion text.
  Look at [Langfuse](https://langfuse.com/) instead.
- **You need a request-routing proxy** (load balancing across API keys/
  providers, centralized rate limiting, a unified OpenAI-compatible
  endpoint in front of multiple providers) — llm-burnwatch doesn't sit in the
  request path at all, it's a logging call you add after the fact. Look at
  [LiteLLM](https://www.litellm.ai/)'s proxy instead (llm-burnwatch's own
  `pricing import` command happens to reuse LiteLLM's *pricing data
  format*, but that's the only connection between the two projects).
- **You need email or a full notification platform** — `detect --follow`
  ships webhook, Slack, Telegram, and local-command (exec) sinks (see
  [System boundaries](#system-boundaries) below), but nothing beyond that;
  for anything else, `detect`'s exit code and `--json` output are still
  meant to be wired into your own cron/CI/monitoring.

## SDK adapters

If you're calling an SDK directly, `CostTracker` has an adapter per
provider that reads usage straight off the response object (dict or
attribute-style) — no need to add the provider's SDK as a dependency of
`llm-burnwatch`, and no need to hand-map fields yourself:

```python
# OpenAI
response = openai_client.chat.completions.create(...)
tracker.log_openai_response(response, label="chat")

# Anthropic
response = anthropic_client.messages.create(...)
tracker.log_anthropic_response(response, label="chat")

# Gemini (google-genai)
response = gemini_client.models.generate_content(...)
tracker.log_gemini_response(response, label="chat")

# Ollama — local models usually have no pricing.json entry, so pass cost=0.0
# (or your own pricing=); only pass the final chunk if you're streaming.
response = ollama_client.chat(...)
tracker.log_ollama_response(response, label="chat", cost=0.0)
```

Each adapter accounts for that provider's own cache-token billing rules
(subset vs. additive counters) so `cached_input_tokens` always means "billed
at the cheaper cached rate", regardless of provider.

If the packaged `pricing.json` is missing a model or has a stale rate, pass
point overrides instead of hand-copying the whole file:

```python
tracker = CostTracker(
    "calls.jsonl",
    pricing_overrides={"my-model": {"input_per_1m": 3.0, "output_per_1m": 9.0}},
)
```

`pricing_overrides` is merged on top of the packaged defaults (everything
else stays as shipped); pass `pricing=` instead if you want to replace the
whole pricing table — the two are mutually exclusive.

## Installation

```bash
pip install -e .                       # core only: report, demo-data, detect (baseline), schema, dashboard
pip install -e ".[anomaly]"            # + train (IsolationForest, requires scikit-learn and skops)
```

## CLI

| Command | What it does | Exit code |
|---|---|---|
| `llm-burnwatch report --log-file <path> [--fx-rate <rate> --currency <code>] [--since <date>] [--until <date>] [--trace-id <id>] [--json \| --format csv]` | Cost summary (total, by label, by model); `--fx-rate`/`--currency` also shows the total converted to that currency at a fixed, manually-supplied rate; `--trace-id` narrows to one request's calls; `--json` prints a machine-readable summary, `--format csv` prints a normalized 3-column CSV (`dimension,key,cost_usd`) instead — the two are mutually exclusive | `0` |
| `llm-burnwatch demo-data --out <path>` | Write a synthetic log with known injected anomalies | `0` |
| `llm-burnwatch detect --log-file <path> [--model-dir <dir>] [--json] [--follow [--poll-interval <secs>]]` | Baseline (+ ML if a trained model exists) anomaly detection; `--follow` streams newly triggered alerts instead of a one-shot report (see [below](#detect---follow)) | `0` clean, `1` anomalies found, `2` error |
| `llm-burnwatch train --log-file <path> --model-dir <dir>` | Train an IsolationForest model (`[anomaly]` extra) | `0` / `2` error |
| `llm-burnwatch schema` | Print the JSONL log schema (`schema.json`) | `0` |
| `llm-burnwatch validate --log-file <path> [--json]` | Check every record against the packaged schema (required fields, types, `minLength`/`minimum`, no unexpected fields) — dependency-free, doesn't use `jsonschema` | `0` clean, `1` invalid records found, `2` error |
| `llm-burnwatch dashboard --log-file <path> --out <path.html> [--fx-rate <rate> --currency <code>] [--since <date>] [--until <date>]` | Static single-file HTML report with a daily journal | `0` / `2` error |
| `llm-burnwatch pricing import <file\|url>` | Import pricing from a local file or an `http(s)://` URL in LiteLLM's `model_prices_and_context_window.json` format, saved to `~/.config/llm-burnwatch/pricing.json` | `0` / `2` error |
| `llm-burnwatch budget set --monthly <usd> --warn-at <0..1>` | Set a monthly USD budget and an early-warning fraction, saved to `~/.config/llm-burnwatch/budget.json` | `0` / `2` error |
| `llm-burnwatch budget show` | Print the currently configured budget (or say none is set) | `0` |

`report`/`dashboard`/`detect` all accept `--pricing-file <path>` to use a
one-off pricing file for that single run. Absent that flag, pricing is
resolved in this order: the file written by `pricing import` (if any), then
the packaged default. `pricing import` is the **only** llm-burnwatch command
that ever makes a network call, and only when given an `http(s)://` URL (a
local file path never touches the network) — see
[`SECURITY.md`](SECURITY.md#pricing-import-url-network-trust-boundary) for
what that does and doesn't protect against, and only import from a source
you trust.

Don't have a pricing file handy? LiteLLM maintains a community-updated one
in this exact format at
[`model_prices_and_context_window.json`](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json)
— that's a third-party file llm-burnwatch has no control over, so treat it
like any other import source: `llm-burnwatch pricing import
https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`.

`--fx-rate`/`--currency` never fetches an exchange rate over the network —
you supply the rate yourself, e.g. `llm-burnwatch report --log-file calls.jsonl
--fx-rate 90 --currency RUB`. The older `--rub-rate <rate>` flag still works
as a RUB-only shorthand for this but is deprecated and will be removed
before v1.0.

`--since`/`--until` (both `YYYY-MM-DD`, inclusive) restrict `report` and
`dashboard` to records whose UTC calendar date falls in that range; records
with a missing or unparseable `timestamp` are excluded whenever either bound
is given.

### Budget tracking (detection, not enforcement)

`llm-burnwatch budget set --monthly 100 --warn-at 0.8` records a monthly USD
budget and an early-warning fraction; once set, `report` prints a `budget:`
section (month-to-date spend, a linear-pace forecast for month-end, and a
status of "within budget" / "on pace to exceed" / "budget exceeded"), and
`detect` gains a `budget` alert kind (`budget_pace_warning` /
`budget_exceeded`) alongside the statistical detectors. Neither section
appears at all until `budget set` has been run — no "budget: not configured"
noise for scripts parsing this output. The forecast is a simple linear
extrapolation ("month-to-date spend / days elapsed so far × days in month"),
flagged as low-confidence for the first few days of a month, when there's too
little data for the projection to mean much.

This is **detection, not enforcement** — `budget`/`report`/`detect` only
tell you the month is trending over budget; nothing here stops a call from
happening or throws partway through a request. Wire `detect`'s exit code
into your own alerting the same way you would for any other anomaly, same
as the rest of this section.

### Enforcing a spend limit in-process (`CostTracker.guard()`)

`budget`/`BudgetDetector` above is post-hoc, cross-process, month-long
*detection*. `CostTracker.guard()` is the opposite trade-off: real-time,
in-process, per-`with`-block *enforcement* — it stops a runaway loop the
moment it goes over, but forgets everything the instant the block exits and
is invisible across processes:

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

Only calls logged with a `trace_id` matching the active `guard()` block
count against it — pass the `trace_id` `guard()` yields you (or your own,
via `guard(trace_id=...)`) to every `log_call()`/adapter call inside the
block. Two `guard()` blocks with different `trace_id`s (even nested, even
concurrent) track completely independent totals. `guard()` and
`budget`/`BudgetDetector` solve different problems and compose freely —
neither replaces the other.

Exit codes are the integration contract for one-shot `detect`. `detect
--follow` additionally supports pushing each newly triggered alert to a
webhook, Slack, Telegram, or a local command (see
[System boundaries](#system-boundaries) below) — still no email or a full
notification platform, though. Wire the exit code and/or `--json` output of
one-shot `detect` into cron, CI, or your own alerting.

Try it end to end:

```bash
llm-burnwatch demo-data --out data/sample_logs.jsonl
llm-burnwatch detect --log-file data/sample_logs.jsonl          # baseline only
llm-burnwatch train --log-file data/sample_logs.jsonl --model-dir models
llm-burnwatch detect --log-file data/sample_logs.jsonl --model-dir models   # + ML cross-check
llm-burnwatch dashboard --log-file data/sample_logs.jsonl --out dashboard.html
```

This generates a local model registry under `models/` in a few seconds — it
isn't committed to the repository (a trained model binary as a trusted
public artifact is a supply-chain liability; see `SECURITY.md`).

## Anomaly detection

Two independent, complementary layers:

- **Baseline** (always available, no dependencies): a robust modified
  z-score (Iglewicz & Hoaglin) on `input_tokens`/`output_tokens`/
  `cost_micros`/`cached_input_tokens`, compared against the history of the
  same `(label, model)` pair — median/MAD rather than mean/stdev, so
  pre-existing outliers in the history don't mask new ones. Degrades
  gracefully (group → model → "not enough data yet") instead of guessing on
  too little history.
- **ML cross-check** (optional, `[anomaly]` extra): an `IsolationForest`
  trained on the same group-relative features, used as a second opinion
  when a model exists. `detect` also compares current per-group statistics
  against the ones recorded at training time and warns if they've drifted
  apart, as a signal that `llm-burnwatch train` should be re-run.

Both are diagnostic aids: they flag statistically unusual calls, they don't
confirm errors, and they can miss real ones. `report`/`detect` print this
disclaimer, plus the pricing data's `last_updated` date, on every run.

### `detect --follow`

```bash
llm-burnwatch detect --log-file data/calls.jsonl --follow --poll-interval 5
```

Instead of a single one-shot report, `--follow` polls `--log-file` every
`--poll-interval` seconds (default `5`), re-analyzes a fixed-size rolling
window of the most recently seen records, and prints each **newly**
triggered alert as one JSON object per line to stdout as soon as it's found
— a different, streaming output format from the one-shot `--json` payload
(passing both together prints a warning and `--json` is ignored). Each
alert line looks like:

```json
{"detector": "rules", "severity": "critical", "kind": "call_cost_exceeded", "group_key": [null, null], "record_ref": 5, "message": "...", "evidence": {...}}
```

Progress (byte offset already read per file, plus the current window) is
saved to `<log-file>.llm-burnwatch-follow-state.json`, next to the log, so
stopping and restarting `--follow` resumes instead of re-scanning the whole
log. A missing state file is a normal first run (no warning); a corrupted or
malformed one is never fatal — `--follow` warns and starts over from the
beginning of the log rather than crashing. Runs until interrupted
(<kbd>Ctrl</kbd>+<kbd>C</kbd>), then exits `0`.

Known limitations, by design:
- Only reads new lines appended to the file(s) at their current path/name;
  rotated backups (`calls.jsonl.1`, `calls.jsonl.2`, ...) are not read while
  following — restart `--follow` after rotation if you need to catch up on
  a backup file's tail. Truncation of the file at its current name (e.g. a
  writer that reopens it in truncate mode) is detected and handled by
  resuming from byte `0`.
- The ML cross-check and the log-wide label-cardinality warning that
  one-shot `detect` prints are not run in `--follow` mode (the former
  reloads a model from disk on every poll, the latter would repeat almost
  identically every poll) — only the baseline/frequency/rules detectors run.
- An alert whose evidence points at an older record already surfaced in a
  previous poll is not re-printed, even if re-analyzing the window is what
  produced it again this time.

## Log format

Each line of the log is one JSON object; the full contract (required
fields, types, optional fields like `cached_input_tokens`/`trace_id`) is
`src/llm_burnwatch/schema.json`, also available via `llm-burnwatch schema`. This is
the source of truth for any non-Python client (Node.js, Go, ...) that wants
to write a compatible log — every record also carries `schema_version` for
future format changes, plus a UTC `timestamp` (ISO 8601) of when the call
happened.

Every record needs a `label` (your own name for the call site, e.g.
`"retrieval"`/`"summarize"`) and a `model` identifier as billed, alongside
`input_tokens`/`output_tokens`/`cost_micros`. An optional free-form `extra`
object lets you attach your own metadata (e.g. `workflow_id`) without
changing the schema.

`cost_micros` is an integer (1 micro = $0.000001), not a float dollar
amount, to avoid rounding a $0.0025 call down to $0.00 and to avoid
float-accumulation drift when summing a large log.

Reasoning tokens (o1/o3-style models) aren't a separate field — bill them
into `output_tokens`, at the same rate.

## Scale and rotation

A single log file rotates via `max_bytes`/`backup_count` on `CostTracker`
(stdlib `RotatingFileHandler`). For multiple processes writing
concurrently, point `log_file` at a directory instead of a file — each
process gets its own file, and `report`/`detect` read and merge the whole
directory. If a single `detect`/`report` call reads more than ~200k
records from one non-rotated, non-directory file, it prints a warning
recommending one of the above.

## System boundaries

`llm-burnwatch`'s zero-dependency core reads and writes local files and
prints to stdout/stderr, and none of `report`/`demo-data`/`schema`/
`validate`/`dashboard`/`detect`/`train`/`budget` make a network call. There are two
opt-in exceptions, both off by default: `llm-burnwatch pricing import <url>`,
which fetches a pricing file over `http(s)://` only when you explicitly run
that command with a URL (a local file path never touches the network); and
`detect --follow --webhook-url`/`--slack-webhook-url`/
`--telegram-bot-token`+`--telegram-chat-id`, which sends each newly
triggered alert to a URL/chat you supply, only in `--follow` mode.
`detect --follow --exec-sink <command...>` is a related, non-network
exception that runs a local command you specify instead. Neither ever runs
implicitly, and one-shot `detect` never touches any of them — its exit code
and `--json` output remain the way to wire llm-burnwatch into your own cron
job / CI step / monitoring system beyond what these built-in sinks cover.

This no-network-calls guarantee is scoped to the core commands listed above
and is checked by `test_core_commands_make_no_network_attempts`
(`tests/test_cli.py`), which patches `socket.socket` to raise if any of them
tries to open one. It's a guarantee about the core, not a permanent ban on
network access anywhere in the project: like `pricing import`'s opt-in
fetch, the webhook/Slack/Telegram sinks are pure stdlib (`urllib.request`)
and so don't need a new pip extra the way the `[anomaly]` extra
(scikit-learn/skops) does — they're an opt-in *network boundary*, documented
as such, not a new dependency. See `ARCHITECTURE.md`'s "Network boundaries"
section for the full policy on adding such exceptions.

See [`SECURITY.md`](SECURITY.md) for the model registry's trust boundary,
the `pricing import` and alert-sinks network trust boundaries, and how to
report a vulnerability.

See [`CHANGELOG.md`](CHANGELOG.md) for version history and
[`PRICING_CHANGELOG.md`](PRICING_CHANGELOG.md) for `pricing.json`'s own
history. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for why the core package
has zero dependencies and how the `[anomaly]`/`[dev]` extras are split out.

## Development

```bash
pip install -e ".[anomaly,dev]"
pytest tests/ -v
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for what a PR needs (tests,
docs, the dashboard screenshot rule).
