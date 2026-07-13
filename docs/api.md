# Public API

This page is the one place that says, explicitly, what's covered by
llm-burnwatch's semver commitments (see
[Versioning](https://github.com/chemodannebro-rgb/llm-burnwatch/blob/main/CONTRIBUTING.md#versioning)
in `CONTRIBUTING.md`) and what isn't. If it's not listed here, it's
internal: it can change shape or disappear in a minor/patch release
without a deprecation warning, even if you can technically import it
today.

## Python API

Everything importable from the top-level package:

```python
from llm_burnwatch import CostTracker, BudgetExceededError, __version__
```

### `CostTracker`

```python
CostTracker(
    log_file=None,          # defaults to default_log_path() if omitted
    *,
    pricing=None,            # full pricing table replacement
    pricing_overrides=None,  # point overrides on top of the built-in pricing.json
    max_bytes=10 * 1024 * 1024,
    backup_count=5,
)
```

`pricing` and `pricing_overrides` are mutually exclusive — passing both
raises `ValueError`. See [Connecting to an existing app](connecting.md#missing-or-stale-pricing)
for when to use which.

| Method | Purpose |
| --- | --- |
| `log_call(*, label, model, input_tokens, output_tokens, cached_input_tokens=0, cost=None, pricing=None, trace_id=None, **extra)` | Log one call. Returns the JSONL record dict that was written. |
| `log_openai_response(response, *, label, model=None, trace_id=None, **extra)` | Adapter: reads `response.usage` (OpenAI SDK shape or an equivalent dict). |
| `log_anthropic_response(response, *, label, model=None, trace_id=None, **extra)` | Adapter: reads `response.usage` (Anthropic SDK shape). |
| `log_gemini_response(response, *, label, model=None, trace_id=None, **extra)` | Adapter: reads `response.usage_metadata` (`google-genai` SDK shape). |
| `log_ollama_response(response, *, label, model=None, trace_id=None, **extra)` | Adapter: reads `prompt_eval_count`/`eval_count` directly off the response (Ollama has no `usage` object). Pass the final streamed chunk, not an intermediate one. |
| `log_langchain_result(result, *, label, model=None, trace_id=None, **extra)` | Adapter: reads `result.usage_metadata` (current LangChain) or falls back to `result.llm_output["token_usage"]` (older `LLMResult`). |
| `guard(*, trace_id=None, max_usd_per_trace=None, max_calls_per_trace=None)` | Context manager; raises `BudgetExceededError` from the `log_call()`/adapter call that pushes a matching-`trace_id` trace over the given limit. In-process, per-trace enforcement — not the same mechanism as `budget`/`BudgetDetector` (cross-process, month-long, post-hoc). See [budget vs guard()](budget-vs-guard.md). |
| `report()` | Returns the same structured summary as `llm-burnwatch report --json` for this instance's log (zeros/empty breakdowns on an empty log, not an error). |
| `total_cost()` | Shortcut for `report()["total_cost_usd"]`. |

Each SDK adapter ends by calling `log_call(...)` — every adapter can raise
`BudgetExceededError` the same way `log_call()` does if a matching
`guard()` block is active.

### `BudgetExceededError`

Raised by `guard()` (and, through it, by `log_call()`/every adapter) when
a guarded trace goes over its limit. The call that triggered it has
already been logged — this is a signal to stop making further calls in
that trace, not a way to undo the one that just happened. See the
exception's own docstring for the full reasoning.

### `__version__`

Matches `pyproject.toml`'s `version` (kept in lockstep — see
`ARCHITECTURE.md`'s Versioning section). Also what `llm-burnwatch
--version` prints.

## CLI

Eleven subcommands (`llm-burnwatch <command> --help` for the full flag
list of any of them; this table covers the flags most users need):

| Command | Purpose | Key flags | Exit codes |
| --- | --- | --- | --- |
| `report` | Summarize cost from a log. Defaults to the last 30 days. | `--log-file` (required), `--all-time`, `--since`/`--until`, `--trace-id`, `--json`, `--format text\|csv`, `--fx-rate`/`--currency` (`--rub-rate` is the deprecated predecessor) | `0` success, `2` missing log/bad flag combo |
| `dashboard` | Write a static, self-contained HTML cost dashboard. | `--log-file`, `--out` (both required), `--since`/`--until`, `--fx-rate`/`--currency` | `0` success, `2` missing log |
| `demo-data` | Write a synthetic demo log (for trying `detect`/`report` without real traffic). | `--out` (required), `--n-normal`, `--n-anomalies`, `--seed` | `0` success |
| `detect` | Run all detectors once against a log, or stream alerts continuously with `--follow`. | `--log-file` (required), `--sensitivity low\|normal\|high` (default `normal`; mutually exclusive with the advanced `--threshold`), `--allowed-models`, `--max-call-cost`, `--max-trace-cost`, `--frequency-detector auto\|on\|off`, `--cusum-detector on\|off`, `--json`, `--follow` (+ `--poll-interval`, `--webhook-url`, `--slack-webhook-url`, `--telegram-bot-token`/`--telegram-chat-id`, `--exec-sink`) | `0` no findings, `1` findings (anomalies/rule violations/frequency spikes/level shifts/budget alerts), `2` missing log |
| `status` | Show which gated detectors (`frequency`/`cusum`/`budget`) are on/off/learning for a log, without running detection. | `--log-file` (required), `--json` | `0` success, `2` missing log |
| `train` | Train the optional ML anomaly model (`llm-burnwatch[anomaly]` extra, requires scikit-learn). | `--log-file` (required), `--model-dir`, `--keep-last`, `--contamination` | `0` success, `2` missing extra / missing log / empty log |
| `schema` | Print the packaged JSONL log schema (`schema.json`). | — | `0` |
| `validate` | Check a log against `schema.json`, or (`--alerts`) check a `detect --json` output file against `alert_schema.json`. | `--log-file`, `--json`, `--alerts` + `--alerts-file` | `0` all valid, `1` invalid record(s) found, `2` missing/bad file |
| `pricing import <source>` | Import per-model rates from a local file or an `http(s)://` URL (LiteLLM's pricing format) into the user pricing config. **The only command that ever makes a network call**, and only for an explicit URL — see [Security model](security.md#pricing-import--trust-boundary). | — | `0` success, `2` import error |
| `budget set` / `budget show` | Configure/inspect the monthly USD budget consulted by `detect`'s `BudgetDetector` and `report`'s Budget section. | `set --monthly --warn-at` | `0` |
| `import otel <source>` | Import an OpenTelemetry GenAI trace export (local file only) into a log. | `--log-file` (required) | `0` success, `2` import error |

Exit code convention across every command: `0` success/no findings, `1`
findings that warrant attention (only `detect` and `validate`), `2`
usage error (bad flags, missing/unreadable file, missing optional
dependency). An unexpected internal error (a bug, not a usage mistake)
also returns `2`, with a message pointing at the issue tracker rather
than a raw traceback.

## `--json` output keys

### `report --json`

`call_count`, `total_cost_micros`, `total_cost_usd`, `by_label_micros`,
`by_model_micros`, `pricing_last_updated`, `period` (`{since, until,
all_time}` — reflects the *effective* period, including the default
30-day window when no period flag was given). Present only when
applicable: `fx_rate`/`currency`/`total_cost_fx` (or the deprecated
`rub_rate`/`total_cost_rub`), `budget`.

### `detect --json`

`alert_schema_version`, `call_count`, `threshold` (the effective,
sensitivity-adjusted baseline threshold), `sensitivity`, `anomaly_count`,
`insufficient_data_count`, `anomalies` (each: `index`, `label`, `model`,
`timestamp`, `features`), `rule_violation_count`, `rule_violations`,
`seasonal_baseline` (`{available, message}`), `frequency_detector_enabled`,
`frequency_spike_count`, `frequency_spikes`, `cusum_detector_enabled`,
`level_shift_count`, `level_shifts`, `budget_detector_enabled`,
`budget_alert_count`, `budget_alerts`, `ml` (present only when the
optional `[anomaly]` extra + a trained model are available).

`sensitivity` was added as a purely additive key (see
`alert_schema.json`'s own additive-keys policy) — it does not bump
`alert_schema_version`, and no existing key changed meaning.

### `status --json`

`call_count`, `detectors` (list of `{name, state, message}` — `state` is
one of `on`/`off`/`learning`).

### `validate --json` / `validate --alerts --json`

`record_count`/`invalid_count`/`invalid` (plain log validation) or
`valid`/`errors` (`--alerts` mode).

## Frozen contracts

These do not change shape within a major version without a
`alert_schema_version`/`schema_version` bump (or, for the others, a
CHANGELOG-documented deprecation cycle per `CONTRIBUTING.md`'s
Versioning section):

- **`schema.json`** — the JSONL log record contract (`schema_version:
  "1.0"`, also printed by `llm-burnwatch schema`).
- **`alert_schema.json`** — the `detect --json`/`detect --follow` alert
  object contract (`alert_schema_version: 1`). New keys can be added
  additively without a version bump; existing keys don't change meaning.
- **`detect --follow`'s NDJSON stream** — one alert object per line,
  same fields as one entry of `detect --json`'s `anomalies`/
  `rule_violations`/etc. arrays.
- **Environment variable names** — `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  `LLM_BURNWATCH_WEBHOOK_URL`, `LLM_BURNWATCH_SLACK_WEBHOOK_URL`,
  `LLM_BURNWATCH_TELEGRAM_BOT_TOKEN`, `LLM_BURNWATCH_TELEGRAM_CHAT_ID`.
  See [Security model](security.md) for what each one's trust boundary
  covers.
- **CLI subcommand and flag names** listed in the table above (adding a
  new optional flag is not a breaking change; removing or repurposing an
  existing one is, and follows the deprecation policy in
  `CONTRIBUTING.md`).

## Internal (not covered by semver)

Everything else, including but not limited to: `detectors/*` (detector
classes, `run_detectors()`, `DEFAULT_REGISTRY`), `anomaly/*` (baseline
statistics, feature extraction, ML training/registry), `logreader.py`,
`follow_state.py`, `dashboard.py`'s rendering internals, and any
`cli.py`/`tracker.py` function or class not listed above (including
ones without a leading underscore, e.g. `build_report()`,
`default_log_path()`, `user_pricing_path()`, `user_budget_path()`,
`resolve_pricing()`, `merge_pricing_overrides()`, `load_default_pricing()`)
— useful to read, not safe to depend on across releases without checking
the CHANGELOG first.
