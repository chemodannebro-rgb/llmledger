from __future__ import annotations

import json
from importlib import resources

import pytest

from llmledger.validation import validate_record


@pytest.fixture
def schema():
    text = resources.files("llmledger").joinpath("schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def _valid_record(**overrides):
    record = {
        "schema_version": "1.0",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "label": "summarize",
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_micros": 500,
    }
    record.update(overrides)
    return record


def test_valid_record_has_no_errors(schema):
    assert validate_record(_valid_record(), schema) == []


def test_missing_required_field_is_reported(schema):
    record = _valid_record()
    del record["model"]
    errors = validate_record(record, schema)
    assert any("missing required field 'model'" in e for e in errors)


def test_wrong_type_is_reported(schema):
    record = _valid_record(input_tokens="not-a-number")
    errors = validate_record(record, schema)
    assert any("input_tokens" in e and "expected type" in e for e in errors)


def test_negative_number_below_minimum_is_reported(schema):
    record = _valid_record(cost_micros=-5)
    errors = validate_record(record, schema)
    assert any("below minimum" in e for e in errors)


def test_empty_label_below_min_length_is_reported(schema):
    record = _valid_record(label="")
    errors = validate_record(record, schema)
    assert any("below minLength" in e for e in errors)


def test_unexpected_field_is_reported(schema):
    record = _valid_record(totally_unknown_field="x")
    errors = validate_record(record, schema)
    assert any("unexpected field" in e for e in errors)


def test_optional_fields_are_accepted_when_present(schema):
    record = _valid_record(trace_id="req-1", cached_input_tokens=10, extra={"k": "v"})
    assert validate_record(record, schema) == []


def test_null_trace_id_is_accepted(schema):
    record = _valid_record(trace_id=None)
    assert validate_record(record, schema) == []


def test_bool_is_not_accepted_as_integer(schema):
    record = _valid_record(input_tokens=True)
    errors = validate_record(record, schema)
    assert any("input_tokens" in e and "expected type" in e for e in errors)
