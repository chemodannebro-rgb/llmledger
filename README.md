# llmledger

> Portfolio / demo engineering project. Not a commercial product — no
> support, no SLA, use at your own risk.

Local, zero-dependency cost tracking and statistical anomaly detection for
LLM/agent calls. Logs go to a plain JSONL file on your own disk; nothing
leaves the machine.

## Quickstart

```bash
pip install llmledger
```

```python
from llmledger.tracker import CostTracker

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
llmledger report --log-file calls.jsonl
```

That's the whole core: no scikit-learn, no database, one JSONL file you can
read yourself.

## Installation

```bash
pip install llmledger                  # core only: report, demo-data, detect (baseline), schema
pip install "llmledger[anomaly]"       # + train (IsolationForest, requires scikit-learn)
```

## CLI

| Command | What it does | Exit code |
|---|---|---|
| `llmledger report --log-file <path>` | Cost summary (total, by label, by model) | `0` |
| `llmledger demo-data --out <path>` | Write a synthetic log with known injected anomalies | `0` |
| `llmledger detect --log-file <path> [--model-dir <dir>] [--json]` | Baseline (+ ML if a trained model exists) anomaly detection | `0` clean, `1` anomalies found, `2` error |
| `llmledger train --log-file <path> --model-dir <dir>` | Train an IsolationForest model (`[anomaly]` extra) | `0` / `2` error |
| `llmledger schema` | Print the JSONL log schema (`schema.json`) | `0` |

Exit codes are the entire integration contract — `llmledger` never sends
notifications itself (no Slack/email/webhook integration, and no such
dependency in `pyproject.toml`). Wire the exit code and/or `--json` output
of `detect` into cron, CI, or your own alerting.

Try it end to end:

```bash
llmledger demo-data --out data/sample_logs.jsonl
llmledger detect --log-file data/sample_logs.jsonl          # baseline only
llmledger train --log-file data/sample_logs.jsonl --model-dir models
llmledger detect --log-file data/sample_logs.jsonl --model-dir models   # + ML cross-check
```

A working example registry trained this way is committed at `models/v1/`.

## Anomaly detection

Two independent, complementary layers:

- **Baseline** (always available, no dependencies): a robust modified
  z-score (Iglewicz & Hoaglin) on `input_tokens`/`output_tokens`/
  `cost_micros`, compared against the history of the same `(label, model)`
  pair — median/MAD rather than mean/stdev, so pre-existing outliers in the
  history don't mask new ones. Degrades gracefully (group → model → "not
  enough data yet") instead of guessing on too little history.
- **ML cross-check** (optional, `[anomaly]` extra): an `IsolationForest`
  trained on the same group-relative features, used as a second opinion
  when a model exists. `detect` also compares current per-group statistics
  against the ones recorded at training time and warns if they've drifted
  apart, as a signal that `llmledger train` should be re-run.

Both are diagnostic aids: they flag statistically unusual calls, they don't
confirm errors, and they can miss real ones. `report`/`detect` print this
disclaimer, plus the pricing data's `last_updated` date, on every run.

## Log format

Each line of the log is one JSON object; the full contract (required
fields, types, optional fields like `cached_input_tokens`/`trace_id`) is
`src/llmledger/schema.json`, also available via `llmledger schema`. This is
the source of truth for any non-Python client (Node.js, Go, ...) that wants
to write a compatible log — every record also carries `schema_version` for
future format changes.

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

`llmledger` reads and writes local files and prints to stdout/stderr. It
never makes a network call, never sends a notification, and has no
optional dependency that would let it (no `requests`, no Slack SDK, etc.).
Any alerting on top of `detect`'s exit code or `--json` output is your own
cron job / CI step / monitoring system to build.

## Development

```bash
pip install -e ".[anomaly,dev]"
pytest tests/ -v
```
