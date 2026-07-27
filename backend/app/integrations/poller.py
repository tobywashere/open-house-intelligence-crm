"""Phase 2 inbound: Gmail reply detection + GCal busy cache (live mode only).

Reply idempotence: every logged reply embeds "[gmail:<message_id>]" in the
event content; a message id seen once is never logged again."""
import asyncio
import re
import time
from datetime import datetime

from ..db import audit, get_conn
from . import composio_client as cc

POLL_SECONDS = 300

NOISE_SENDER = re.compile(
    r"no-?reply|do-?not-?reply|newsletter|notification|mailer|daemon|unsubscribe",
    re.I)


def _extract_address(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    return (m.group(1) if m else sender).strip().lower()


def check_inbox() -> dict:
    """One polling pass over the inbox: replies from known leads (logged,
    reminder cleared, fields re-extracted) + auto-intake of lead-like mail
    from unknown senders via the existing raw-text pipeline."""
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


def _seen(msg_id: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM events WHERE content LIKE ?",
                            (f"%[gmail:{msg_id}]%",)).fetchone() is not None


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
        audit(conn, "cron", "gmail_reply_detected", {"lead_id": lead["id"]},
              {"message_id": msg_id}, lead["id"])
    try:
        # new info in a reply may fill missing fields / change the score
        from ..routers.leads import process_lead
        asyncio.run(process_lead(lead["id"]))
    except Exception:
        pass  # re-extraction is best-effort; the reply event is already logged
    return 1


def _intake_lead(addr: str, subject: str, body: str, msg_id: str) -> int:
    if not addr or "@" not in addr or NOISE_SENDER.search(addr) or not body.strip():
        return 0
    if _seen(msg_id):
        return 0
    raw = f"Email from {addr}\nSubject: {subject}\n\n{body[:1000]}\n[gmail:{msg_id}]"
    try:
        from ..routers.leads import LeadIn, create_lead
        lead = asyncio.run(create_lead(LeadIn(raw_text=raw, source="email", email=addr)))
    except Exception:
        return 0
    with get_conn() as conn:
        audit(conn, "cron", "email_lead_intake", {"from": addr, "subject": subject},
              {"lead_id": lead["id"]}, lead["id"])
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
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)
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
            "timezone": "America/Los_Angeles",
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
