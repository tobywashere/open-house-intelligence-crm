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


# Every slug this file's own named helpers call below — the single gate all
# tools.execute() traffic passes through. Destructive/unreviewed slugs (e.g.
# GMAIL_DELETE_MESSAGE) are refused even if the model tries to invoke them
# directly via tools.execute(slug, args).
ALLOWED_SLUGS = frozenset({
    "GMAIL_SEND_EMAIL",                # send_email
    "GMAIL_CREATE_EMAIL_DRAFT",        # create_draft
    "GMAIL_FETCH_EMAILS",              # fetch_emails
    "GOOGLECALENDAR_CREATE_EVENT",     # create_event
    "GOOGLECALENDAR_FREE_BUSY_QUERY",  # free_busy
    "GOOGLECALENDAR_EVENTS_LIST",      # list_events
})


def _cli() -> str:
    path = shutil.which("composio") or os.path.expanduser("~/.composio/composio")
    if not os.path.exists(path):
        raise IntegrationError("composio CLI not found on this machine")
    return path


def _known_lead_emails() -> set:
    """Case-insensitive lead emails from the CRM (crm-db-operations skill,
    installed as a sibling directory per SKILL.md rule 2) — gates
    send_email's recipient. Loaded by file path under a distinct module name
    (not `import tools`) since this file is itself named tools.py and a bare
    `import tools` would collide with this module in sys.modules."""
    import importlib.util
    import sys as _sys
    key = "_crm_db_operations_tools"
    mod = _sys.modules.get(key)
    try:
        if mod is None:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "crm-db-operations", "tools.py")
            spec = importlib.util.spec_from_file_location(key, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _sys.modules[key] = mod
        leads = mod.list_leads(sort="recent")
    except IntegrationError:
        raise
    except Exception as e:
        raise IntegrationError(
            "cannot verify recipient: crm-db-operations skill not installed "
            f"alongside this one ({e})") from e
    return {l["email"].strip().lower() for l in leads if l.get("email")}


def _as_list(value) -> list:
    """A bare string passed where a list of addresses is expected must become
    a single-element list, not unpack per-character (`for c in "a@b.com"`
    would iterate letters and reject the call on the first character). Any
    other non-list, non-string value (int, dict, ...) is wrapped as a single
    item too — never fed to the stdlib `list()` where it would raise
    TypeError on a non-iterable — so downstream type checking (not a crash)
    is what rejects it."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# GMAIL_SEND_EMAIL's real input schema (verified against
# ~/.composio/tool_definitions/GMAIL_SEND_EMAIL.json, not guessed): cc, bcc,
# body, is_html, subject, user_id, attachment, from_email, recipient_email,
# extra_recipients. "to" isn't a schema property but recipient_email's own
# description documents it as an accepted alias, and it works in practice —
# so it's whitelisted too. Any argument key outside this set is refused: a
# future schema revision that adds a new recipient-shaped field (or a
# hand-crafted call using an undocumented one) fails CLOSED, not open.
_SEND_EMAIL_SAFE_KEYS = frozenset({
    "recipient_email", "to", "extra_recipients", "cc", "bcc",
    "subject", "body", "is_html", "user_id", "attachment", "from_email",
})
# Every key among the above that can carry a recipient address.
_SEND_EMAIL_RECIPIENT_KEYS = ("recipient_email", "to", "extra_recipients", "cc", "bcc")


def _addr_strings(value) -> list:
    """Coerce a recipient-shaped argument value into a flat list of address
    strings, raising IntegrationError (never AttributeError) for anything
    that isn't a plain string — e.g. a nested list smuggled in as one of the
    cc/bcc/extra_recipients entries."""
    out = []
    for item in _as_list(value):
        if not isinstance(item, str):
            raise IntegrationError(
                f"refusing to send: recipient value {item!r} is not a string")
        out.append(item)
    return out


def _check_send_email_allowed(arguments: dict) -> None:
    """Deny-by-default gate for GMAIL_SEND_EMAIL. Two failure modes must both
    raise, not silently pass: an argument key this file hasn't reviewed
    (instead of only checking a fixed short list of keys and ignoring
    everything else), and a call with no recognized recipient field at all
    (a send must never go out unvalidated just because the recipient lives
    under a key this guard didn't happen to check)."""
    unknown_keys = set(arguments) - _SEND_EMAIL_SAFE_KEYS
    if unknown_keys:
        raise IntegrationError(
            f"refusing to send: unrecognized argument(s) {sorted(unknown_keys)} — "
            "not in the reviewed GMAIL_SEND_EMAIL schema")
    addrs = []
    for key in _SEND_EMAIL_RECIPIENT_KEYS:
        addrs.extend(_addr_strings(arguments.get(key)))
    if not addrs:
        raise IntegrationError(
            "cannot verify recipients: no recipient field recognized in this call")
    known = _known_lead_emails()
    for addr in addrs:
        if addr.strip().lower() not in known:
            raise IntegrationError(
                f"refusing to send: {addr!r} does not match any lead's email in the CRM")


def _attendee_email(item) -> str:
    """GOOGLECALENDAR_CREATE_EVENT attendees are each either a bare email
    string or an object with an 'email' key (schema: GOOGLECALENDAR_CREATE_EVENT.json)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("email"), str):
        return item["email"]
    raise IntegrationError(f"refusing to create event: invalid attendee entry {item!r}")


def _check_attendees_allowed(arguments: dict) -> None:
    """GOOGLECALENDAR_CREATE_EVENT is an outbound-mail channel too — Google
    emails every attendee the event summary/description. `attendees` must
    pass the same recipient allowlist as GMAIL_SEND_EMAIL."""
    attendees = arguments.get("attendees")
    if not attendees:
        return
    known = _known_lead_emails()
    for item in _as_list(attendees):
        addr = _attendee_email(item)
        if addr.strip().lower() not in known:
            raise IntegrationError(
                f"refusing to create event: attendee {addr!r} does not match "
                "any lead's email in the CRM")


def execute(slug: str, arguments: dict) -> dict:
    """Run one Composio tool. Returns the `data` payload or raises IntegrationError."""
    if slug not in ALLOWED_SLUGS:
        raise IntegrationError(f"{slug}: not in the approved catalog")
    if slug == "GMAIL_SEND_EMAIL":
        # Chokepoint: SKILL.md explicitly tells the agent it may call
        # tools.execute(slug, args) directly for anything beyond the
        # send_email/create_draft/... wrappers, so send_email's own guard
        # (below) is not sufficient on its own — enforce here too, where
        # every GMAIL_SEND_EMAIL call actually goes through regardless of
        # caller.
        _check_send_email_allowed(arguments)
    elif slug == "GOOGLECALENDAR_CREATE_EVENT":
        _check_attendees_allowed(arguments)
    try:
        proc = subprocess.run(
            [_cli(), "execute", slug, "-d", json.dumps(arguments)],
            capture_output=True, text=True, timeout=TIMEOUT,
            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise IntegrationError(f"{slug}: timed out after {TIMEOUT}s")
    payload = {}
    for line in reversed([l for l in proc.stdout.splitlines() if l.strip()]):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if proc.returncode == 0 and payload.get("successful"):
        return payload.get("data") or {}
    # never surface raw stderr (may contain tokens/paths/tracebacks) into chat
    err = payload.get("error") or (
        "composio CLI failed — check `composio link` / logs" if proc.stderr.strip()
        else f"exit {proc.returncode}")
    raise IntegrationError(f"{slug}: {err}")


# ---- Gmail -----------------------------------------------------------------

def send_email(to: str, subject: str, body: str, *, cc: list | str | None = None,
               bcc: list | str | None = None) -> dict:
    """Send a real email. Only call after the user has confirmed recipient + content.
    Refuses to send if `to`, or ANY address in `cc`/`bcc`, isn't (case-insensitively)
    an existing lead's email — never invent or accept an arbitrary recipient (SKILL.md
    rule 2). Every field is checked: a known `to` cannot smuggle an unknown bcc past
    the guard (the classic prompt-injection exfiltration path). `cc`/`bcc` may be a
    single address string or a list.

    This runs the same deny-by-default check execute() enforces at the
    GMAIL_SEND_EMAIL chokepoint (this wrapper just gives a friendlier early
    error for the common case) — so the guard cannot be bypassed by skipping
    this wrapper and calling tools.execute("GMAIL_SEND_EMAIL", ...) directly,
    which SKILL.md explicitly allows."""
    args = {"recipient_email": to, "subject": subject, "body": body}
    cc, bcc = _as_list(cc), _as_list(bcc)
    if cc:
        args["cc"] = cc
    if bcc:
        args["bcc"] = bcc
    _check_send_email_allowed(args)
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
