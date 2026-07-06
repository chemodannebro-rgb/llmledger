"""Trains the IsolationForest anomaly-detection model and saves it via the
model registry (`registry.py`).

`scikit-learn` is imported at module level. This is safe for the
zero-dependency core guarantee because this module is only ever imported
from `cli.py`'s `train` command handler inside a `try/except ImportError`
block -- nothing on the `report`/`demo-data`/`detect`-without-a-model code
path imports `llm-burnwatch.anomaly.train`.
"""

from __future__ import annotations

import random
from typing import Sequence

from sklearn.ensemble import IsolationForest

from .constants import (
    CONTAMINATION,
    EVAL_HOLDOUT_FRACTION,
    EVAL_HOLDOUT_MIN_EXAMPLES,
    EVAL_HOLDOUT_SEED,
    KEEP_LAST_DEFAULT,
)
from .features import check_label_cardinality, compute_reference_stats, extract_features
from .registry import save_model


def _evaluate_holdout(X: list, *, contamination) -> dict:
    """Fit a throwaway `IsolationForest` on a deterministic subset of `X`
    and evaluate it against the rest, so `train` can report an honest
    (if crude) eval metric instead of none at all.

    There's no ground truth for a real customer log (see the same caveat in
    `tests/test_anomaly_sanity.py`), so this cannot be a precision/recall
    metric -- it's a self-consistency check: what fraction of examples the
    model never saw during training does it still flag as anomalous. A
    fraction wildly different from `contamination` suggests the training
    data itself is unusually homogeneous or unusually noisy.
    """
    n = len(X)
    if n < EVAL_HOLDOUT_MIN_EXAMPLES:
        return {
            "holdout_used": False,
            "reason": (
                f"fewer than {EVAL_HOLDOUT_MIN_EXAMPLES} training examples "
                f"({n}); skipped the held-out split"
            ),
        }

    indices = list(range(n))
    random.Random(EVAL_HOLDOUT_SEED).shuffle(indices)
    n_holdout = max(1, int(n * EVAL_HOLDOUT_FRACTION))
    holdout_idx = set(indices[:n_holdout])

    X_train = [x for i, x in enumerate(X) if i not in holdout_idx]
    X_holdout = [x for i, x in enumerate(X) if i in holdout_idx]

    eval_model = IsolationForest(contamination=contamination, random_state=0)
    eval_model.fit(X_train)
    flagged = sum(1 for pred in eval_model.predict(X_holdout) if pred == -1)

    return {
        "holdout_used": True,
        "n_train_examples": len(X_train),
        "n_holdout_examples": len(X_holdout),
        "flagged_count": flagged,
        "flagged_fraction": flagged / len(X_holdout),
    }


def train(
    records: Sequence[dict],
    *,
    model_dir,
    keep_last: int = KEEP_LAST_DEFAULT,
    contamination=CONTAMINATION,
):
    """Train an `IsolationForest` on `records` and save it (plus reference
    drift statistics and a held-out eval metric) to the model registry at
    `model_dir`. Returns `(version_dir, eval_metrics)`.

    The saved/production model is fit on the *full* `records`, not just the
    training portion of the internal holdout split -- the split only exists
    to produce `eval_metrics`, not to shrink the final model's training data.
    """
    check_label_cardinality(records)
    X, _kept_indices = extract_features(records)
    if not X:
        raise ValueError(
            "no records had enough (label, model) or model-level history to "
            "train on; log more calls first"
        )

    eval_metrics = _evaluate_holdout(X, contamination=contamination)

    model = IsolationForest(contamination=contamination, random_state=0)
    model.fit(X)

    version_dir = save_model(
        model_dir,
        model,
        n_examples=len(X),
        reference_stats=compute_reference_stats(records),
        keep_last=keep_last,
        eval_metrics=eval_metrics,
    )
    return version_dir, eval_metrics
