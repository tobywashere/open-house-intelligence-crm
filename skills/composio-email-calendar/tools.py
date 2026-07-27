"""OpenClaw tool layer for Gmail + Google Calendar via the Composio CLI.

Shells out to the locally-authed `composio` CLI (managed OAuth — no API key or
token handling here). Every call returns the tool's `data` dict on success and
raises IntegrationError on failure. See SKILL.md for the contract & guardrails.

Copy this whole directory (tools.py + SKILL.md) to
~/.openclaw/skills/composio-email-calendar on the GB10.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

DEFAULT_TZ = os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles")
TIMEOUT = float(os.environ.get("COMPOSIO_CLI_TIMEOUT_SECONDS", "30"))


class IntegrationError(Exception):
    """Raised when the Composio tool call fails (auth, network, bad args...)."""


def _cli() -> str:
    path = shutil.which("composio") or os.path.expanduser("~/.composio/composio")
    if not os.path.exists(path):
        raise IntegrationError("composio CLI not found on this machine")
    return path


def execute(slug: str, arguments: dict) -> dict:
    """Run one Composio tool. Returns the `data` payload or raises IntegrationError."""
    try:
        proc = subprocess.run(
            [_cli(), "execute", slug, "-d", json.dumps(arguments)],
            capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise IntegrationError(f"{slug}: timed out after {TIMEOUT}s")
    out = proc.stdout.strip()
    try:
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
    except (ValueError, json.JSONDecodeError):
        payload = {}
    if proc.returncode == 0 and payload.get("successful"):
        return payload.get("data") or {}
    err = payload.get("error") or proc.stderr.strip()[:300] or f"exit {proc.returncode}"
    raise IntegrationError(f"{slug}: {err}")


# ---- Gmail -----------------------------------------------------------------

def send_email(to: str, subject: str, body: str, *, cc: list | None = None,
               bcc: list | None = None) -> dict:
    """Send a real email. Only call after the user has confirmed recipient + content."""
    args = {"recipient_email": to, "subject": subject, "body": body}
    if cc:
        args["cc"] = cc
    if bcc:
        args["bcc"] = bcc
    return execute("GMAIL_SEND_EMAIL", args)


def create_draft(to: str, subject: str, body: str) -> dict:
    """Create a Gmail draft (safe: nothing is sent until a human hits send)."""
    return execute("GMAIL_CREATE_EMAIL_DRAFT",
                   {"recipient_email": to, "subject": subject, "body": body})


def fetch_emails(query: str = "in:inbox", max_results: int = 10) -> list[dict]:
    """Search the mailbox. `query` is Gmail search syntax (from:, newer_than:2d, ...)."""
    data = execute("GMAIL_FETCH_EMAILS",
                   {"query": query, "max_results": max_results})
    return data.get("messages") or []


# ---- Google Calendar -------------------------------------------------------

def create_event(summary: str, start_datetime: str, *, duration_minutes: int = 30,
                 description: str = "", location: str = "",
                 attendees: list | None = None, timezone: str = DEFAULT_TZ,
                 calendar_id: str = "primary") -> dict:
    """Create a calendar event. start_datetime is ISO-8601 local time; returns event data (incl. id)."""
    args = {"calendar_id": calendar_id, "summary": summary,
            "start_datetime": start_datetime,
            "event_duration_minutes": duration_minutes, "timezone": timezone}
    if description:
        args["description"] = description
    if location:
        args["location"] = location
    if attendees:
        args["attendees"] = attendees
    return execute("GOOGLECALENDAR_CREATE_EVENT", args)


def free_busy(time_min: str, time_max: str,
              calendar_ids: list | None = None) -> dict:
    """Busy blocks between two ISO timestamps. Check before proposing any slot."""
    return execute("GOOGLECALENDAR_FREE_BUSY_QUERY", {
        "timeMin": time_min, "timeMax": time_max,
        "items": [{"id": c} for c in (calendar_ids or ["primary"])]})


def list_events(time_min: str, time_max: str,
                calendar_id: str = "primary") -> list[dict]:
    """Events on the calendar between two ISO timestamps."""
    data = execute("GOOGLECALENDAR_EVENTS_LIST", {
        "calendarId": calendar_id, "timeMin": time_min, "timeMax": time_max,
        "singleEvents": True, "orderBy": "startTime"})
    return data.get("items") or []
