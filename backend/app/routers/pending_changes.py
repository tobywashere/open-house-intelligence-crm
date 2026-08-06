"""Approve/deny queue for agent-initiated CRM writes (see ..approvals).

Every row here was queued by one of the gated CRM mutation endpoints when the
caller sent `X-Actor: agent` (only skills/crm-db-operations/tools.py does).
Approving replays the original request through the same `_apply_*` function
the direct (dashboard) path uses, so approved and directly-applied writes go
through identical logic — with one exception: create_lead's payload is
already-resolved fields (see leads.py's _resolve_create_fields), so it goes
through _apply_resolved_create instead of re-running extraction."""
import inspect
import json

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

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


def _claim_pending(pending_id: int) -> dict:
    """Atomically reserve one proposal for exactly one approval worker."""
    with get_conn() as conn:
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


def _release_claim(pending_id: int) -> None:
    """Make a pre-mutation validation/conflict failure editable and retryable."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_changes SET status = 'pending' "
            "WHERE id = ? AND status = 'applying'",
            (pending_id,),
        )


def _finish_claim(pending_id: int, row: dict, result: dict) -> None:
    with get_conn() as conn:
        finished = conn.execute(
            f"UPDATE pending_changes SET status = 'approved', result = ?, "
            f"decided_at = ({NOW}) WHERE id = ? AND status = 'applying'",
            (json.dumps(result, default=str), pending_id),
        )
        if finished.rowcount != 1:
            raise HTTPException(409, f"pending change {pending_id} lost its approval claim")
        audit(
            conn,
            "user",
            "approve_pending_change",
            {"pending_id": pending_id},
            {"operation": row["operation"]},
            row["lead_id"],
        )


def _audit_post_hook_failure(row: dict, result: dict, exc: Exception) -> None:
    tool = (
        "gmail_create_draft (failed)"
        if row["operation"] == "create_lead"
        else "gcal_create_event (failed)"
    )
    lead_id = result.get("id") if row["operation"] == "create_lead" else row["lead_id"]
    try:
        with get_conn() as conn:
            audit(
                conn,
                "user",
                tool,
                {"pending_id": row["id"]},
                {"error": str(exc)},
                lead_id,
            )
    except Exception:
        pass


async def _run_post_approval_hook(row: dict, result: dict) -> None:
    """Run external work only after the proposal is durably non-replayable."""
    try:
        if row["operation"] == "create_lead":
            await run_in_threadpool(leads_router.hooks.on_lead_created, result)
        elif row["operation"] == "book_appointment":
            from . import calendar as calendar_router

            with get_conn() as conn:
                lead = leads_router.fetch_lead(conn, result["lead_id"])
            await run_in_threadpool(calendar_router.hooks.on_tour_booked, lead, result)
        elif row["operation"] == "schedule_followup":
            from . import misc as misc_router

            await run_in_threadpool(misc_router.hooks.on_reminder_created, result)
    except Exception as exc:
        _audit_post_hook_failure(row, result, exc)


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
    row = _claim_pending(pending_id)
    row["id"] = pending_id
    mutation_committed = False
    try:
        payload = {
            **json.loads(row["payload"]),
            **((body.fields if body else None) or {}),
        }

        if row["operation"] == "create_lead":
            result = await leads_router._apply_resolved_create(payload, run_hook=False)
        else:
            model_cls, apply_fn, needs_lead_id = _operation(row["operation"])
            parsed_body = model_cls(**payload)
            kwargs = (
                {"run_hook": False}
                if row["operation"] in {"book_appointment", "schedule_followup"}
                else {}
            )
            call = (
                apply_fn(row["lead_id"], parsed_body, **kwargs)
                if needs_lead_id
                else apply_fn(parsed_body, **kwargs)
            )
            result = await call if inspect.isawaitable(call) else call
        mutation_committed = True
        _finish_claim(pending_id, row, result)
    except ValidationError as exc:
        if not mutation_committed:
            _release_claim(pending_id)
        raise HTTPException(422, detail=json.loads(exc.json(include_url=False))) from None
    except BaseException:
        if not mutation_committed:
            _release_claim(pending_id)
        raise

    await _run_post_approval_hook(row, result)
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
