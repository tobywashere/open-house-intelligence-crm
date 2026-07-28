import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..agent import get_driver
from ..calendar_adapter import calendar
from ..db import audit, get_conn, row_to_dict
from ..integrations import hooks
from .leads import fetch_lead
from ..scoring import is_high_priority

router = APIRouter(tags=["misc"])

NEGLECT_AFTER_DAYS = 2


class AdvanceTimeIn(BaseModel):
    days: int = 3


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
def create_reminder(body: ReminderIn):
    with get_conn() as conn:
        fetch_lead(conn, body.lead_id)  # 404 instead of an FK IntegrityError 500
        cur = conn.execute(
            "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
            (body.lead_id, body.due_ts, body.note),
        )
        audit(conn, "agent", "schedule_followup", body.model_dump(), {}, body.lead_id)
        reminder = dict(conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone())
    # create_reminder is a sync `def` endpoint: FastAPI already runs the
    # whole handler in the AnyIO threadpool, so this call can't freeze the
    # event loop — no run_in_threadpool wrapping needed here.
    hooks.on_reminder_created(reminder)
    return reminder


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
        neglected = run_neglect_check(conn)
    return {"neglected": neglected}


@router.get("/audit")
def audit_log(limit: int = 50):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.*, l.name AS lead_name FROM audit_log a "
            "LEFT JOIN leads l ON l.id = a.lead_id "
            "ORDER BY a.id DESC LIMIT ?", (limit,))]


@router.get("/metrics")
def metrics():
    with get_conn() as conn:
        leads = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM leads WHERE status != 'closed'")]
        appts = conn.execute("SELECT COUNT(*) c FROM appointments").fetchone()["c"]
    return {
        "active_leads": len(leads),
        "high_priority": sum(1 for l in leads if is_high_priority(l.get("score"))),
        "followups_due": sum(1 for l in leads if l["is_neglected"]),
        "appointments_booked": appts,
        "avg_response_minutes": 4,  # TODO(Toby): compute from events once real data flows
        "agent_mode": os.environ.get("AGENT_MODE", "mock"),
        "cloud_llm_requests": 0,
    }


@router.get("/health")
async def health():
    driver = get_driver()
    return {"ok": True, "agent_mode": driver.name, "agent_connected": await driver.connected()}
