"""Generic infra for gating agent-initiated lead writes behind human approval.

Only the agent's HTTP tool client (skills/crm-db-operations/tools.py) sends
`X-Actor: agent` — the dashboard's fetch calls (dashboard/src/api.ts) never
set it, so their writes are unaffected and keep applying immediately. See
docs/CONTRACT.md's pending-changes section for the full contract.
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse

from .db import get_conn


def is_agent_write(request: Request | None) -> bool:
    # request is None for in-process callers (e.g. the Gmail poller's direct
    # create_lead(LeadIn(...)) call, which has no HTTP request at all) —
    # those are out of scope for this gate and always apply immediately.
    return request is not None and request.headers.get("X-Actor") == "agent"


def queue_pending_change(operation: str, lead_id: int | None, payload: dict, summary: str) -> JSONResponse:
    """Records the proposed write and returns the 202 the agent's tool call sees
    instead of the lead — the caller must NOT also apply the mutation."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_changes (operation, lead_id, payload, summary) VALUES (?,?,?,?)",
            (operation, lead_id, json.dumps(payload, default=str), summary),
        )
        pending_id = cur.lastrowid
    return JSONResponse(
        status_code=202,
        content={
            "pending": True,
            "id": pending_id,
            "operation": operation,
            "summary": summary,
            "status": "pending",
        },
    )
