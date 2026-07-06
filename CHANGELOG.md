# Changelog

All notable changes to this project are documented in this file.

## [0.7.0] - 2026-07-05

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
