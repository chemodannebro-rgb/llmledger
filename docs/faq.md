# FAQ

## Does llm-burnwatch ever call the network?

The core (`report`/`demo-data`/`schema`/`validate`/`dashboard`/`detect`/
`train`/`budget`) never does — enforced by a test that patches
`socket.socket` to raise if any of them tries to open one. There are two
explicit, opt-in exceptions, both off unless you supply a flag/argument:
`pricing import <url>`, and `detect --follow`'s webhook/Slack/Telegram
sinks. See [Security model](security.md) for exactly what each does and
doesn't protect against. `detect --follow --exec-sink` is a related,
non-network exception — it runs a local command, not a network call.

## Does llm-burnwatch store my prompts or completions?

No — a logged record is `label`/`model`/`input_tokens`/`output_tokens`/
`cost_micros` (plus optional `cached_input_tokens`/`trace_id`), never the
prompt or completion text. The SDK adapters (`log_openai_response()`,
etc.) only read usage/metadata fields off the response object, not its
content.

The one place raw content *could* end up in the log is the optional
free-form `extra` object, if you put it there yourself — `log_call()`
warns (once) if any `extra` field is a string longer than 200 characters,
since that's a sign you might be logging raw prompt/response content
rather than a short piece of metadata (e.g. `workflow_id`). This is a
one-time nudge, not a validation that blocks the call — llm-burnwatch has
no way to know what's actually in a field you choose to populate.

## Why didn't my alert fire?

A few common reasons a call you expected to be flagged wasn't:

- **Insufficient data.** The [baseline](detectors/baseline.md)/
  [CUSUM](detectors/cusum.md) detectors need at least 5 records
  (`MIN_GROUP_SAMPLES`) for the exact `(label, model)` pair before trusting
  group-relative statistics; below that, they fall back to the model's
  stats across all labels, and below *that*, a call is marked
  `insufficient_data` (baseline) or silently skipped for that group/feature
  (CUSUM) rather than either flagged or silently treated as normal.
- **Seasonal coverage not yet reached.** The
  [frequency detector](detectors/frequency.md) needs a log spanning at
  least 14 calendar days before its seasonal (weekday × hour) comparison
  kicks in, and is disabled by default until then — a burst in a brand-new
  log may not trip it at all.
- **Cold start.** Any of the statistical detectors need real history to
  compare against; the very first calls for a new `(label, model)` pair
  can't be anomalous relative to a history that doesn't exist yet.
- **You're looking at the wrong detector for the shape of the problem.** A
  single expensive call is the [baseline detector's](detectors/baseline.md)
  job; a *sustained* rise that stays under that threshold call-by-call is
  [CUSUM's](detectors/cusum.md); a burst of otherwise-normal calls is
  [frequency's](detectors/frequency.md); an explicit hard limit you set
  yourself needs [rules](detectors/rules.md) or
  [budget](detectors/budget.md) configured, since neither runs with any
  built-in default.
- **`detect --follow` only re-analyzes a fixed-size rolling window**
  (5000 records, `FOLLOW_WINDOW_SIZE`) and only reads new lines appended
  at the log's current path — rotated backup files aren't read while
  following, and the ML cross-check / log-wide cardinality warning that
  one-shot `detect` prints don't run in `--follow` mode at all.

## Are the detectors proof of a real problem?

No — both the baseline/CUSUM/frequency detectors are diagnostic aids: they
flag statistically unusual calls, they don't confirm errors, and they can
miss real ones. `report`/`detect` print this disclaimer, plus the pricing
data's `last_updated` date, on every run. The [rules](detectors/rules.md)
and [budget](detectors/budget.md) detectors are different — they flag an
explicit violation of a policy you configured, not a statistical judgment
call, so a `rules`/`budget` alert firing means exactly what you told it to
mean.

## What if my log has more than one process writing to it?

Point `--log-file` at a directory instead of a single file — each process
gets its own file, and `report`/`detect`/`dashboard` read and merge the
whole directory. A single non-rotated, non-directory file with more than
about 200,000 records triggers a warning recommending one of the above; see
[Scale and rotation](https://github.com/chemodannebro-rgb/llm-burnwatch#scale-and-rotation)
in the README.

## Where do I ask something not covered here?

Open a GitHub issue — see [Security model](security.md#reporting-a-vulnerability)
for the same process, applied to non-security questions too (there's no
separate support channel for this early-stage project).
