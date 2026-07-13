# CUSUM (level-shift) detector

> **Terminology note:** `detect`'s plain-text console output calls this a
> **"gradual cost increase"** rather than "level shift" — same detector,
> same alert, just a friendlier surface name for people who think in
> incidents, not algorithms (`Alert.detector == "cusum"` and
> `Alert.kind == "level_shift"` are unchanged in `--json`). This page keeps
> using "level shift" throughout because it's the more precise technical
> term for what the math below actually detects.

**Catches:** a sustained, cumulative rise in a group's `output_tokens` or
`cost_micros` relative to its own reference median — e.g. a prompt change
that quietly makes every response longer/pricier. This is a level shift
that no single call's own z-score would necessarily trip, because each
individual call can stay under the [baseline detector's](baseline.md)
threshold while still being part of a real, sustained shift.

**Always available, no dependencies. Enabled by default** — unlike the
[frequency detector](frequency.md), a sustained rise in tokens/cost isn't
subject to the same "every Monday morning" seasonal false-positive risk, so
there's no reason to ship it off.

## The math

A one-sided tabular CUSUM (Page, 1954; Montgomery, *Introduction to
Statistical Quality Control*) over `output_tokens` and `cost_micros`
independently — only these two features, unlike the baseline detector's
four, because a level shift that matters financially always shows up in
output volume and/or cost; `input_tokens`/`cached_input_tokens` are
caller-controlled (the prompt itself), not a symptom of a shift the callee
(model/prompt template) introduced.

For each new value, the detector accumulates `(value - median - slack)`,
resetting to zero whenever the running sum would go negative, and flags
the first record where the cumulative sum exceeds `h_multiplier * MAD`.
Reference median/MAD come from the group's own records passed into
`analyze()` — this detector never depends on a trained model.

Two tunables, both empirically chosen by simulation (not textbook
constants like the baseline detector's 3.5):

- **`CUSUM_H_MULTIPLIER = 12.0`** — how many reference MADs the cumulative
  sum must exceed before a sustained rise counts as a level shift. A raw
  MAD is a smaller unit than a normal-distribution sigma
  (MAD ≈ 0.6745 × sigma), so this is intentionally larger than the classic
  `h = 4–5 sigma` guidance for tabular CUSUM. Chosen so the false-positive
  rate stays under 1% across group sizes of 20–200 records on stable
  synthetic data, while still flagging a sustained 35% rise within a
  bounded number of records after it starts.
- **`CUSUM_SLACK_MULTIPLIER = 0.5`** — subtracted from each deviation
  before accumulating ("allowance"), so the cumulative sum only grows on a
  *persistent* rise and drifts back to zero under normal, mean-reverting
  fluctuation. Without it, a one-sided CUSUM can wander upward indefinitely
  on purely stable data. 0.5 MAD follows the standard recommendation of
  setting the slack to about half the smallest shift you want to reliably
  detect.

## Degrading gracefully with too little history

Same fallback as the baseline detector: a group needs at least **5**
records (`MIN_GROUP_SAMPLES`) of its own; below that, it falls back to the
model's records across all labels. If even that has fewer than 5, or the
reference MAD is `0` (no spread to normalize against), the group is
silently skipped for that feature.

## Tuning

`h_multiplier`/`slack_multiplier` aren't CLI flags — construct
`CusumDetector(h_multiplier=..., slack_multiplier=...)` directly if your
own traffic needs a different balance between sensitivity and
false-positive rate (see `detectors/cusum_detector.py`).

## Known limitations

- Only tracks **upward** shifts — a *drop* in tokens/cost is never treated
  as a "runaway" symptom worth alerting on.
- A level shift small enough to stay under `h_multiplier * MAD` indefinitely
  will never trigger — this detector is tuned for a clear, sustained shift,
  not a subtle one.
