"""Phase 2 inbound: Gmail reply detection + GCal busy cache (live mode only).

Reply idempotence: every logged reply embeds "[gmail:<message_id>]" in the
event content; a message id seen once is never logged again."""
import asyncio
import os
import re
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from ..approvals import insert_pending_change
from ..db import audit, get_conn
from . import composio_client as cc

POLL_SECONDS = 300

NOISE_SENDER = re.compile(
    r"no-?reply|do-?not-?reply|newsletter|notification|mailer|daemon|unsubscribe",
    re.I)


def _escape_untrusted(text: str) -> str:
    """Escape every '<' in attacker-controlled text so no fragment of it can
    ever be reassembled into a tag — a single regex pass that strips a
    literal "</untrusted-email-content>" is bypassable by splitting the tag
    across two fragments that fuse back together once the inner one is
    removed (e.g. "</untrusted-<untrusted-email-content>email-content>"), and
    whitespace variants ("< / untrusted-email-content >") slip past a
    pattern match entirely. Escaping is unconditional and idempotent: there
    is no way to reconstruct a real '<' from '&lt;' text, regardless of how
    the input is composed. Content is preserved for the model to read, just
    never parseable as markup."""
    return text.replace("<", "&lt;")


def _wrap_untrusted(addr: str, subject: str, body: str, msg_id: str) -> str:
    """Wrap inbound-email-derived text before it reaches the model. The tag
    carries a random per-message nonce (`secrets.token_hex`, never
    attacker-predictable) so even if escaping were somehow bypassed, the
    attacker cannot know the exact closing tag to forge."""
    nonce = secrets.token_hex(3)
    tag = f"untrusted-email-content-{nonce}"
    addr_e, subject_e, body_e = (_escape_untrusted(s) for s in (addr, subject, body))
    return (f"<{tag}>\nEmail from {addr_e}\nSubject: {subject_e}\n\n"
            f"{body_e}\n[gmail:{msg_id}]\n</{tag}>")


def _extract_address(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    return (m.group(1) if m else sender).strip().lower()


def check_inbox() -> dict:
    """One polling pass over the inbox: replies from known leads (logged,
    reminder cleared, fields re-extracted) + review proposals for lead-like
    mail from unknown senders via the existing raw-text pipeline."""
    with get_conn() as conn:
        leads = [dict(r) for r in conn.execute(
            "SELECT id, name, email FROM leads WHERE email IS NOT NULL AND email != ''")]
    by_email = {l["email"].lower(): l for l in leads}
    data = cc.execute("GMAIL_FETCH_EMAILS",
                      {"query": "in:inbox newer_than:2d", "max_results": 25})
    inner = data.get("response_data") or data
    messages = inner.get("messages") or []
    counts = {"replies": 0, "intake": 0}
    for m in messages:
        msg_id = m.get("messageId") or m.get("id")
        if not msg_id:
            continue
        addr = _extract_address(m.get("sender") or m.get("from") or "")
        preview = m.get("preview") or {}
        body = (preview.get("body") if isinstance(preview, dict) else None) \
            or m.get("snippet") or ""
        lead = by_email.get(addr)
        if lead:
            counts["replies"] += _log_reply(lead, msg_id, body)
        else:
            counts["intake"] += _intake_lead(addr, m.get("subject") or "", body, msg_id)
    return counts


def _seen_in_conn(conn, msg_id: str) -> bool:
    marker = f"%[gmail:{msg_id}]%"
    if conn.execute(
        "SELECT 1 FROM events WHERE content LIKE ?", (marker,)
    ).fetchone():
        return True
    return conn.execute(
        "SELECT 1 FROM pending_changes "
        "WHERE operation = 'create_lead' AND payload LIKE ?",
        (marker,),
    ).fetchone() is not None


def _seen(msg_id: str) -> bool:
    with get_conn() as conn:
        return _seen_in_conn(conn, msg_id)


def _log_reply(lead: dict, msg_id: str, snippet: str) -> int:
    if _seen(msg_id):
        return 0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (lead["id"], "email",
             f"Reply received: {(snippet or '(no preview)')[:300]} [gmail:{msg_id}]"))
        conn.execute(
            "UPDATE reminders SET done = 1 WHERE lead_id = ? AND done = 0 "
            "AND note LIKE 'Check for a reply%'", (lead["id"],))
        conn.execute(
            "UPDATE leads SET last_activity_at = strftime('%Y-%m-%dT%H:%M:%S','now','localtime') "
            "WHERE id = ?", (lead["id"],))
        audit(conn, "cron", "gmail_reply_detected", {"lead_id": lead["id"]},
              {"message_id": msg_id}, lead["id"])
    try:
        # new info in a reply may fill missing fields / change the score
        from ..routers.leads import process_lead
        asyncio.run(process_lead(lead["id"]))
    except HTTPException as exc:
        if exc.status_code == 409:
            with get_conn() as conn:
                audit(
                    conn,
                    "cron",
                    "agent_processing_deferred",
                    {"lead_id": lead["id"]},
                    {"reason": "deterministic_fallback"},
                    lead["id"],
                )
    except Exception:
        pass  # re-extraction is best-effort; the reply event is already logged
    return 1


def _intake_lead(addr: str, subject: str, body: str, msg_id: str) -> int:
    if not addr or "@" not in addr or NOISE_SENDER.search(addr) or not body.strip():
        return 0
    if _seen(msg_id):
        return 0
    # Untrusted: this text comes from an inbound email an attacker fully
    # controls. It reaches the same model that holds a live Gmail send tool —
    # delimit it so the extract prompt (openclaw.py) treats it as data to
    # read, never as instructions to follow. See _wrap_untrusted for why the
    # wrapper is escaping-based and nonce-tagged rather than a stripped
    # literal tag.
    raw = _wrap_untrusted(addr, subject, body[:1000], msg_id)
    try:
        from ..routers.leads import (
            LeadIn,
            _resolve_create_fields,
            _resolved_create_proposal,
        )

        resolution = asyncio.run(
            _resolve_create_fields(LeadIn(raw_text=raw, source="email", email=addr))
        )
        payload, summary = _resolved_create_proposal(resolution)
        # Extraction may take minutes and therefore stays above the transaction.
        # BEGIN IMMEDIATE serializes this final re-check, proposal insert, and
        # audit so concurrent pollers cannot both claim the same Gmail message.
        with get_conn() as conn:
            if _seen_in_conn(conn, msg_id):
                return 0
            insert_pending_change(conn, "create_lead", None, payload, summary)
            audit(
                conn,
                "cron",
                "email_intake_review_required",
                {"from": addr, "subject": subject},
                {
                    "message_id": msg_id,
                    "reason": "backup_parser" if resolution.fallback else "automatic_intake",
                },
            )
    except Exception as e:
        with get_conn() as conn:
            audit(conn, "cron", "email_intake_failed",
                  {"from": addr, "subject": subject}, {"error": str(e)})
        return 0
    return 1


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(check_inbox)
        except Exception:
            pass  # transient failure — next tick retries
        await asyncio.sleep(POLL_SECONDS)


# ---- GCal busy cache (used by the availability endpoint, Task 7) ----
_busy_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def _local_naive(iso: str) -> str:
    # Assumption: this converts to GCAL_TIMEZONE (defaulting to Pacific)
    # rather than the process's own local TZ, unlike calendar.parse_ts()
    # which converts to the process TZ. On a box where the process TZ and
    # GCAL_TIMEZONE differ, GCal busy blocks and locally-stored appointment
    # times would compare in two different zones. This assumes the two are
    # kept in sync operationally (GB10/demo boxes are provisioned Pacific);
    # if that ever isn't true, switch this to `.astimezone()` (process TZ)
    # to match parse_ts exactly.
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo:
        tz = ZoneInfo(os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles"))
        dt = dt.astimezone(tz).replace(tzinfo=None)
    return dt.isoformat()


def busy_blocks(date_str: str) -> list[tuple[str, str]]:
    """Busy (start,end) local-naive ISO pairs for 'primary' on date_str.
    5-min cache; [] on any failure so availability degrades to local-only."""
    cached = _busy_cache.get(date_str)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        data = cc.execute("GOOGLECALENDAR_FREE_BUSY_QUERY", {
            "time_min": f"{date_str}T00:00:00",
            "time_max": f"{date_str}T23:59:59",
            "timezone": os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles"),
            "items": [{"id": "primary"}],
        })
        inner = data.get("response_data") or data
        cal = (inner.get("calendars") or {}).get("primary", {})
        blocks = [(_local_naive(b["start"]), _local_naive(b["end"]))
                  for b in cal.get("busy", [])]
    except Exception:
        blocks = []
    _busy_cache[date_str] = (time.time(), blocks)
    return blocks
