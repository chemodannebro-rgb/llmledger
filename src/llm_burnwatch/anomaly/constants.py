"""Single place for every tunable constant used across llm-burnwatch.

These are intentionally NOT exposed as CLI flags / config file options:
each one is either a statistical-soundness parameter (not a matter of taste)
or a safety mechanism that should not be casually disabled.
"""

# Minimum number of samples required in a (label, model) group before the
# baseline/ML detectors trust group-relative statistics for it.
MIN_GROUP_SAMPLES = 5

# How many times larger the current group MAD must be vs. the reference MAD
# stored at train() time before `detect` prints a drift warning.
DRIFT_MULTIPLIER = 2.0

# String fields in `**extra` longer than this many characters trigger a
# one-time warning about the risk of logging raw prompt/response content.
PII_FIELD_LENGTH_THRESHOLD = 200

# IsolationForest contamination parameter: "auto" lets scikit-learn estimate
# it from the data instead of a value hand-tuned to our demo data.
CONTAMINATION = "auto"

# Robust modified z-score threshold (Iglewicz & Hoaglin), a standard value
# from the robust-statistics literature, not tuned by eye.
Z_SCORE_THRESHOLD = 3.5

# Number of model versions to keep in the registry; older versions are
# deleted automatically when a new one is trained.
KEEP_LAST_DEFAULT = 5

# If a single `detect`/`report` invocation reads more records than this from
# a single non-rotated, non-directory log file, warn that rotation or
# directory mode should be enabled.
SCALE_WARNING_THRESHOLD = 200_000

# If more than this fraction of (label, model) groups have fewer than
# MIN_GROUP_SAMPLES records, group-relative statistics are unreliable for
# most of the data -- warn about high label cardinality.
HIGH_CARDINALITY_WARNING_FRACTION = 0.5

# Finite numeric stand-in for a feature's z-score when MAD == 0 and the
# value still deviates from the group median (a true z-score would be
# infinite). Only used internally as an ML feature value, never shown to a
# human -- `baseline.format_score` renders the same situation as "extreme
# deviation" for display.
EXTREME_Z_SENTINEL = 100.0

# Fraction of training examples held out (deterministically) to self-evaluate
# the model against data it never trained on. The model actually saved to the
# registry is still fit on the FULL dataset afterwards -- this split only
# exists to produce an honest eval metric for `train` to print/store.
EVAL_HOLDOUT_FRACTION = 0.2

# Fixed seed for the holdout shuffle, so training on the same log always
# reports the same eval metric instead of a different random split per run.
EVAL_HOLDOUT_SEED = 0

# Below this many total training examples, holding out EVAL_HOLDOUT_FRACTION
# would leave too few points in either split to mean anything -- skip the
# holdout and say so explicitly instead of printing a metric computed on a
# handful of points.
EVAL_HOLDOUT_MIN_EXAMPLES = 20
