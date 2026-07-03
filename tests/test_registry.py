from __future__ import annotations

import concurrent.futures
import os
import stat

import pytest

from llmledger.anomaly.registry import (
    latest_version_dir,
    load_model,
    save_model,
)


def test_save_model_creates_v1_with_expected_files(tmp_path):
    model_dir = tmp_path / "models"
    version_dir = save_model(
        model_dir, {"fake": "model"}, n_examples=42, reference_stats={"a": 1}
    )

    assert version_dir == model_dir / "v1"
    assert (version_dir / "model.skops").exists()
    assert (version_dir / "metadata.json").exists()


def test_saved_files_have_0600_permissions(tmp_path):
    model_dir = tmp_path / "models"
    version_dir = save_model(
        model_dir, {"fake": "model"}, n_examples=1, reference_stats={}
    )
    for name in ("model.skops", "metadata.json"):
        mode = stat.S_IMODE(os.stat(version_dir / name).st_mode)
        assert mode == 0o600


def test_metadata_contains_expected_fields(tmp_path):
    import json

    model_dir = tmp_path / "models"
    version_dir = save_model(
        model_dir,
        {"fake": "model"},
        n_examples=42,
        reference_stats={"input_tokens": {"median": 100, "mad": 10}},
    )
    metadata = json.loads((version_dir / "metadata.json").read_text())

    assert metadata["version"] == 1
    assert metadata["n_examples"] == 42
    assert metadata["reference_stats"] == {"input_tokens": {"median": 100, "mad": 10}}
    assert "package_version" in metadata
    assert "created_at" in metadata
    assert "model_sha256" in metadata


def test_sequential_saves_allocate_sequential_versions(tmp_path):
    model_dir = tmp_path / "models"
    dirs = [
        save_model(model_dir, {"i": i}, n_examples=1, reference_stats={})
        for i in range(3)
    ]
    assert [d.name for d in dirs] == ["v1", "v2", "v3"]


def test_keep_last_prunes_old_versions(tmp_path):
    model_dir = tmp_path / "models"
    for i in range(7):
        save_model(model_dir, {"i": i}, n_examples=1, reference_stats={}, keep_last=3)

    remaining = sorted(p.name for p in model_dir.iterdir())
    assert remaining == ["v5", "v6", "v7"]


def test_latest_version_dir_returns_none_when_empty(tmp_path):
    assert latest_version_dir(tmp_path / "models") is None


def test_latest_version_dir_returns_highest_version(tmp_path):
    model_dir = tmp_path / "models"
    for i in range(3):
        save_model(model_dir, {"i": i}, n_examples=1, reference_stats={})
    assert latest_version_dir(model_dir).name == "v3"


def test_load_model_round_trips_the_object(tmp_path, capsys):
    model_dir = tmp_path / "models"
    version_dir = save_model(
        model_dir, {"payload": [1, 2, 3]}, n_examples=1, reference_stats={}
    )
    model, metadata = load_model(version_dir)
    assert model == {"payload": [1, 2, 3]}
    assert metadata["version"] == 1


def test_load_model_warns_to_trust_source(tmp_path, capsys):
    model_dir = tmp_path / "models"
    version_dir = save_model(model_dir, {"x": 1}, n_examples=1, reference_stats={})
    load_model(version_dir)
    captured = capsys.readouterr()
    assert "only load models from a source you trust" in captured.err


def test_load_model_warns_on_package_version_mismatch(tmp_path):
    import json

    model_dir = tmp_path / "models"
    version_dir = save_model(model_dir, {"x": 1}, n_examples=1, reference_stats={})
    metadata_path = version_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["package_version"] = "0.0.0-different"
    os.chmod(metadata_path, 0o600)
    metadata_path.write_text(json.dumps(metadata))

    import io
    import contextlib

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        load_model(version_dir)
    assert "may have changed" in stderr.getvalue()


def test_load_model_rejects_corrupted_model_file(tmp_path):
    model_dir = tmp_path / "models"
    version_dir = save_model(model_dir, {"x": 1}, n_examples=1, reference_stats={})

    model_path = version_dir / "model.skops"
    os.chmod(model_path, 0o600)
    with model_path.open("ab") as fh:
        fh.write(b"corruption")

    with pytest.raises(ValueError, match="integrity check"):
        load_model(version_dir)


def test_concurrent_saves_from_multiple_threads_get_unique_versions(tmp_path):
    model_dir = tmp_path / "models"
    n_saves = 20

    def _save(i):
        return save_model(
            model_dir, {"i": i}, n_examples=1, reference_stats={}, keep_last=1000
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        dirs = list(pool.map(_save, range(n_saves)))

    names = [d.name for d in dirs]
    assert len(set(names)) == n_saves, "expected every concurrent save to get a unique version"
