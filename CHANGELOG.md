# Changelog

All notable changes to this project are documented in this file.

## [0.4.0] - 2026-07-05

### Added
- `dashboard` command: static, single-file HTML report (no JS, no CDN,
  no network calls). Central element is a **daily journal** — one
  collapsible `<details>` row per day (date, call count, cost, a
  fixed-width mini cost bar, top label, anomaly badge), expanding into
  that day's own by-label/by-model breakdown.
- `--since`/`--until` (`YYYY-MM-DD`, inclusive) on both `report` and
  `dashboard`, filtering by the record's UTC calendar date
  (`llmledger.logreader.filter_by_period`). The dashboard header shows
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
  features (`llmledger[anomaly]` extra), plus training-time vs.
  current per-group statistics drift detection.
- Versioned, SHA256-verified model registry (`models/vN/`); a working
  example registry committed at `models/v1/`.
- CLI: `report`, `demo-data`, `detect`, `train`, `schema`.
- JSONL log schema contract (`schema.json`, `llmledger schema`).
- CI (GitHub Actions): core-only smoke test + full test matrix
  (Python 3.9 & 3.12) with `[anomaly,dev]` extras, plus `pip-audit`.
