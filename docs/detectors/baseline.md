# Baseline detector

**Catches:** a single call whose token counts or cost are unusual for its
`(label, model)` pair — a prompt-template change that suddenly triples
output length, a model swap that quietly changed pricing, a one-off
runaway response.

**Always available, no dependencies. Enabled by default.**

## The math

For each of `input_tokens`, `output_tokens`, `cost_micros`, and
`cached_input_tokens`, the detector computes the modified z-score of
Iglewicz & Hoaglin (1993):

```
M_i = 0.6745 * (x_i - median) / MAD
```

against the history of calls sharing the same `(label, model)` pair —
median/MAD (median absolute deviation) rather than mean/standard
deviation, so outliers already present in the history don't inflate the
spread and mask new ones the way a mean/stdev z-score would.

A call is flagged **anomalous** on a feature when `|M_i|` exceeds the
threshold, **3.5** — the standard cutoff from the same paper, not tuned by
eye against this project's own demo data.

## Degrading gracefully with too little history

- If the exact `(label, model)` pair has fewer than **5** records
  (`MIN_GROUP_SAMPLES`), the detector falls back to statistics for the
  model across all labels.
- If that's still too small, the call is marked `insufficient_data`
  (severity `info`) rather than silently treated as normal, or silently
  dropped. This is a distinct alert kind precisely so you can tell "nothing
  unusual" apart from "not enough data to say".
- A feature whose reference MAD is exactly `0` (every prior call had the
  identical value) can't produce a normal z-score; the detector instead
  flags any value that differs at all from that (zero-spread) median as an
  "extreme deviation" — the modified z-score's stand-in for what would
  otherwise be an infinite score.

## Tuning

The threshold isn't exposed as a CLI flag by design — it's a
statistical-soundness parameter (a standard value from the robust-statistics
literature), not a matter of taste. If you find you need a different
threshold for your own traffic, construct `BaselineDetector(threshold=...)`
directly rather than through the CLI (see `detectors/baseline_detector.py`).

## Known limitations

- Per-call only — a *sustained* rise that stays under this threshold call
  by call but adds up over many calls is not this detector's job; see the
  [CUSUM detector](cusum.md) for that.
- No notion of expected call *frequency* — a burst of otherwise-normal-sized
  calls doesn't trip this detector at all; see the
  [Frequency detector](frequency.md).
- Diagnostic aid, not proof — a flagged call is statistically unusual, not
  confirmed to be an error, and a real problem can still slip under the
  threshold. `report`/`detect` print this disclaimer, plus the pricing
  data's `last_updated` date, on every run.

## Optional: ML cross-check

With the `[anomaly]` extra installed and a model trained
(`llm-burnwatch train`), `detect` additionally runs an `IsolationForest` over
the same group-relative features as a second opinion. `detect` also
compares current per-group statistics against the ones recorded at
training time and warns if they've drifted apart (more than **2×** the
reference MAD, `DRIFT_MULTIPLIER`), as a signal that `llm-burnwatch train`
should be re-run. Both layers are diagnostic aids, not a guarantee.
