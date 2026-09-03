"""Pure builders for the dashboard's request-scoped client tools."""
from __future__ import annotations

from copy import deepcopy


CRM_REQUEST_TOOL = "openhouse_crm_request"
FINISH_TOOL = "finish_crm_response"


def build_dashboard_client_tools(contract: dict) -> list[dict]:
    """Build the exact production CRM request and finish function schemas."""
    branches = []
    for operation, entry in contract["operations"].items():
        branches.append({
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "arguments"],
            "properties": {
                "operation": {
                    "const": operation,
                    "description": entry["description"],
                },
                "arguments": deepcopy(entry["arguments"]),
            },
        })
    return [
        {
            "type": "function",
            "function": {
                "name": CRM_REQUEST_TOOL,
                "description": (
                    "Read the local CRM or propose one reviewed CRM change. "
                    "Use only contract arguments and never invent CRM facts."
                ),
                "parameters": {"oneOf": branches},
            },
        },
        {
            "type": "function",
            "function": {
                "name": FINISH_TOOL,
                "description": (
                    "Finish with a classification supported by collected CRM evidence."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "classification",
                        "message",
                        "evidence_call_ids",
                    ],
                    "properties": {
                        "classification": {
                            "type": "string",
                            "enum": [
                                "answered",
                                "queued",
                                "needs_clarification",
                                "failed",
                            ],
                        },
                        "message": {"type": "string", "maxLength": 4000},
                        "evidence_call_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                            "maxItems": 6,
                        },
                        "pending_id": {"type": "integer", "minimum": 1},
                    },
                },
            },
        },
    ]
