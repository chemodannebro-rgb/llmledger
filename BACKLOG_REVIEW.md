# Backlog team review — round 4

`BACKLOG.md` (items 1-30) was built over three rounds by pulling one or two
people in at a time (an audit pass, then two brainstorm passes). This
document is the first pass where **every role on the team reviews the whole
backlog at once**, on the record, and each contributes at least one item
nobody has named yet. CTO and Product Director close it out by reconciling
all of it into one final table, ordered strictly by priority.

Nothing below duplicates a Round 1-3 item under a new number — every new
item (31-38) was checked against actual source before being added, same bar
as Round 2/3.

---

## Product Director

**Review of 1-30:** the backlog has grown to 30 items across three
uncoordinated rounds with no defined cadence for re-prioritizing it —
nothing says *when* "Needs decision" items (1, 9, 10, 29) actually get
revisited, so they risk sitting forever. That's a process gap in how this
document itself is run, not a code gap.

**New item (31 in the earlier draft numbering, folded in below as #38):**
there is no stated success metric for this portfolio project (stars? a
specific number of "used this to debug a real incident" reports? just "I
personally use it"?). Without one, prioritizing marketing items (19, 20,
35) against engineering items (21, 22) is a matter of taste, not evidence —
every round so far has ordered them by gut feel.

**Sign-off:** endorses CTO's promotion of #21 to the top of the list. Will
own adding a recurring "revisit Needs-decision items" checkpoint once
CONTRIBUTING.md (#6) exists.

---

## CTO / Tech Lead

**Review of 1-30:** confirms #21 (registry race condition) is the correct
top item — it's the only one in the whole backlog that's an active
correctness bug rather than a missing feature, and it's cheap to fix (pin
the resolved version number before `detect` loads it, don't re-resolve
"latest" mid-operation). Also confirms #17 (property-based tests) is worth
approving as a dev-only `hypothesis` dependency — see #32 below, which is
exactly the kind of edge case a fuzzer would have surfaced before a human
had to go read `analyze()` line by line to find it.

**New item — no `ARCHITECTURE.md` stating the "core stays zero-dependency,
only extras add dependencies" rule as an explicit, standalone policy.**
Verified: this rule is currently enforced by one test
(`test_core_commands_make_no_network_attempts`) and stated in README prose
in two different sections ("Installation", "System boundaries"), but there
is no single doc a contributor can point to when a PR proposes a new
dependency. Items 7, 9, 10, and 17 in this backlog all independently
re-derive this same constraint from scratch in their own notes — that's a
sign the rule should be written down once, not re-argued per item.

**Sign-off:** approves scheduling #21 immediately; groups #22 and the new
#32 together as one "scale correctness" pass since they compound on the
same large-log scenario.

---

## Backend/Core Engineer

**Review of 1-30:** agrees #13 (`trace_id` dead weight), #15 (no
`pricing.json` history), #26 (`report` missing `--json`) are all small,
additive, and ready to schedule as-is.

**New item — no `llmledger validate` command.** Verified: `cli.py` only
exposes `report`, `dashboard`, `demo-data`, `detect`, `train`, `schema` —
there is no subcommand that runs a log file through the JSON-schema checks
`llmledger` already performs internally on its own writes, so a non-Python
client (Node.js, Go, ...) implementing the schema by hand (which the README
explicitly invites in the "Log format" section) has no way to check their
output against `schema.json` except by installing `llmledger` and reading
Python internals. A thin `llmledger validate --log-file <path>` that
reports the first N schema violations would close that gap directly for
the exact audience the README's own wording is aimed at.

**Sign-off:** flags #7 (LangChain/CrewAI/AutoGen) as the biggest single
item on the list — should not start before #37 (ARCHITECTURE.md) exists,
so the "own extra, no core dependency" boundary is written down first
rather than negotiated ad hoc mid-PR.

---

## ML Engineer

**Review of 1-30:** endorses #23 (`cached_input_tokens` not scored) and
#24 (`train` prints no metrics) as the two most concrete ML gaps. Notes
#14 (time-of-day/day-of-week conditioning) should stay P2/parked — it's a
real statistics change, not a config flag, and conflicts with the project's
stated preference for simple, explainable stats over a fancier model.

**New item — `analyze()` recomputes median/MAD from scratch for every
single record in a group, instead of once per group.** Verified in
`anomaly/baseline.py`: the outer loop (`for r in records`) calls
`_score_feature(...)` → `_median_mad(history)` again for every record `r`,
even though `history` (the group's full value list for that feature) is
identical for every record in the same group. For a group of size G this
recomputes the same median/MAD G times instead of once, making `analyze()`
effectively O(G² · len(FEATURES)) instead of O(G log G · len(FEATURES)) per
group. Harmless on the demo log's small groups; on a real high-volume
`(label, model)` pair with thousands of calls, this is the actual
bottleneck long before the "~200k records" whole-log warning
(`check_scale()`) would even trigger.

**Sign-off:** this is a pure refactor (hoist `_median_mad` out of the
per-record loop, keyed by group), no behavior/output change, no new
dependency — safe to schedule ahead of #14.

---

## Security Engineer

**Review of 1-30:** endorses #21's severity assessment — a race condition
that can point `detect` at a pruned model directory is also a security-
relevant integrity issue (silent fallback/crash on load), not purely a
concurrency nuisance. Continues to treat #10 (encryption) and #9 (alerting)
as "Needs decision," not gaps, consistent with prior rounds.

**New item — no provenance/tampering check on training data itself.**
Verified: `anomaly/train.py`'s `train()` reads whichever log file/records
it's given and calls `model.fit(X)` directly — there is no hash or
checksum of the training input recorded anywhere in the resulting
`model.skops` bundle's metadata (only the model's own SHA256 exists, per
`SECURITY.md`, which protects the *output* artifact, not the *input*). If
someone with write access to a shared log file/directory quietly inserted
records shaped to normalize what would otherwise be flagged as anomalous
(a "poison the baseline" attack), nothing would currently catch it. Lower
severity than #21 — it requires an attacker who already has log write
access, which is a narrower threat model than most items here — but worth
recording since `SECURITY.md` doesn't currently mention this trust
boundary at all.

**Sign-off:** recommends this stay P2 unless a real multi-writer/shared-
log deployment (see README's "directory mode for multiple processes")
becomes a stated use case, at which point it should be revisited as a
"Needs decision" instead.

---

## Frontend/UX Engineer

**Review of 1-30:** endorses #16 (`@media print`) and #11 (period-cost
sparkline, so long as it stays fixed-width — same constraint that fixed
the v0.4.0 bug). No objection to #20 (GitHub Pages demo) going ahead
independently of any dashboard code change.

**New item — no per-day anchor/deep-link in the daily journal.**
Verified: `dashboard.py`'s `_render_journal()` emits `<details
class="day">` with no `id` attribute on any entry. With 60+ collapsed days
in a long-period dashboard, there's no way to link directly to, or
bookmark, one specific day (e.g. to send a teammate "look at July 3") —
every visit starts fully collapsed and requires manually finding and
opening the right row. Adding `id="day-{date}"` costs nothing (no JS, no
new dependency) and is purely additive to the existing static HTML.

**Sign-off:** this is the cheapest UX fix on the list — smaller than #16
or #11, should slot in alongside whichever of those ships first.

---

## QA/Test Engineer

**Review of 1-30:** endorses #17 (property-based tests) and #30 (thin
`logreader.py`/`_messages.py` coverage) as the two highest-value testing
gaps, and #18 (coverage measurement in CI) as the cheapest way to make
gaps like these visible automatically instead of by manual code reading
(as this whole review round had to do).

**No new numbered item** — instead, a reinforcing note on existing #8
(the "visual check required" process rule): the mobile CSS overlap bug
from v0.4.0 is proof that *structural* tests (grep-for-a-CSS-rule) and
*visual* checks catch genuinely different bug classes, and #8 already
captures the right process fix. Considered proposing an automated visual-
regression tool here, but that would mean a new dependency (Playwright or
similar) — explicitly out of scope until CTO/Product Director decide the
project wants one, so this is deliberately left as reinforcement of #8
rather than a new item.

**Sign-off:** would like #18 (coverage in CI) sequenced early precisely
because it would have made gaps like ML Engineer's new #32 or ##23/24
visible as "untested branch" automatically, rather than requiring a full
manual line-by-line read of `baseline.py`/`train.py` to find them.

---

## Technical Writer/Docs

**Review of 1-30:** endorses #6 (CONTRIBUTING.md), #15 (pricing changelog),
#19 (ICP line).

**New item — `examples/basic_tracking.py` and `examples/full_pipeline.py`
were never updated for the `dashboard` command or `--since`/`--until`.**
Verified: `grep -l "dashboard\|since\|until" examples/*.py` returns no
matches in either file. Both examples predate the v0.3.0/v0.4.0 dashboard
work entirely — someone reading the examples directory today would not
know the dashboard command exists at all, despite it being the single most
visually demo-able feature in the whole project (see #3, #20).

**Sign-off:** this is a documentation-only fix, ships independently of any
other item, and directly reinforces the marketing case being made in #3
and #20 — a reader following the examples should land on the dashboard,
not miss it entirely.

---

## Marketing/DevRel

**Review of 1-30:** endorses #3 (dashboard screenshot) and #20 (live demo)
as the two highest-leverage portfolio improvements available, and #19
(ICP line) as the cheapest positioning fix.

**New item — README has no badges beyond CI.** Verified: `grep -n
"badge\|shields.io" README.md` returns only the one existing CI badge
(line 3). No license badge, no supported-Python-version badge. Cheap,
static, no network call at page-render time beyond the badge image itself
(same category as the existing CI badge, not a new precedent) — a fast
trust signal for a portfolio reviewer scanning the repo for ten seconds.

**Sign-off:** ranks this below #3/#19/#20 — those are actual visual/
positioning gaps, badges are a smaller polish item.

---

## CTO + Product Director — final synthesis

Both reviewed every item above (1-30 plus 31-38) together. Reconciliation
notes:

- **#21 stays the single top item.** It is the only correctness bug in the
  entire backlog; everything else is a missing feature or a documentation
  gap. Nothing raised in this review changes that.
- **#22 and the new #32 are sequenced together** as one "scale
  correctness" pass — both only matter on a large log, both are about the
  same class of problem (doing more work than necessary as records grow),
  and #32's fix is trivial enough to ship in the same PR as #22 without
  meaningfully increasing its scope.
- **#17 (property-based tests) is approved** as a new dev-only
  `hypothesis` dependency — CTO's sign-off above, plus the fact that #32
  is exactly the kind of case a fuzzer would have found mechanically
  instead of requiring a manual line-by-line read, is enough evidence to
  approve it now rather than leave it pending.
- **#37 (ARCHITECTURE.md) is pulled forward ahead of #7** (LangChain/
  CrewAI/AutoGen adapters) specifically because Backend/Core Engineer
  flagged that #7 keeps re-deriving the same "own extra, no core
  dependency" rule that #37 would state once, for good.
- **New items 31, 33, 34, 35 are folded into the existing P1/P2 bands**
  at the priority level their proposing role suggested — none of them
  changed the ranking of any item already in Round 1-3.
- **Item 38 (no defined success metric) stays a process note, not a
  scheduled item** — Product Director owns following up on it, not
  engineering.
- **"Needs decision" items (1, 9, 10, 29) are unchanged** — reconfirmed
  as deliberately deferred per the project owner, not reprioritized by
  this review, and are listed last because no code can be scheduled
  against them until that explicit decision happens.

### Final backlog — single table, ordered by priority

| Priority | # | Item | Owner(s) |
|---|---|---|---|
| **P0** | 21 | Race condition between concurrent `train`/`detect` on the same `model_dir` | Backend/Core Engineer, Security Engineer |
| **P0** | 2 | Structured explainability (`reasons` field) in `detect --json` | ML Engineer |
| **P0** | 3 | README dashboard screenshot/GIF | Marketing/DevRel, Frontend/UX Engineer |
| **P1** | 22 | `report`/`detect` materialize the entire log before processing | Backend/Core Engineer, CTO |
| **P1** | 32 | `analyze()` recomputes median/MAD per-record instead of once per group | ML Engineer |
| **P1** | 26 | `report` has no `--json` flag | Backend/Core Engineer |
| **P1** | 13 | `trace_id` captured but never read back | Backend/Core Engineer, Product Director |
| **P1** | 23 | `cached_input_tokens` not scored by either detector | ML Engineer |
| **P1** | 24 | `train` prints no evaluation metrics, no held-out split | ML Engineer |
| **P1** | 17 | No property-based/fuzz tests for anomaly math (approved: new dev-only `hypothesis` dep) | QA/Test Engineer, CTO |
| **P1** | 15 | No change history for `pricing.json` itself | Backend/Core Engineer, Technical Writer/Docs |
| **P1** | 36 | `examples/*.py` never updated for `dashboard`/`--since`/`--until` | Technical Writer/Docs |
| **P1** | 37 | No `ARCHITECTURE.md` stating the zero-dependency-core/extras-only rule | CTO |
| **P1** | 19 | Explicit ICP line in README | Product Director, Marketing/DevRel |
| **P1** | 4 | `--pricing-file` point overrides | Backend/Core Engineer |
| **P1** | 5 | CSV/tabular export (`report --format csv`) | Backend/Core Engineer |
| **P1** | 6 | CONTRIBUTING.md | Technical Writer/Docs, Product Director |
| **P1** | 31 | `llmledger validate` command | Backend/Core Engineer |
| **P1** | 7 | LangChain/CrewAI/AutoGen adapters (blocked on #37 landing first) | ML Engineer, CTO |
| **P1** | 8 | Process rule: visual check required for dashboard CSS/HTML changes | QA/Test Engineer, Product Director |
| **P2** | 34 | Per-day anchor/deep-link (`id="day-..."`) in the dashboard journal | Frontend/UX Engineer |
| **P2** | 11 | Inline period-cost sparkline in dashboard header (fixed-width) | Frontend/UX Engineer |
| **P2** | 16 | `@media print` stylesheet for the dashboard | Frontend/UX Engineer |
| **P2** | 18 | Test-coverage measurement in CI (`pytest-cov`, visibility only) | QA/Test Engineer |
| **P2** | 30 | Thin test coverage: `logreader.py` corruption edges, `_messages.py` | QA/Test Engineer |
| **P2** | 20 | Live-hosted demo dashboard (GitHub Pages) | Marketing/DevRel, Frontend/UX Engineer |
| **P2** | 35 | README badges beyond CI (license, Python versions) | Marketing/DevRel |
| **P2** | 14 | Baseline detector: no time-of-day/day-of-week conditioning | ML Engineer, CTO |
| **P2** | 25 | `demo_data.py` injects only one anomaly shape | ML Engineer, QA/Test Engineer |
| **P2** | 27 | No rollback/promote command for the model registry | Backend/Core Engineer |
| **P2** | 28 | `pricing.json` flat per-model, no tiers/provider disambiguation | Backend/Core Engineer, Product Director |
| **P2** | 33 | No provenance/tampering check on training data | Security Engineer |
| **P2** | 12 | Async logging mode for `CostTracker` | Backend/Core Engineer |
| **P2** | 38 | No defined success metric for the portfolio project (process, not code) | Product Director |
| **Needs decision** | 1 | PyPI publication | CTO, Marketing/DevRel, Product Director |
| **Needs decision** | 9 | Budget alerts / notification integration | CTO, Security Engineer, Product Director |
| **Needs decision** | 10 | Log-file-at-rest encryption | Security Engineer, CTO, Product Director |
| **Needs decision** | 29 | No idempotency/dedup safeguard against double-logging | Backend/Core Engineer, CTO |
