"""Outbound Google hooks (spec: docs/superpowers/specs/2026-07-26-gcal-gmail-
integration-design.md). Fire-and-forget: a hook must never raise — failures
land in audit_log and the triggering request succeeds regardless. Hooks open
their own connection because they run after the caller's transaction commits.

These functions are synchronous and can block for the full duration of a
live Composio call (15-30s). Callers in async endpoints MUST wrap these in
run_in_threadpool (see fastapi.concurrency) or they will freeze the whole
event loop; sync (`def`) endpoints already run in FastAPI's AnyIO threadpool
and may call them directly."""
import json
import logging
import os
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from ..db import audit, get_conn
from . import composio_client as cc


_LEAD_CALL_SUMMARY_PREFIX = "📞 Call new lead: "


class HookOutcome(Enum):
    """Outcome visible to durable dispatchers.

    A boolean cannot distinguish a real provider success from the deliberate
    off-mode simulation used by the dashboard. Durable live intents may only
    accept LIVE_DELIVERED as terminal success.
    """

    LIVE_DELIVERED = "live_delivered"
    SIMULATED = "simulated"
    FAILED = "failed"


def _tz() -> str:
    return os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles")


def _signoff() -> str:
    """Sign-off block for AI-drafted intro emails. Omitted entirely (not a
    placeholder) when AGENT_DISPLAY_NAME is unset — an unsigned friendly note
    reaching a real client is fine; a draft signed "Best,\n<placeholder>" is not."""
    name = os.environ.get("AGENT_DISPLAY_NAME", "").strip()
    return f"\n\nBest,\n{name}" if name else ""


def _sanitized_error(exc: Exception) -> str:
    message = str(exc) or type(exc).__name__
    for env_name in (
        "COMPOSIO_API_KEY",
        "OHI_API_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_API_TOKEN",
    ):
        secret = os.environ.get(env_name)
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _lead_details(lead: dict) -> str:
    budget = f"${lead['budget']:,}" if lead.get("budget") else "—"
    return (f"Phone: {lead.get('phone') or '—'}\n"
            f"Email: {lead.get('email') or '—'}\n"
            f"Budget: {budget}\n"
            f"Area: {lead.get('area') or '—'}\n"
            f"Timeline: {lead.get('timeline') or '—'}\n\n"
            "— Open House Intelligence")


def _create_event(
    lead_id: int | None,
    args: dict,
    *,
    live: bool | None = None,
    delivery_step: str | None = None,
) -> tuple[HookOutcome, str | None]:
    """Create a GCal event (or simulate) and report which one happened.

    The Composio network call runs with NO get_conn() open: it can take
    15-30s in live mode, and get_conn() now holds an exclusive BEGIN
    IMMEDIATE write lock for its whole block — holding that across a network
    round trip would serialize every other writer on busy_timeout (and 500
    them once it expires). Each audit() below gets its own short-lived
    connection instead."""
    live = cc.is_live() if live is None else live
    audit_input = dict(args)
    if delivery_step:
        audit_input["delivery_step"] = delivery_step
    if not live:
        with get_conn() as conn:
            audit(conn, "user", "gcal_create_event (simulated)", audit_input,
                  {"simulated": True}, lead_id)
        return HookOutcome.SIMULATED, None
    try:
        data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", args)
        event_id = (data.get("response_data") or data).get("id")
    except cc.IntegrationError as e:
        with get_conn() as conn:
            audit(conn, "user", "gcal_create_event (failed)", audit_input,
                  {"error": _sanitized_error(e)}, lead_id)
        return HookOutcome.FAILED, None
    with get_conn() as conn:
        audit(conn, "user", "gcal_create_event", audit_input,
              {"event_id": event_id}, lead_id)
    return HookOutcome.LIVE_DELIVERED, event_id


def _audit_hook_failure(
    tool: str,
    lead_id: int | None,
    exc: Exception,
    *,
    delivery_key: str | None = None,
) -> None:
    """Log hook failure without allowing an audit outage to break the caller."""
    try:
        audit_input = {"delivery_key": delivery_key} if delivery_key else {}
        with get_conn() as conn:
            audit(
                conn,
                "user",
                f"{tool} (failed)",
                audit_input,
                {"error": _sanitized_error(exc)},
                lead_id,
            )
    except Exception:
        logging.exception("could not persist %s failure audit", tool)


def _step_was_delivered(tool: str, lead_id: int, step_key: str | None) -> bool:
    """Return whether audit history proves this exact delivery step succeeded."""
    if not step_key:
        return False
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT input FROM audit_log WHERE lead_id = ? AND tool = ? "
            "ORDER BY id DESC",
            (lead_id, tool),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["input"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("delivery_step") == step_key:
            return True
    return False


def _delivered_event_id(lead_id: int, step_key: str | None) -> str | None:
    """Return the provider event ID saved for an exact Calendar delivery step."""
    if not step_key:
        return None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT input, output FROM audit_log WHERE lead_id = ? "
            "AND tool = 'gcal_create_event' ORDER BY id DESC",
            (lead_id,),
        ).fetchall()
    for row in rows:
        try:
            audit_input = json.loads(row["input"])
            audit_output = json.loads(row["output"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(audit_input, dict) or not isinstance(audit_output, dict):
            continue
        event_id = audit_output.get("event_id")
        if (
            audit_input.get("delivery_step") == step_key
            and isinstance(event_id, str)
            and event_id.strip()
        ):
            return event_id
    return None


def _on_tour_booked_impl(
    lead: dict,
    appt: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    live = False if force_simulated else cc.is_live()
    if live and appt.get("gcal_event_id"):
        return HookOutcome.LIVE_DELIVERED
    calendar_step = f"{delivery_key}:calendar" if delivery_key else None
    recovered_event_id = (
        _delivered_event_id(lead["id"], calendar_step) if live else None
    )
    if recovered_event_id:
        with get_conn() as conn:
            conn.execute(
                "UPDATE appointments SET gcal_event_id = ? WHERE id = ?",
                (recovered_event_id, appt["id"]),
            )
        return HookOutcome.LIVE_DELIVERED
    start = datetime.fromisoformat(appt["start_ts"])
    end = datetime.fromisoformat(appt["end_ts"])
    minutes = max(int((end - start).total_seconds() // 60), 15)
    outcome, event_id = _create_event(
        lead["id"],
        {
            "calendar_id": "primary",
            "summary": f"Home tour with {lead['name']}",
            "description": _lead_details(lead),
            "location": appt.get("location") or "",
            "start_datetime": appt["start_ts"],
            "event_duration_minutes": minutes,
            "timezone": _tz(),
        },
        live=live,
        delivery_step=calendar_step,
    )
    if outcome is HookOutcome.FAILED:
        return outcome
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE appointments SET gcal_event_id = ? WHERE id = ?",
                         (event_id, appt["id"]))
    return outcome


def on_tour_booked(
    lead: dict,
    appt: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    """Book tour in GCal. Blanket guard ensures this never raises into the calling request."""
    try:
        return _on_tour_booked_impl(
            lead,
            appt,
            force_simulated=force_simulated,
            delivery_key=delivery_key,
        )
    except Exception as e:
        _audit_hook_failure(
            "gcal_create_event",
            lead.get("id"),
            e,
            delivery_key=delivery_key,
        )
        return HookOutcome.FAILED


def _on_lead_created_impl(
    lead: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    # local wall-clock in the event's timezone — naive now() on a UTC box would
    # schedule the call block ~7h off
    start = (datetime.now(ZoneInfo(_tz())) + timedelta(minutes=30)).replace(
        second=0, microsecond=0, tzinfo=None)
    live = False if force_simulated else cc.is_live()
    calendar_step = f"{delivery_key}:calendar" if delivery_key else None
    gmail_step = f"{delivery_key}:gmail-draft" if delivery_key else None
    if live and _step_was_delivered(
        "gcal_create_event", lead["id"], calendar_step
    ):
        event_outcome = HookOutcome.LIVE_DELIVERED
    else:
        event_outcome, _event_id = _create_event(
            lead["id"],
            {
                "calendar_id": "primary",
                "summary": f"{_LEAD_CALL_SUMMARY_PREFIX}{lead['name']}",
                "description": _lead_details(lead),
                "start_datetime": start.isoformat(),
                "event_duration_minutes": 30,
                "timezone": _tz(),
            },
            live=live,
            delivery_step=calendar_step,
        )
    if event_outcome is HookOutcome.FAILED:
        return HookOutcome.FAILED
    if not lead.get("email"):
        return event_outcome
    first = lead["name"].split()[0]
    subject = (f"Your home search in {lead['area']}" if lead.get("area")
               else "Great to connect!")
    body = (f"Hi {first},\n\nThanks for reaching out — I'd love to help with "
            "your home search. When would be a good time for a quick call?"
            + _signoff())
    args = {"recipient_email": lead["email"], "subject": subject, "body": body}
    # same rule as _create_event: run the Composio call with no get_conn() open.
    if not live:
        audit_input = dict(args)
        if gmail_step:
            audit_input["delivery_step"] = gmail_step
        with get_conn() as conn:
            audit(conn, "user", "gmail_create_draft (simulated)", audit_input,
                  {"simulated": True}, lead["id"])
        return HookOutcome.SIMULATED
    if _step_was_delivered("gmail_create_draft", lead["id"], gmail_step):
        return HookOutcome.LIVE_DELIVERED
    audit_input = dict(args)
    if gmail_step:
        audit_input["delivery_step"] = gmail_step
    try:
        data = cc.execute("GMAIL_CREATE_EMAIL_DRAFT", args)
        draft_id = (data.get("response_data") or data).get("id")
    except cc.IntegrationError as e:
        with get_conn() as conn:
            audit(conn, "user", "gmail_create_draft (failed)", audit_input,
                  {"error": _sanitized_error(e)}, lead["id"])
        return HookOutcome.FAILED
    with get_conn() as conn:
        audit(
            conn,
            "user",
            "gmail_create_draft",
            audit_input,
            {"id": draft_id},
            lead["id"],
        )
    return (
        HookOutcome.LIVE_DELIVERED
        if event_outcome is HookOutcome.LIVE_DELIVERED
        else HookOutcome.FAILED
    )


def on_lead_created(
    lead: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    """Create call block + intro draft. Blanket guard ensures this never raises into the calling request."""
    try:
        return _on_lead_created_impl(
            lead,
            force_simulated=force_simulated,
            delivery_key=delivery_key,
        )
    except Exception as e:
        _audit_hook_failure(
            "lead_created_hook",
            lead.get("id"),
            e,
            delivery_key=delivery_key,
        )
        return HookOutcome.FAILED


def _on_reminder_created_impl(
    reminder: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    live = False if force_simulated else cc.is_live()
    if live and reminder.get("gcal_event_id"):
        return HookOutcome.LIVE_DELIVERED
    calendar_step = f"{delivery_key}:calendar" if delivery_key else None
    recovered_event_id = (
        _delivered_event_id(reminder["lead_id"], calendar_step) if live else None
    )
    if recovered_event_id:
        with get_conn() as conn:
            conn.execute(
                "UPDATE reminders SET gcal_event_id = ? WHERE id = ?",
                (recovered_event_id, reminder["id"]),
            )
        return HookOutcome.LIVE_DELIVERED
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM leads WHERE id = ?",
                           (reminder["lead_id"],)).fetchone()
    name = row["name"] if row else f"lead #{reminder['lead_id']}"
    outcome, event_id = _create_event(
        reminder["lead_id"],
        {
            "calendar_id": "primary",
            "summary": f"Follow up: {name}"
            + (f" — {reminder['note']}" if reminder.get("note") else ""),
            "description": "Scheduled by Open House Intelligence.",
            "start_datetime": reminder["due_ts"],
            "event_duration_minutes": 15,
            "timezone": _tz(),
        },
        live=live,
        delivery_step=calendar_step,
    )
    if outcome is HookOutcome.FAILED:
        return outcome
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE reminders SET gcal_event_id = ? WHERE id = ?",
                         (event_id, reminder["id"]))
    return outcome


def on_reminder_created(
    reminder: dict,
    *,
    force_simulated: bool = False,
    delivery_key: str | None = None,
) -> HookOutcome:
    """Create follow-up reminder in GCal. Blanket guard ensures this never raises into the calling request."""
    try:
        return _on_reminder_created_impl(
            reminder,
            force_simulated=force_simulated,
            delivery_key=delivery_key,
        )
    except Exception as e:
        _audit_hook_failure(
            "gcal_create_event",
            reminder.get("lead_id"),
            e,
            delivery_key=delivery_key,
        )
        return HookOutcome.FAILED
