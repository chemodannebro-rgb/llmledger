# Security policy

`llm-burnwatch` is an early-stage project — there is no SLA, but reports are
still welcome.

## Reporting a vulnerability

Please open a GitHub issue on this repository describing the problem. There
is no dedicated security contact or private disclosure channel for this
project; treat any issue you open as public from the start.

## Model registry trust boundary

`llm-burnwatch train` (the `[anomaly]` extra) writes a versioned model registry
under `models/vN/`: a `model.skops` file plus a `metadata.json` recording,
among other things, a sha256 hash of `model.skops`. `llm-burnwatch detect` reads
this registry back for its ML cross-check.

What this protects against:

- **Corruption or accidental substitution.** `load_model()`
  (`src/llm_burnwatch/anomaly/registry.py`) recomputes the sha256 of
  `model.skops` and refuses to load if it doesn't match `metadata.json`.
- **Arbitrary code execution via deserialization.** Models are serialized
  with `skops.io`, not `pickle`. Unlike `pickle`, `skops` refuses by
  construction to construct any type outside an explicit trusted list, so a
  tampered or unexpected file is rejected at load time (`load_model()` also
  checks `skops.io.get_untrusted_types()` before deserializing).

What this does **not** protect against:

- **A coordinated substitution by the same author/commit.** If whoever
  controls the repository (or the CI job that runs `llm-burnwatch train`)
  replaces `model.skops` with a different model trained on different data,
  they can simply recompute the sha256 and write the new, matching value
  into `metadata.json` at the same time. The integrity check only detects
  a mismatch between the two files — it cannot tell a legitimate
  `llm-burnwatch train` run from a malicious one by the same party, because
  both produce an internally consistent pair of files.

This is a root-of-trust limitation, not a bug: no purely local, code-level
check can distinguish "the maintainer re-trained the model" from "the
maintainer (or anyone with commit/CI access) swapped in a different model"
— that distinction is a question of *who* you trust to touch the registry,
which is a process concern (e.g. code review on the diff introducing a new
`models/vN/` directory before merging), not something a checksum can
resolve. `load_model()` prints a warning to this effect every time it loads
a model, as a reminder to only load registries from a source you trust.

See also the [System boundaries](README.md#system-boundaries) section of
the README for `llm-burnwatch`'s no-network-calls guarantee.

## `pricing import <url>` network trust boundary

`llm-burnwatch pricing import <source>` is the one explicit, opt-in exception
to the no-network-calls guarantee above (see "Network boundaries" in
`ARCHITECTURE.md`). It never runs implicitly — only when you invoke this
exact subcommand with a URL.

What it does: fetches `<source>` over `http(s)://` (a 10 second timeout, a
10 MB response cap, rejection of any other URL scheme such as `file://`,
and refusal to follow a redirect that downgrades an `https://` source to a
plain `http://` response), parses it strictly as JSON (rejecting `Infinity`/`NaN`/non-object payloads),
extracts only numeric cost-per-token fields, and writes the result to
`~/.config/llm-burnwatch/pricing.json`. The fetched content is never executed —
it is read as data (numbers keyed by model name), the same way `report`/
`dashboard`/`detect` already read the packaged `pricing.json`.

What you're trusting when you run it: the content at the URL you supply.
Only import from a source you trust — a malicious or compromised URL could
supply inflated or deflated per-model rates, which would silently skew every
future `report`/`dashboard`/`detect` cost calculation until you re-import a
correct file or delete `~/.config/llm-burnwatch/pricing.json`. This is a data
(pricing accuracy) risk, not a code-execution risk.

## `detect --follow` state-file trust boundary

`detect --follow` persists its progress (the byte offset already consumed
from `--log-file`, and the current rolling analysis window) to
`<log-file>.llm-burnwatch-follow-state.json`, a plain JSON file written next
to the log with the same atomic-write pattern (`tempfile.mkstemp` +
`os.replace`) already used by `pricing import`.

What this protects against: a process killed mid-write never leaves a
half-written state file behind (the temp file is renamed into place only
after the write completes). At load time, the file's top-level shape is
validated (`offsets` must be an object, `window` a list) before its contents
are trusted; a state file that's missing, unreadable, not valid JSON, or the
wrong shape is never fatal — `--follow` warns and starts over from the
beginning of the log rather than crashing or silently misbehaving.

What this does **not** protect against: the state file is read back as data
(byte offsets and a list of previously seen log records) with no integrity
check analogous to the model registry's sha256 above. Someone with write
access to this file could hand-edit it to change the byte offset `--follow`
resumes from (causing it to skip or re-read parts of the log) or inject
arbitrary JSON objects into the persisted `window`, which would then be
re-analyzed by the detector registry on the next poll alongside genuine log
records. This is the same trust level as the log file itself: if you don't
trust everyone with write access to `--log-file`'s directory, you're
already trusting them not to tamper with the log, and the follow-state file
sitting alongside it carries no stronger guarantee.
