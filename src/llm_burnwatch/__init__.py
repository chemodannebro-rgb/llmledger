"""llm-burnwatch — local, zero-dependency cost tracking and anomaly detection
for LLM/agent calls.
"""

from .tracker import BudgetExceededError, CostTracker

__version__ = "1.0.9"

__all__ = ["CostTracker", "BudgetExceededError", "__version__"]
