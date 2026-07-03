"""Single point of user-facing warning/error output for llmledger.

Every part of the package that needs to print a warning or error message to
the user MUST go through :func:`warn` or :func:`error` instead of calling
``print(..., file=sys.stderr)`` or ``warnings.warn`` directly. This keeps the
``[llmledger]`` prefix consistent across the whole codebase and is enforced
by ``tests/test_messages.py``, which greps the package source for any
stderr-writing call that bypasses these two functions.
"""

from __future__ import annotations

import sys

_PREFIX = "[llmledger]"


def warn(message: str) -> None:
    """Print a warning message to stderr with the standard prefix."""
    print(f"{_PREFIX} warning: {message}", file=sys.stderr)


def error(message: str) -> None:
    """Print an error message to stderr with the standard prefix."""
    print(f"{_PREFIX} error: {message}", file=sys.stderr)
