from __future__ import annotations

import json
from importlib import resources

import jsonschema
import pytest

from llmledger.tracker import CostTracker


@pytest.fixture()
def schema():
    with resources.files("llmledger").joinpath("schema.json").open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def test_schema_file_is_valid_json_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def test_log_call_record_matches_schema(tmp_path, schema):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    record = tracker.log_call(
        label="summarize", model="gpt-4o", input_tokens=100, output_tokens=20
    )
    jsonschema.validate(record, schema)


def test_log_call_record_with_optional_fields_matches_schema(tmp_path, schema):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    record = tracker.log_call(
        label="summarize",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=10,
        trace_id="req-123",
        workflow_id="wf-1",
    )
    jsonschema.validate(record, schema)


def test_log_openai_response_record_matches_schema(tmp_path, schema):
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
    jsonschema.validate(record, schema)


def test_log_anthropic_response_record_matches_schema(tmp_path, schema):
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
    jsonschema.validate(record, schema)


def test_log_gemini_response_record_matches_schema(tmp_path, schema):
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
    jsonschema.validate(record, schema)


def test_log_ollama_response_record_matches_schema(tmp_path, schema):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model": "llama3", "prompt_eval_count": 50, "eval_count": 20}
    record = tracker.log_ollama_response(response, label="chat", cost=0.0)
    jsonschema.validate(record, schema)


def test_record_missing_required_field_fails_schema(schema):
    record = {
        "schema_version": "1.0",
        "timestamp": "2026-06-01T00:00:00+00:00",
        "label": "x",
        "model": "gpt-4o",
        "input_tokens": 1,
        "output_tokens": 1,
        # cost_micros missing
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, schema)


def test_record_with_unknown_field_fails_schema(schema):
    record = {
        "schema_version": "1.0",
        "timestamp": "2026-06-01T00:00:00+00:00",
        "label": "x",
        "model": "gpt-4o",
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_micros": 1,
        "unexpected_field": "nope",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, schema)
