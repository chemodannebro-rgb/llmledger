"""Tests for `CostTracker.log_langchain_result()` -- the LangChain adapter
added in 0.9.5 (see CHANGELOG.md).

Covers both result shapes the adapter supports: the modern, standardized
`AIMessage.usage_metadata` field (current `langchain-core`, consistent field
names across providers) and the older `LLMResult.llm_output["token_usage"]`
shape from the `.generate()`/`.agenerate()` API (provider-specific field
names, commonly OpenAI-style). No `langchain` package is installed or
imported -- fixtures below are plain dicts/attribute objects mimicking only
the fields the adapter reads.
"""

from __future__ import annotations

from llm_burnwatch.tracker import CostTracker


def test_log_langchain_result_uses_usage_metadata_when_present(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "usage_metadata": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
        },
        "response_metadata": {"model_name": "gpt-4o"},
    }

    record = tracker.log_langchain_result(result, label="chat")

    assert record["model"] == "gpt-4o"
    assert record["input_tokens"] == 1000
    assert record["output_tokens"] == 200
    assert record["cached_input_tokens"] == 0


def test_log_langchain_result_subtracts_cache_read_from_input_tokens(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "usage_metadata": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_token_details": {"cache_read": 300},
        },
        "response_metadata": {"model_name": "gpt-4o"},
    }

    record = tracker.log_langchain_result(result, label="chat")

    assert record["input_tokens"] == 700
    assert record["cached_input_tokens"] == 300


def test_log_langchain_result_resolves_model_from_response_metadata_model_key(tmp_path):
    # Some providers populate response_metadata["model"] rather than "model_name".
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "usage_metadata": {"input_tokens": 10, "output_tokens": 5},
        "response_metadata": {"model": "claude-sonnet-4"},
    }

    record = tracker.log_langchain_result(result, label="chat")

    assert record["model"] == "claude-sonnet-4"


def test_log_langchain_result_explicit_model_overrides_response_metadata(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "usage_metadata": {"input_tokens": 10, "output_tokens": 5},
        "response_metadata": {"model_name": "gpt-4o"},
    }

    record = tracker.log_langchain_result(result, label="chat", model="gpt-4o-mini")

    assert record["model"] == "gpt-4o-mini"


def test_log_langchain_result_falls_back_to_llm_output_token_usage(tmp_path):
    # Older LLMResult shape from .generate()/.agenerate(): no usage_metadata,
    # usage nested under llm_output["token_usage"] instead.
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "llm_output": {
            "token_usage": {"prompt_tokens": 500, "completion_tokens": 100},
            "model_name": "gpt-4o",
        }
    }

    record = tracker.log_langchain_result(result, label="chat")

    assert record["model"] == "gpt-4o"
    assert record["input_tokens"] == 500
    assert record["output_tokens"] == 100
    assert record["cached_input_tokens"] == 0


def test_log_langchain_result_prefers_usage_metadata_over_llm_output(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {
        "usage_metadata": {"input_tokens": 10, "output_tokens": 5},
        "response_metadata": {"model_name": "gpt-4o"},
        "llm_output": {
            "token_usage": {"prompt_tokens": 999, "completion_tokens": 999},
            "model_name": "gpt-4o",
        },
    }

    record = tracker.log_langchain_result(result, label="chat")

    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 5


def test_log_langchain_result_handles_missing_usage_entirely(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = {}

    record = tracker.log_langchain_result(result, label="chat", model="gpt-4o", cost=0.0)

    assert record["input_tokens"] == 0
    assert record["output_tokens"] == 0
    assert record["cached_input_tokens"] == 0


class _FakeUsageMetadata:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.input_token_details = None


class _FakeAIMessage:
    def __init__(self, model_name: str, input_tokens: int, output_tokens: int) -> None:
        self.usage_metadata = _FakeUsageMetadata(input_tokens, output_tokens)
        self.response_metadata = {"model_name": model_name}


def test_log_langchain_result_accepts_attribute_style_ai_message(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    result = _FakeAIMessage(model_name="gpt-4o", input_tokens=50, output_tokens=10)

    record = tracker.log_langchain_result(result, label="chat")

    assert record["model"] == "gpt-4o"
    assert record["input_tokens"] == 50
    assert record["output_tokens"] == 10
