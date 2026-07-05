# Security policy

`llmledger` is a portfolio / demo engineering project, not a commercial
product — there is no SLA, but reports are still welcome.

## Reporting a vulnerability

Please open a GitHub issue on this repository describing the problem. There
is no dedicated security contact or private disclosure channel for this
project; treat any issue you open as public from the start.

## Model registry trust boundary

`llmledger train` (the `[anomaly]` extra) writes a versioned model registry
under `models/vN/`: a `model.skops` file plus a `metadata.json` recording,
among other things, a sha256 hash of `model.skops`. `llmledger detect` reads
this registry back for its ML cross-check.

What this protects against:

- **Corruption or accidental substitution.** `load_model()`
  (`src/llmledger/anomaly/registry.py`) recomputes the sha256 of
  `model.skops` and refuses to load if it doesn't match `metadata.json`.
- **Arbitrary code execution via deserialization.** Models are serialized
  with `skops.io`, not `pickle`. Unlike `pickle`, `skops` refuses by
  construction to construct any type outside an explicit trusted list, so a
  tampered or unexpected file is rejected at load time (`load_model()` also
  checks `skops.io.get_untrusted_types()` before deserializing).

What this does **not** protect against:

- **A coordinated substitution by the same author/commit.** If whoever
  controls the repository (or the CI job that runs `llmledger train`)
  replaces `model.skops` with a different model trained on different data,
  they can simply recompute the sha256 and write the new, matching value
  into `metadata.json` at the same time. The integrity check only detects
  a mismatch between the two files — it cannot tell a legitimate
  `llmledger train` run from a malicious one by the same party, because
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
the README for `llmledger`'s no-network-calls guarantee.
