"""Approve/deny queue for agent-initiated lead writes (see ..approvals).

Every row here was queued by one of the 5 gated leads.py endpoints when the
caller sent `X-Actor: agent` (only skills/crm-db-operations/tools.py does).
Approving replays the original request through the same `_apply_*` function
the direct (dashboard) path uses, so approved and directly-applied writes go
through identical logic — with one exception: create_lead's payload is
already-resolved fields (see leads.py's _resolve_create_fields), so it goes
through _apply_resolved_create instead of re-running extraction."""
import inspect
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import audit, get_conn
from . import leads as leads_router

router = APIRouter(prefix="/pending-changes", tags=["pending-changes"])

NOW = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"

# operation -> (pydantic model to rebuild the payload, apply fn, apply fn takes lead_id first)
# create_lead is handled separately in approve_pending — its payload is a
# plain dict of resolved fields, not a LeadIn (see module docstring).
_OPS = {
    "update_lead": (leads_router.LeadPatch, leads_router._apply_patch_lead, True),
    "close_lead": (leads_router.CloseLeadIn, leads_router._apply_close_lead, True),
    "delete_lead": (leads_router.LeadDelete, leads_router._apply_delete_lead, True),
    "merge_leads": (leads_router.MergeIn, leads_router._apply_merge_leads, False),
}


class DenyIn(BaseModel):
    reason: str | None = None


class ApproveIn(BaseModel):
    # Operator edits from the dialog, keyed the same as the queued payload —
    # merged over (overriding) the stored payload before applying. Omit or
    # send {} to approve the queued change verbatim.
    fields: dict | None = None


def _fetch(conn, pending_id: int) -> dict:
    row = conn.execute("SELECT * FROM pending_changes WHERE id = ?", (pending_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"pending change {pending_id} not found")
    return dict(row)


def _parsed(row: dict) -> dict:
    row = dict(row)
    row["payload"] = json.loads(row["payload"])
    if row.get("result"):
        row["result"] = json.loads(row["result"])
    return row


@router.get("")
def list_pending(status: str = "pending"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return [_parsed(dict(r)) for r in rows]


@router.post("/{pending_id}/approve")
async def approve_pending(pending_id: int, body: ApproveIn = None):
    with get_conn() as conn:
        row = _fetch(conn, pending_id)
    if row["status"] != "pending":
        raise HTTPException(400, f"pending change {pending_id} is already {row['status']}")

    payload = {**json.loads(row["payload"]), **((body.fields if body else None) or {})}

    if row["operation"] == "create_lead":
        result = await leads_router._apply_resolved_create(payload)
    else:
        model_cls, apply_fn, needs_lead_id = _OPS[row["operation"]]
        parsed_body = model_cls(**payload)
        # apply_fn may be sync or async (only create's is) — handle both
        # without forcing every _apply_* signature to be async for uniformity.
        call = apply_fn(row["lead_id"], parsed_body) if needs_lead_id else apply_fn(parsed_body)
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
        return _parsed(_fetch(conn, pending_id))
