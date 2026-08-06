#!/usr/bin/env python3
"""Restricted command-line dispatcher for the CRM skill."""

import argparse
import json
import sys

import tools


OPERATIONS = {
    name: getattr(tools, name)
    for name in (
        "create_lead",
        "update_lead",
        "add_note",
        "close_lead",
        "find_duplicate_leads",
        "merge_leads",
        "get_lead_context",
        "list_leads",
        "score_lead",
        "draft_followup",
        "check_availability",
        "list_appointments",
        "book_appointment",
        "schedule_followup",
        "find_neglected_leads",
        "generate_dashboard_insights",
        "post_briefing",
        "get_research_settings",
        "get_insights",
        "get_summary",
        "delete_lead",
        "search_knowledge",
    )
}


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
