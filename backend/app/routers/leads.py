import difflib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent import get_driver
from ..db import audit, get_conn, row_to_dict
from ..integrations import hooks
from ..scoring import score_lead

router = APIRouter(prefix="/leads", tags=["leads"])

NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
STATUSES = ["new", "contacted", "meeting_booked", "closed"]


class LeadIn(BaseModel):
    raw_text: str | None = None
    source: str = "note"
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    budget: int | None = None
    area: str | None = None
    timeline: str | None = None
    intent: str | None = None


class LeadPatch(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None
    budget: int | None = None
    area: str | None = None
    timeline: str | None = None
    intent: str | None = None
    score: int | None = None
    score_reason: str | None = None
    is_neglected: int | None = None
    persona: str | None = None
    relationship_summary: str | None = None


class EventIn(BaseModel):
    type: str
    content: str


class MergeIn(BaseModel):
    primary_id: int
    duplicate_id: int


class LeadDelete(BaseModel):
    reason: str = ""


def fetch_lead(conn, lead_id: int) -> dict:
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"lead {lead_id} not found")
    return row_to_dict(row)


@router.post("")
async def create_lead(body: LeadIn):
    fields = body.model_dump(exclude={"raw_text"}, exclude_none=True)
    name = fields.pop("name", None)

    if body.raw_text:
        extracted = await get_driver().extract(body.raw_text)
        name = name or extracted.pop("name", None)
        for k in ("phone", "email", "budget", "area", "timeline", "intent"):
            if extracted.get(k) is not None:
                fields.setdefault(k, extracted[k])
        fields["preferences"] = json.dumps(extracted.get("preferences", []))
        fields["missing_fields"] = json.dumps(extracted.get("missing_fields", []))

    name = name or "Unknown lead"
    with get_conn() as conn:
        cols = ["name", *fields.keys()]
        cur = conn.execute(
            f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [name, *fields.values()],
        )
        lead_id = cur.lastrowid
        if body.raw_text:
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead_id, body.source if body.source in ("form", "text", "note", "call") else "note",
                 body.raw_text),
            )
        lead = fetch_lead(conn, lead_id)
        audit(conn, "agent", "create_lead", {"source": body.source}, {"lead_id": lead_id}, lead_id)
    hooks.on_lead_created(lead)
    return lead


@router.get("")
def list_leads(sort: str = "priority", status: str | None = None, neglected: int | None = None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if neglected is not None:
        where.append("is_neglected = ?")
        params.append(neglected)
    q = "SELECT * FROM leads"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += (" ORDER BY is_neglected DESC, score DESC NULLS LAST, last_activity_at DESC"
          if sort == "priority" else " ORDER BY created_at DESC")
    with get_conn() as conn:
        return [row_to_dict(r) for r in conn.execute(q, params).fetchall()]


@router.get("/{lead_id}")
def get_lead(lead_id: int):
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
        lead["events"] = [dict(r) for r in conn.execute(
            "SELECT * FROM events WHERE lead_id = ? ORDER BY created_at DESC", (lead_id,))]
        lead["appointments"] = [dict(r) for r in conn.execute(
            "SELECT * FROM appointments WHERE lead_id = ? ORDER BY start_ts", (lead_id,))]
    return lead


@router.patch("/{lead_id}")
def patch_lead(lead_id: int, body: LeadPatch):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    if "status" in fields and fields["status"] not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    with get_conn() as conn:
        old = fetch_lead(conn, lead_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE leads SET {sets}, last_activity_at = ({NOW}) WHERE id = ?",
            [*fields.values(), lead_id],
        )
        if "status" in fields and fields["status"] != old["status"]:
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead_id, "status_change", f"{old['status']} → {fields['status']}"),
            )
        audit(conn, "agent", "update_lead", fields, {}, lead_id)
        return fetch_lead(conn, lead_id)


@router.post("/{lead_id}/events")
def add_event(lead_id: int, body: EventIn):
    with get_conn() as conn:
        fetch_lead(conn, lead_id)
        cur = conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (lead_id, body.type, body.content),
        )
        conn.execute(f"UPDATE leads SET last_activity_at = ({NOW}) WHERE id = ?", (lead_id,))
        return dict(conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/{lead_id}/duplicates")
def find_duplicates(lead_id: int):
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
        others = [row_to_dict(r) for r in
                  conn.execute("SELECT * FROM leads WHERE id != ?", (lead_id,))]
        matches = []
        for o in others:
            if lead.get("phone") and o.get("phone") == lead["phone"]:
                matches.append({"lead": o, "match_on": "phone"})
            elif lead.get("email") and o.get("email") == lead["email"]:
                matches.append({"lead": o, "match_on": "email"})
            elif difflib.SequenceMatcher(
                    None, lead["name"].lower(), o["name"].lower()).ratio() > 0.85:
                matches.append({"lead": o, "match_on": "name"})
        audit(conn, "agent", "find_duplicate_leads", {"lead_id": lead_id},
              {"count": len(matches)}, lead_id)
    return matches


@router.post("/merge")
def merge_leads(body: MergeIn):
    with get_conn() as conn:
        primary = fetch_lead(conn, body.primary_id)
        dup = fetch_lead(conn, body.duplicate_id)
        # duplicate fills primary's blanks; primary wins conflicts
        fills = {k: dup[k] for k in
                 ("phone", "email", "budget", "area", "timeline", "intent")
                 if not primary.get(k) and dup.get(k)}
        if fills:
            sets = ", ".join(f"{k} = ?" for k in fills)
            conn.execute(f"UPDATE leads SET {sets} WHERE id = ?",
                         [*fills.values(), body.primary_id])
        conn.execute("UPDATE events SET lead_id = ? WHERE lead_id = ?",
                     (body.primary_id, body.duplicate_id))
        conn.execute("UPDATE appointments SET lead_id = ? WHERE lead_id = ?",
                     (body.primary_id, body.duplicate_id))
        conn.execute("UPDATE reminders SET lead_id = ? WHERE lead_id = ?",
                     (body.primary_id, body.duplicate_id))
        conn.execute("UPDATE audit_log SET lead_id = ? WHERE lead_id = ?",
                     (body.primary_id, body.duplicate_id))
        conn.execute("DELETE FROM leads WHERE id = ?", (body.duplicate_id,))
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (body.primary_id, "merge",
             f"Merged duplicate '{dup['name']}' (#{body.duplicate_id}) into this profile"),
        )
        conn.execute(f"UPDATE leads SET last_activity_at = ({NOW}) WHERE id = ?",
                     (body.primary_id,))
        audit(conn, "agent", "merge_leads",
              {"primary_id": body.primary_id, "duplicate_id": body.duplicate_id},
              {"filled": list(fills)}, body.primary_id)
        return fetch_lead(conn, body.primary_id)


@router.post("/{lead_id}/process")
async def process_lead(lead_id: int):
    """Extract (if raw events exist) → score → draft. The core pipeline.

    Never hold a connection across the driver awaits: in openclaw mode each can
    run minutes, and an open write transaction would lock out every other
    writer (chat inserts, bookings) past busy_timeout."""
    driver = get_driver()
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
        event_count = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE lead_id = ?", (lead_id,)).fetchone()["c"]
        latest = None
        if not lead.get("budget") or not lead.get("timeline"):
            latest = conn.execute(
                "SELECT content FROM events WHERE lead_id = ? AND type IN ('note','form','text','email') "
                "ORDER BY created_at DESC LIMIT 1", (lead_id,)).fetchone()

    # re-extract from the latest raw note if fields are missing
    if latest:
        extracted = await driver.extract(latest["content"])
        fills = {k: extracted[k] for k in
                 ("phone", "email", "budget", "area", "timeline", "intent")
                 if not lead.get(k) and extracted.get(k) is not None}
        # the model may answer "1.1M" — a non-numeric budget breaks score_lead
        # for this lead forever, so drop it rather than store it
        if "budget" in fills and not isinstance(fills["budget"], (int, float)):
            try:
                fills["budget"] = int(float(str(fills["budget"]).replace(",", "").strip()))
            except ValueError:
                del fills["budget"]
        if fills:
            with get_conn() as conn:
                sets = ", ".join(f"{k} = ?" for k in fills)
                conn.execute(f"UPDATE leads SET {sets} WHERE id = ?",
                             [*fills.values(), lead_id])
                lead = fetch_lead(conn, lead_id)

    score = score_lead(lead, event_count)
    reason = await driver.explain_score(lead, score)
    draft = await driver.draft_followup(lead)

    with get_conn() as conn:
        conn.execute("UPDATE leads SET score = ?, score_reason = ? WHERE id = ?",
                     (score, reason, lead_id))
        audit(conn, "agent", "score_lead", {"lead_id": lead_id},
              {"score": score, "reason": reason}, lead_id)
        audit(conn, "agent", "draft_followup", {"lead_id": lead_id},
              {"draft": draft}, lead_id)
        lead = fetch_lead(conn, lead_id)
    return {"lead": lead, "followup_draft": draft}


@router.delete("/{lead_id}")
def delete_lead(lead_id: int, body: LeadDelete = None):
    """Delete a lead and its linked rows. events/appointments/reminders have
    NOT NULL lead_id columns, so they are removed with the lead; audit_log rows
    are kept (lead_id set NULL) so the paper trail survives the delete."""
    with get_conn() as conn:
        old = fetch_lead(conn, lead_id)
        for table in ("events", "appointments", "reminders"):
            conn.execute(f"DELETE FROM {table} WHERE lead_id = ?", (lead_id,))
        conn.execute("UPDATE audit_log SET lead_id = NULL WHERE lead_id = ?", (lead_id,))
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        # lead_id=None: the row is gone, an FK reference to it would be rejected
        audit(conn, "agent", "delete_lead",
              {"lead_id": lead_id, "reason": (body.reason if body else "")},
              {"name": old.get("name")}, None)
    return {"deleted": True, "lead_id": lead_id, "name": old.get("name")}
