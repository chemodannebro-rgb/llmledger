"""Model registry: versioned storage for the trained anomaly-detection
model (`train.py` writes to it, `cli.py`'s `detect`/`train` read from it).

Each version lives in `models/v{N}/`, containing:
- `model.pkl` -- the pickled model (0600 permissions)
- `metadata.json` -- package version, creation timestamp, number of
  training examples, sha256 of `model.pkl`, and reference statistics used
  to detect drift later (0600 permissions)

Loading a model recomputes and checks the sha256 against `metadata.json`
*before* unpickling. This does not eliminate the fundamental risk of
`pickle.load` on untrusted input (that is not possible), but it does catch
a corrupted or substituted model file, which is a real and useful
integrity check. A warning is also printed reminding the caller to only
load models from a source they trust.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__ as PACKAGE_VERSION
from .._messages import warn
from .constants import KEEP_LAST_DEFAULT


def _version_dir(model_dir: Path, version: int) -> Path:
    return model_dir / f"v{version}"


def _existing_versions(model_dir: Path) -> list[int]:
    if not model_dir.exists():
        return []
    versions = []
    for child in model_dir.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            versions.append(int(child.name[1:]))
    return sorted(versions)


def _allocate_version_dir(model_dir: Path) -> tuple[int, Path]:
    """Atomically claim the next version number.

    Uses exclusive directory creation (`os.mkdir`, which raises
    `FileExistsError` if the directory already exists) so that two
    concurrent `train()` calls racing for the same version number cannot
    silently clobber each other's output -- the loser retries the next
    number instead.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    candidate = max(_existing_versions(model_dir), default=0) + 1
    while True:
        path = _version_dir(model_dir, candidate)
        try:
            os.mkdir(path)
            return candidate, path
        except FileExistsError:
            candidate += 1


def save_model(
    model_dir,
    model: Any,
    *,
    n_examples: int,
    reference_stats: dict,
    keep_last: int = KEEP_LAST_DEFAULT,
) -> Path:
    """Pickle `model` into a newly allocated version directory, write its
    metadata (including a sha256 integrity hash and reference statistics
    for later drift detection), chmod both files 0600, and prune old
    versions beyond `keep_last`. Returns the new version directory.
    """
    model_dir = Path(model_dir)
    version, version_dir = _allocate_version_dir(model_dir)

    model_path = version_dir / "model.pkl"
    with model_path.open("wb") as fh:
        pickle.dump(model, fh)
    os.chmod(model_path, 0o600)

    model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    metadata = {
        "version": version,
        "package_version": PACKAGE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_examples": n_examples,
        "model_sha256": model_sha256,
        "reference_stats": reference_stats,
    }
    metadata_path = version_dir / "metadata.json"
    with metadata_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
    os.chmod(metadata_path, 0o600)

    _prune_old_versions(model_dir, keep_last)
    return version_dir


def _prune_old_versions(model_dir: Path, keep_last: int) -> None:
    versions = _existing_versions(model_dir)
    to_remove = versions[:-keep_last] if keep_last > 0 else versions
    for v in to_remove:
        shutil.rmtree(_version_dir(model_dir, v), ignore_errors=True)


def latest_version_dir(model_dir) -> Path | None:
    model_dir = Path(model_dir)
    versions = _existing_versions(model_dir)
    if not versions:
        return None
    return _version_dir(model_dir, versions[-1])


def load_model(version_dir) -> tuple[Any, dict]:
    """Load the model and metadata from `version_dir`.

    Raises `ValueError` if the model file's sha256 does not match the
    value recorded in `metadata.json` at save time (corruption or
    substitution) -- this check happens before any unpickling. On
    success, prints a warning reminding the caller to only load models
    from a trusted source, plus a separate warning if the metadata's
    `package_version` differs from the currently installed llmledger
    version (feature engineering may have changed between versions).
    """
    version_dir = Path(version_dir)
    model_path = version_dir / "model.pkl"
    metadata_path = version_dir / "metadata.json"

    with metadata_path.open("r", encoding="utf-8") as fh:
        metadata = json.load(fh)

    actual_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    expected_sha256 = metadata.get("model_sha256")
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"model file {model_path} failed integrity check (sha256 "
            "mismatch); it may be corrupted or was substituted. Refusing "
            "to load. Re-run `llmledger train` to regenerate it."
        )

    warn(
        f"loading model from {version_dir}; only load models from a source "
        "you trust (unpickling is not safe against untrusted/adversarial "
        "input)."
    )

    if metadata.get("package_version") != PACKAGE_VERSION:
        warn(
            f"model was trained with llmledger {metadata.get('package_version')!r}, "
            f"currently installed is {PACKAGE_VERSION!r}; feature engineering "
            "may have changed. Consider running `llmledger train` again."
        )

    with model_path.open("rb") as fh:
        model = pickle.load(fh)

    return model, metadata
