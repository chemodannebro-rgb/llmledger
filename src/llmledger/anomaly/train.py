"""Trains the IsolationForest anomaly-detection model and saves it via the
model registry (`registry.py`).

`scikit-learn` is imported at module level. This is safe for the
zero-dependency core guarantee because this module is only ever imported
from `cli.py`'s `train` command handler inside a `try/except ImportError`
block -- nothing on the `report`/`demo-data`/`detect`-without-a-model code
path imports `llmledger.anomaly.train`.
"""

from __future__ import annotations

from typing import Sequence

from sklearn.ensemble import IsolationForest

from .constants import CONTAMINATION, KEEP_LAST_DEFAULT
from .features import check_label_cardinality, compute_reference_stats, extract_features
from .registry import save_model


def train(
    records: Sequence[dict],
    *,
    model_dir,
    keep_last: int = KEEP_LAST_DEFAULT,
    contamination=CONTAMINATION,
):
    """Train an `IsolationForest` on `records` and save it (plus reference
    drift statistics) to the model registry at `model_dir`. Returns the
    saved version directory.
    """
    check_label_cardinality(records)
    X, _kept_indices = extract_features(records)
    if not X:
        raise ValueError(
            "no records had enough (label, model) or model-level history to "
            "train on; log more calls first"
        )

    model = IsolationForest(contamination=contamination, random_state=0)
    model.fit(X)

    return save_model(
        model_dir,
        model,
        n_examples=len(X),
        reference_stats=compute_reference_stats(records),
        keep_last=keep_last,
    )
