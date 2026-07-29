"""Approve/deny queue for agent-initiated lead writes (see ..approvals).

Every row here was queued by one of the 5 gated leads.py endpoints when the
caller sent `X-Actor: agent` (only skills/crm-db-operations/tools.py does).
Approving replays the original request through the same `_apply_*` function
the direct (dashboard) path uses, so approved and directly-applied writes go
through identical logic."""
import inspect
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import audit, get_conn
from . import leads as leads_router

router = APIRouter(prefix="/pending-changes", tags=["pending-changes"])

NOW = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"

# operation -> (pydantic model to rebuild the payload, apply fn, apply fn takes lead_id first)
_OPS = {
    "create_lead": (leads_router.LeadIn, leads_router._apply_create_lead, False),
    "update_lead": (leads_router.LeadPatch, leads_router._apply_patch_lead, True),
    "close_lead": (leads_router.CloseLeadIn, leads_router._apply_close_lead, True),
    "delete_lead": (leads_router.LeadDelete, leads_router._apply_delete_lead, True),
    "merge_leads": (leads_router.MergeIn, leads_router._apply_merge_leads, False),
}


class DenyIn(BaseModel):
    reason: str | None = None


def _fetch(conn, pending_id: int) -> dict:
    row = conn.execute("SELECT * FROM pending_changes WHERE id = ?", (pending_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"pending change {pending_id} not found")
    return dict(row)


@router.get("")
def list_pending(status: str = "pending"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/{pending_id}/approve")
async def approve_pending(pending_id: int):
    with get_conn() as conn:
        row = _fetch(conn, pending_id)
    if row["status"] != "pending":
        raise HTTPException(400, f"pending change {pending_id} is already {row['status']}")

    model_cls, apply_fn, needs_lead_id = _OPS[row["operation"]]
    body = model_cls(**json.loads(row["payload"]))
    # apply_fn may be sync or async (only create_lead's is) — handle both
    # without forcing every _apply_* signature to be async for uniformity.
    call = apply_fn(row["lead_id"], body) if needs_lead_id else apply_fn(body)
    result = await call if inspect.isawaitable(call) else call

    with get_conn() as conn:
        conn.execute(
            f"UPDATE pending_changes SET status = 'approved', result = ?, "
            f"decided_at = ({NOW}) WHERE id = ?",
            (json.dumps(result, default=str), pending_id),
        )
        audit(conn, "user", "approve_pending_change", {"pending_id": pending_id},
              {"operation": row["operation"]}, row["lead_id"])
    return result


@router.post("/{pending_id}/deny")
def deny_pending(pending_id: int, body: DenyIn = None):
    reason = body.reason if body else None
    with get_conn() as conn:
        row = _fetch(conn, pending_id)
        if row["status"] != "pending":
            raise HTTPException(400, f"pending change {pending_id} is already {row['status']}")
        conn.execute(
            f"UPDATE pending_changes SET status = 'denied', deny_reason = ?, "
            f"decided_at = ({NOW}) WHERE id = ?",
            (reason, pending_id),
        )
        audit(conn, "user", "deny_pending_change", {"pending_id": pending_id, "reason": reason},
              {"operation": row["operation"]}, row["lead_id"])
        return _fetch(conn, pending_id)
