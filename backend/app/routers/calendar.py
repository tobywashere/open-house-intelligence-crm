from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from datetime import date as _date

from ..calendar_adapter import calendar
from ..db import audit, get_conn
from .leads import NOW, fetch_lead

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
            calendar.parse_ts(v)
        except ValueError:
            raise ValueError("must be an ISO-8601 timestamp, e.g. 2026-08-01T17:00:00")
        return v


@router.get("/availability")
def availability(date: str):
    try:
        _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    with get_conn() as conn:
        slots = calendar.free_slots(conn, date)
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
