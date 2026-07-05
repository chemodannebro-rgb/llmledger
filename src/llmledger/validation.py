"""Lightweight, dependency-free validator for llmledger's JSONL log schema.

Deliberately does NOT import the `jsonschema` package (a `[dev]`-only
dependency -- see ARCHITECTURE.md) so `llmledger validate` stays a core,
zero-dependency command, the same guarantee `report`/`demo-data`/`detect`
(without a trained model)/`schema`/`dashboard` already give. This module
understands only the small subset of JSON Schema that `schema.json` actually
uses: `type` (including `["string", "null"]` unions), `required`,
`minLength`, `minimum`, and `additionalProperties: false`. It is not a
general-purpose JSON Schema validator.
"""

from __future__ import annotations

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "object": dict,
    "null": type(None),
}


def _type_matches(value, type_names: list[str]) -> bool:
    for name in type_names:
        py_type = _TYPE_MAP.get(name)
        if py_type is None:
            continue
        if py_type is int and isinstance(value, bool):
            # JSON has no separate boolean-vs-integer distinction concern here,
            # but Python's bool is a subclass of int -- schema.json's
            # "integer" fields (input_tokens, cost_micros, ...) should never
            # actually accept True/False, so treat bool as a type mismatch.
            continue
        if isinstance(value, py_type):
            return True
    return False


def validate_record(record: dict, schema: dict) -> list[str]:
    """Return a list of human-readable error strings for one already-parsed
    JSON object (`record`) against `schema` (as loaded from `schema.json`).
    An empty list means the record is valid.
    """
    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in record:
            errors.append(f"missing required field {field!r}")

    for field, value in record.items():
        if field not in properties:
            if schema.get("additionalProperties") is False:
                errors.append(f"unexpected field {field!r} not in schema")
            continue

        spec = properties[field]
        type_spec = spec.get("type")
        if type_spec is not None:
            type_names = [type_spec] if isinstance(type_spec, str) else type_spec
            if not _type_matches(value, type_names):
                errors.append(
                    f"field {field!r}: expected type {type_names}, got "
                    f"{type(value).__name__}"
                )
                continue  # further constraints below assume the right type

        if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
            errors.append(
                f"field {field!r}: length {len(value)} is below minLength {spec['minLength']}"
            )
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and "minimum" in spec
            and value < spec["minimum"]
        ):
            errors.append(f"field {field!r}: value {value} is below minimum {spec['minimum']}")

    return errors
