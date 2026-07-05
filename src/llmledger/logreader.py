"""Safe reading of llmledger JSONL logs.

Handles three realistic complications that a naive `open(path).readlines()`
would not:

1. Rotated backups (`path.1`, `path.2`, ...) created by
   `logging.handlers.RotatingFileHandler` — read in chronological order.
2. Directory mode: `log_file` may be a directory containing one `*.jsonl`
   file per process (the multi-process-safety design used instead of file
   locks) — all files in it are read and merged.
3. Corrupt/partial JSON lines (e.g. a process crashed mid-write) — skipped
   with a warning instead of raising, and counted.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ._messages import warn
from .anomaly.constants import SCALE_WARNING_THRESHOLD


def _rotated_backup_paths(base: Path) -> list[Path]:
    """Return existing rotated backups of `base`, oldest first.

    RotatingFileHandler renames on rollover so that `base.1` is the most
    recently rotated-out file and higher numbers are progressively older.
    """
    backups = []
    n = 1
    while True:
        candidate = base.with_name(base.name + f".{n}")
        if candidate.exists():
            backups.append(candidate)
            n += 1
        else:
            break
    return list(reversed(backups))


def _read_jsonl_file(path: Path, on_corrupt) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                on_corrupt(path, line_no)


def _iter_file_with_backups(path: Path, on_corrupt) -> Iterator[dict]:
    for f in _rotated_backup_paths(path) + [path]:
        yield from _read_jsonl_file(f, on_corrupt)


def iter_log_records(path) -> Iterator[dict]:
    """Yield log records (dicts) from `path`.

    `path` may be a single log file (its rotated backups are included
    automatically) or a directory containing one `*.jsonl` file per process
    (each file's own rotated backups are included too). Corrupt lines are
    skipped with a warning; a final summary count is printed once the whole
    log has been read.

    Raises FileNotFoundError if `path` does not exist at all.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"log path does not exist: {path}")

    corrupt_count = 0

    def on_corrupt(file_path, line_no):
        nonlocal corrupt_count
        corrupt_count += 1
        warn(f"skipping corrupt JSONL line {file_path}:{line_no}")

    if path.is_dir():
        for f in sorted(path.glob("*.jsonl")):
            yield from _iter_file_with_backups(f, on_corrupt)
    else:
        yield from _iter_file_with_backups(path, on_corrupt)

    if corrupt_count:
        warn(f"skipped {corrupt_count} corrupt log line(s) total")


def parse_date(timestamp) -> str | None:
    """Return the UTC calendar date (`YYYY-MM-DD`) for a schema `timestamp`
    string, or `None` if it's missing/unparseable.

    Normalizes a trailing `Z` to `+00:00` first: `datetime.fromisoformat`
    only accepts a bare `Z` suffix from Python 3.11 onward, but the schema
    (`schema.json`) is a contract for non-Python clients too, some of which
    may write `Z`-suffixed timestamps.
    """
    if not isinstance(timestamp, str):
        return None
    text = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def filter_by_period(
    records: list[dict], since: str | None, until: str | None
) -> list[dict]:
    """Keep only records whose UTC calendar date falls within
    `[since, until]` (both optional `YYYY-MM-DD` strings, inclusive).

    A no-op (returns `records` unchanged) when both bounds are `None`.
    Otherwise, records with a missing/unparseable `timestamp` can't be
    placed in the period and are dropped, with a single `warn()` for the
    whole skip rather than one per record.
    """
    if since is None and until is None:
        return records

    kept = []
    dropped = 0
    for record in records:
        date = parse_date(record.get("timestamp"))
        if date is None or (since and date < since) or (until and date > until):
            dropped += 1
            continue
        kept.append(record)

    if dropped:
        warn(
            f"{dropped} record(s) fell outside --since/--until or lacked a "
            "usable timestamp and were excluded from this period"
        )
    return kept


def check_scale(path, record_count: int) -> None:
    """Warn if a single call read an unexpectedly large number of records
    from a plain file that has neither rotation backups nor directory mode
    enabled — the two supported ways to keep per-file size bounded.
    """
    path = Path(path)
    if record_count <= SCALE_WARNING_THRESHOLD:
        return
    if path.is_dir():
        return
    if _rotated_backup_paths(path):
        return
    warn(
        f"read {record_count} records from a single non-rotated log file "
        f"({path}). Consider enabling rotation (max_bytes/backup_count) or "
        "directory mode (log_file pointing at a directory) to keep files "
        "bounded in size."
    )
