"""Strict, source-controlled model-facing CRM operation contract."""
from __future__ import annotations

import json
from pathlib import Path
import re


_EFFECTS = frozenset({"read", "proposal", "narrative", "validated_write"})
_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_SCHEMA_KEYWORDS = frozenset({
    "type", "additionalProperties", "required", "properties", "items", "enum",
    "const", "minimum", "maximum", "minLength", "maxLength", "pattern", "anyOf",
})


class _ValidationError(Exception):
    pass


def _is_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return value is None


def _invalid_contract() -> RuntimeError:
    return RuntimeError("invalid CRM operation contract")


def _validate_schema_shape(schema: object) -> None:
    if not isinstance(schema, dict):
        raise _invalid_contract()
    if set(schema) - _SCHEMA_KEYWORDS:
        raise _invalid_contract()
    schema_type = schema.get("type")
    if "type" in schema and (not isinstance(schema_type, str) or schema_type not in _TYPES):
        raise _invalid_contract()
    if "additionalProperties" in schema:
        if not isinstance(schema["additionalProperties"], bool) or schema_type != "object":
            raise _invalid_contract()
    if "required" in schema:
        if not isinstance(schema["required"], list) or schema_type != "object":
            raise _invalid_contract()
    if "required" in schema and not all(isinstance(item, str) for item in schema["required"]):
        raise _invalid_contract()
    if "properties" in schema:
        if not isinstance(schema["properties"], dict) or schema_type != "object":
            raise _invalid_contract()
        for name, child in schema["properties"].items():
            if not isinstance(name, str):
                raise _invalid_contract()
            _validate_schema_shape(child)
    if "items" in schema:
        if schema_type != "array":
            raise _invalid_contract()
        _validate_schema_shape(schema["items"])
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise _invalid_contract()
    if "anyOf" in schema:
        if (
            schema_type != "object"
            or not isinstance(schema["anyOf"], list)
            or not schema["anyOf"]
        ):
            raise _invalid_contract()
        properties = schema.get("properties", {})
        for child in schema["anyOf"]:
            if not isinstance(child, dict) or set(child) != {"required"}:
                raise _invalid_contract()
            required = child["required"]
            if (
                not isinstance(required, list)
                or not required
                or not all(isinstance(name, str) and name in properties for name in required)
            ):
                raise _invalid_contract()
    for key in ("minimum", "maximum"):
        if key in schema and (
            schema_type not in {"integer", "number"}
            or not isinstance(schema[key], (int, float))
            or isinstance(schema[key], bool)
        ):
            raise _invalid_contract()
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise _invalid_contract()
    for key in ("minLength", "maxLength"):
        if key in schema and (
            schema_type != "string"
            or not isinstance(schema[key], int)
            or isinstance(schema[key], bool)
            or schema[key] < 0
        ):
            raise _invalid_contract()
    if "minLength" in schema and "maxLength" in schema and schema["minLength"] > schema["maxLength"]:
        raise _invalid_contract()
    if "pattern" in schema:
        if schema_type != "string" or not isinstance(schema["pattern"], str):
            raise _invalid_contract()
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise _invalid_contract() from exc


def load_contract() -> dict:
    try:
        raw = json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid_contract() from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise _invalid_contract()
    operations = raw.get("operations")
    if not isinstance(operations, dict) or not operations:
        raise _invalid_contract()
    for name, entry in operations.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise _invalid_contract()
        if not isinstance(entry, dict) or set(entry) != {"description", "effect", "arguments"}:
            raise _invalid_contract()
        if not isinstance(entry["description"], str) or not entry["description"]:
            raise _invalid_contract()
        if entry["effect"] not in _EFFECTS:
            raise _invalid_contract()
        arguments = entry["arguments"]
        _validate_schema_shape(arguments)
        if arguments.get("type") != "object" or arguments.get("additionalProperties") is not False:
            raise _invalid_contract()
    return raw


CONTRACT = load_contract()


def operation_names() -> tuple[str, ...]:
    return tuple(CONTRACT["operations"])


def _argument_error(operation: str, name: str | None = None, *, unsupported: bool = False,
                    missing: bool = False) -> _ValidationError:
    if unsupported and name is not None:
        return _ValidationError(f"Unsupported argument: {name}")
    if missing and name is not None:
        return _ValidationError(f"Missing required argument: {name}")
    if name is not None:
        return _ValidationError(f"Invalid argument: {name}")
    return _ValidationError(f"Invalid CRM arguments: {operation}")


def _validate_value(schema: dict, value: object, operation: str, name: str | None) -> None:
    if "const" in schema and value != schema["const"]:
        raise _argument_error(operation, name)
    expected = schema.get("type")
    if expected is not None and not _is_type(value, expected):
        raise _argument_error(operation, name)
    if "enum" in schema and value not in schema["enum"]:
        raise _argument_error(operation, name)
    if "minimum" in schema and value < schema["minimum"]:
        raise _argument_error(operation, name)
    if "maximum" in schema and value > schema["maximum"]:
        raise _argument_error(operation, name)
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise _argument_error(operation, name)
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise _argument_error(operation, name)
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise _argument_error(operation, name)
    if expected == "object":
        _validate_object(schema, value, operation, name)
    elif expected == "array":
        items = schema.get("items")
        if items is not None:
            for item in value:
                _validate_value(items, item, operation, name)


def _validate_object(schema: dict, value: object, operation: str, name: str | None = None) -> None:
    if not isinstance(value, dict):
        raise _argument_error(operation, name)
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                raise _argument_error(operation, key, unsupported=True)
    for key in schema.get("required", []):
        if key not in value:
            raise _argument_error(operation, key, missing=True)
    for key, item in value.items():
        if key in properties:
            _validate_value(properties[key], item, operation, key)
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                _validate_object(alternative, value, operation, name)
                break
            except _ValidationError:
                continue
        else:
            raise _argument_error(operation)


def validate_arguments(operation: str, arguments: dict) -> dict:
    entry = CONTRACT["operations"].get(operation)
    if entry is None:
        raise ValueError(f"Unknown CRM operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("CRM arguments must be an object")
    try:
        _validate_object(entry["arguments"], arguments, operation)
        if operation == "merge_leads" and arguments["primary_id"] == arguments["duplicate_id"]:
            raise _argument_error(operation)
    except _ValidationError as exc:
        raise ValueError(str(exc)) from None
    return dict(arguments)
