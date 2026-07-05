# Contributing

`llmledger` is a portfolio/demo engineering project (see README's opening
note — no support, no SLA), but it takes contributions seriously enough to
write this down instead of leaving it as tribal knowledge.

## Setup

```bash
git clone <this-repo> && cd llmledger
pip install -e ".[anomaly,dev]"
```

`[dev]` brings in `pytest`/`jsonschema`/`hypothesis`; `[anomaly]` brings in
`scikit-learn`/`skops` so the full test suite (including `train`/ML
cross-check tests) runs. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for why
these are separate optional extras rather than plain dependencies.

## Running tests

```bash
pytest tests/ -v
```

All tests must pass before a PR is merged. If you add a new CLI command or
change an existing one, also run it once by hand end-to-end (see README's
"Try it end to end" section) — the test suite mocks a lot, but nothing
catches a genuinely broken example script faster than actually running it
(`examples/full_pipeline.py` shipped broken for a while because nothing
executed it in CI; see `CHANGELOG.md`).

## What a PR needs

1. **Tests.** New behavior needs a new test; changed behavior needs its
   existing test updated to match. A PR that changes `src/` without
   touching `tests/` should be able to explain why (e.g. a pure
   docs/comment change).
2. **Documentation.** If the change is user-visible (a new CLI flag, a new
   command, a changed default), update `README.md`'s CLI table and/or the
   relevant `examples/*.py`. If it changes `pricing.json`, also update
   `PRICING_CHANGELOG.md` (see the rule at the top of that file).
3. **No accidental new dependencies.** If your change needs a third-party
   package, read [`ARCHITECTURE.md`](ARCHITECTURE.md) first — it must go
   behind a new or existing optional extra, never into the zero-dependency
   core, and it must not break
   `test_core_commands_make_no_network_attempts`.

## Dashboard (HTML/CSS) changes

`tests/test_dashboard.py` has structural tests (e.g. "the generated HTML
contains this `@media` rule", "this CSS class is present") — these are
useful regression guards, but they do **not** catch real visual/layout
bugs. A past change passed every structural test while shipping a layout
bug that only showed up when actually rendered in a browser at a narrow
width.

**Rule:** any change to `src/llmledger/dashboard.py`'s HTML/CSS output
must include, in the PR description, at least one real screenshot of the
generated dashboard at a normal desktop width and one at a narrow
(mobile-ish, ~375px) width. Generate one with:

```bash
llmledger demo-data --out /tmp/demo.jsonl
llmledger dashboard --log-file /tmp/demo.jsonl --out /tmp/dashboard.html
```

then open `/tmp/dashboard.html` in a browser, resize, and screenshot. This
is a process rule, not a lint check — there's no dependency (e.g.
Playwright) enforcing it automatically, so it relies on the PR author (and
reviewer) actually doing it.

## Style

- Match the existing code: docstrings explain *why*, not just *what* (see
  any module in `src/llmledger/anomaly/` for the pattern).
- `warn()`/`error()` from `_messages.py` are the only sanctioned
  stderr-writing functions — don't call `print(..., file=sys.stderr)`
  directly (enforced by a grep-based test).
- Constants that are statistical/safety parameters (not style preferences)
  belong in `anomaly/constants.py`, not hardcoded inline.

## Versioning

Semantic versioning, kept in lockstep between `pyproject.toml` and
`src/llmledger/__init__.py`. Document what changed in `CHANGELOG.md` as
part of the same PR that bumps the version.

## Security

See [`SECURITY.md`](SECURITY.md) for the model registry's trust boundary
and how to report a vulnerability privately instead of via a public issue.
