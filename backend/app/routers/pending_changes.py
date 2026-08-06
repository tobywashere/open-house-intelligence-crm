"""Approve/deny queue for agent-initiated CRM writes (see ..approvals).

Every row here was queued by one of the gated CRM mutation endpoints when the
caller sent `X-Actor: agent` (only skills/crm-db-operations/tools.py does).
Approving replays the original request through the same `_apply_*` function
the direct (dashboard) path uses, so approved and directly-applied writes go
through identical logic — with one exception: create_lead's payload is
already-resolved fields (see leads.py's _resolve_create_fields), so it goes
through _apply_resolved_create instead of re-running extraction."""
import json

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

from ..db import audit, get_conn
from ..integrations import hook_outbox
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


def _operation(operation: str):
    if operation == "add_event":
        return leads_router.EventIn, leads_router._apply_add_event, True
    if operation == "book_appointment":
        from . import calendar as calendar_router
        return calendar_router.AppointmentIn, calendar_router._apply_book_appointment, False
    if operation == "schedule_followup":
        from . import misc as misc_router
        return misc_router.ReminderIn, misc_router._apply_create_reminder, False
    try:
        return _OPS[operation]
    except KeyError:
        raise HTTPException(400, f"unknown pending operation {operation}") from None


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


def _claim_pending(conn, pending_id: int) -> dict:
    """Reserve a proposal inside the approval's caller-owned transaction."""
    row = _fetch(conn, pending_id)
    if row["status"] != "pending":
        raise HTTPException(
            400, f"pending change {pending_id} is already {row['status']}"
        )
    claimed = conn.execute(
        "UPDATE pending_changes SET status = 'applying' "
        "WHERE id = ? AND status = 'pending'",
        (pending_id,),
    )
    if claimed.rowcount != 1:
        raise HTTPException(400, f"pending change {pending_id} is already being applied")
    return row


def _finish_claim(conn, pending_id: int, row: dict, result: dict) -> None:
    finished = conn.execute(
        f"UPDATE pending_changes SET status = 'approved', result = ?, "
        f"decided_at = ({NOW}) WHERE id = ? AND status = 'applying'",
        (json.dumps(result, default=str), pending_id),
    )
    if finished.rowcount != 1:
        raise HTTPException(409, f"pending change {pending_id} lost its approval claim")
    approval_lead_id = (
        result.get("id")
        if row["operation"] == "create_lead"
        else None if row["operation"] == "delete_lead" else row["lead_id"]
    )
    audit(
        conn,
        "user",
        "approve_pending_change",
        {"pending_id": pending_id},
        {"operation": row["operation"]},
        approval_lead_id,
    )


def _validate_pending_mutation(row: dict, payload: dict):
    """Validate edited fields before entering the generic mutation seam."""
    if row["operation"] == "create_lead":
        return payload
    model_cls, apply_fn, needs_lead_id = _operation(row["operation"])
    return model_cls(**payload), apply_fn, needs_lead_id


def _apply_pending_mutation(conn, row: dict, validated):
    """Apply any supported operation through the shared caller-owned transaction."""
    if row["operation"] == "create_lead":
        return leads_router._apply_resolved_create_in_conn(conn, validated)
    parsed_body, apply_fn, needs_lead_id = validated
    kwargs = {"conn": conn}
    if row["operation"] in {"book_appointment", "schedule_followup"}:
        kwargs["run_hook"] = False
    return (
        apply_fn(row["lead_id"], parsed_body, **kwargs)
        if needs_lead_id
        else apply_fn(parsed_body, **kwargs)
    )


async def _dispatch_committed_hook(outbox_id: int) -> None:
    """Dispatch durable intent after commit without blocking the event loop."""
    await run_in_threadpool(hook_outbox.dispatch_hook, outbox_id)


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
    outbox_id = None
    try:
        with get_conn() as conn:
            row = _claim_pending(conn, pending_id)
            row["id"] = pending_id
            payload = {
                **json.loads(row["payload"]),
                **((body.fields if body else None) or {}),
            }
            validated = _validate_pending_mutation(row, payload)
            result = _apply_pending_mutation(conn, row, validated)
            _finish_claim(conn, pending_id, row, result)
            outbox_id = hook_outbox.enqueue_approval_hook(
                conn, pending_id, row["operation"], result
            )
    except ValidationError as exc:
        raise HTTPException(422, detail=json.loads(exc.json(include_url=False))) from None

    if outbox_id is not None:
        await _dispatch_committed_hook(outbox_id)
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
