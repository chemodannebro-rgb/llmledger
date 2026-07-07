"""Confirms whether `CostTracker.log_openai_response()` already works,
unmodified, against a `litellm.ModelResponse` -- the object LiteLLM's SDK
returns from `litellm.completion(...)` -- before writing any LiteLLM-specific
adapter code (see CHANGELOG.md [0.9.5]).

LiteLLM normalizes every provider it wraps to the same OpenAI-compatible
response shape: a `.model` attribute and a `.usage` object exposing
`.prompt_tokens`/`.completion_tokens`/`.total_tokens`, with
`.usage.prompt_tokens_details.cached_tokens` for providers that report a
prompt-caching discount -- exactly the fields `log_openai_response()` already
reads via `_get()`. `_FakeLiteLLMModelResponse`/`_FakeUsage`/
`_FakePromptTokensDetails` below mimic only that attribute shape (real
`ModelResponse`/`Usage` objects are pydantic models with several more fields
irrelevant here); no `litellm` package is installed or imported.

Result: this test passes with zero changes to `tracker.py` -- confirming
`log_openai_response()` needs no LiteLLM-specific adapter.
"""

from __future__ import annotations

from llm_burnwatch.tracker import CostTracker


class _FakePromptTokensDetails:
    def __init__(self, cached_tokens: int) -> None:
        self.cached_tokens = cached_tokens


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.prompt_tokens_details = (
            _FakePromptTokensDetails(cached_tokens) if cached_tokens else None
        )


class _FakeLiteLLMModelResponse:
    """Mimics the attribute shape of `litellm.ModelResponse` relevant to
    `log_openai_response()`: `.model` and `.usage.*` -- the same
    OpenAI-compatible shape LiteLLM normalizes every wrapped provider to.
    """

    def __init__(self, model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
        self.model = model
        self.object = "chat.completion"
        self.usage = _FakeUsage(prompt_tokens, completion_tokens, cached_tokens)


def test_log_openai_response_works_unmodified_on_litellm_model_response(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = _FakeLiteLLMModelResponse(
        model="gpt-4o", prompt_tokens=1000, completion_tokens=200, cached_tokens=300
    )

    record = tracker.log_openai_response(response, label="litellm-call")

    assert record["model"] == "gpt-4o"
    assert record["input_tokens"] == 700
    assert record["cached_input_tokens"] == 300
    assert record["output_tokens"] == 200


def test_log_openai_response_works_unmodified_on_litellm_model_response_without_caching(tmp_path):
    # Most providers LiteLLM wraps don't report prompt-caching details at all.
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = _FakeLiteLLMModelResponse(model="gpt-4o", prompt_tokens=500, completion_tokens=50)

    record = tracker.log_openai_response(response, label="litellm-call")

    assert record["input_tokens"] == 500
    assert record["cached_input_tokens"] == 0
    assert record["output_tokens"] == 50
