"""Synthetic call log generator for demos and internal sanity testing.

Produces a realistic-looking mixture of calls across a handful of
(label, model) pairs, with a small, known number of injected anomalies
(unusually large token counts) so `llmledger detect` has something to find
on a first run, and so the internal anomaly sanity test can check that
detection actually finds them. The seed is fixed by default so demo runs
and the sanity test are deterministic, not flaky.
"""

from __future__ import annotations

import random
from typing import NamedTuple

from .tracker import CostTracker

DEFAULT_SEED = 42

_LABELS_MODELS = [
    ("summarize", "gpt-4o"),
    ("summarize", "gpt-4o-mini"),
    ("retrieval", "gpt-4o-mini"),
    ("chat", "claude-sonnet-4"),
    ("tool-call", "claude-haiku-3.5"),
]


class DemoCall(NamedTuple):
    label: str
    model: str
    input_tokens: int
    output_tokens: int
    is_anomaly: bool


def generate_demo_calls(
    n_normal: int = 200,
    n_anomalies: int = 10,
    seed: int = DEFAULT_SEED,
) -> list[DemoCall]:
    """Return a shuffled list of synthetic calls: `n_normal` calls with
    realistic token counts drawn from a per-(label, model) baseline, plus
    `n_anomalies` calls with token counts far outside that baseline.
    """
    rng = random.Random(seed)
    calls: list[DemoCall] = []

    for _ in range(n_normal):
        label, model = rng.choice(_LABELS_MODELS)
        input_tokens = max(1, int(rng.gauss(800, 150)))
        output_tokens = max(1, int(rng.gauss(150, 40)))
        calls.append(DemoCall(label, model, input_tokens, output_tokens, False))

    for _ in range(n_anomalies):
        label, model = rng.choice(_LABELS_MODELS)
        input_tokens = int(rng.uniform(8_000, 20_000))
        output_tokens = int(rng.uniform(2_000, 5_000))
        calls.append(DemoCall(label, model, input_tokens, output_tokens, True))

    rng.shuffle(calls)
    return calls


def write_demo_log(
    path,
    *,
    n_normal: int = 200,
    n_anomalies: int = 10,
    seed: int = DEFAULT_SEED,
) -> list[tuple[dict, bool]]:
    """Generate synthetic calls and log them via a real `CostTracker` at
    `path`. Returns `(logged_record, is_anomaly)` pairs in the order they
    were written, so tests can check detection recall/precision without
    the anomaly flag needing to live in the on-disk record itself.
    """
    tracker = CostTracker(path)
    calls = generate_demo_calls(n_normal=n_normal, n_anomalies=n_anomalies, seed=seed)
    results = []
    for call in calls:
        record = tracker.log_call(
            label=call.label,
            model=call.model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
        )
        results.append((record, call.is_anomaly))
    return results
