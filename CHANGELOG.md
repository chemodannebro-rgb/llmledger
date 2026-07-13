# Changelog

All notable changes to this project are documented in this file.

## [1.0.8] - 2026-07-13

API stabilization: `docs/api.md` is now the one place that says what's
covered by semver and what isn't, and `CONTRIBUTING.md`'s versioning
policy is spelled out against it instead of being a one-line promise.

### Added
- `src/llm_burnwatch/__init__.py` now exports `CostTracker`,
  `BudgetExceededError`, and `__version__` -- previously only `__version__`
  was there, so `from llm_burnwatch import CostTracker` didn't work despite
  being the form shown in `docs/index.md`'s Quickstart.
- `docs/api.md` (+ `docs/api.ru.md`), linked from `mkdocs.yml`'s nav right
  after "Connecting to an existing app": the Python API (`CostTracker`'s
  constructor and 8 methods, `BudgetExceededError`, `__version__`), all 11
  CLI subcommands with their flags and exit codes, `--json` output keys for
  `report`/`detect`/`status`/`validate`, and a "Frozen contracts" list
  (`schema.json`, `alert_schema.json`, `detect --follow`'s NDJSON stream,
  env var names, CLI subcommand/flag names) versus an explicit "Internal
  (not covered by semver)" list.
- `CONTRIBUTING.md`'s `## Versioning` section: a `### Semver commitments`
  subsection (MAJOR/MINOR/PATCH criteria tied to `docs/api.md`'s frozen
  vs. internal split) and a `### Deprecation policy` subsection (`warn()`
  plus a `CHANGELOG.md` entry for at least one minor release before
  removal, never in the same release that introduced the warning).
- `tests/test_package.py`: locks in the new top-level exports and
  `__version__`'s value so a future change to either doesn't slip by
  unnoticed.

### Fixed
- `__init__.py`'s `__version__` was `"0.7.0"`, out of lockstep with
  `pyproject.toml`'s `"0.9.0"` (a drift `ARCHITECTURE.md`'s own Versioning
  section says shouldn't happen). Since `llm-burnwatch --version` reads
  this value, it had been printing the stale `0.7.0` -- now prints `0.9.0`,
  matching `pyproject.toml`.

### Known issue (flagged, not fixed here)
- `report --rub-rate`'s deprecation warning says "will be removed before
  v1.0" -- that hasn't happened as part of this release; the flag is still
  present. Removing a CLI flag is a functional change outside this
  release's literal scope (`__init__.py`/`docs/api.md`/`CONTRIBUTING.md`/
  `tests/test_package.py`), so it's left for a follow-up decision rather
  than done here without being asked.

## [1.0.7] - 2026-07-13

Alert text as a product, not an afterthought: console output, chat sink
messages, and error messages all get rewritten in money/action language
instead of statistical jargon and raw enum values, without touching any
`--json`/NDJSON contract.

### Added
- Console-only human-readable rendering for `detect`'s text output: cost
  and usage features are shown as dollar amounts and plain-English feature
  names instead of `z=`/`MAD=`/`cusum=`/`micros`; each of the 5 alert
  detectors gets a "type of incident" phrase (e.g. "cost/usage spike",
  "gradual cost increase", "unusually frequent calls", "rule violated:
  ...") instead of a raw `detector`/`kind` pair; a terminology-bridge note
  was added to `docs/detectors/cusum.md`(+ru) explaining that "gradual
  cost increase" (console) and "level shift" (docs, `Alert.kind`) name the
  same thing.
- A next-step hint on every alert type in the console (e.g. pointing at
  `report --json`, `budget show`, the dashboard, or `--max-call-cost`/
  `--allowed-models`), without referencing the not-yet-existing `explain`
  subcommand.
- A regression-locking jargon linter (5 new tests): runs `detect` against
  a real triggering scenario for each of the 5 detector types and asserts
  none of `z=`/`MAD`/`cusum=`/`quantile`/`micros` or a raw snake_case
  `detector`/`kind` value leaks into console text.
- `alert_text.py`: the rendering logic above, shared by `detect`'s console
  output and by `SlackSink`/`TelegramSink`, which now send one readable
  line per alert (with a severity emoji) instead of
  `[severity] detector/kind: message`. `WebhookSink`/`ExecSink` are
  unchanged (they still send the full alert payload for machine
  consumers). `SECURITY.md` updated to describe the new one-line format
  precisely -- still plain text, `parse_mode` still unused.
- A concrete next step on every CLI error message that previously lacked
  one: a shared `_log_file_not_found_error()` helper (6 call sites) that
  suggests checking `--log-file` or generating a demo log; specific fixes
  for `train`'s "nothing to train on" and `validate --alerts`'s file/JSON
  errors.
- Onboarding steps (the Quickstart's `log_call` → `report` → `demo-data` →
  `detect` sequence) shown by `report`/`status` on a genuinely empty log,
  instead of a bare "no records found" -- kept separate from a *missing*
  log file, which still exits `2` via the error path above.

### Notes
- `Alert.message`, `alert_schema_version`, and `detect --follow`'s NDJSON
  format are all unchanged -- every rewrite above lives in a separate
  text-rendering layer, not in the `Alert` dataclass or its JSON encoding.

## [1.0.6] - 2026-07-13

Detector status and detection sensitivity become explicit and
user-controllable instead of implicit in code and flag combinations.

### Added
- `llm-burnwatch status --log-file <path>`: a new subcommand reporting
  whether the `frequency`/`cusum`/`budget` detectors are on, off, or still
  learning, and why (reuses the existing seasonal-coverage message for
  `frequency`; no new gating logic).
- `--sensitivity low|normal|high` for `detect`, mutually exclusive with
  the existing `--threshold`: scales the baseline, frequency, and CUSUM
  detectors' thresholds together via one multiplier
  (`SENSITIVITY_MULTIPLIERS` in `anomaly/constants.py`). `detect --json`
  gains an additive `sensitivity` key; `alert_schema_version` is
  unchanged.
- `report` now defaults to the last 30 days instead of all-time; `--all-time`
  restores the previous behavior. `report --json` gains a `period`
  key (`{since, until, all_time}`).
- `CostTracker(log_file=...)`'s `log_file` argument is now optional,
  defaulting to a new `default_log_path()` (`$XDG_DATA_HOME/llm-burnwatch/log.jsonl`,
  following the same pattern as the existing pricing/budget config paths
  under `$XDG_CONFIG_HOME`).

### Notes
- No existing CLI flag was removed or renamed; `--threshold` keeps working
  exactly as before when given explicitly (sensitivity multiplier `1.0`).

## [1.0.5] - 2026-07-13

Russian translation of the documentation site with an EN/RU language
switcher, English staying the default (root URL, no prefix).

### Added
- `mkdocs-static-i18n` (`docs` optional extra) -- Material for MkDocs'
  language switcher, contextually linking to the translation of the
  current page rather than always the other language's root.
- A `<page>.ru.md` Russian translation alongside every existing English
  page (12 total): `index`, `connecting`, the five `detectors/*` pages,
  `security`, `budget-vs-guard`, `performance`, `comparison`, `faq`.
  Prose is translated; code blocks, CLI commands, function/constant names,
  numbers and thresholds are left unchanged.
- `markdown_extensions`' `toc.slugify` set to
  `pymdownx.slugs.slugify(case="lower")` -- the default slugifier strips
  non-ASCII characters, which would silently break every anchor link on
  the Russian pages (e.g. `#сообщить-об-уязвимости`).
- Fixed a pre-existing broken anchor (`security.md#pricing-import-url-trust-boundary`
  → `#pricing-import--trust-boundary`) in `connecting.md`, found while
  verifying anchors on the new Russian pages; the English site's
  `--strict` build doesn't fail on a missing in-page anchor (only a
  missing *page*), so this had gone unnoticed.

### Not in scope
- `README.md` and code docstrings stay English-only -- the user asked for
  the docs site specifically.


Documentation as a product: an mkdocs-material site replacing "read the
whole README" with one page per topic, editorial work over already-vetted
source material (docstrings, `SECURITY.md`, `ARCHITECTURE.md`) rather than
new research.

### Added
- `docs` optional extra (`mkdocs-material`) -- a build-time tool, not a
  runtime dependency; `pyproject.toml`'s core `dependencies` stay empty.
- `mkdocs.yml` with `strict: true` (fails the build on any broken internal
  link or nav entry pointing at a missing page -- the release plan's
  "link-checker", scoped to internal links).
- `docs/index.md`: Quickstart (five minutes to a first alert).
- `docs/connecting.md`: SDK adapters (OpenAI/Anthropic/Gemini/Ollama/
  LangChain/LiteLLM) and `import otel`, for an app that already makes LLM
  calls.
- `docs/detectors/{baseline,cusum,frequency,rules,budget}.md`: one page per
  detector -- what it catches, the math, its tunable constants and why
  they're set where they are, how to tune it, known limitations.
- `docs/security.md`: a readable version of `SECURITY.md`'s trust
  boundaries plus `ARCHITECTURE.md`'s network-boundaries table.
- `docs/budget-vs-guard.md`: `budget`/`BudgetDetector` (cross-process,
  post-hoc detection) vs `CostTracker.guard()` (in-process, real-time
  enforcement), side by side with examples.
- `docs/comparison.md`: llm-burnwatch vs Langfuse/LiteLLM/Helicone -- when
  to use which, expanding on the README's "When NOT to use" section.
- `docs/faq.md`: the no-network guarantee, whether prompts/completions are
  ever stored, and the common reasons an expected alert didn't fire
  (insufficient data, seasonal coverage not yet reached, cold start).
- `docs` CI job (`.github/workflows/ci.yml`): `pip install -e ".[docs]"` +
  `mkdocs build --strict` on every push/PR.
- README: a "Full documentation" pointer to `docs/index.md`; CONTRIBUTING:
  a "Documentation site" section on building/previewing locally and adding
  new pages to `mkdocs.yml`'s nav.

### Notes
- Publishing the built site (e.g. GitHub Pages) is a separate, later
  release step -- this only builds it, in CI and locally.
- External copyediting by a native English speaker remains a manual,
  human step, same as previous rounds' external-designer/external-editor
  precedent.

## [1.0.3] - 2026-07-13

Performance with numbers: measured (not assumed) throughput/memory
behavior against the v1.0 release plan's thresholds, using two new
dev-only benchmark scripts (`scripts/bench.py`, `scripts/soak_follow.py`
-- not part of the installed package, not run in CI).

### Added
- `scripts/bench.py`: times `build_report()` over 1M records, the full
  detector registry over 1M records (one-shot `detect`) and over a single
  full `FOLLOW_WINDOW_SIZE` window (one `--follow` poll's dominant cost),
  and `parse_otel_spans()` over 100k synthetic spans.
- `scripts/soak_follow.py`: drives thousands of poll-equivalent iterations
  back-to-back and samples RSS throughout, as an automated stand-in for a
  real multi-hour `--follow` soak (which remains a manual pre-release step
  -- see `docs/performance.md`).
- `docs/performance.md`: methodology, results table, and what the numbers
  do/don't prove.

### Verified, no code changes needed
- `report` on 1,000,000 records: 0.82s, well inside the plan's < 5s
  threshold. No aggregate cache was added -- the plan is explicit that a
  sidecar cache is only justified once the threshold is actually missed.
- A single `--follow` poll's full-window detector re-analysis: 0.036s,
  well inside the < 1s threshold. No memoization was added to the
  frequency detector's seasonal bucketing -- not warranted given the
  measured margin.
- 10,000 poll-equivalent iterations: RSS grew +0.4% (26.5MB -> 26.6MB) --
  flat, no evidence of a leak.

## [1.0.2] - 2026-07-08

Dashboard 3.0: a modern visual redesign, real sort/filter/copy
interactivity, and readable dual-currency money formatting. The single
biggest change in the file's history is the relaxation of the
"zero-JavaScript" principle: a small amount of inline vanilla JS now
powers table sorting, live filtering, and one-click copy-to-clipboard --
still no external library, no CDN, and no network call, so the
zero-dependency/no-network guarantee is unchanged, it's just no longer
literally zero-script.

### Changed
- Money formatting: every dollar amount in the dashboard now renders via
  `_format_usd()` -- thousands separator + 2 decimals (e.g. `$1,234.57`
  instead of `$1234.567891`) -- falling back to the old 6-decimal form
  only when 2 decimals would silently show a real, nonzero micro-cost as
  `$0.00`. The exact 6-decimal value is never dropped: it's always one
  click away via a small copy-to-clipboard button next to the amount.
- Dual-currency display (`--rub-rate`/`--fx-rate --currency`) is no
  longer limited to the top summary card: every rendered cost -- table
  rows, the daily journal's per-day cost, the budget block -- now shows
  the configured currency's amount in parentheses alongside the USD
  figure.
- Full CSS redesign: a single indigo accent color (light/dark), CSS-grid
  summary cards instead of `inline-block`, zebra-striped tables with a
  sticky header, pill/chip-style anomaly/severity badges, a taller budget
  bar with the percentage overlaid as text, and a custom disclosure
  triangle for the daily journal's `<details>` entries (replacing the
  inconsistent native browser marker).
- `dashboard.py`'s module docstring updated to describe the new "small
  amount of inline vanilla JavaScript" relaxation of the former
  zero-script guarantee.

### Added
- Table sorting: clicking a `<th>` in the by-label/by-model/active-
  detectors tables sorts that table's rows by that column (numeric for
  cost/alert-count columns, alphabetic for name columns), toggling
  ascending/descending on repeated clicks, with `aria-sort` kept in sync
  for accessibility.
- Live filtering: a search box above the by-label table, the by-model
  table, and the daily journal filters rows/entries by case-insensitive
  substring match against their text content.
- Copy-to-clipboard: every rendered money value has a small button that
  copies its exact (6-decimal) value via `navigator.clipboard.writeText`,
  with a brief "Copied" visual confirmation; a no-op, not an error, when
  the Clipboard API is unavailable.
- Sticky anchor navigation (`<nav class="section-nav">`) linking to the
  Budget (when configured)/Totals/Active detectors/Daily journal
  sections, so a long dashboard doesn't require scrolling to navigate.

### Added (tests)
- `_format_usd()` unit tests covering the thousands-separator case and
  the small-value fallback (a nonzero micro-cost must never render as
  `$0.00`).
- A test asserting the inline `<script>` block contains none of
  `fetch(`, `XMLHttpRequest`, `http://`, or `https://` -- an explicit,
  automated guard on the no-network claim, not just a manual assertion.
- Tests covering: full-precision copy-button `data-copy` attributes,
  `data-sort`/`data-sort-value`/`aria-sort` markup on sortable columns,
  `data-filter-target` markup on the three filter inputs, dual-currency
  amounts appearing in table cells (not just the summary card), and the
  anchor-navigation links.
- Re-verified the existing 300 KB size-regression guard still passes
  with the added CSS/JS.

## [1.0.1] - 2026-07-08

Dashboard 2.0: brings `dashboard --out file.html` up to the same level of
detail already available via `report`/`detect`, still as a single offline
HTML file with no new dependency, network call, or JavaScript.

### Changed
- `dashboard.py` now runs the full detector registry
  (`detectors.engine.run_detectors()` -- baseline z-score, frequency,
  CUSUM level-shift, rules, budget) instead of only the baseline z-score
  analyzer, mirroring `detect`'s auto-mode defaults (no CLI flags are added
  to `dashboard` itself, so frequency/rules/budget behave exactly as they
  would with no `--threshold`/`--allowed-models`/etc. overrides).
- The daily journal's per-day entries now show a `severity-badge`
  (highest severity alert that day, across every detector) alongside the
  existing `anomaly-badge` (unchanged: still scoped to baseline z-score
  findings only), plus an expandable "Alerts" list of every alert from
  every detector that fired that day.
- `render_dashboard()` gained an optional `budget_records` keyword
  parameter: the unfiltered log, used only to compute the budget block,
  so that block reports the same month-to-date/forecast numbers as
  `report`'s "budget:" section even when the rest of the dashboard is
  scoped by `--since`/`--until`. Defaults to `records` when omitted, so
  every existing caller is unaffected.

### Added
- A "Budget" section, mirroring `report`'s three-state budget UX: nothing
  shown when budget tracking isn't configured; a one-line message when
  configured but the log has no records yet in the current UTC calendar
  month; otherwise a progress bar plus the same month-to-date/projected
  month-end/budget numbers `report` prints, sourced from the same
  `detectors.budget_detector.compute_budget_status()`.
- Inline-SVG cost and call-count sparklines, one per label/model row in
  the "Totals for this period" tables, showing that name's day-by-day
  trend across the period (each sparkline is normalized to its own max,
  not comparable across rows -- the standard sparkline convention).
- An "Active detectors" table listing all five detectors, whether each is
  enabled (including *why* frequency is auto-disabled on short logs), its
  threshold/parameters, and how many alerts it raised this period --
  transparency into what's actually being watched, not just what fired.

### Fixed
- `dashboard`'s test suite is now isolated from the real
  `$XDG_CONFIG_HOME/llm-burnwatch/budget.json`: since `render_dashboard()`
  now always calls `load_budget()`, tests that didn't set `XDG_CONFIG_HOME`
  could previously read (and be affected by) a developer's real local
  budget config.
- `tests/test_core_commands_make_no_network_attempts` now also exercises
  `dashboard` (previously untested there) since it now reads `budget.json`
  and runs the full detector registry -- confirming neither makes a
  network call either.

### Added (tests)
- A regression test asserting the rendered HTML stays under 300 KB on a
  demo-data-scale log, guarding against the new sections/sparklines
  growing faster than the log itself.

## [1.0.0] - 2026-07-07

Closes four debts identified by the post-v0.9.0 audit.

### Security
- `sinks/webhook_sink.py`: `SinkError` messages raised from `post_json()`
  (shared by `WebhookSink`/`SlackSink`/`TelegramSink`) no longer include the
  secret-bearing path of the target URL -- a Slack incoming-webhook path or a
  Telegram `bot<token>/...` path was previously printed in full via `warn()`
  on any delivery failure. A new `_redact_url()` helper keeps only
  `scheme://netloc`. `URLError` handling now also uses `exc.reason` instead
  of `str(exc)`, since the latter can itself embed the original request/URL.
  Updated `telegram_sink.py`'s docstring, which previously (incorrectly)
  documented `SinkError` as including the full URL.
- `sinks/webhook_sink.py`: `post_json()` now rejects a followed HTTP
  redirect that lands on a different host/port (`response.geturl()`'s
  `netloc` differs from the configured URL's) or that downgrades
  `https://` to a non-`https` scheme -- mirroring the check
  `pricing_import.py`'s `_fetch_url()` already performs (that existing
  protection was audited and confirmed correct; no code changes were needed
  there). Deliberately does not add a custom `HTTPRedirectHandler`/opener,
  since that would bypass the module-level `urlopen` monkeypatch the sink
  test suite relies on.

### Changed
- `cli.py`'s `report`: when a budget *is* configured (`budget set` has been
  run) but the log has no records yet in the current UTC calendar month,
  text-mode output now prints a one-line
  `budget: configured ($X.XX/month) -- no records this month yet` message,
  distinguishing that case from "budget tracking isn't configured at all"
  (previously both were silently identical -- no Budget section either way).
  `--json` output is unchanged: the `"budget"` key is still only present
  when there's an actual month-to-date status to report.

### Added
- `validate --alerts --alerts-file <path>`: validates a `detect --json`
  output file (a single JSON object, not `detect --follow`'s streaming
  format) against the packaged `alert_schema.json`, symmetric to plain
  `validate`'s check of a log against `schema.json`. Closes the gap left
  open in [0.8.6]. `--log-file` is now optional on `validate` (required
  unless `--alerts` is given).
- `alert_schema.json`: added `cusum_detector_enabled`/`level_shift_count`/
  `level_shifts` and `budget_detector_enabled`/`budget_alert_count`/
  `budget_alerts` -- these were already part of `detect --json`'s real
  output (from the CUSUM and budget detectors) but had never been added to
  the schema, which would have made every real `detect --json` output
  invalid under its own schema on day one of `validate --alerts` existing.
  Purely additive; `alert_schema_version` is not bumped.

### Fixed
- `validation.py`'s `_TYPE_MAP` had no entries for JSON Schema's `array`,
  `boolean`, or `number` types -- harmless for `schema.json` (which never
  uses them) but meant `validate_record()` would report a spurious type
  mismatch for every array/boolean/number field in `alert_schema.json`
  (`anomalies`, `frequency_detector_enabled`, `threshold`, ...), found while
  building `validate --alerts`'s dogfooding test against a real `detect
  --json` payload. `number` matches `(int, float)` while still excluding
  `bool` from matching, the same way `integer` already did.

## [0.9.6] - 2026-07-07

### Added
- `examples/e2e_actions_demo.py`: an end-to-end smoke-test/demo tying
  together every "actions" milestone from v0.9 in one run --
  `CostTracker.log_langchain_result()` (0.9.5) logging calls, a real
  `budget.json` (0.9.2, via `budget.save_budget()`) so `BudgetDetector`
  fires, `detect --follow` (0.9.1's registry) polling that log, and a
  `WebhookSink` (0.9.1) delivering alerts to a genuine local
  `http.server.HTTPServer` started by the script itself -- unlike every
  `test_sinks_webhook.py`/`test_detect_follow.py` test, which monkeypatches
  `urlopen` directly, this is the one place in the repo that proves the
  webhook sink's HTTP POST actually round-trips over a real socket.
  Deliberately not a pytest test: `detect --follow`'s poll loop is
  intentionally infinite, so the script bounds it to exactly one poll by
  patching `time.sleep` to raise `KeyboardInterrupt` -- the same trick
  `test_detect_follow.py` already uses -- rather than adding a real
  integration test that runs an infinite loop under pytest.

### Scoped out
- No GIF was added for this demo. The original plan called for one "as
  already accepted for other demo sections", but no such precedent
  actually exists in the repo: the only visual asset is a static
  `docs/dashboard.png` screenshot of the HTML dashboard (a different
  feature, with its own screenshot rule in `CONTRIBUTING.md`), and there is
  no GIF/terminal-recording tooling anywhere in the project. Adding one
  would mean introducing a new dev-only tool (e.g. `vhs`/`asciinema`) with
  no other use in the codebase, just for one README demo -- decided against
  after asking; the demo's own printed output (included in its docstring
  and README's usage) documents what it does instead.

## [0.9.5] - 2026-07-07

### Added
- `CostTracker.log_langchain_result()`: adapter for a LangChain chat model
  result, following the same `_get()`-based, no-SDK-import pattern as the
  four existing adapters (openai/anthropic/gemini/ollama). Tries
  `result.usage_metadata` first -- the modern, provider-standardized field
  current `langchain-core` populates on every returned `AIMessage`
  (`input_tokens`/`output_tokens`, with `input_token_details.cache_read` as
  a subset of `input_tokens`, subtracted the same way OpenAI/Gemini's
  cached-token counters are) -- and falls back to the older
  `result.llm_output["token_usage"]` shape from the `.generate()`/
  `.agenerate()` `LLMResult` API (provider-specific field names, commonly
  OpenAI-style `prompt_tokens`/`completion_tokens`; no cache accounting
  attempted in this fallback, since that older shape doesn't standardize
  one). No new pip extra: exactly like the other four adapters, this reads
  fields off whatever object the caller already has, never importing
  `langchain` itself.

### Verified (no code change)
- `test_tracker_litellm_adapter.py` confirms `log_openai_response()` already
  works, unmodified, against a `litellm.ModelResponse`-shaped object:
  LiteLLM normalizes every provider it wraps to the same OpenAI-compatible
  response shape (`.model`, `.usage.prompt_tokens`/`.completion_tokens`,
  `.usage.prompt_tokens_details.cached_tokens`) that `log_openai_response()`
  already reads via `_get()`. Per the plan's "test first, write code only
  if the test actually needs it" rule for this milestone: it didn't, so no
  `log_litellm_response()` was written -- this fact is recorded here as the
  reproducible result of that check, not just a commit-message note.

## [0.9.4] - 2026-07-07

### Added
- `llm-burnwatch import otel <file> --log-file <dest>`: import an
  OpenTelemetry GenAI semantic-convention trace export (OTLP JSON, a JSON
  array of such exports, or JSONL -- one export object per line, as an OTel
  Collector file exporter typically writes) into a llm-burnwatch JSONL log.
  `source` must be a local file path -- unlike `pricing import <url>`, this
  deliberately does not accept an `http(s)://` URL; it would be a second,
  unrelated network boundary nothing asked for, trivial to add later as an
  explicit opt-in flag if it's ever needed.
- `otel_import.parse_otel_spans()`/`import_otel()`: tolerant of both
  attribute-naming generations the GenAI semantic conventions have had in
  the wild -- current (`gen_ai.request.model`,
  `gen_ai.usage.input_tokens`/`output_tokens`) and older/OpenLLMetry-style
  (`gen_ai.system`, `gen_ai.usage.prompt_tokens`/`completion_tokens`), current
  name preferred when both are present on the same span. Spans lacking a
  model or lacking both an input- and output-token count are silently
  skipped -- the same tolerant-parsing precedent as
  `pricing_import.parse_litellm_pricing`, since a real trace export is
  expected to contain plenty of non-GenAI spans (HTTP handlers, DB calls,
  ...). A model unresolvable in `pricing.json` imports at `cost_micros=0`
  with a one-time-per-model warning rather than aborting the whole batch.
- Each span's own `startTimeUnixNano` becomes the imported record's
  `timestamp` (not the time of the import run), so historical data lands on
  its real calendar date for `report --since`/`--until` and `BudgetDetector`.
  `traceId` is passed through opaquely as `trace_id` (no decoding/re-encoding
  attempted -- OTLP JSON trace/span IDs are inconsistently
  base64-vs-hex-encoded across real-world exporters, and llm-burnwatch never
  needs to interpret the value, only correlate on it).
- Appends to `--log-file` rather than replacing it (it's a log other
  processes may already be writing to, unlike `pricing.json`'s atomic
  replace), creating parent directories and locking the file down to `0600`
  the same way `CostTracker`'s own log file is, but only if the file didn't
  already exist.

## [0.9.3] - 2026-07-07

### Added
- `CostTracker.guard(trace_id=None, max_usd_per_trace=None,
  max_calls_per_trace=None)`: a context manager giving in-process,
  real-time enforcement of a spend/call-count cap on a single trace,
  raising a new `BudgetExceededError` from whichever `log_call()`/adapter
  call inside the `with` block pushes the trace over the limit -- meant to
  break a runaway agent loop as soon as it goes over, not to gate a call
  before it happens. `trace_id`, if not given, defaults to a generated
  UUID4 hex, yielded to the block so the caller can pass it to every
  `log_call(..., trace_id=...)`/adapter call it wants counted against this
  guard; calls with no `trace_id`, or a different one, are invisible to it.
  Calling `guard()` with neither limit raises `ValueError` immediately (a
  no-op guard is far more likely a caller mistake than an intentional one).
- The call that trips `BudgetExceededError` is still written to the log
  first: the real API call already happened and already cost money by the
  time `log_call()`/an adapter is invoked, so silently dropping its record
  would misrepresent actual spend to `report`/`detect`/`BudgetDetector`.
  `BudgetExceededError` is a signal to stop the *next* call in this trace,
  not a way to undo the one that just ran.
- Accounting is purely in-memory, per `CostTracker` instance, keyed by
  `trace_id`: two `guard()` blocks (even nested, even with overlapping
  lifetimes) using different `trace_id`s track fully independent totals,
  and all state for a `trace_id` is discarded the instant its `with` block
  exits (normally or via exception) -- re-entering `guard()` with the same
  `trace_id` afterward starts a clean count.

### Note
This is **enforcement, not detection** -- the opposite trade-off from
`[0.9.2]`'s `budget`/`BudgetDetector`: `guard()` is in-process and
per-`with`-block, so it can stop a loop the instant it overspends, but two
processes (or two `CostTracker` instances) sharing a `trace_id` are
invisible to each other, and it is not a daily/monthly budget -- it
forgets everything when the block exits. `budget`/`BudgetDetector` is the
opposite: cross-process, month-long, but purely after-the-fact. Neither
mechanism substitutes for the other; both are documented side by side in
README so they aren't conflated.

## [0.9.2] - 2026-07-07

### Added
- `llm-burnwatch budget set --monthly <usd> --warn-at <0..1>` /
  `llm-burnwatch budget show`: a user-level monthly USD budget, saved to
  `~/.config/llm-burnwatch/budget.json` (`tracker.user_budget_path()`, an
  exact copy of `user_pricing_path()`'s XDG resolution logic, so both files
  coexist under one `llm-burnwatch/` config directory -- proven by a new
  coexistence test, not just assumed). `budget.py`'s `load_budget()`/
  `save_budget()` follow the same atomic-write (`tempfile.mkstemp` +
  `os.replace`) and graceful-degradation discipline already used by
  `pricing_import`/`follow_state`: a missing `budget.json` is silently
  "not configured" (the expected state before `budget set` has ever run),
  while a corrupt/malformed one is reported via `warn()` and then treated
  the same as "not configured" -- never a crash.
- `detectors.budget_detector.BudgetDetector`: a new detector, registered in
  `DEFAULT_REGISTRY`, that sums `cost_micros` for the current UTC calendar
  month and linearly extrapolates "month-to-date / days elapsed so far ×
  days in month" into a projected month-end total. Emits `budget_exceeded`
  (critical, month-to-date already over the configured monthly budget) or
  `budget_pace_warning` (warning, the *forecast* exceeds `--warn-at`'s
  fraction of the budget, even though month-to-date hasn't crossed it yet).
  Deliberately does not reuse the seasonal (weekday × hour) baselines from
  `anomaly/seasonal.py` -- "will this month exceed budget at the current
  pace" is a different question from "is this hour unusual", and conflating
  them would add complexity without improving the forecast. Like
  `RulesDetector`, `enabled_by_default = False` and stays a silent no-op
  until `budget set` has actually been run; unlike `RulesDetector`, it also
  needs an explicit `enabled_overrides={"budget": True}` from the CLI,
  computed fresh from `budget.json`'s presence on every `detect`/`detect
  --follow` invocation, since its configuration comes from a file rather
  than flags on `detect` itself.
- **Low-confidence forecast flag**: the linear-pace projection is
  inherently noisy in the first few days of a month (little data, high
  variance). `compute_budget_status()` flags this explicitly
  (`low_confidence`/`days_elapsed`) rather than silently presenting an
  early-month forecast with unwarranted precision -- surfaced in both the
  detector's alert message and `report`'s new `budget:` section.
- `report` gained a `budget:` section (text and `--json`, keyed
  `"budget"`) showing month, monthly budget, month-to-date spend,
  projected month-end, and status -- present only when `budget set` has
  been run (never a "budget: not configured" placeholder, so scripts
  parsing `report --json` don't have to special-case an empty/absent
  feature). Deliberately reads the whole, unfiltered log for this section
  regardless of `--since`/`--until`/`--trace-id` -- budget tracking is
  about the current calendar month's actual spend, not whatever period the
  rest of `report` was asked to summarize -- and is skipped entirely for
  `--format csv`, matching CSV's existing "stays a stripped 3-column table"
  precedent. `detect`/`detect --follow` likewise gained `budget_alerts`
  (plus `budget_detector_enabled`/`budget_alert_count` in `--json`),
  wired the same way the `rules`/`frequency` detectors already are.

### Note
This is **detection, not enforcement** -- nothing added in this release
stops a call from happening or interrupts a request in progress; it only
surfaces that the current month is trending over budget, the same way the
other detectors surface a statistical anomaly. A future in-process
enforcement mechanism (`CostTracker.guard()`, planned for `[0.9.3]`) is a
deliberately separate concern from this one and should not be assumed to
exist yet.

## [0.9.1] - 2026-07-06

### Added
- `llm_burnwatch.sinks`: a new internal package giving `detect --follow` a
  way to push a newly triggered alert to a destination you configure,
  instead of only printing it. `sinks.protocol.Sink` is a one-method
  protocol (`send(alert) -> None`); `sinks.protocol.send_to_all()` is the
  single place that calls every configured sink for a given alert and
  catches *any* exception a sink raises, reporting it via `warn()` instead
  of letting it crash the poll loop or stop the remaining sinks -- the same
  graceful-degradation discipline already used for the ML cross-check and
  the follow-state file.
  - `sinks.webhook_sink.WebhookSink(url)`: POSTs the alert as JSON to `url`
    over `urllib.request`, reusing the same fixed-10s-timeout discipline
    (and the same rejection of any non-`http(s)://` scheme, at construction
    time) as `pricing import <url>`. The response body is never read (only
    `response.status` is inspected), so there's no equivalent to
    `pricing_import`'s response-size cap to enforce.
  - `sinks.slack_sink.SlackSink(webhook_url)`: composes `WebhookSink`'s
    POST logic (no duplicated HTTP handling, and inherits its scheme
    validation) to send a Slack-compatible `{"text": ...}` payload instead
    of the raw alert JSON.
  - `sinks.telegram_sink.TelegramSink(bot_token, chat_id)`: also composes
    `WebhookSink`, POSTing to the fixed `https://api.telegram.org/bot<token>/
    sendMessage` endpoint (the host is hard-coded by the sink itself, not
    caller-supplied, so there's no arbitrary scheme to validate here) with
    the same plain-text format `SlackSink` uses
    (`[severity] detector/kind: message`) rather than Telegram's Markdown/
    HTML `parse_mode`, deliberately avoiding MarkdownV2's escaping rules for
    a notification line.
  - `sinks.exec_sink.ExecSink(command)`: runs a local command (an argv
    list, never a shell string) with the alert JSON written to its
    **stdin** -- deliberately not appended to argv, since process argv
    (unlike stdin) is visible to every other local user via
    `ps`/`/proc/<pid>/cmdline`, the same reasoning behind preferring
    `LLM_BURNWATCH_WEBHOOK_URL` over `--webhook-url` for a secret-bearing
    URL, just applied to the payload instead of the destination.
    `subprocess.run(..., shell=False)` is hard-coded, not a configurable
    option, so alert content (which can include user-supplied `label`/
    `extra` text carried through from the log) can never be interpreted as
    shell syntax by this sink itself -- see `SECURITY.md`'s new "alert
    sinks trust boundary" section for what this does and does **not**
    protect against (a command that itself reinterprets its stdin, e.g.
    `sh`, is out of scope).
- `detect --follow` gained five new flags, wired in `_build_sinks()`
  (`cli.py`): `--webhook-url`, `--slack-webhook-url`, `--telegram-bot-token`
  + `--telegram-chat-id` (each falling back to the
  `LLM_BURNWATCH_WEBHOOK_URL`/`LLM_BURNWATCH_SLACK_WEBHOOK_URL`/
  `LLM_BURNWATCH_TELEGRAM_BOT_TOKEN`/`LLM_BURNWATCH_TELEGRAM_CHAT_ID`
  environment variable when the flag isn't given, so a secret-bearing
  value doesn't have to appear in argv/`ps` output), and
  `--exec-sink COMMAND...`. The two Telegram flags must be given together
  (both flags/env vars, or neither) -- `_build_sinks()` raises `ValueError`
  if only one is set. None of these do anything unless explicitly
  configured, and none apply to one-shot `detect` -- only `--follow`, where
  an alert "happens once" and a push notification is meaningful. Proven,
  not just claimed:
  `test_run_detect_follow_with_no_sinks_opens_no_sockets_and_spawns_no_processes`
  (`tests/test_detect_follow.py`) patches both `socket.socket` and
  `subprocess.Popen` and asserts neither is touched by `--follow` when no
  sink flags are given, even with a triggering alert present --
  `test_core_commands_make_no_network_attempts` can't cover this itself,
  since it already excludes `--follow` as a long-running poll loop.

### Fixed
- **Corrected this project's own stated plan for how sinks would ship.**
  The `[0.8.0]` entry above (and README's "System boundaries"/"When NOT to
  use" sections, before this entry) committed to v0.9 shipping webhook/Slack
  sinks "behind an opt-in `[alerts]` extra," parallel to `[anomaly]`. That
  turned out not to fit `ARCHITECTURE.md`'s own rule for *why* extras exist
  once actually implemented: extras gate **third-party packages** (`[anomaly]`
  gates scikit-learn/skops), and all four sinks need zero third-party
  packages -- `urllib.request` and `subprocess` are both stdlib. A new
  `[alerts]` extra would therefore have nothing to add; it would be a
  pip-installable no-op. Sinks instead follow the precedent already set by
  `pricing import <url>`: an opt-in **network/process boundary** -- gated by
  explicit CLI flags (or env vars for secrets), not by what's installed --
  documented as a new row in `ARCHITECTURE.md`'s "Network boundaries" table
  and a new section in `SECURITY.md`, exactly like `pricing import` already
  is. `test_core_commands_make_no_network_attempts` is unaffected: sinks
  only ever run from `detect --follow`, which that test already excludes
  (it's a long-running poll loop, not a single invocation) for the same
  reason `pricing import` needed no exception carved into it either.

## [0.8.0] - 2026-07-06

### Added
- `llm_burnwatch.detectors`: a new internal package laying the architectural
  foundation for the v0.8 "Detection Engine 2.0" milestone (frequency, CUSUM,
  and rule-based detectors to follow). `detectors.protocol.Detector` is a
  single-method protocol, `analyze(records) -> list[Alert]` — deliberately
  not the `feed`/`finalize` streaming split originally sketched for this
  milestone, since the planned `detect --follow` will re-run detectors over
  a small fixed-size window per poll rather than accumulate incremental
  state, so a streaming API would add complexity with no matching benefit.
  `detectors.engine.run_detectors()` orchestrates a registry of detectors,
  supports per-detector `enabled_overrides`, and merges/sorts their alerts.
  `detectors.baseline_detector.BaselineDetector` wraps the existing
  `anomaly.baseline.analyze()` as the first (and, for now, only) registered
  detector, without changing any baseline detection logic.
- `detect`'s internals now route through `detectors.engine.run_detectors()`
  instead of calling `anomaly.baseline.analyze()` directly. Its `--json` and
  text output are unchanged — this is purely an internal refactor.
- `detectors.frequency_detector.FrequencyDetector` (v0.8.1): a new detector
  that flags time windows (`FREQUENCY_WINDOW_SECONDS`, 60s) whose call count
  is anomalously high relative to that same `(label, model)` group's own
  history of window counts (modified z-score over `_median_mad()`, same
  robust-statistics family as the baseline detector), plus an independent
  log-wide check that catches a fan-out burst spread across many different
  labels/models. An absolute fail-safe (`FREQUENCY_ABS_CALLS_PER_WINDOW`,
  100 calls/window) flags a burst even with no prior window history to
  compare against. Only increases are flagged — a quiet window is never a
  "runaway agent." Ships **disabled by default**
  (`enabled_by_default = False`) and is not yet wired into `detect`'s CLI
  registry: without a notion of expected time-of-day/day-of-week call
  volume (seasonal baselines, planned for v0.8.4), a routine "Monday
  morning" burst looks statistically identical to a runaway agent. It is
  registered in `detectors.engine.DEFAULT_REGISTRY` for future callers, but
  this has no effect on `detect`'s current output.
- `logreader.parse_timestamp()`: parses a schema `timestamp` string to a
  full-precision `datetime` (same trailing-`Z` normalization as the existing
  `parse_date()`), added alongside `parse_date()` without changing it or its
  only caller (`filter_by_period`). Needed by the frequency detector to
  bucket records into fixed-size time windows.
- `detectors.cusum_detector.CusumDetector` (v0.8.2): a new detector that
  flags a *sustained* rise in a group's `output_tokens`/`cost_micros` using
  a one-sided tabular CUSUM against that group's own reference median/MAD
  — catching a level shift (e.g. a prompt change that quietly makes every
  response longer/pricier) even when no single call's own z-score crosses
  the baseline detector's threshold on its own. Two new constants,
  `CUSUM_H_MULTIPLIER` (12.0) and `CUSUM_SLACK_MULTIPLIER` (0.5), were
  chosen by simulation to keep the false-positive rate on stable synthetic
  data under 1% while still detecting a sustained 35% rise within a
  bounded number of records (see `test_cusum_detector.py`). Only rises are
  flagged, never drops. Ships **enabled by default**
  (`enabled_by_default = True`, unlike the frequency detector — a
  sustained cost/token shift isn't subject to the same day-of-week
  false-positive risk) and is registered in
  `detectors.engine.DEFAULT_REGISTRY`, but `detect`'s CLI still builds its
  own explicit registry, so this has no effect on `detect`'s current
  output.
- `detectors.rules_detector.RulesDetector` (v0.8.3): a new detector that
  enforces explicit, user-configured policies rather than a statistical
  threshold — a model allowlist, a per-call cost cap, and a per-`trace_id`
  cost cap. Unlike the other detectors, these aren't tuned from statistics;
  they're the caller's own limits, so they're exposed as three new `detect`
  CLI flags: `--allowed-models <model ...>`, `--max-call-cost <usd>`, and
  `--max-trace-cost <usd>`. Every alert is `severity="critical"`
  (`model_not_allowed`, `call_cost_exceeded`, `trace_cost_exceeded`) — this
  is a hard safety net, not a heuristic. The per-trace check reports the
  specific call whose cumulative cost pushed the trace's running total over
  the cap, not just the trace's last or priciest call. Ships **enabled by
  default**, but an unconfigured `RulesDetector()` (no flags passed) is a
  deliberate no-op: there's no safe universal default for "which models are
  allowed" or "how much a call should cost."
- `detect --json` gains two new, purely additive keys, `rule_violation_count`
  and `rule_violations`; the text output gains a new "N rule violation(s)
  found" section. All pre-existing keys/output (`anomalies`, `anomaly_count`,
  `threshold`, `insufficient_data_count`, `ml`) are unchanged. `detect`'s
  exit code now also returns `1` when a rule violation is found, in addition
  to the existing statistical-anomaly case.
- `anomaly.seasonal` (v0.8.4): a new module answering "does this log span
  enough calendar time for a day-of-week x hour-of-day comparison to be
  meaningful at all" (`has_seasonal_coverage()`, gated on calendar *range* via
  the new `MIN_SEASONAL_SPAN_DAYS` constant, 14 days -- not record count, the
  same million-calls-in-one-afternoon-still-can't-tell-Monday-from-Friday
  reasoning already used for `MIN_GROUP_SAMPLES` elsewhere), plus
  `seasonal_coverage_message()` for an honest, never-silent explanation of
  that decision (mirrors `insufficient_data` elsewhere in `anomaly/`).
- `FrequencyDetector` now buckets each time window by `(weekday, hour)` and
  compares it against that bucket's own history once a log has seasonal
  coverage, instead of only ever comparing against the group's flat, pooled
  history. A bucket's history deliberately excludes windows from the *same*
  calendar date as the window being scored -- without that exclusion, a
  single new hour-long burst would dominate its own bucket's statistics and
  "learn itself" as normal the instant it happens, permanently blinding the
  detector to it. Any `(weekday, hour)` bucket without at least
  `MIN_GROUP_SAMPLES` worth of history from *other* dates falls back to the
  pre-existing flat comparison. Net effect: a burst that recurs identically
  enough times to become the expected pattern for its time slot (e.g. "every
  Monday morning") stops being flagged, while a first-ever burst, or one
  that's unusually large even for its own normally-busy time slot, still is
  (see `test_features_seasonal.py`).
- `detect` gains a new `--frequency-detector {auto,on,off}` flag (default
  `auto`) and now wires `FrequencyDetector` into its registry. `auto` enables
  it only when `has_seasonal_coverage()` is true for the log being analyzed;
  `on`/`off` override that decision explicitly in either direction, the same
  override pattern already established for `RulesDetector`'s flags.
  `--json` gains three new, purely additive keys -- `seasonal_baseline`
  (`{"available", "message"}`), `frequency_detector_enabled`, and
  `frequency_spike_count`/`frequency_spikes` -- and the text output gains a
  new "N frequency spike(s) found" section, printed only when the detector
  was enabled for this run. `detect`'s exit code now also returns `1` when a
  frequency spike is found. `CusumDetector` remains unwired into `detect`'s
  CLI registry -- out of scope for this change, unchanged from v0.8.2.
- `detect --follow` (v0.8.5): a new streaming mode, plus `--poll-interval
  <seconds>` (default `5.0`). Instead of a single one-shot report, `--follow`
  polls `--log-file` repeatedly, re-running the same detector registry over
  a fixed-size rolling window (`FOLLOW_WINDOW_SIZE`, 5000 records -- new
  constant in `anomaly/constants.py`) of the most recently seen records, and
  prints each newly triggered alert as one JSON object per line to stdout as
  soon as it's found. `--json` is ignored (with a `warn()`) if passed
  together with `--follow` -- the streaming format is a distinct contract
  from the one-shot `--json` payload, not a replacement for it.
  - `logreader.read_new_records()`: a new function (alongside, not
    replacing, `iter_log_records()`) that reads only the complete lines
    appended to a file since a given byte offset, using binary-mode reads
    so offsets stay exact regardless of multi-byte UTF-8 content. A
    trailing partial line (writer still mid-write) is left unread and
    picked up whole on a later poll rather than parsed truncated. Supports
    both single-file and directory-mode logs, tracking one offset per file.
    Known limitation: only reads new lines appended at a file's current
    name -- rotated backups (`calls.jsonl.1`, `.2`, ...) are not read while
    following. If a tracked file has shrunk since its last recorded offset
    (truncation, or an in-place-rewriting log rotation), reading restarts
    from byte `0` for that file.
  - `follow_state` (new module): persists the byte offsets already consumed
    and the current rolling window to `<log>.llm-burnwatch-follow-state.json`,
    a sibling of the log file, written atomically (`tempfile.mkstemp` +
    `os.replace`, the same pattern `pricing_import.import_pricing` already
    uses) so a process killed mid-write never leaves a half-written state
    file behind. A missing state file (first run) is silent; a corrupted or
    malformed one is never fatal -- `--follow` warns and starts over from
    the beginning of the log, the same graceful-degradation discipline
    already used for a tampered ML model registry.
  - Each poll only reports alerts triggered by data that arrived *this*
    poll, not the whole window being re-analyzed from scratch -- since the
    rolling window only ever evicts from its oldest (left) end, records
    newly appended this poll are always at the tail, so alerts referencing
    an index at or after that tail boundary are "new" and everything before
    it is filtered out as already surfaced in an earlier poll. Known,
    accepted trade-off: an alert whose evidence points at an *older* record
    (e.g. a frequency spike's first record in its window) is filtered out
    even if the detection itself only became true because of newly arrived
    data -- a consequence of re-running stateless, batch detectors over a
    sliding window rather than giving each detector its own incremental
    state.
  - Deliberately not run in `--follow` mode, unlike one-shot `detect`: the
    ML cross-check (reloads a model from disk, too expensive every poll) and
    the log-wide label-cardinality warning (would repeat almost identically
    every poll).
- `alert_schema_version` (v0.8.6): one-shot `detect --json` output now
  includes a top-level `"alert_schema_version": 1` key
  (`detectors.protocol.ALERT_SCHEMA_VERSION`, already introduced in v0.8.0's
  architectural foundation but not previously surfaced in any output),
  mirroring how `schema.json`/`schema_version` already version the input log
  format. This is the **only** change to `detect`'s public JSON contract
  across the whole v0.8 milestone -- every other v0.8.1-0.8.5 sub-task
  deliberately left the existing `--json`/text output untouched so this
  would be a single, isolated, easy-to-describe versioning change. All
  pre-existing keys (`call_count`, `anomaly_count`, `anomalies`, `ml`, etc.)
  are unchanged; `alert_schema_version` is purely additive. New file
  `src/llm_burnwatch/alert_schema.json` documents this output shape, by
  analogy with the existing `schema.json` for the input log format (added
  to `package-data` alongside it). `detect --follow`'s separate,
  newline-delimited streaming format (v0.8.5) is unaffected -- it isn't
  wrapped in this key, since it's a distinct, already-documented contract.
  A dedicated `validate --alerts` command analogous to the existing
  `validate` (which checks input records against `schema.json`) is a
  reasonable future addition but out of scope for this change.
- `demo_data` (v0.8.7): five new independent synthetic scenario generators
  — `runaway_loop()`, `model_swap()`, `prompt_regression()`, `gradual_drift()`,
  `weekend_pattern()` — one per detector added in v0.8.1-0.8.4, each modeling
  a distinct real money-losing incident the pre-existing single
  amplitude-outlier profile (`generate_demo_calls`/`write_demo_log`, still
  unchanged) can't exercise: a burst of call *volume* (`FrequencyDetector`),
  a swapped-in disallowed model (`RulesDetector`'s `allowed_models`), a
  sudden or gradual level shift in response size (`CusumDetector`), and a
  recurring weekly calendar pattern (`FrequencyDetector`'s seasonal
  bucketing). Each generator builds schema-compliant record dicts directly
  (rather than through `CostTracker.log_call()`, which always stamps the
  real wall-clock time and so can't produce the multi-window/multi-week
  synthetic timestamps several of these scenarios need) and returns
  `list[tuple[dict, str | None]]` — the record plus the injected scenario
  name for calls that are part of the incident, `None` for normal calls,
  mirroring the existing `is_anomaly` convention. Each has its own fixed
  seed (`_SCENARIO_SEEDS`) so no scenario's random data depends on which
  other scenarios happen to run in the same process — a cross-contamination
  risk raised on review.
- `tests/test_anomaly_sanity.py` extended with one recall/precision test per
  scenario against its paired detector (including, for `weekend_pattern`,
  separate checks that the same recurring burst is flagged without seasonal
  coverage, learned as normal with enough weeks of history, and still
  flagged if it's abnormally large even for its own weekly slot), plus a
  check that a clean, anomaly-free synthetic log triggers no false positives
  from `CusumDetector`/`RulesDetector` (bounded, not exactly zero, for CUSUM
  — its threshold is tuned for a low false-positive *rate*, not a hard
  guarantee against random Gaussian noise; `FrequencyDetector` is excluded
  from this specific check since `write_demo_log`'s real-time stamping logs
  all calls back-to-back into a single window, an artifact of the demo
  script rather than a realistic call rate).

### Fixed
- **`CusumDetector` was never reachable through `detect`'s CLI.** Every
  bullet above from v0.8.2 onward candidly noted this ("registered in
  `DEFAULT_REGISTRY`, but `detect`'s CLI still builds its own explicit
  registry" / "out of scope for this change") -- documented as a known gap
  rather than a silent omission, but a gap all the same: the milestone's
  headline scenario (a prompt change that quietly makes every response
  longer/pricier, with no single call crossing the baseline z-score
  threshold on its own) was invisible through `detect`, even though a
  direct `run_detectors()` call over the same records correctly caught it
  (see `test_prompt_regression_scenario_is_flagged_by_cusum_detector`).
  Found and reported in review before this tag was ever pushed/published.
  Fixed the same way `FrequencyDetector`/`RulesDetector` were wired in:
  `CusumDetector()` added to `_detect_registry()`, a new `--cusum-detector
  {on,off}` flag (default `on`, matching `CusumDetector.enabled_by_default`
  -- no `auto` state, since unlike frequency there's no seasonal
  false-positive risk to gate on), and three new, purely additive `--json`
  keys: `cusum_detector_enabled`, `level_shift_count`, `level_shifts`
  (same shape as `frequency_spikes`). The text output gains a new "N level
  shift(s) found" section, and `detect`'s exit code now also returns `1`
  when a level shift is found. Applies to both one-shot `detect` and
  `detect --follow`, which share the same registry-building code.
- **README/SECURITY.md/CONTRIBUTING.md still contradicted the planned v0.9
  milestone.** The "Changed" entry above for this same version says README's
  opening note now *leads with* "Early stage — API may change before v1.0"
  -- but it left the older "Portfolio/demo... not a commercial product, no
  support, no SLA" sentence in place right after it, and "System boundaries"
  still claimed `llm-burnwatch` "never sends a notification and has no
  optional dependency that would let it (no Slack SDK, etc.)" -- an absolute,
  permanent-sounding claim that the next milestone (v0.9, an opt-in
  `[alerts]` extra for webhook/Slack sinks) would have to directly break.
  Found and reported in review before this tag was ever pushed/published.
  Resolved in favor of keeping the door open for v0.9 (the alternative --
  permanently ruling out any notification sink and descoping v0.9 down to
  budgets/guard/OTel/adapters only -- was also on the table, but a
  zero-dependency core that already ships optional extras for *other*
  things (scikit-learn/skops behind `[anomaly]`, an explicit opt-in fetch
  for `pricing import <url>`) doesn't need to foreclose a future optional
  extra for notification sinks too): dropped the portfolio/commercial
  sentence from README's opening note and from `SECURITY.md`'s/
  `CONTRIBUTING.md`'s opening lines (all three now just point to "Early
  stage — API may change before v1.0"), and reworded README's "When NOT to
  use", "entire integration contract", and "System boundaries" sections
  from absolute "by design"/"never...that would let it" claims to
  present-tense ones ("doesn't ship a notification sink yet") that describe
  today's core accurately without ruling out a future notification sink
  living behind its own optional extra, the same way `[anomaly]` and
  `pricing import` already do. No code changed --
  `test_core_commands_make_no_network_attempts` and the no-network
  guarantee it checks are untouched.
- **`FrequencyDetector` spikes could be silently dropped by `detect
  --follow`.** `_detect_follow_poll` decides whether an alert is "new this
  poll" by checking `record_ref >= new_start_index`, which only works if
  `record_ref` points at the most recent record that contributed to the
  alert -- but `FrequencyDetector` reported a spike window's *first* record
  instead. If that record already existed from an earlier poll, a spike
  confirmed only by calls arriving *this* poll was filtered out as
  "already surfaced," even though the detection itself was brand new (see
  `test_detect_follow_poll_reports_frequency_spike_confirmed_by_new_records`
  for the exact scenario). Fixed by having `FrequencyDetector` report a
  spike window's *last* record instead -- purely an internal `record_ref`
  choice, `evidence`/`message`/severity/exit-code behavior are unchanged.
  Found and reported in review before this tag was ever pushed/published.
- **`pricing import <url>` didn't notice an `https://` source silently
  redirecting to a plain `http://` response.** `urlopen` follows redirects
  transparently; a compromised or misconfigured server could downgrade an
  encrypted request to plaintext partway through without the caller ever
  knowing. `fetch_source`/`_fetch_url` now check the final URL after
  redirects (`response.geturl()`) and refuse to proceed if an `https://`
  source ended up at a non-`https://` response (an `http://` source
  redirecting elsewhere was never protected to begin with, so it's
  unaffected). Documented in `SECURITY.md`'s `pricing import <url>`
  network trust boundary section. Found and reported in review before this
  tag was ever pushed/published.
- `pyproject.toml`'s `authors` field named the package itself
  (`"llm-burnwatch"`) rather than a person -- an anonymous author is a
  weaker trust signal on PyPI than a real name or a stable pseudonym.
  Changed to the GitHub account that already publicly owns this repository
  (`chemodannebro-rgb`), which was already visible in the `Homepage`/
  `Repository`/`Issues` URLs just below it -- no new information disclosed.

## [0.7.0] - 2026-07-05

> **Known gap (tech debt):** `.github/workflows/release.yml` publishes to
> PyPI via trusted publishing (OIDC) on `v*` tags, but the one-time PyPI
> Trusted Publisher registration for `llm-burnwatch` has not been done
> yet. Until it is, the `v0.7.0` tag is intentionally not pushed — pushing
> it would trigger a workflow run that fails at the publish step.

### Changed
- **Project renamed from `llmledger` to `llm-burnwatch`** (PyPI/CLI name
  `llm-burnwatch`, importable package `llm_burnwatch`) ahead of the first
  PyPI publish — `burnwatch` was already taken by an unrelated project.
  Every user-facing surface moved together: the console script
  (`llmledger` → `llm-burnwatch`), the import path
  (`from llmledger... import` → `from llm_burnwatch... import`), the XDG
  config directory (`~/.config/llmledger/` →
  `~/.config/llm-burnwatch/`), and all GitHub/PyPI URLs in
  `pyproject.toml`. No compatibility shim or deprecated alias is provided
  for the old name — the package has not been published under the old
  name yet, so there are no existing installs to carry forward.

### Added
- `llm-burnwatch pricing import <file|url>`: imports pricing from a local file
  or an `http(s)://` URL in LiteLLM's `model_prices_and_context_window.json`
  format, saved to `~/.config/llm-burnwatch/pricing.json`
  (`$XDG_CONFIG_HOME/llm-burnwatch/pricing.json` if set). This is the only
  network access point outside the zero-dependency core (10 second
  timeout, 10 MB response cap, `http(s)://`-only scheme allowlist, strict
  JSON parsing that rejects `Infinity`/`NaN`, atomic write) — see
  "Network boundaries" in `ARCHITECTURE.md` and the new trust-boundary
  section in `SECURITY.md`. `report`/`dashboard`/`detect` now resolve
  pricing in priority order: explicit `--pricing-file` > this user config
  file > the packaged default; `detect` previously had no `--pricing-file`
  flag at all and always used the packaged default.
- `--fx-rate <rate> --currency <code>` on `report`/`dashboard`, replacing
  the RUB-only `--rub-rate <rate>`. `--rub-rate` still works as a
  deprecated alias for `--fx-rate <rate> --currency RUB` (prints a
  `warn()`) and will be removed before v1.0.
- `python -m llm_burnwatch.cli` now runs the CLI (`__main__` guard), instead
  of silently doing nothing.
- `.github/workflows/release.yml`: publishes to PyPI via trusted
  publishing (OIDC) on `v*` tags — no long-lived API token stored in the
  repository. CI's Python version matrix extended from `3.9`/`3.12` to
  `3.9`–`3.13`.
- `README.md`: a real dashboard screenshot (`docs/dashboard.png`,
  generated from `demo-data` + `dashboard`), a "When NOT to use
  llm-burnwatch" section (points to Langfuse for traces/evals, LiteLLM for a
  proxy), and `pip install llm-burnwatch` as the primary install path now
  that the package is published.

### Changed
- README's opening note now leads with "Early stage — API may change
  before v1.0" ahead of the existing portfolio/demo-project disclaimer,
  and its "System boundaries" section now accurately describes
  `pricing import <url>` as the one explicit network exception (it
  previously claimed llm-burnwatch never makes a network call at all).
- `CONTRIBUTING.md`'s security section no longer claims there's a private
  vulnerability-disclosure channel — `SECURITY.md` has never had one; all
  reports go through public GitHub issues.

### Fixed
- `llm-burnwatch train` (the `[anomaly]` extra): a missing `skops` install
  (with `scikit-learn` present) surfaced as a generic "unexpected error"
  instead of the intended `pip install "llm-burnwatch[anomaly]"` message,
  because `cmd_train`'s `except ImportError` only wrapped the `import` of
  `anomaly.train` itself, not the later call to `train()` — where `skops`
  is actually imported lazily, inside `registry.save_model()`. Both call
  sites are now covered by the same handler.

### Removed
- `BACKLOG.md`/`BACKLOG_REVIEW.md` (internal planning drafts, deleted
  outright — superseded by an internal, gitignored role/review scaffold
  not published in this repo) and the example `models/v1/` registry
  (`model.skops` + `metadata.json`, untracked via `git rm --cached` but
  left on disk locally) are no longer tracked in git. The model registry
  is trivially regenerated via `demo-data` + `train`, and a committed
  model binary was already flagged in `SECURITY.md` as a weak trust
  boundary for a public
  repository.

## [0.6.0] - 2026-07-05

### Added
- `llm-burnwatch validate --log-file <path> [--json]`: checks every record
  against the packaged `schema.json` (required fields, types including
  `["string", "null"]` unions, `minLength`, `minimum`,
  `additionalProperties: false`). Implemented as a small, dependency-free
  validator (`validation.py`) rather than importing the `[dev]`-only
  `jsonschema` package, so `validate` stays a core, zero-dependency
  command like `report`/`demo-data`/`schema`/`dashboard`. Exit `0` clean,
  `1` invalid records found, `2` error.
- `report --format csv`: prints a normalized 3-column CSV
  (`dimension,key,cost_usd` — one `total` row, then one row per label,
  then one row per model), meant to be piped into a spreadsheet or
  another program. Mutually exclusive with `--json`.
- `CostTracker(pricing_overrides={...})`: point overrides for individual
  model rates, merged on top of the packaged `pricing.json` defaults via
  the new `merge_pricing_overrides()` helper — no need to hand-copy the
  whole pricing file to add or correct one model. Mutually exclusive
  with the existing `pricing=` (full-replacement) kwarg.
- `ARCHITECTURE.md`: documents the zero-dependency-core /
  extras-only (`[anomaly]`, `[dev]`) rule as an explicit, citable policy
  instead of prose scattered across README sections and independently
  rediscovered in backlog notes.
- `PRICING_CHANGELOG.md`: records what actually changed between
  `pricing.json` snapshots (not just its `last_updated` date), so a
  changed historical `report` total can be traced back to a specific
  pricing update.
- `CONTRIBUTING.md`: setup, test-running, what a PR needs (tests,
  docs, no accidental new dependencies), and the dashboard
  screenshot-required-for-CSS-changes rule.
- README: explicit ICP line ("Built for: ...") clarifying the primary
  use case (a solo developer shipping an LLM-powered feature) versus the
  secondary one (a small team via directory mode).

### Fixed
- `examples/full_pipeline.py` no longer breaks on the `train()` signature
  change from v0.5.0 (`train()` returns `(version_dir, eval_metrics)`);
  the example previously did `version_dir = train(...)` without
  unpacking, silently broken since examples aren't covered by pytest.
  The example now also demonstrates `dashboard`/`filter_by_period`/
  `parse_date`, and `basic_tracking.py` now points readers at `report`/
  `dashboard` CLI usage — both were written before those commands
  existed.

## [0.5.0] - 2026-07-05

### Added
- `report --json`: prints a machine-readable summary (same shape as the
  human-readable output), matching the flag `detect` already had.
- `report --trace-id <id>`: narrows the report to the calls belonging to
  one request (e.g. a multi-step RAG turn logged under a shared
  `trace_id`), finally surfacing a field that was captured on every call
  but never read back anywhere.
- `train` now computes and prints a held-out self-consistency eval
  metric: a throwaway `IsolationForest` is fit on a deterministic ~80%
  split of the feature matrix and evaluated against the remaining ~20%
  it never saw, reporting what fraction of unseen examples it still
  flags as anomalous. The model actually saved to the registry is still
  fit on the full dataset afterward — the split only exists to produce
  an honest metric. Skipped (with an explicit reason) below 20 total
  training examples. Persisted in `metadata.json` as `eval_metrics`.
  `train()` now returns `(version_dir, eval_metrics)`.
- `cached_input_tokens` is now a feature evaluated by both the baseline
  z-score detector and the `IsolationForest` cross-check (previously
  logged but never scored by either detector) — closes a false-negative
  gap where a group starting to hit cache heavily would look anomalously
  cheap without anything actually being wrong.
- `hypothesis`-based property/fuzz tests for the baseline z-score
  statistics (`tests/test_baseline_properties.py`, new **dev-only**
  `hypothesis` dependency): random-input coverage of `_median_mad`/
  `_score_feature`/`analyze()` edge cases (all-identical values, MAD=0,
  single-sample groups, negative/extreme magnitudes) beyond the existing
  example-based tests.

### Changed
- `report` no longer materializes the whole log into a list before
  processing it — it now streams records through a generator
  (`_filter_report_records`) straight into `build_report()`, matching
  the already-streaming design of `iter_log_records()`. `detect`/
  `dashboard` are unchanged (out of scope: both need full group history
  in memory for cross-record statistics).
- `analyze()` computes each `(label, model)` group's median/MAD once per
  group instead of once per record scored against it (previously
  `O(group_size^2)` per group).

### Fixed
- `models/v1` (the committed example model registry) retrained to match
  the new 4-feature shape (adding `cached_input_tokens`); the old
  3-feature model would otherwise mismatch `IsolationForest.predict()`.

## [0.4.1] - 2026-07-05

### Added
- `detect --json`: each flagged feature now also includes a `reason`
  string — the same human-readable z-score/median/MAD explanation
  already printed by the non-JSON output (`baseline.format_score()`),
  so JSON consumers don't have to recompute it from the raw numbers.
- README: one-line mention that a `dashboard` command exists (a
  screenshot is intentionally deferred until the dashboard's next
  redesign).

### Fixed
- `detect`'s ML cross-check no longer treats a version pruned by a
  concurrent `train()` run as a hard failure: `latest_version_dir()`
  resolving a version right before a concurrent `train()` prunes it
  (e.g. `keep_last=1`) used to make that run's ML cross-check
  unavailable even though a newer, perfectly good model existed a
  moment later. `_run_ml_cross_check` now retries (bounded, 3 attempts)
  by re-resolving "latest" specifically on `FileNotFoundError`.

## [0.4.0] - 2026-07-05

### Added
- `dashboard` command: static, single-file HTML report (no JS, no CDN,
  no network calls). Central element is a **daily journal** — one
  collapsible `<details>` row per day (date, call count, cost, a
  fixed-width mini cost bar, top label, anomaly badge), expanding into
  that day's own by-label/by-model breakdown.
- `--since`/`--until` (`YYYY-MM-DD`, inclusive) on both `report` and
  `dashboard`, filtering by the record's UTC calendar date
  (`llm_burnwatch.logreader.filter_by_period`). The dashboard header shows
  the active period ("Period: all time" if neither is given).
- `SECURITY.md`: model registry trust boundary and vulnerability
  reporting process.
- `<meta name="viewport">` and a `@media (max-width: 600px)` layout for
  the daily journal, plus a `@media (prefers-color-scheme: dark)` theme
  mirroring the light palette.
- Test: `test_readme_log_format_section_mentions_all_schema_fields` —
  guards `schema.json`'s `properties` and README's "Log format" section
  against silently drifting apart.

### Fixed
- Removed the unbounded-width whole-log SVG chart (a quarter's worth of
  days made the chart wider than `<body>` and broke layout). Replaced
  with the fixed-size (100×14px) per-day mini bar described above —
  width is now constant regardless of log length.
- Mobile (`≤600px`) daily-journal row: an early implementation assigned
  the same CSS `grid-area` to four sibling elements (call count, mini
  bar, top label, anomaly badge), causing them to render stacked on top
  of each other instead of in visible rows. Rewritten as
  `display: flex; flex-wrap: wrap` with per-element `order`, so all
  fields stay legible and non-overlapping at narrow widths. Found and
  fixed via actual browser rendering/visual QA, not just structural
  (grep-based) HTML tests.

## [0.2.0] - 2026-07-03

### Added
- `--rub-rate` flag on `report`: shows total cost also converted to RUB
  at a fixed, manually-supplied rate (still no network calls).
- `log_gemini_response()` / `log_ollama_response()` adapters on
  `CostTracker`, following the same defensive `_get(x, name, 0) or 0`
  pattern as the existing OpenAI/Anthropic adapters.

### Changed
- **Breaking (model registry file format):** replaced `pickle` with
  `skops` in `anomaly/registry.py`. `skops` refuses by construction to
  construct any type outside its trusted-by-default list, removing the
  arbitrary-code-execution risk `pickle` carried on untrusted model
  files. The existing SHA256 integrity check is kept alongside it.
  `models/v1/` was retrained/recommitted in the new `model.skops`
  format.

## [0.1.0] - 2026-07-03

### Added
- Initial release: `CostTracker` (JSONL cost logging, log rotation via
  stdlib `RotatingFileHandler`, directory mode for multi-process
  writers) with OpenAI/Anthropic response adapters.
- Baseline anomaly detection: robust modified z-score (Iglewicz &
  Hoaglin) on `input_tokens`/`output_tokens`/`cost_micros` against the
  history of the same `(label, model)` pair — no third-party
  dependencies required.
- Optional ML cross-check: `IsolationForest` on group-relative
  features (`llm-burnwatch[anomaly]` extra), plus training-time vs.
  current per-group statistics drift detection.
- Versioned, SHA256-verified model registry (`models/vN/`); a working
  example registry committed at `models/v1/`.
- CLI: `report`, `demo-data`, `detect`, `train`, `schema`.
- JSONL log schema contract (`schema.json`, `llm-burnwatch schema`).
- CI (GitHub Actions): core-only smoke test + full test matrix
  (Python 3.9 & 3.12) with `[anomaly,dev]` extras, plus `pip-audit`.
