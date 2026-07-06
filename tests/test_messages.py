"""Enforces that all user-facing stderr output goes through
``llm_burnwatch._messages.warn``/``error`` instead of ad-hoc
``print(..., file=sys.stderr)`` or ``warnings.warn`` calls.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "llm_burnwatch"

# Files allowed to reference stderr/warnings directly (the single sanctioned
# implementation of warn()/error()).
EXEMPT_FILES = {"_messages.py"}

BYPASS_PATTERNS = [
    re.compile(r"print\([^)]*file\s*=\s*sys\.stderr"),
    re.compile(r"warnings\.warn\("),
]


def _iter_source_files():
    for path in PACKAGE_ROOT.rglob("*.py"):
        if path.name in EXEMPT_FILES:
            continue
        yield path


def test_no_direct_stderr_writes_outside_messages_module():
    offenders = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for pattern in BYPASS_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{line_no}")

    assert not offenders, (
        "Direct stderr/warnings usage found outside _messages.py "
        f"(use warn()/error() instead): {offenders}"
    )


def test_warn_and_error_use_expected_prefix(capsys):
    from llm_burnwatch._messages import warn, error

    warn("something happened")
    error("something failed")
    captured = capsys.readouterr()
    assert "[llm-burnwatch] warning: something happened" in captured.err
    assert "[llm-burnwatch] error: something failed" in captured.err
