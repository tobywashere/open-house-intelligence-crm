#!/usr/bin/env python3
"""Restricted command-line dispatcher for the CRM skill."""

import argparse
import json
from pathlib import Path
import re
import sys

import tools


def _load_operation_names() -> tuple[str, ...]:
    catalog_path = Path(__file__).with_name("operations.json")
    names = json.loads(catalog_path.read_text(encoding="utf-8"))
    if (
        not isinstance(names, list)
        or not names
        or len(names) != len(set(names))
        or not all(
            isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]*", name)
            for name in names
        )
    ):
        raise RuntimeError("invalid CRM operation catalog")
    return tuple(names)


OPERATIONS = {name: getattr(tools, name) for name in _load_operation_names()}


def dispatch(operation: str, arguments: dict):
    function = OPERATIONS.get(operation)
    if function is None:
        raise ValueError(f"unknown CRM operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("--args must decode to a JSON object")
    return function(**arguments)


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
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": result}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
