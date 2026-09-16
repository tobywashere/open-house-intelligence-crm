#!/usr/bin/env python3
"""Restricted command-line dispatcher for the CRM skill."""

import argparse
import json
import re
import sys

from contract import CONTRACT, operation_names, validate_arguments
import tools


OPERATIONS = {name: getattr(tools, name) for name in operation_names()}


def _bounded_crm_message(code: str, _message: str) -> str:
    return {
        "backend_unavailable": "CRM backend is unavailable",
        "not_found": "CRM record was not found",
        "schedule_conflict": "Requested schedule conflicts with an existing appointment",
        "invalid_arguments": "Invalid CRM arguments",
        "operation_failed": "CRM operation failed",
        "outcome_unknown": "CRM mutation outcome is unknown",
    }.get(code, "CRM operation failed")


def _bounded_argument_message(exc: Exception) -> str:
    message = str(exc)
    if message == "--args must decode to a JSON object":
        return message
    if re.fullmatch(r"(?:Unsupported|Missing|Invalid) argument: [a-z][a-z0-9_]*", message):
        return message
    if re.fullmatch(r"Invalid CRM arguments: [a-z][a-z0-9_]*", message):
        return message
    return "Invalid CRM arguments"


def _is_mutating_operation(operation: str | None) -> bool:
    entry = CONTRACT["operations"].get(operation) if operation else None
    return bool(entry and entry["effect"] in {"proposal", "validated_write"})


def _is_deterministic_http_rejection(status: int) -> bool:
    return 400 <= status < 500 and status not in {408, 499}


def _safe_error(exc: Exception, *, operation: str | None = None) -> dict:
    mutation = _is_mutating_operation(operation)
    if isinstance(exc, tools.CRMError):
        if mutation and not _is_deterministic_http_rejection(exc.status):
            return {
                "code": "outcome_unknown",
                "message": "CRM mutation outcome is unknown",
                "retryable": False,
            }
        code = {
            0: "backend_unavailable",
            404: "not_found",
            409: "schedule_conflict",
            400: "invalid_arguments",
            422: "invalid_arguments",
        }.get(exc.status, "operation_failed")
        return {
            "code": code,
            "message": _bounded_crm_message(code, exc.message),
            "retryable": code in {"backend_unavailable", "timeout"},
        }
    if isinstance(exc, (TypeError, ValueError)):
        return {
            "code": "invalid_arguments",
            "message": _bounded_argument_message(exc),
            "retryable": False,
        }
    if mutation:
        return {
            "code": "outcome_unknown",
            "message": "CRM mutation outcome is unknown",
            "retryable": False,
        }
    return {"code": "operation_failed", "message": "CRM operation failed", "retryable": False}


def dispatch(operation: str, arguments: dict):
    function = OPERATIONS.get(operation)
    if function is None:
        raise ValueError(f"unknown CRM operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("--args must decode to a JSON object")
    return function(**validate_arguments(operation, arguments))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one named Open House CRM operation"
    )
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    parser.add_argument("--args", default="{}", help="JSON object of named arguments")
    args = parser.parse_args()
    try:
        result = dispatch(args.operation, json.loads(args.args))
    except Exception as exc:
        print(
            json.dumps({
                "ok": False,
                "error": _safe_error(exc, operation=args.operation),
            }),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"ok": True, "result": result}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
