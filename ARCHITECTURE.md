# Architecture

This document exists so a contributor proposing a change — most notably a
new dependency — has one place to check the rule against, instead of each
PR re-deriving it from scratch in its own notes.

## The one rule: zero-dependency core, extras-only for everything else

`pyproject.toml` declares `dependencies = []`. Every third-party package
`llm-burnwatch` uses is behind an **optional extra**:

| Extra | Adds | Used by |
|---|---|---|
| *(none — core)* | nothing | `CostTracker`, its SDK-response adapters, `report`, `demo-data`, `detect` (baseline-only), `validate`, `schema`, `dashboard` |
| `llm-burnwatch[anomaly]` | `scikit-learn`, `skops` | `train`; `detect`'s ML cross-check (only once a model has actually been trained) |
| `llm-burnwatch[dev]` | `pytest`, `jsonschema`, `hypothesis` | running the test suite — never imported by any shipped code path |

This is enforced two ways, not just documented:

- `test_core_commands_make_no_network_attempts` (`tests/test_cli.py`)
  patches `socket.socket` to raise and runs `schema`/`demo-data`/`report`/
  `detect` (no trained model) through it — a core command that ever tried
  to open a socket (e.g. because a new dependency phoned home) would fail
  this test immediately.
- Every module that needs an `[anomaly]`-only package
  (`anomaly/train.py`, `anomaly/registry.py`) is imported lazily, inside a
  `try/except ImportError`, only from the one CLI handler that needs it
  (`cmd_train`, and `cmd_detect`'s ML cross-check). No module reachable
  from `report`/`demo-data`/`detect`-without-a-model/`validate`/`schema`/
  `dashboard` imports `scikit-learn`/`skops` at module level.

**Checklist for adding a new dependency** (e.g. a hypothetical CrewAI/AutoGen
adapter extra -- `log_langchain_result()` (0.9.5) turned out *not* to need
this: like the other four adapters, it reads fields via `_get()` off
whatever object the caller already has, without importing `langchain` at
all, so no new extra was warranted):

1. It goes behind a **new** extra (e.g. `llm-burnwatch[langchain]`), never
   into `dependencies = []` or an existing extra whose users didn't ask
   for it.
2. The module using it is imported lazily (inside the one function/command
   that needs it), guarded by `try/except ImportError` with a clear error
   message naming the extra to install — the same pattern `cli.py` already
   uses for `train`/`detect`.
3. `test_core_commands_make_no_network_attempts` (or an equivalent new
   test, if the new command itself needs network access to be useful —
   which is itself a decision requiring explicit sign-off, see
   "Network boundaries" below) must still pass unmodified for every
   existing core command.
4. The extra is documented in the table above and in README's
   `## Installation` section.

## Network boundaries

The core (see table above) never opens a socket — enforced by
`test_core_commands_make_no_network_attempts`. As of v0.9.1 there are two
explicit, opt-in exceptions:

| Command | Network access | Why it's safe to be an exception |
|---|---|---|
| `pricing import <url>` | Fetches a pricing JSON file over `http(s)://` when given a URL (a local file path does not touch the network) | Explicit, one-shot, user-initiated; prints a `warn()` before fetching; not on any path reachable from `report`/`detect`/`dashboard`/`train`/`demo-data`/`validate`/`schema` |
| `detect --follow --webhook-url`/`--slack-webhook-url` | POSTs each newly triggered alert as JSON (or a Slack-compatible payload) to a URL the caller supplies | Only runs when the caller passes one of these flags (or the matching `LLM_BURNWATCH_*_URL` env var) *and* `--follow`; one-shot `detect` never touches it. A delivery failure is caught by `sinks.protocol.send_to_all`, warned about, and never aborts the poll loop -- see SECURITY.md's "Alert sinks" section |
| `detect --follow --telegram-bot-token`+`--telegram-chat-id` | POSTs each newly triggered alert as a plain-text message to the Telegram Bot API (`api.telegram.org`, host fixed by the sink, not caller-supplied) | Same opt-in/failure-isolation properties as the webhook/Slack row -- only runs when *both* flags (or both matching `LLM_BURNWATCH_TELEGRAM_*` env vars) *and* `--follow` are given; internally composes `WebhookSink`, so it's the same HTTP code path, not a new one |

`detect --follow --exec-sink <command...>` is a related opt-in exception
that runs a *local command* (never a network call) for each newly triggered
alert -- see SECURITY.md for its specific threat model (`shell=False` is
hard-coded, not configurable).

Alert sinks (`sinks/` -- `WebhookSink`/`SlackSink`/`TelegramSink`/`ExecSink`)
need **zero** third-party packages (`urllib.request`/`subprocess`, both
stdlib), so unlike `[anomaly]` they are not gated behind a new pip extra -- there would be
nothing for such an extra to add. They follow the exact precedent already
set by `pricing import <url>` above: an opt-in *network/process* boundary,
documented in this table and in SECURITY.md, gated by explicit CLI flags
(or env vars for secrets) rather than by what's installed. See
`CHANGELOG.md`'s `[0.9.1]` entry for why this corrects an earlier
(`[0.8.0]`) plan to ship sinks behind a `[alerts]` extra.

`import otel <file> --log-file <dest>` (0.9.4) is **not** in this table: unlike
`pricing import <url>`, it deliberately accepts only a local file path, not an
`http(s)://` URL -- reading an already-exported OTLP JSON/JSONL file never
touches the network, and adding URL support wasn't asked for. Trivial to add
later as an explicit opt-in flag if it ever is.

Any future command that needs network access must be added to this table
and to `test_core_commands_make_no_network_attempts`'s command list (so the
no-network guarantee stays an enforced fact about the core, not just a
claim about the whole CLI). `detect --follow` itself is already excluded
from that test (it's a long-running poll loop, not a single invocation);
sinks add no new exception to that exclusion. Since that test can't cover
`--follow`, the equivalent guarantee for it is checked separately by
`test_run_detect_follow_with_no_sinks_opens_no_sockets_and_spawns_no_processes`
(`tests/test_detect_follow.py`), which patches `socket.socket` **and**
`subprocess.Popen` (covering both the network and process-spawning axes,
since `--exec-sink` is a process exception, not a network one) and asserts
neither is touched when no sink flags are given, even with a triggering
alert present.

## Why this rule, not just "keep deps low"

The project's own value proposition (README: *"a plain JSONL file on your
own disk; nothing leaves the machine"*) is a claim about what the *core*
package can do, not a vague aspiration. A transitive dependency pulled
into core by accident (e.g. an agent-framework adapter importing its SDK
at module level) would silently make that claim false for every user who
just wants `CostTracker`/`report`, even if they never call the new
adapter. Extras keep the blast radius of "I want feature X" limited to
users who actually opted into X.

## Module map

```
src/llm_burnwatch/
├── tracker.py          CostTracker: log_call() + SDK-response adapters
│                        (openai/anthropic/gemini/ollama/langchain --
│                        LiteLLM needs no adapter of its own, see
│                        CHANGELOG.md [0.9.5]), build_report(),
│                        guard() (in-process, per-trace_id spend/call-count
│                        enforcement -- BudgetExceededError; distinct from
│                        detectors/budget_detector.py's cross-process,
│                        post-hoc BudgetDetector)
├── logreader.py         iter_log_records() (rotation + directory-mode
│                        merge + corrupt-line skipping), parse_date(),
│                        filter_by_period(), check_scale()
├── demo_data.py         synthetic log generator (demo-data, tests)
├── budget.py            load_budget()/save_budget(): user-level monthly
│                        budget config (`budget set`/`show`, `BudgetDetector`),
│                        same XDG path + atomic-write pattern as pricing.json
├── otel_import.py       import_otel()/parse_otel_spans(): local-file-only
│                        OTLP JSON/JSONL -> llm-burnwatch JSONL (`import
│                        otel`), tolerant of both GenAI semconv attribute-
│                        naming generations, same tolerant-parsing precedent
│                        as pricing_import.parse_litellm_pricing
├── dashboard.py         render_dashboard(): static single-file HTML,
│                        core-only (no scikit-learn)
├── cli.py               argparse wiring for all subcommands
├── _messages.py         warn()/error(): the only sanctioned stderr writers
├── schema.json           JSONL log record contract (also `llm-burnwatch schema`)
├── pricing.json          per-model $/1M-token rates (see PRICING_CHANGELOG.md)
└── anomaly/
    ├── constants.py      every tunable constant, in one place
    ├── baseline.py        analyze(): robust (median/MAD) z-score, core-only
    ├── features.py        extract_features(), drift detection (core-only
    │                       except where it feeds `[anomaly]`-only code)
    ├── train.py           IsolationForest training (imports scikit-learn
    │                       at module level — only ever imported from
    │                       cli.py's cmd_train, inside try/except)
    └── registry.py         versioned model registry: save_model()/
                            load_model() (skops, sha256 integrity check —
                            see SECURITY.md)
```

## No-network guarantee

Covered above and in README's `## System boundaries` section. There is
exactly one test enforcing it
(`test_core_commands_make_no_network_attempts`); any new core command
should be added to that test's coverage, not assumed to inherit it.

## Model registry trust boundary

See [`SECURITY.md`](SECURITY.md) — out of scope for this document, which
is about dependency/import structure, not the trust model of on-disk
artifacts.

## Versioning

Semantic versioning (`pyproject.toml` / `src/llm_burnwatch/__init__.py`,
kept in lockstep). See [`CHANGELOG.md`](CHANGELOG.md) for what changed in
each release.
