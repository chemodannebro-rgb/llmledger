# Frequency detector

**Catches:** a runaway agent loop, or any other burst of calls that's
anomalously frequent — this is the only detector that looks at *how many*
calls happened in a time window, rather than what each call cost.

**Always available, no dependencies. Disabled by default** — `detect`
decides whether to actually run it for a given log, based on whether the
log has enough calendar span for a seasonal comparison.

## Why disabled by default

Without a notion of expected call volume by time-of-day/day-of-week, a
routine "every Monday morning" burst looks statistically identical to a
runaway agent looping out of control. Rather than either accept that
false-positive rate or refuse to ship the detector at all, it ships off by
default and `detect` only turns it on once the log has enough history for
a seasonal baseline to make it worthwhile.

## The math

Calls are bucketed into fixed **60-second** windows
(`FREQUENCY_WINDOW_SECONDS`) per `(label, model)` group, plus one
log-wide "global" bucketing that catches a fan-out burst spread across many
different labels/models that no single group's own history would flag.

For each window, the detector computes the same modified z-score as the
[baseline detector](baseline.md) (`0.6745 * (count - median) / MAD`) over
that group's history of window counts, and flags a spike when
`z > 3.5` (`FREQUENCY_Z_THRESHOLD`).

An **absolute fail-safe** flags a window regardless of z-score once its
call count reaches **100** (`FREQUENCY_ABS_CALLS_PER_WINDOW`) — this covers
two cases the z-score can't: a first-ever burst with no prior windows to
compare against at all, and a burst so far past history that the
zero-MAD "extreme deviation" fallback would otherwise be the only thing
that could catch it.

## Seasonal (day-of-week × hour-of-day) baselines

Once a log spans at least **14 calendar days**
(`MIN_SEASONAL_SPAN_DAYS`, checked by date *range*, not record count), each
window is additionally compared against its own `(weekday, hour)` bucket's
history — built only from *other calendar dates* that share that
weekday/hour, never the window's own date. This is what lets a recurring
"every Monday 9am" burst stop being flagged once it's happened often
enough to be the expected pattern for that slot, while a burst that's
still new — or unusually large even for a normally-busy Monday morning —
is still caught. A `(weekday, hour)` bucket that doesn't yet have 5 samples
(`MIN_GROUP_SAMPLES`) worth of history from other dates falls back to the
flat, group-wide comparison.

## Tuning

`z_threshold`/`abs_threshold` aren't CLI flags — construct
`FrequencyDetector(z_threshold=..., abs_threshold=...)` directly if your
own traffic needs different sensitivity (see
`detectors/frequency_detector.py`).

## Known limitations

- Batch/offline like the baseline detector — windows are built from the
  whole input sequence at once, not accumulated incrementally. In
  `detect --follow`, this means the full rolling window is re-bucketed on
  every poll (see [Performance](../performance.md) for what that costs in
  practice).
- Records with a missing or unparseable timestamp can't be placed in a
  window and are silently excluded.
- Below the 14-day seasonal-coverage threshold, this detector effectively
  doesn't run at all (or runs with only the flat, non-seasonal comparison,
  depending on how `detect` invokes it) — a runaway loop in a
  brand-new log won't be caught by frequency alone; the
  [baseline](baseline.md)/[rules](rules.md) detectors are still active in
  the meantime.
