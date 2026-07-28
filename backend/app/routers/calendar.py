from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from datetime import date as _date

from ..calendar_adapter import calendar
from ..db import audit, get_conn
from ..integrations import composio_client as cc
from ..integrations.poller import busy_blocks as integrations_busy
from ..integrations import hooks
from .leads import NOW, fetch_lead, ALLOWED_TRANSITIONS

router = APIRouter(tags=["calendar"])


class AppointmentIn(BaseModel):
    lead_id: int
    start_ts: str
    end_ts: str
    location: str | None = None

    @field_validator("start_ts", "end_ts")
    @classmethod
    def _parseable(cls, v: str) -> str:
        try:
            dt = calendar.parse_ts(v)
        except ValueError:
            raise ValueError("must be an ISO-8601 timestamp, e.g. 2026-08-01T17:00:00")
        # normalize through parse_ts, same as ReminderIn: an aware/Z-suffixed
        # start_ts from a client must be CONVERTED to local before storage,
        # not stored raw — storing raw here would silently reintroduce the
        # mixed-convention bug Task 7 fixed, just for appointments instead
        # of reminders.
        return dt.isoformat(timespec="seconds")


@router.get("/availability")
def availability(date: str):
    try:
        _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    # integrations_busy() hits the Composio free/busy API (can take seconds in
    # live mode) — run it with no get_conn() open, since get_conn() now holds
    # an exclusive BEGIN IMMEDIATE write lock for its whole block.
    live = cc.is_live()
    busy = integrations_busy(date) if live else []
    with get_conn() as conn:
        slots = calendar.free_slots(conn, date)
        if live:
            slots = [s for s in slots if not any(
                s["start_ts"] < b_end and s["end_ts"] > b_start
                for b_start, b_end in busy)]
        audit(conn, "agent", "check_availability", {"date": date}, {"free": len(slots)})
    return slots


@router.get("/appointments")
def list_appointments():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.*, l.name AS lead_name FROM appointments a "
            "JOIN leads l ON l.id = a.lead_id ORDER BY a.start_ts")]


@router.post("/appointments")
def book_appointment(body: AppointmentIn):
    with get_conn() as conn:
        lead = fetch_lead(conn, body.lead_id)
        if calendar.has_conflict(conn, body.start_ts, body.end_ts):
            raise HTTPException(409, "slot conflicts with an existing appointment")
        # Validate status transition before creating appointment
        if lead["status"] != "meeting_booked" and "meeting_booked" not in ALLOWED_TRANSITIONS[lead["status"]]:
            raise HTTPException(400, f"cannot book: invalid status transition {lead['status']} -> meeting_booked")
        cur = conn.execute(
            "INSERT INTO appointments (lead_id, start_ts, end_ts, location) VALUES (?,?,?,?)",
            (body.lead_id, body.start_ts, body.end_ts, body.location),
        )
        conn.execute(
            f"UPDATE leads SET status = 'meeting_booked', last_activity_at = ({NOW}) "
            "WHERE id = ?", (body.lead_id,))
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (body.lead_id, "status_change", f"Meeting booked for {body.start_ts}"),
        )
        appt = dict(conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (cur.lastrowid,)).fetchone())
        audit(conn, "agent", "book_appointment", body.model_dump(),
              {"appointment_id": appt["id"]}, body.lead_id)
    # book_appointment is a sync `def` endpoint: FastAPI already runs the
    # whole handler in the AnyIO threadpool, so this call can't freeze the
    # event loop — no run_in_threadpool wrapping needed here.
    hooks.on_tour_booked(lead, appt)
    return appt


@router.get("/appointments/{appt_id}/ics")
def appointment_ics(appt_id: int):
    with get_conn() as conn:
        appt = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
        if not appt:
            raise HTTPException(404, "appointment not found")
        lead = fetch_lead(conn, appt["lead_id"])
    return Response(
        calendar.to_ics(dict(appt), lead["name"]),
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=appointment-{appt_id}.ics"},
    )
