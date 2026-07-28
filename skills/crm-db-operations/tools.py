"""OpenClaw tool layer for the Open House Intelligence CRM.

Thin, zero-dependency (stdlib only) client for the backend REST API described in
docs/CONTRACT.md. Each function here is one entry in the tool catalog the local
model is allowed to call — see SKILL.md in this directory for the
full contract, guardrails, and usage examples.

The model must never see or write raw SQL; every DB read/write goes through one
of these functions, which call the FastAPI backend (Toby's layer) over HTTP.

Configure the backend location with the CRM_API_URL env var
(default: http://localhost:8080/api — same host as the backend when both run on
the GB10 for the demo).

Copy this whole directory (tools.py + SKILL.md) to ~/.openclaw/skills/crm-db-operations
on the GB10 instance.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("CRM_API_URL", "http://localhost:8080/api").rstrip("/")
TIMEOUT = float(os.environ.get("CRM_API_TIMEOUT_SECONDS", "120"))
API_TOKEN = os.environ.get("OHI_API_TOKEN", "")


class CRMError(Exception):
    """Raised when the backend rejects a call (validation, 404, 409 conflict, ...)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"CRM API {status}: {message}")
        self.status = status
        self.message = message


def _request(method: str, path: str, *, params: dict | None = None,
             body: dict | None = None) -> dict | list:
    url = BASE_URL + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["X-API-Token"] = API_TOKEN
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise CRMError(e.code, str(detail)) from None
    except urllib.error.URLError as e:
        raise CRMError(0, f"could not reach CRM backend at {BASE_URL}: {e.reason}") from None
    except (TimeoutError, OSError) as e:  # read-timeouts bypass URLError wrapping
        raise CRMError(0, f"CRM backend timed out or dropped the connection: {e}") from None


# --------------------------------------------------------------------------
# Lead lifecycle
# --------------------------------------------------------------------------

def create_lead(raw_text: str | None = None, source: str = "note", *, name: str | None = None,
                 phone: str | None = None, email: str | None = None, budget: int | None = None,
                 area: str | None = None, timeline: str | None = None,
                 intent: str | None = None) -> dict:
    """Create a lead. Pass raw_text for an unstructured note/form/text (gets
    extracted server-side); pass structured fields directly if already known.
    source: form | text | note | referral.
    """
    body = {"raw_text": raw_text, "source": source, "name": name, "phone": phone,
             "email": email, "budget": budget, "area": area, "timeline": timeline,
             "intent": intent}
    return _request("POST", "/leads", body={k: v for k, v in body.items() if v is not None})


def update_lead(lead_id: int, **fields) -> dict:
    """Patch one or more fields on a lead (name, phone, email, status, budget,
    area, timeline, intent, score, score_reason, is_neglected). Only send fields
    that actually changed — never guess a lead_id, always resolve it first via
    find_duplicate_leads / get_lead_context.
    """
    if not fields:
        raise ValueError("update_lead requires at least one field to change")
    return _request("PATCH", f"/leads/{lead_id}", body=fields)


def find_duplicate_leads(lead_id: int) -> list:
    """Return other leads that look like the same person: exact phone/email
    match, or fuzzy name match. Use before merging or before creating a new
    lead you suspect already exists.
    """
    return _request("GET", f"/leads/{lead_id}/duplicates")


def merge_leads(primary_id: int, duplicate_id: int) -> dict:
    """Merge duplicate_id into primary_id: fills primary's blank fields from the
    duplicate, moves its activity history over, and deletes the duplicate row.
    Primary wins on any field conflict.
    """
    return _request("POST", "/leads/merge", body={"primary_id": primary_id,
                                                    "duplicate_id": duplicate_id})


def get_lead_context(lead_id: int) -> dict:
    """Full profile for one lead: fields + activity timeline (events) +
    appointments, most recent first. Use this before drafting messages or
    answering "what do we know about X" questions.
    """
    return _request("GET", f"/leads/{lead_id}")


def list_leads(sort: str = "priority", status: str | None = None,
                neglected: int | None = None) -> list:
    """List leads. sort='priority' orders neglected-first, then score desc;
    sort='recent' orders by created_at desc. Filter by status
    (new|contacted|meeting_booked|closed) and/or neglected (0|1).
    """
    return _request("GET", "/leads", params={"sort": sort, "status": status,
                                              "neglected": neglected})


# --------------------------------------------------------------------------
# Scoring & follow-up
# --------------------------------------------------------------------------

def _process_lead(lead_id: int) -> dict:
    return _request("POST", f"/leads/{lead_id}/process")


def score_lead(lead_id: int) -> dict:
    """Run the deterministic scoring formula for a lead and persist score +
    score_reason. Returns {"lead_id", "score", "score_reason"}.
    Note: this call and draft_followup share one backend round trip
    (POST /leads/{id}/process) — calling either re-runs both.
    """
    result = _process_lead(lead_id)
    lead = result["lead"]
    return {"lead_id": lead_id, "score": lead["score"], "score_reason": lead["score_reason"]}


def draft_followup(lead_id: int) -> str:
    """Generate a personalized follow-up message for a lead, grounded in their
    stored context (budget, area, timeline, intent, activity).
    """
    return _process_lead(lead_id)["followup_draft"]


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def check_availability(date: str) -> list:
    """Free meeting slots on a given date (YYYY-MM-DD), with existing
    appointments already excluded. Times are ISO 8601, local calendar.
    """
    return _request("GET", "/availability", params={"date": date})


def list_appointments() -> list:
    """Every booked appointment, across all leads, ordered by start_ts, each
    row including lead_name. Use this to find who has an appointment today —
    filter the returned list client-side on start_ts's date — before deciding
    which leads need get_lead_context for a schedule/briefing.
    """
    return _request("GET", "/appointments")


def book_appointment(lead_id: int, start_ts: str, end_ts: str,
                      location: str | None = None) -> dict:
    """Book a meeting. Raises CRMError(status=409) if the slot conflicts with
    an existing appointment — check_availability first. On success the lead's
    status flips to meeting_booked automatically.
    """
    return _request("POST", "/appointments", body={"lead_id": lead_id, "start_ts": start_ts,
                                                     "end_ts": end_ts, "location": location})


def schedule_followup(lead_id: int, due_ts: str, note: str | None = None) -> dict:
    """Schedule a reminder (e.g. after find_neglected_leads flags someone, or
    after sending an initial follow-up) — the dashboard polls for due ones.
    """
    return _request("POST", "/reminders", body={"lead_id": lead_id, "due_ts": due_ts,
                                                   "note": note})


# --------------------------------------------------------------------------
# Scheduled / summary work
# --------------------------------------------------------------------------

def find_neglected_leads() -> list:
    """Evaluate every open lead against the neglect rule (no activity for 2+
    days) right now, flag newly-neglected ones (is_neglected=1), and return
    just the leads that were newly flagged by this call.
    Use list_leads(neglected=1) to see all currently-neglected leads without
    re-running the check.
    """
    return _request("POST", "/demo/advance-time", body={"days": 0})["neglected"]


def generate_dashboard_insights() -> dict:
    """Return the deterministic dashboard numbers (active leads, high-priority
    count, follow-ups due, appointments booked, avg response time, inference
    mode). This tool returns raw numbers only — the model is expected to
    compose the natural-language summary/insight on top of them; the backend
    does not write prose here.
    """
    return _request("GET", "/metrics")


def post_briefing(payload: dict) -> dict:
    """Upsert the day's morning-briefing JSON (POST /briefing, upsert by
    date). payload must match the shape in docs/BRIEFING-UI.md and include a
    "date" key (YYYY-MM-DD) — that's the upsert key. Used by the
    daily-command-center skill's final step; the dashboard reads it back via
    GET /briefing?date=.
    """
    return _request("POST", "/briefing", body=payload)


def delete_lead(lead_id: int, reason: str = "") -> dict:
    """Permanently delete a lead. Destructive — the skill doc requires explicit
    user confirmation before calling this. Removes the lead and its
    events/appointments/reminders (audit rows survive)."""
    return _request("DELETE", f"/leads/{int(lead_id)}",
                     body={"reason": reason} if reason else None)
