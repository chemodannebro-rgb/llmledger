# Changelog

All notable changes to this project are documented in this file.

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
