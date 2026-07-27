"""Phase 2 inbound: Gmail reply detection + GCal busy cache (live mode only).

Reply idempotence: every logged reply embeds "[gmail:<message_id>]" in the
event content; a message id seen once is never logged again."""
import asyncio
import time
from datetime import datetime

from ..db import audit, get_conn
from . import composio_client as cc

POLL_SECONDS = 300


def check_replies() -> int:
    with get_conn() as conn:
        leads = [dict(r) for r in conn.execute(
            "SELECT id, name, email FROM leads WHERE email IS NOT NULL "
            "AND email != '' AND status IN ('contacted','meeting_booked')")]
    if not leads:
        return 0
    by_email = {l["email"].lower(): l for l in leads}
    query = "from:(" + " OR ".join(by_email) + ") newer_than:7d"
    data = cc.execute("GMAIL_FETCH_EMAILS", {"query": query, "max_results": 20})
    inner = data.get("response_data") or data
    messages = inner.get("messages") or []
    new = 0
    with get_conn() as conn:
        for m in messages:
            msg_id = m.get("messageId") or m.get("id")
            sender = (m.get("sender") or m.get("from") or "").lower()
            lead = next((l for e, l in by_email.items() if e in sender), None)
            if not msg_id or not lead:
                continue
            if conn.execute("SELECT 1 FROM events WHERE lead_id = ? AND content LIKE ?",
                            (lead["id"], f"%[gmail:{msg_id}]%")).fetchone():
                continue
            preview = m.get("preview") or {}
            snippet = (preview.get("body") if isinstance(preview, dict) else None) \
                or m.get("snippet") or "(no preview)"
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead["id"], "email",
                 f"Reply received: {snippet[:300]} [gmail:{msg_id}]"))
            conn.execute(
                "UPDATE reminders SET done = 1 WHERE lead_id = ? AND done = 0 "
                "AND note LIKE 'Check for a reply%'", (lead["id"],))
            audit(conn, "cron", "gmail_reply_detected", {"lead_id": lead["id"]},
                  {"message_id": msg_id}, lead["id"])
            new += 1
    return new


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(check_replies)
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
    except (cc.IntegrationError, KeyError, ValueError):
        blocks = []
    _busy_cache[date_str] = (time.time(), blocks)
    return blocks
