from __future__ import annotations

import pytest

from llm_burnwatch.tracker import CostTracker


@pytest.fixture(autouse=True)
def _reset_pii_warning_flag():
    """`CostTracker._warned_about_extra_length` is a class-level flag so the
    PII warning fires only once per process, not once per log file. Without
    resetting it between tests, whichever test happens to trigger it first
    (an ordering detail, not something tests should depend on) would make
    every later test that expects to see the warning fail silently.
    """
    CostTracker._warned_about_extra_length = False
    yield
    CostTracker._warned_about_extra_length = False
