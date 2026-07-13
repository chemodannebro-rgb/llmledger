# Contributing

`llm-burnwatch` is an early-stage project (see README's opening note — API
may change before v1.0), but it takes contributions seriously enough to
write this down instead of leaving it as tribal knowledge.

## Setup

```bash
git clone <this-repo> && cd llm-burnwatch
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

**Rule:** any change to `src/llm_burnwatch/dashboard.py`'s HTML/CSS output
must include, in the PR description, at least one real screenshot of the
generated dashboard at a normal desktop width and one at a narrow
(mobile-ish, ~375px) width. Generate one with:

```bash
llm-burnwatch demo-data --out /tmp/demo.jsonl
llm-burnwatch dashboard --log-file /tmp/demo.jsonl --out /tmp/dashboard.html
```

then open `/tmp/dashboard.html` in a browser, resize, and screenshot. This
is a process rule, not a lint check — there's no dependency (e.g.
Playwright) enforcing it automatically, so it relies on the PR author (and
reviewer) actually doing it.

## Performance

`scripts/bench.py` and `scripts/soak_follow.py` are dev-only (not part of
the installed package, not run in CI) benchmarks for the hot paths that
have measured thresholds in the release plan (`report` on a 1M-record log,
one `--follow` poll's full-window detector re-analysis). If your change
touches `tracker.py`'s `build_report()`, `detectors/engine.py`, or any
detector's `analyze()`, run:

```bash
.venv/bin/python3 scripts/bench.py
```

before/after your change and compare against `docs/performance.md`'s
numbers — a regression against those thresholds should be caught here, not
by a user filing an issue.

## Documentation site

`docs/` is an [mkdocs-material](https://squidfunk.github.io/mkdocs-material/)
site (the `docs` optional extra — a build-time tool, not a runtime
dependency of the package, see [`ARCHITECTURE.md`](ARCHITECTURE.md)). Build
and preview it locally:

```bash
pip install -e ".[docs]"
mkdocs serve
```

CI runs `mkdocs build --strict`, which fails the build on any broken
internal link or a nav entry pointing at a missing page — if you add a new
page, add it to `mkdocs.yml`'s `nav` too, or the strict build will catch
the omission. If the change is user-visible, prefer updating the relevant
`docs/*.md` page over (or in addition to) `README.md` — the docs site is
the more detailed, better-organized version of the same information.

### Russian translation

Every English page has a `<name>.ru.md` translation alongside it (`mkdocs-static-i18n`'s
`suffix` structure — e.g. `security.md` + `security.ru.md`), with English as
the default (root URL) and Russian at `/ru/`. Adding a new English page
without its `.ru.md` counterpart doesn't break the build (the plugin falls
back to the English content for the missing translation), but leaves that
page untranslated on the Russian site. When adding a translation:

- Translate prose; leave code blocks, CLI commands, function/constant
  names, and numbers/thresholds unchanged.
- Point internal links inside a `.ru.md` file at the `.ru.md` version of
  the target page (e.g. `[...](security.ru.md)`, not `[...](security.md)`)
  — otherwise the language switcher on that link would silently drop the
  reader back into English.
- If a new nav node's title needs translating, add it to `mkdocs.yml`'s
  `plugins.i18n.languages[locale: ru].nav_translations` map.
- Anchor links (`#some-heading`) can differ between languages, since the
  slug is generated from the (translated) heading text — verify with
  `mkdocs build --strict` and check the actual `id="..."` in the built
  `site/ru/.../index.html` if unsure, rather than guessing the slug.

## Style

- Match the existing code: docstrings explain *why*, not just *what* (see
  any module in `src/llm_burnwatch/anomaly/` for the pattern).
- `warn()`/`error()` from `_messages.py` are the only sanctioned
  stderr-writing functions — don't call `print(..., file=sys.stderr)`
  directly (enforced by a grep-based test).
- Constants that are statistical/safety parameters (not style preferences)
  belong in `anomaly/constants.py`, not hardcoded inline.

## Versioning

Semantic versioning, kept in lockstep between `pyproject.toml` and
`src/llm_burnwatch/__init__.py`. Document what changed in `CHANGELOG.md` as
part of the same PR that bumps the version.

[`docs/api.md`](docs/api.md) is the one place that says what's covered by
these commitments and what isn't — the criteria below only make sense
relative to that page's "Frozen contracts"/"Internal" split.

### Semver commitments (from v1.0)

- **MAJOR** — anything that changes shape or behavior for something listed
  in `docs/api.md`'s "Frozen contracts" section: a `schema_version` or
  `alert_schema_version` bump, removing or repurposing a documented CLI
  subcommand/flag, removing or changing the signature of a documented
  Python API method/class, renaming a documented environment variable,
  or changing the meaning of an existing `--json` key.
- **MINOR** — additive, backward-compatible changes: a new optional CLI
  flag or subcommand, a new optional `--json` key (per `alert_schema.json`'s
  own additive-keys policy, this doesn't require a schema version bump
  either), a new Python API method, a new default that a flag can still
  override (e.g. `report`'s default 30-day window, overridable with
  `--all-time`).
- **PATCH** — bug fixes, documentation, and internal refactors that don't
  change anything documented in `docs/api.md`. Changes to anything listed
  under that page's "Internal (not covered by semver)" section (detector
  internals, `anomaly/*`, `logreader.py`, etc.) are PATCH-level even if
  the change is substantial, precisely because nothing there is a
  commitment to begin with.

### Deprecation policy

Before removing or repurposing anything covered by the commitments above:

1. Keep the old behavior working, but call `warn()` (from `_messages.py`)
   when it's used, pointing at the replacement.
2. Add a `CHANGELOG.md` entry for the minor release that introduces the
   warning, stating what's deprecated and what to use instead.
3. Only remove it in a later MAJOR release — never in the same release
   that introduced the warning, and never in a MINOR/PATCH release.

Example already in the codebase: `report --rub-rate` is deprecated in favor
of `--fx-rate`/`--currency` — it still works today and calls `warn()` when
used. Its own removal was scheduled ("before v1.0") before this policy was
written down, so it's not a clean example of the full three-step cycle
above; the `warn()`-plus-`CHANGELOG.md`-entry mechanism it uses is still
the right one to copy for the next deprecation.

## Security

See [`SECURITY.md`](SECURITY.md) for the model registry's trust boundary,
the `pricing import` network trust boundary, and how to report a
vulnerability (there is no private disclosure channel for this project —
see `SECURITY.md` for what that means in practice).
