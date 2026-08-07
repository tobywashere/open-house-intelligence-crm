import json
import os
import uuid
from dataclasses import asdict, replace

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..agent import get_driver
from ..agent.status import AgentProbe, record_crm_capability
from ..approvals import is_agent_write, queue_pending_change
from ..calendar_adapter import calendar
from ..db import audit, get_conn, row_to_dict
from ..integrations import hooks
from ..integrations import composio_client
from .leads import fetch_lead
from ..scoring import is_high_priority

router = APIRouter(tags=["misc"])

NEGLECT_AFTER_DAYS = 2


class AdvanceTimeIn(BaseModel):
    days: int = Field(3, ge=0)


class ReminderIn(BaseModel):
    lead_id: int
    due_ts: str
    note: str | None = None

    @field_validator("due_ts")
    @classmethod
    def _normalize_due_ts(cls, v: str) -> str:
        try:
            dt = calendar.parse_ts(v)
        except ValueError:
            raise ValueError("must be an ISO-8601 timestamp, e.g. 2026-08-01T17:00:00")
        return dt.isoformat(timespec="seconds")


@router.post("/reminders")
def create_reminder(body: ReminderIn, request: Request = None):
    if is_agent_write(request):
        with get_conn() as conn:
            lead = fetch_lead(conn, body.lead_id)
        summary = (
            f"Schedule follow-up for #{body.lead_id} ({lead.get('name')}) "
            f"at {body.due_ts}"
        )
        if body.note:
            summary += f": {body.note}"
        return queue_pending_change(
            "schedule_followup", body.lead_id, body.model_dump(), summary
        )
    return _apply_create_reminder(body, actor="user")


def _apply_create_reminder(
    body: ReminderIn, actor: str = "agent", *, run_hook: bool = True, conn=None
) -> dict:
    if conn is not None and run_hook:
        raise ValueError("caller-owned reminder transactions cannot run external hooks")

    if conn is None:
        with get_conn() as owned_conn:
            reminder = _create_reminder_in_conn(owned_conn, body, actor)
    else:
        reminder = _create_reminder_in_conn(conn, body, actor)
    # create_reminder is a sync `def` endpoint: FastAPI already runs the
    # whole handler in the AnyIO threadpool, so this call can't freeze the
    # event loop — no run_in_threadpool wrapping needed here.
    if run_hook:
        hooks.on_reminder_created(reminder)
    return reminder


def _create_reminder_in_conn(conn, body: ReminderIn, actor: str) -> dict:
    fetch_lead(conn, body.lead_id)  # 404 instead of an FK IntegrityError 500
    cur = conn.execute(
        "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
        (body.lead_id, body.due_ts, body.note),
    )
    audit(conn, actor, "schedule_followup", body.model_dump(), {}, body.lead_id)
    return dict(conn.execute(
        "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/reminders")
def list_reminders(due: int | None = None):
    q = ("SELECT r.*, l.name AS lead_name FROM reminders r JOIN leads l ON l.id = r.lead_id "
         "WHERE r.done = 0")
    if due:
        q += " AND r.due_ts <= strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q + " ORDER BY r.due_ts")]


@router.patch("/reminders/{reminder_id}")
def complete_reminder(reminder_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"reminder {reminder_id} not found")
        # no crm-db-operations tool marks a reminder done (schedule_followup
        # only creates them) — the dashboard's reminder banner is the only
        # caller, so this is a "user" action.
        audit(conn, "user", "complete_reminder", {"reminder_id": reminder_id},
              {"done": True}, row["lead_id"])
    return dict(row)


def run_neglect_check(conn) -> list[dict]:
    """Flag open leads with no activity for NEGLECT_AFTER_DAYS+. Used by the API
    and by the scheduled job (agent/cron)."""
    rows = conn.execute(
        "SELECT * FROM leads WHERE status IN ('new','contacted') AND is_neglected = 0 "
        f"AND last_activity_at < strftime('%Y-%m-%dT%H:%M:%S','now','localtime','-{NEGLECT_AFTER_DAYS} days')"
    ).fetchall()
    neglected = [row_to_dict(r) for r in rows]
    for lead in neglected:
        conn.execute("UPDATE leads SET is_neglected = 1 WHERE id = ?", (lead["id"],))
    if neglected:
        audit(conn, "cron", "find_neglected_leads", {},
              {"count": len(neglected), "ids": [n["id"] for n in neglected]})
    return neglected


@router.post("/demo/advance-time")
def advance_time(body: AdvanceTimeIn):
    """Demo helper: backdate all activity so the neglect check fires on stage."""
    with get_conn() as conn:
        if body.days:
            conn.execute(
                "UPDATE leads SET last_activity_at = "
                "strftime('%Y-%m-%dT%H:%M:%S', datetime(last_activity_at, ?)) ",
                (f"-{body.days} days",))
            # unconditional: this rewrites last_activity_at on every open lead
            # even when none of them cross the neglect threshold below, so the
            # conditional audit in run_neglect_check() alone would miss it.
            audit(conn, "user", "advance_time", {"days": body.days}, {})
        neglected = run_neglect_check(conn)
    return {"neglected": neglected}


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=500)):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.*, l.name AS lead_name FROM audit_log a "
            "LEFT JOIN leads l ON l.id = a.lead_id "
            "ORDER BY a.id DESC LIMIT ?", (limit,))]


@router.get("/metrics")
def metrics(
    request: Request,
    probe_nonce: str | None = Query(
        None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9-]+$",
    ),
):
    with get_conn() as conn:
        leads = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM leads WHERE status != 'closed'")]
        appts = conn.execute("SELECT COUNT(*) c FROM appointments").fetchone()["c"]
        # Mean minutes from a lead's created_at to its first event's created_at,
        # over leads with >=1 event only. Timestamps are naive-local strings
        # (Task 7), so julianday() differences are directly comparable — no
        # timezone normalization needed. NULL (no qualifying lead) -> None.
        avg_row = conn.execute(
            "SELECT AVG((julianday(f.first_event_at) - julianday(l.created_at)) * 1440.0) AS avg_min "
            "FROM leads l "
            "JOIN (SELECT lead_id, MIN(created_at) AS first_event_at FROM events GROUP BY lead_id) f "
            "ON f.lead_id = l.id"
        ).fetchone()
        raw_avg = avg_row["avg_min"]
        # julianday() arithmetic isn't bit-exact (e.g. 10.000000409781933 for
        # an intended 10.0) — round to a sane display precision.
        avg_response_minutes = round(raw_avg, 1) if raw_avg is not None else None
        result = {
            "active_leads": len(leads),
            "high_priority": sum(1 for l in leads if is_high_priority(l.get("score"))),
            "followups_due": sum(1 for l in leads if l["is_neglected"]),
            "appointments_booked": appts,
            "avg_response_minutes": avg_response_minutes,
            "agent_mode": os.environ.get("AGENT_MODE", "mock"),
            # Composio tool calls (Gmail/Calendar) on the live path — NOT
            # local-LLM inference requests. The openclaw driver's calls are
            # deliberately excluded: they never leave the box.
            "cloud_llm_requests": composio_client.request_count(),
        }
        if is_agent_write(request):
            audit(
                conn,
                "agent",
                "generate_dashboard_insights",
                {"probe_nonce": probe_nonce} if probe_nonce else {},
                result,
            )
    return result


@router.get("/health")
async def health():
    driver = get_driver()
    probe = await driver.probe()
    return {
        "ok": True,
        "agent_mode": driver.name,
        "agent_connected": probe.gateway_reachable,
        "agent_status": asdict(probe),
    }


@router.post("/health/agent-check")
async def agent_check():
    driver = get_driver()
    return asdict(await driver.live_check())


@router.post("/health/crm-check")
async def crm_check():
    driver = get_driver()
    if driver.name == "mock":
        raise HTTPException(409, "CRM capability check requires the OpenClaw agent")

    with get_conn() as conn:
        before = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS id FROM audit_log"
        ).fetchone()["id"]

    probe_nonce = uuid.uuid4().hex
    try:
        await driver.request_crm_capability(
            f"crm-check-{probe_nonce}",
            probe_nonce,
        )
    except Exception:
        record_crm_capability(False, "capability request failed")
        return asdict(_capability_result(
            await driver.probe(),
            verified=False,
            detail="capability request failed",
        ))

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT input FROM audit_log "
            "WHERE id > ? AND actor = 'agent' "
            "AND tool = 'generate_dashboard_insights'",
            (before,),
        ).fetchall()
    found = any(_audit_has_nonce(row["input"], probe_nonce) for row in rows)

    detail = None if found else "no audited CRM call"
    record_crm_capability(found, detail)
    return asdict(_capability_result(
        await driver.probe(),
        verified=found,
        detail=detail,
    ))


def _audit_has_nonce(raw_input: str, probe_nonce: str) -> bool:
    try:
        payload = json.loads(raw_input)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("probe_nonce") == probe_nonce


def _capability_result(
    probe: AgentProbe,
    *,
    verified: bool,
    detail: str | None,
) -> AgentProbe:
    if not probe.gateway_reachable or not probe.endpoint_enabled:
        return replace(probe, crm_verified=False)
    if verified:
        if probe.last_chat_ok is False:
            return replace(
                probe,
                status="degraded",
                crm_verified=True,
            )
        return replace(
            probe,
            status="crm_verified",
            crm_verified=True,
            detail=None,
        )
    if probe.last_chat_ok is True:
        status = "chat_verified"
    elif probe.last_chat_ok is False:
        status = "failed"
    else:
        status = "endpoint_enabled"
    return replace(
        probe,
        status=status,
        crm_verified=False,
        detail=detail,
    )
