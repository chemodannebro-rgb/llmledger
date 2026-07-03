from __future__ import annotations

import concurrent.futures
import os
import stat

import pytest

from llmledger.logreader import iter_log_records
from llmledger.tracker import CostTracker


def test_log_call_basic_and_report(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    tracker.log_call(label="summarize", model="gpt-4o", input_tokens=1000, output_tokens=200)
    tracker.log_call(label="summarize", model="gpt-4o", input_tokens=500, output_tokens=100)

    report = tracker.report()
    assert report["call_count"] == 2
    assert report["total_cost_micros"] > 0
    assert report["by_label_micros"]["summarize"] == report["total_cost_micros"]
    assert report["by_model_micros"]["gpt-4o"] == report["total_cost_micros"]
    assert report["pricing_last_updated"] == "2026-06-01"


def test_report_empty_log_does_not_raise(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    report = tracker.report()
    assert report["call_count"] == 0
    assert report["total_cost_micros"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", -1),
        ("input_tokens", None),
        ("output_tokens", -5),
        ("output_tokens", None),
        ("cached_input_tokens", -1),
    ],
)
def test_log_call_rejects_invalid_token_counts(tmp_path, field, value):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    kwargs = {"label": "x", "model": "gpt-4o", "input_tokens": 10, "output_tokens": 10}
    kwargs[field] = value
    with pytest.raises(ValueError):
        tracker.log_call(**kwargs)


def test_log_call_unknown_model_without_cost_raises(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with pytest.raises(ValueError, match="no pricing found"):
        tracker.log_call(label="x", model="totally-unknown-model", input_tokens=10, output_tokens=10)


def test_log_call_cost_override_skips_pricing_lookup(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    record = tracker.log_call(
        label="image-gen", model="unknown-image-model", input_tokens=0, output_tokens=0, cost=0.04
    )
    assert record["cost_micros"] == 40_000


def test_log_call_pricing_override(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    record = tracker.log_call(
        label="x",
        model="custom-model",
        input_tokens=1_000_000,
        output_tokens=0,
        pricing={"input_per_1m": 1.0, "output_per_1m": 2.0},
    )
    assert record["cost_micros"] == 1_000_000


def test_log_file_created_with_0600_permissions(tmp_path):
    path = tmp_path / "calls.jsonl"
    CostTracker(path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_long_extra_field_warns_once(tmp_path, capsys):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    long_text = "x" * 500
    tracker.log_call(label="x", model="gpt-4o", input_tokens=1, output_tokens=1, extra=long_text)
    tracker.log_call(label="x", model="gpt-4o", input_tokens=1, output_tokens=1, extra=long_text)
    captured = capsys.readouterr()
    assert captured.err.count("risks leaking sensitive") == 1


def test_rotation_creates_backups_and_logreader_reads_all(tmp_path):
    # backup_count is generous (50) relative to how many rotations 50 tiny
    # records will trigger with max_bytes=300, so no backup gets evicted —
    # this isolates "rotation happened and logreader reads across all of the
    # resulting files correctly" from "backupCount bounds retained history"
    # (a separate, expected behavior covered by test_rotation_evicts_old_backups_beyond_backup_count).
    path = tmp_path / "calls.jsonl"
    tracker = CostTracker(path, max_bytes=300, backup_count=50)
    for i in range(50):
        tracker.log_call(label="x", model="gpt-4o", input_tokens=1, output_tokens=1, extra=f"call-{i}")

    backups = list(tmp_path.glob("calls.jsonl.*"))
    assert len(backups) > 1, "expected rotation to have created multiple backup files"

    records = list(iter_log_records(path))
    assert len(records) == 50
    seen = {r["extra"]["extra"] for r in records}
    assert seen == {f"call-{i}" for i in range(50)}


def test_rotation_evicts_old_backups_beyond_backup_count(tmp_path):
    # With a small backup_count, RotatingFileHandler intentionally deletes
    # the oldest backups once the count is exceeded — this is expected
    # bounded-retention behavior, not data corruption. logreader should
    # still read whatever remains without error.
    path = tmp_path / "calls.jsonl"
    tracker = CostTracker(path, max_bytes=300, backup_count=2)
    for i in range(50):
        tracker.log_call(label="x", model="gpt-4o", input_tokens=1, output_tokens=1, extra=f"call-{i}")

    records = list(iter_log_records(path))
    assert 0 < len(records) < 50


def test_concurrent_writes_from_multiple_threads_are_not_corrupted(tmp_path):
    path = tmp_path / "calls.jsonl"
    tracker = CostTracker(path)
    n_calls = 300

    def _log(i):
        tracker.log_call(label="x", model="gpt-4o", input_tokens=1, output_tokens=1, extra=str(i))

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(_log, range(n_calls)))

    records = list(iter_log_records(path))
    assert len(records) == n_calls
    seen = {r["extra"]["extra"] for r in records}
    assert seen == {str(i) for i in range(n_calls)}


def test_directory_mode_aggregates_multiple_process_files(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    tracker_a = CostTracker(log_dir)
    tracker_b = CostTracker(log_dir)
    tracker_a.log_call(label="a", model="gpt-4o", input_tokens=10, output_tokens=10)
    tracker_b.log_call(label="b", model="gpt-4o", input_tokens=10, output_tokens=10)

    jsonl_files = list(log_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 2, "each tracker instance should write to its own file"

    report = tracker_a.report()
    assert report["call_count"] == 2
    assert set(report["by_label_micros"]) == {"a", "b"}


def test_log_openai_response_adapter_extracts_cached_tokens(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 300},
        },
    }
    record = tracker.log_openai_response(response, label="chat")
    assert record["input_tokens"] == 700
    assert record["cached_input_tokens"] == 300
    assert record["output_tokens"] == 200
    assert record["model"] == "gpt-4o"


def test_log_anthropic_response_adapter_handles_cache_fields(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {
        "model": "claude-sonnet-4",
        "usage": {
            "input_tokens": 500,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 400,
            "output_tokens": 150,
        },
    }
    record = tracker.log_anthropic_response(response, label="chat")
    assert record["input_tokens"] == 600  # base + cache_creation
    assert record["cached_input_tokens"] == 400  # cache_read only
    assert record["output_tokens"] == 150
    assert record["model"] == "claude-sonnet-4"


class _FakeUsage:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeResponse:
    def __init__(self, model, usage):
        self.model = model
        self.usage = usage


def test_adapters_accept_attribute_style_sdk_objects(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = _FakeResponse(
        model="gpt-4o",
        usage=_FakeUsage(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=None),
    )
    record = tracker.log_openai_response(response, label="chat")
    assert record["input_tokens"] == 100
    assert record["cached_input_tokens"] == 0


def test_log_gemini_response_adapter_extracts_cached_tokens(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {
        "model_version": "gemini-1.5-pro",
        "usage_metadata": {
            "prompt_token_count": 1000,
            "cached_content_token_count": 300,
            "candidates_token_count": 200,
        },
    }
    record = tracker.log_gemini_response(response, label="chat", cost=0.0)
    assert record["input_tokens"] == 700
    assert record["cached_input_tokens"] == 300
    assert record["output_tokens"] == 200
    assert record["model"] == "gemini-1.5-pro"


def test_log_gemini_response_adapter_handles_missing_usage_metadata(tmp_path):
    # usage_metadata can be entirely absent, e.g. a safety-filter-blocked response.
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model_version": "gemini-1.5-pro", "usage_metadata": None}
    record = tracker.log_gemini_response(response, label="chat", cost=0.0)
    assert record["input_tokens"] == 0
    assert record["cached_input_tokens"] == 0
    assert record["output_tokens"] == 0


def test_log_gemini_response_adapter_prefers_explicit_model_over_model_version(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model_version": "gemini-1.5-pro", "usage_metadata": {}}
    record = tracker.log_gemini_response(
        response, label="chat", model="gemini-2.0-flash", cost=0.0
    )
    assert record["model"] == "gemini-2.0-flash"


def test_log_ollama_response_adapter_extracts_token_counts(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model": "llama3", "prompt_eval_count": 50, "eval_count": 20}
    record = tracker.log_ollama_response(response, label="chat", cost=0.0)
    assert record["input_tokens"] == 50
    assert record["output_tokens"] == 20
    assert record["cached_input_tokens"] == 0
    assert record["model"] == "llama3"


def test_log_ollama_response_adapter_handles_missing_eval_count(tmp_path):
    # An intermediate streaming chunk (done=False) lacks these counters entirely.
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model": "llama3", "done": False}
    record = tracker.log_ollama_response(response, label="chat", cost=0.0)
    assert record["input_tokens"] == 0
    assert record["output_tokens"] == 0
    assert record["cached_input_tokens"] == 0
