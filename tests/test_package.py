from __future__ import annotations

import re
from pathlib import Path

import llm_burnwatch
from llm_burnwatch import BudgetExceededError, CostTracker, __version__
from llm_burnwatch.tracker import BudgetExceededError as _TrackerBudgetExceededError
from llm_burnwatch.tracker import CostTracker as _TrackerCostTracker

_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_top_level_exports_are_the_tracker_module_s_originals():
    # docs/api.md documents `from llm_burnwatch import CostTracker,
    # BudgetExceededError, __version__` as the public Python API -- these
    # must be the same objects as `llm_burnwatch.tracker`'s, not copies.
    assert CostTracker is _TrackerCostTracker
    assert BudgetExceededError is _TrackerBudgetExceededError


def test_all_is_exactly_the_three_documented_names():
    # docs/api.md's "Internal (not covered by semver)" section relies on
    # `__all__` staying deliberately minimal -- anything else importable
    # from the top-level package (there is nothing else today) is not a
    # public API commitment.
    assert llm_burnwatch.__all__ == ["CostTracker", "BudgetExceededError", "__version__"]


def test_version_matches_pyproject_toml():
    # ARCHITECTURE.md's Versioning section says these two are kept in
    # lockstep manually -- this test is the automated check for that.
    pyproject_text = _PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject_text)
    assert match is not None, "pyproject.toml has no top-level version field"
    assert __version__ == match.group(1)
