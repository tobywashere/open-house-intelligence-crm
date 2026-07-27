"""Outbound Google hooks (spec: docs/superpowers/specs/2026-07-26-gcal-gmail-
integration-design.md). Fire-and-forget: a hook must never raise — failures
land in audit_log and the triggering request succeeds regardless. Hooks open
their own connection because they run after the caller's transaction commits."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..db import audit, get_conn
from . import composio_client as cc


def _tz() -> str:
    return os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles")


def _lead_details(lead: dict) -> str:
    budget = f"${lead['budget']:,}" if lead.get("budget") else "—"
    return (f"Phone: {lead.get('phone') or '—'}\n"
            f"Email: {lead.get('email') or '—'}\n"
            f"Budget: {budget}\n"
            f"Area: {lead.get('area') or '—'}\n"
            f"Timeline: {lead.get('timeline') or '—'}\n\n"
            "— Open House Intelligence")


def _create_event(lead_id: int | None, args: dict) -> str | None:
    """Create a GCal event (or simulate). Returns the Google event id when live."""
    with get_conn() as conn:
        if not cc.is_live():
            audit(conn, "user", "gcal_create_event (simulated)", args,
                  {"simulated": True}, lead_id)
            return None
        try:
            data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", args)
            event_id = (data.get("response_data") or data).get("id")
            audit(conn, "user", "gcal_create_event", args,
                  {"event_id": event_id}, lead_id)
            return event_id
        except cc.IntegrationError as e:
            audit(conn, "user", "gcal_create_event (failed)", args,
                  {"error": str(e)}, lead_id)
            return None


def _audit_hook_failure(tool: str, lead_id: int | None, exc: Exception) -> None:
    """Safely log hook failure to audit log. Never raises, even if DB is broken."""
    try:
        with get_conn() as conn:
            audit(conn, "user", f"{tool} (failed)", {}, {"error": str(exc)}, lead_id)
    except Exception:
        pass  # If audit itself fails, swallow it — never let the hook raise


def _on_tour_booked_impl(lead: dict, appt: dict) -> None:
    start = datetime.fromisoformat(appt["start_ts"])
    end = datetime.fromisoformat(appt["end_ts"])
    minutes = max(int((end - start).total_seconds() // 60), 15)
    event_id = _create_event(lead["id"], {
        "calendar_id": "primary",
        "summary": f"Home tour with {lead['name']}",
        "description": _lead_details(lead),
        "location": appt.get("location") or "",
        "start_datetime": appt["start_ts"],
        "event_duration_minutes": minutes,
        "timezone": _tz(),
    })
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE appointments SET gcal_event_id = ? WHERE id = ?",
                         (event_id, appt["id"]))


def on_tour_booked(lead: dict, appt: dict) -> None:
    """Book tour in GCal. Blanket guard ensures this never raises into the calling request."""
    try:
        _on_tour_booked_impl(lead, appt)
    except Exception as e:
        _audit_hook_failure("gcal_create_event", lead.get("id"), e)


def _on_lead_created_impl(lead: dict) -> None:
    # local wall-clock in the event's timezone — naive now() on a UTC box would
    # schedule the call block ~7h off
    start = (datetime.now(ZoneInfo(_tz())) + timedelta(minutes=30)).replace(
        second=0, microsecond=0, tzinfo=None)
    _create_event(lead["id"], {
        "calendar_id": "primary",
        "summary": f"📞 Call new lead: {lead['name']}",
        "description": _lead_details(lead),
        "start_datetime": start.isoformat(),
        "event_duration_minutes": 30,
        "timezone": _tz(),
    })
    if not lead.get("email"):
        return
    first = lead["name"].split()[0]
    subject = (f"Your home search in {lead['area']}" if lead.get("area")
               else "Great to connect!")
    body = (f"Hi {first},\n\nThanks for reaching out — I'd love to help with "
            "your home search. When would be a good time for a quick call?\n\n"
            "Best,\nJohaan")
    args = {"recipient_email": lead["email"], "subject": subject, "body": body}
    with get_conn() as conn:
        if not cc.is_live():
            audit(conn, "user", "gmail_create_draft (simulated)", args,
                  {"simulated": True}, lead["id"])
            return
        try:
            data = cc.execute("GMAIL_CREATE_EMAIL_DRAFT", args)
            audit(conn, "user", "gmail_create_draft", args,
                  {"id": (data.get("response_data") or data).get("id")}, lead["id"])
        except cc.IntegrationError as e:
            audit(conn, "user", "gmail_create_draft (failed)", args,
                  {"error": str(e)}, lead["id"])


def on_lead_created(lead: dict) -> None:
    """Create call block + intro draft. Blanket guard ensures this never raises into the calling request."""
    try:
        _on_lead_created_impl(lead)
    except Exception as e:
        _audit_hook_failure("gmail_create_draft", lead.get("id"), e)


def _on_reminder_created_impl(reminder: dict) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM leads WHERE id = ?",
                           (reminder["lead_id"],)).fetchone()
    name = row["name"] if row else f"lead #{reminder['lead_id']}"
    event_id = _create_event(reminder["lead_id"], {
        "calendar_id": "primary",
        "summary": f"Follow up: {name}" + (f" — {reminder['note']}" if reminder.get("note") else ""),
        "description": "Scheduled by Open House Intelligence.",
        "start_datetime": reminder["due_ts"],
        "event_duration_minutes": 15,
        "timezone": _tz(),
    })
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE reminders SET gcal_event_id = ? WHERE id = ?",
                         (event_id, reminder["id"]))


def on_reminder_created(reminder: dict) -> None:
    """Create follow-up reminder in GCal. Blanket guard ensures this never raises into the calling request."""
    try:
        _on_reminder_created_impl(reminder)
    except Exception as e:
        _audit_hook_failure("gcal_create_event", reminder.get("lead_id"), e)
