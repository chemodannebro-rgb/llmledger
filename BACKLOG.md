# Backlog

This file is the single source of truth for "what's next" on llmledger.
It replaces the informal split between a "big team" (architecture/ML/
security-level review) and a "mini team" (UI/UX/frontend review) used in
earlier planning sessions — those two groups are merged below into one
roster, with overlapping roles consolidated so no one reviews the same
thing twice under a different hat.

## Team

| Role | Owns | Consolidation note |
|---|---|---|
| **Product Director** *(new)* | Backlog ownership & grooming, prioritization, sequencing, resolving trade-offs between roles (e.g. "ship a screenshot" vs. "zero image bloat"), flagging which items need the project owner's explicit go/no-go before work starts | New role — nobody previously owned "is this worth doing and in what order," only "how would we build it" |
| **CTO / Tech Lead** | Architecture, scope guardrails (zero required core dependencies, no network calls — both enforced by tests), technical sign-off on any cross-cutting trade-off | Unchanged |
| **Backend/Core Engineer** | `CostTracker`, SDK adapters, log format/schema, CLI plumbing | Unchanged |
| **ML Engineer** | Baseline (z-score) + `IsolationForest` anomaly detection, model registry, drift detection | Unchanged |
| **Security Engineer** | Model registry trust boundary, `SECURITY.md`, supply chain (`skops`, `pip-audit`), no-network guarantee | Unchanged |
| **Frontend/UX Engineer** | Dashboard HTML/CSS, layout, responsive/dark-mode behavior | **Merged**: former separate "UX/UI" and "Designer" roles from the mini team collapsed into one — for an artifact as small as a single static HTML file, a design hand-off between two people was pure overhead |
| **QA/Test Engineer** | Full `pytest` suite (backend + frontend), *and* browser-based visual verification (screenshots, viewport resize, DOM checks) | **Merged**: former mini-team "frontend tester" absorbed here — this session is the concrete proof it was redundant as a separate role: the same pass that ran `pytest` also caught the mobile CSS overlap bug via a screenshot, something a structural-only test missed |
| **Technical Writer/Docs** | README, `CHANGELOG.md`, `SECURITY.md` wording, the schema/README drift-guard test | Unchanged |
| **Marketing/DevRel** | Portfolio narrative, positioning, README visuals | Unchanged |

Net effect: 2 roles removed as duplicates (separate "Designer" and
"frontend tester"), 1 role added (Product Director) — same or better
coverage with fewer distinct reviewers per change.

## How items are prioritized

- **P0** — next up; clear value, no open design conflict, no external
  sign-off needed beyond normal review.
- **P1** — valuable, sequenced after current P0s.
- **P2** — nice-to-have / exploratory; pick up opportunistically.
- **Needs decision** — before any code is written, Product Director
  must get an explicit call from the project owner, because the item
  trades off against a design principle the README already states as a
  guarantee (zero-dependency core, no network calls, plain-text
  readable log, "portfolio project, no support").

## Backlog

| # | Item | Owner(s) | Priority | Notes |
|---|---|---|---|---|
| 1 | **PyPI publication** (`pip install llmledger`, CI publish workflow, package-name check) | CTO, Marketing/DevRel, Product Director | Needs decision | Not rejected — deliberately deferred (per project owner). Marketing case: removes the git-clone step for anyone evaluating the portfolio. CTO/Security case: a real PyPI package implies real external users and quietly raises the support bar for a project the README currently calls "no SLA, use at your own risk." Product Director should bring both sides back to the project owner before scheduling. |
| 2 | **Structured explainability in `detect --json`** | ML Engineer | P0 | The z-score/median/MAD/feature breakdown already exists and is printed in human-readable form (`baseline.format_score()`); it just isn't exposed as a `reasons` field in the JSON output. Small, additive, no new dependency, makes the existing diagnostic machinery consumable by other tools. |
| 3 | **README dashboard screenshot/GIF** | Marketing/DevRel, Frontend/UX Engineer | P0 | README currently has zero images anywhere. The dashboard is the most visually demo-able artifact in the project and isn't shown once. Cheapest, highest-visibility portfolio improvement available. |
| 4 | **`--pricing-file` point overrides** (e.g. `--set model=rate`, not just whole-file replacement) | Backend/Core Engineer | P1 | Real gap found in the audit: today you either use the bundled `pricing.json` or replace the entire file. A single-model override is a common real need (new/unlisted model) that doesn't require a full custom file. |
| 5 | **CSV/tabular export** (`llmledger report --format csv`) | Backend/Core Engineer | P1 | Dashboard is HTML-only, `report` is stdout-text-only; no raw tabular output for anyone who wants to pull numbers into a spreadsheet. Zero new dependencies (stdlib `csv`). |
| 6 | **CONTRIBUTING.md** | Technical Writer/Docs, Product Director | P1 | No contribution guidance exists for a public GitHub repo. Even a short "how to run tests, what a PR needs (tests + docs), the zero-dependency-core rule" doc is a maturity signal and would have made this session's "screenshot every CSS change" lesson (item 8) discoverable instead of tribal knowledge. |
| 7 | **LangChain / CrewAI / AutoGen callback adapters** | ML Engineer, CTO | P1 | Real gap: only raw-SDK adapters (OpenAI/Anthropic/Gemini/Ollama) exist today, no agent-framework adapters. CTO constraint: must ship as a separate optional extra (e.g. `llmledger[langchain]`), never pull a framework's transitive dependencies into the zero-dependency core. |
| 8 | **Process rule: visual check required for any dashboard CSS/HTML change** | QA/Test Engineer, Product Director | P1 (process, not code) | Not a code item — a lesson from this session. Structural/grep tests (`"@media (max-width: 600px)" in result`) passed while a real overlapping-element bug shipped. Until there's an appetite for a Playwright dependency, the rule is: no dashboard CSS change merges without at least one real screenshot at desktop + mobile width. Belongs in CONTRIBUTING.md (item 6) once that exists. |
| 9 | **Budget alerts / notification integration** (Slack, email, webhook) | CTO, Security Engineer, Product Director | Needs decision | Deferred, not rejected (per project owner). Currently the README states this as a hard boundary backed by a test that patches `socket.socket` to fail on any core command — i.e. it's not just "not built yet," it's actively tested-against. If this is wanted later, it must ship as a clearly optional, non-core extra, and the no-network-calls test/claim for the core commands has to be scoped explicitly to exclude it, not silently removed. |
| 10 | **Log-file-at-rest encryption** | Security Engineer, CTO, Product Director | Needs decision | Tension flagged, not a simple gap: README's own stated value prop is "a plain JSONL file... nothing leaves the machine... you can read it yourself." Transparent encryption cuts against "read it yourself." Worth a real decision (e.g. opt-in only, off by default) rather than treating it as an obvious missing feature. |
| 11 | **Inline period-cost sparkline in the dashboard header** | Frontend/UX Engineer | P2 | Nice-to-have complement to the per-day mini bars: a single small, fixed-width trend line across the *visible* period at the top of the page. Must stay fixed-width (the whole reason the old whole-log chart was removed in v0.4.0) — same bug class must not come back. |
| 12 | **Async logging mode for `CostTracker`** | Backend/Core Engineer | P2 | `CostTracker` is fully synchronous today. Only worth doing if a real use case (very high call volume, latency-sensitive caller) shows up — no evidence of that yet, so kept low priority/exploratory rather than scheduled. |

## Round 2 backlog — fresh brainstorm, verified against actual code

Items 1-12 above leaned heavily on the earlier code audit (gaps someone
had already named: PyPI, alerting, explainability, LangChain, etc.).
This second pass is the team actually re-reading the source looking for
things nobody had named yet — each item below was confirmed by reading
the relevant file before being added, not assumed.

| # | Item | Owner(s) | Priority | Notes |
|---|---|---|---|---|
| 13 | **`trace_id` is captured but functionally dead** | Backend/Core Engineer, Product Director | P1 | Verified in `tracker.py`: every adapter accepts and stores `trace_id`, but `build_report`/`dashboard`/`detect` never read it back — there is no way to link a multi-step call (e.g. retrieval + generation in one RAG turn) into a single "cost of this request" number. Only `by_label`/`by_model` aggregation exists today. A `--group-by trace_id` view (or a `report --trace-id <id>` lookup) would make an already-collected field actually useful instead of dead weight. |
| 14 | **Baseline anomaly detection has no time-of-day/day-of-week conditioning** | ML Engineer, CTO | P2 | Verified in `anomaly/baseline.py`: grouping is purely `(label, model)` history, nothing time-based. A legitimate recurring weekly batch job would get flagged as anomalous every single week forever, since it's scored against all-time history with no seasonal adjustment. Real fix is a genuine statistics change (not just a missing flag), so CTO should weigh it against the project's stated "simple, explainable stats over a fancier model" philosophy before scheduling. |
| 15 | **No change history for `pricing.json` itself** | Backend/Core Engineer, Technical Writer/Docs | P1 | `pricing.json` only carries a single `last_updated` date with no record of *what* changed since the previous snapshot. For a cost-tracking tool this is a real integrity gap: re-running `report` on the same log after updating `pricing.json` can silently change historical totals with no way to see why. A short `PRICING_CHANGELOG.md` (or a `previous_rate`/`changed` note per model) would close it cheaply. |
| 16 | **`@media print` stylesheet for the dashboard** | Frontend/UX Engineer | P2 | Not proposed before. The dashboard is a static single HTML file already — a print stylesheet (collapse the `<details>` open, drop hover/dark-mode-only styling) would let someone hand a monthly report to a stakeholder as a clean printed page/PDF, with zero new dependencies and no JS. |
| 17 | **No property-based/fuzz tests for the anomaly math** | QA/Test Engineer, CTO | P1 | Verified: `tests/` only has example-based cases for `_median_mad`/z-score (specific handcrafted inputs). Edge cases like all-identical values (MAD=0), single-sample groups, negative or extreme `cost_micros` are each tested individually but never fuzzed systematically. Would need `hypothesis` as a new **dev-only** dependency (`[dev]` extra) — doesn't touch the shipped zero-dependency core, but is still a new dependency the CTO should explicitly approve, since the project has so far been deliberately minimal even in its dev tooling. |
| 18 | **No test-coverage measurement in CI** | QA/Test Engineer | P2 | Nothing tracks how much of the code the 123 passing tests actually exercise (e.g. the dashboard's dark-mode CSS branch, or registry error paths). Adding `pytest-cov` to `[dev]` and reporting a number in CI (no hard gate yet, just visibility) is low-risk and would surface untested branches like item 17 before they cause a real bug — the same class of blind spot that let the mobile CSS overlap bug (v0.4.0) ship past the existing test suite. |

## Round 2 — product-level ideas (not code)

| # | Item | Owner(s) | Priority | Notes |
|---|---|---|---|---|
| 19 | **Explicit ICP ("who is this for") line in README** | Product Director, Marketing/DevRel | P1 | Today the README describes *what* it does but not *who* specifically it's for. It quietly serves two different users at once — a solo dev with one JSONL file, and a small team via directory mode/multi-process — without ever saying so. A one-line "built for: a solo builder shipping their own LLM feature who wants cost/anomaly visibility without adopting a full observability platform" would make the positioning land faster for a reviewer skimming the repo. |
| 20 | **Live-hosted demo dashboard (GitHub Pages), not just a code sample** | Marketing/DevRel, Frontend/UX Engineer | P2 | Right now, seeing the dashboard requires cloning and running `demo-data` + `dashboard` yourself. Since the dashboard is already a single static HTML file with zero JS/network calls, publishing the generated demo output to GitHub Pages costs nothing extra and lets anyone see it live in one click — the single highest-leverage, lowest-effort thing for a portfolio piece whose best artifact is visual. |

## Already covered by existing tests (checked before adding, not duplicated here)

- End-to-end `dashboard` CLI smoke test (file gets written, correct
  permissions, correct exit codes on bad input) — already exists in
  `tests/test_dashboard.py`.
- README/`schema.json` drift guard — already exists
  (`test_readme_log_format_section_mentions_all_schema_fields`, added
  in v0.4.0).
