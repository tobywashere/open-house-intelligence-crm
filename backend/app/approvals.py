"""Generic infra for gating agent-initiated lead writes behind human approval.

The agent's HTTP tool client (skills/crm-db-operations/tools.py) sends
`X-Actor: agent`; automatic mailbox intake calls the same queue explicitly.
The dashboard's fetch calls (dashboard/src/api.ts) never set the header, so
manual writes remain immediate. See docs/CONTRACT.md for the full contract.
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse

from .db import get_conn


def is_agent_write(request: Request | None) -> bool:
    # In-process automation has no Request from which to derive an actor and
    # must opt into queue_pending_change explicitly, as the mailbox poller does.
    return request is not None and request.headers.get("X-Actor") == "agent"


def insert_pending_change(
    conn,
    operation: str,
    lead_id: int | None,
    payload: dict,
    summary: str,
    *,
    dedupe_key: str | None = None,
) -> dict:
    """Insert a proposal using the caller's transaction.

    The caller owns commit/rollback. This lets automation atomically pair a
    proposal with its audit row without holding the database lock during slow
    extraction.
    """
    serialized = json.dumps(payload, default=str)
    if dedupe_key is None:
        cur = conn.execute(
            "INSERT INTO pending_changes (operation, lead_id, payload, summary) "
            "VALUES (?,?,?,?)",
            (operation, lead_id, serialized, summary),
        )
        pending_id = cur.lastrowid
        status = "pending"
    else:
        conn.execute(
            "INSERT OR IGNORE INTO pending_changes "
            "(operation, lead_id, payload, summary, dedupe_key) VALUES (?,?,?,?,?)",
            (operation, lead_id, serialized, summary, dedupe_key),
        )
        row = conn.execute(
            "SELECT id, operation, lead_id, summary, status FROM pending_changes "
            "WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if not row:
            raise RuntimeError("deduplicated pending change was not persisted")
        if row["operation"] != operation or row["lead_id"] != lead_id:
            raise RuntimeError("pending change dedupe key conflicts with another proposal")
        pending_id = row["id"]
        summary = row["summary"]
        status = row["status"]
    return {
        "pending": True,
        "id": pending_id,
        "operation": operation,
        "summary": summary,
        "status": status,
    }


def queue_pending_change(
    operation: str, lead_id: int | None, payload: dict, summary: str
) -> JSONResponse:
    """Record a proposed write and return the agent-facing 202 response."""
    with get_conn() as conn:
        content = insert_pending_change(conn, operation, lead_id, payload, summary)
    return JSONResponse(status_code=202, content=content)
