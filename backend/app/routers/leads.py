import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent import get_driver
from ..approvals import insert_pending_change, is_agent_write, queue_pending_change
from ..db import audit, get_conn, row_to_dict
from ..duplicates import PLACEHOLDER_NAME, find_duplicate_candidates
from ..integrations import hooks
from ..scoring import score_lead

router = APIRouter(prefix="/leads", tags=["leads"])

NOW = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"
STATUSES = ["new", "contacted", "meeting_booked", "closed"]
DETERMINISTIC_FALLBACK_PREFIX = "[deterministic fallback] "
PROCESS_FIELDS = ("phone", "email", "budget", "area", "timeline", "intent")

# forward-only lifecycle; any state may close. Backward moves need a human
# with DB access — the agent must never un-close a lead.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "new": {"contacted", "meeting_booked", "closed"},
    "contacted": {"meeting_booked", "closed"},
    "meeting_booked": {"closed"},
    "closed": set(),
}


class LeadIn(BaseModel):
    raw_text: str | None = None
    source: Literal["form", "text", "note", "referral", "email"] = "note"
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
    score: int | None = Field(None, ge=0, le=100)
    score_reason: str | None = None
    is_neglected: int | None = Field(None, ge=0, le=1)
    persona: str | None = None
    relationship_summary: str | None = None


class ResolvedLeadCreate(BaseModel):
    """Operator-reviewed create payload allowed to reach the SQL insert seam."""

    model_config = ConfigDict(extra="forbid")

    name: str
    raw_text: str | None = None
    source: Literal["form", "text", "note", "referral", "email"] = "note"
    phone: str | None = None
    email: str | None = None
    budget: int | None = None
    area: str | None = None
    timeline: str | None = None
    intent: str | None = None
    preferences: list[str] | None = None
    missing_fields: list[str] | None = None


class EventIn(BaseModel):
    type: str
    content: str

    @field_validator("content")
    @classmethod
    def _nonempty_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be empty")
        return value


class MergeIn(BaseModel):
    primary_id: int
    duplicate_id: int


class LeadDelete(BaseModel):
    reason: str = ""


class CloseLeadIn(BaseModel):
    outcome: Literal["won", "lost"]
    reason: str | None = Field(default=None, max_length=2000)


@dataclass(frozen=True)
class LeadCreateResolution:
    """Resolved create fields plus non-persisted extraction provenance."""

    name: str
    fields: dict
    raw_text: str | None
    fallback: str | None = None


def fetch_lead(conn, lead_id: int) -> dict:
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"lead {lead_id} not found")
    return row_to_dict(row)


def _fmt_val(key: str, value) -> str:
    if value is None or value == "":
        return "—"
    if key == "budget":
        try:
            return f"${int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


# Human-readable one-liners for the pending-changes approval dialog. Each does
# a quick read (via fetch_lead, which 404s on a bad id — appropriately: a
# write for a nonexistent lead should fail fast, not get queued).
def summarize_create_lead(name: str, fields: dict, *, fallback: str | None = None) -> str:
    extra = [x for x in (fields.get("area"), f"${fields['budget']:,}" if fields.get("budget") else None) if x]
    suffix = f" ({', '.join(extra)})" if extra else ""
    summary = f"Create lead: {name}{suffix}"
    if fallback:
        return f"Backup parser used. Review every field before approving. {summary}"
    return summary


def summarize_update_lead(lead_id: int, body: "LeadPatch") -> str:
    fields = body.model_dump(exclude_none=True)
    with get_conn() as conn:
        old = fetch_lead(conn, lead_id)
    return _summarize_update_fields(old, fields)


def _summarize_update_fields(old: dict, fields: dict) -> str:
    changes = [f"{k} {_fmt_val(k, old.get(k))} → {_fmt_val(k, v)}"
               for k, v in fields.items() if old.get(k) != v]
    detail = "; ".join(changes) if changes else "no field changes"
    return f"Update lead #{old['id']} ({old.get('name')}): {detail}"


def _normalize_process_value(key: str, value):
    """Return a reviewable value, or None when extraction supplied no value."""
    if value is None or isinstance(value, bool):
        return None
    if key == "budget":
        cleaned = re.sub(r"[$,\s]", "", str(value))
        if not cleaned:
            return None
        try:
            return int(float(cleaned))
        except (TypeError, ValueError, OverflowError):
            return None

    text = " ".join(str(value).split())
    if not text:
        return None
    if key == "phone":
        digits = re.sub(r"\D", "", text)
        if not digits:
            return None
        return ("+" if text.startswith("+") else "") + digits
    if key in {"email", "intent"}:
        text = text.casefold()
    if key == "intent" and text == "unknown":
        return None
    return text


def _process_comparison_value(key: str, value):
    normalized = _normalize_process_value(key, value)
    if normalized is None:
        return None
    if key == "phone":
        return re.sub(r"\D", "", normalized)
    if key in {"area", "timeline"}:
        return normalized.casefold()
    return normalized


def _changed_process_fields(lead: dict, extracted: dict) -> dict:
    changes = {}
    for key in PROCESS_FIELDS:
        value = _normalize_process_value(key, extracted.get(key))
        if value is None:
            continue
        if _process_comparison_value(key, value) != _process_comparison_value(
            key, lead.get(key)
        ):
            changes[key] = value
    return changes


def _proposal_identity(proposed_fields: dict) -> dict:
    """Stable write identity without nondeterministic explanatory prose."""
    return {
        key: value
        for key, value in proposed_fields.items()
        if key != "score_reason"
    }


def summarize_close_lead(lead_id: int, body: "CloseLeadIn") -> str:
    with get_conn() as conn:
        old = fetch_lead(conn, lead_id)
    base = f"Close lead #{lead_id} ({old.get('name')}) as {body.outcome}"
    return base + (f": {body.reason}" if body.reason else "")


def summarize_delete_lead(lead_id: int, body: "LeadDelete | None") -> str:
    with get_conn() as conn:
        old = fetch_lead(conn, lead_id)
    reason = f" — {body.reason}" if body and body.reason else ""
    return f"Delete lead #{lead_id} ({old.get('name')}){reason}"


def summarize_merge_leads(body: "MergeIn") -> str:
    with get_conn() as conn:
        primary = fetch_lead(conn, body.primary_id)
        dup = fetch_lead(conn, body.duplicate_id)
    return f"Merge #{body.duplicate_id} ({dup.get('name')}) into #{body.primary_id} ({primary.get('name')})"


def summarize_add_event(lead_id: int, body: "EventIn") -> str:
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
    return f"Add {body.type} to #{lead_id} ({lead.get('name')}): {body.content}"


@router.post("")
async def create_lead(body: LeadIn, request: Request = None):
    if is_agent_write(request):
        # Resolve (extract) BEFORE queuing, not at approve time: the operator
        # needs to see and edit real field values in the dialog, not a raw
        # note. This does mean the agent's create_lead call pays the
        # extraction cost up front instead of never paying it until later —
        # a worthwhile trade since there'd otherwise be nothing concrete to
        # show or edit.
        resolution = await _resolve_create_fields(body)
        return _queue_resolved_create(resolution)
    return await _apply_create_lead(body)


async def _resolve_create_fields(body: LeadIn) -> LeadCreateResolution:
    """The extraction step, split out so both the immediate-apply path and
    the pending-approval queue can share it. `fields`' preferences/
    missing_fields are plain lists here (not yet JSON-encoded) — easy to
    show/edit in the approval dialog; `_insert_lead` encodes them at write
    time."""
    fields = body.model_dump(exclude={"raw_text"}, exclude_none=True)
    name = fields.pop("name", None)
    fallback = None

    if body.raw_text:
        extracted = await get_driver().extract(body.raw_text)
        fallback = extracted.pop("_fallback_used", None)
        name = name or extracted.pop("name", None)
        for k in ("phone", "email", "budget", "area", "timeline", "intent"):
            if extracted.get(k) is not None:
                fields.setdefault(k, extracted[k])
        fields["preferences"] = extracted.get("preferences", [])
        fields["missing_fields"] = extracted.get("missing_fields", [])

    return LeadCreateResolution(
        name=name or PLACEHOLDER_NAME,
        fields=fields,
        raw_text=body.raw_text,
        fallback=fallback,
    )


def _queue_resolved_create(resolution: LeadCreateResolution):
    payload, summary = _resolved_create_proposal(resolution)
    return queue_pending_change("create_lead", None, payload, summary)


def _resolved_create_proposal(resolution: LeadCreateResolution) -> tuple[dict, str]:
    payload = {
        "name": resolution.name,
        "raw_text": resolution.raw_text,
        **resolution.fields,
    }
    summary = summarize_create_lead(
        resolution.name,
        resolution.fields,
        fallback=resolution.fallback,
    )
    return payload, summary


def _insert_lead(name: str, fields: dict, raw_text: str | None, *, conn=None) -> dict:
    if conn is None:
        with get_conn() as owned_conn:
            return _insert_lead(name, fields, raw_text, conn=owned_conn)

    fields = dict(fields)
    fields.pop("_fallback_used", None)
    if "preferences" in fields:
        fields["preferences"] = json.dumps(fields["preferences"])
    if "missing_fields" in fields:
        fields["missing_fields"] = json.dumps(fields["missing_fields"])
    source = fields.get("source", "note")
    cols = ["name", *fields.keys()]
    cur = conn.execute(
        f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [name, *fields.values()],
    )
    lead_id = cur.lastrowid
    if raw_text:
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (lead_id, source if source in ("form", "text", "note", "call") else "note", raw_text),
        )
    lead = fetch_lead(conn, lead_id)
    audit(conn, "agent", "create_lead", {"source": source}, {"lead_id": lead_id}, lead_id)
    return lead


async def _apply_create_lead(body: LeadIn):
    resolution = await _resolve_create_fields(body)
    lead = _insert_lead(resolution.name, resolution.fields, resolution.raw_text)
    # create_lead is `async def`: a synchronous hook call here would freeze the
    # whole event loop (e.g. Composio's Gmail/GCal calls run ~15-30s live) —
    # run it in the threadpool so other requests keep flowing.
    await run_in_threadpool(hooks.on_lead_created, lead)
    return lead


def _apply_resolved_create_in_conn(conn, payload: dict) -> dict:
    """Approval path for a queued create_lead: payload already holds
    resolved (and possibly operator-edited) fields from _resolve_create_fields
    — this must NOT re-run extraction, which would silently overwrite any
    edit the operator made to preferences/missing_fields (extraction assigns
    those unconditionally, not just when absent)."""
    payload = dict(payload)
    payload.pop("_fallback_used", None)
    name = payload.pop("name", None) or PLACEHOLDER_NAME
    raw_text = payload.pop("raw_text", None)
    if payload.get("budget") is not None:
        try:
            payload["budget"] = int(payload["budget"])
        except (TypeError, ValueError):
            del payload["budget"]
    return _insert_lead(name, payload, raw_text, conn=conn)


async def _apply_resolved_create(payload: dict, *, run_hook: bool = True) -> dict:
    with get_conn() as conn:
        lead = _apply_resolved_create_in_conn(conn, payload)
    if run_hook:
        await run_in_threadpool(hooks.on_lead_created, lead)
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
def patch_lead(lead_id: int, body: LeadPatch, request: Request = None):
    if is_agent_write(request):
        return queue_pending_change(
            "update_lead", lead_id, body.model_dump(exclude_none=True),
            summarize_update_lead(lead_id, body))
    return _apply_patch_lead(lead_id, body)


def _apply_patch_lead(lead_id: int, body: LeadPatch, *, conn=None):
    if conn is None:
        with get_conn() as owned_conn:
            return _apply_patch_lead(lead_id, body, conn=owned_conn)

    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    if "status" in fields and fields["status"] not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    if fields.get("status") == "closed":
        raise HTTPException(
            400,
            "Use the close endpoint and choose whether the opportunity was won or lost.",
        )
    old = fetch_lead(conn, lead_id)
    if "status" in fields and fields["status"] != old["status"]:
        new_status = fields["status"]
        if new_status not in ALLOWED_TRANSITIONS[old["status"]]:
            raise HTTPException(400, f"invalid status transition {old['status']} -> {new_status}")
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


@router.post("/{lead_id}/close")
def close_lead(lead_id: int, body: CloseLeadIn, request: Request = None):
    if is_agent_write(request):
        return queue_pending_change(
            "close_lead", lead_id, body.model_dump(exclude_none=True),
            summarize_close_lead(lead_id, body))
    return _apply_close_lead(lead_id, body)


def _apply_close_lead(lead_id: int, body: CloseLeadIn, *, conn=None):
    if conn is None:
        with get_conn() as owned_conn:
            return _apply_close_lead(lead_id, body, conn=owned_conn)

    reason = body.reason.strip() if body.reason else None
    reason = reason or None
    old = fetch_lead(conn, lead_id)
    if old["status"] == "closed":
        raise HTTPException(400, "This opportunity is already closed.")
    if "closed" not in ALLOWED_TRANSITIONS[old["status"]]:
        raise HTTPException(
            400, f"invalid status transition {old['status']} -> closed"
        )
    conn.execute(
        f"UPDATE leads SET status = 'closed', outcome = ?, close_reason = ?, "
        f"last_activity_at = ({NOW}) WHERE id = ?",
        (body.outcome, reason, lead_id),
    )
    content = f"{old['status']} → closed ({body.outcome})"
    if reason:
        content += f": {reason}"
    conn.execute(
        "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
        (lead_id, "status_change", content),
    )
    audit(
        conn,
        "agent",
        "close_lead",
        {"outcome": body.outcome, "reason": reason},
        {},
        lead_id,
    )
    return fetch_lead(conn, lead_id)


@router.post("/{lead_id}/events")
def add_event(lead_id: int, body: EventIn, request: Request = None):
    if is_agent_write(request):
        return queue_pending_change(
            "add_event", lead_id, body.model_dump(), summarize_add_event(lead_id, body)
        )
    return _apply_add_event(lead_id, body, actor="user")


def _apply_add_event(
    lead_id: int, body: EventIn, actor: str = "agent", *, conn=None
) -> dict:
    if conn is None:
        with get_conn() as owned_conn:
            return _apply_add_event(lead_id, body, actor, conn=owned_conn)

    fetch_lead(conn, lead_id)
    cur = conn.execute(
        "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
        (lead_id, body.type, body.content),
    )
    conn.execute(f"UPDATE leads SET last_activity_at = ({NOW}) WHERE id = ?", (lead_id,))
    audit(conn, actor, "add_event", {"type": body.type, "content": body.content},
          {"event_id": cur.lastrowid}, lead_id)
    return dict(conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/{lead_id}/duplicates")
def find_duplicates(lead_id: int):
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
        matches = find_duplicate_candidates(
            conn, lead, exclude_lead_id=lead_id
        )
        audit(conn, "agent", "find_duplicate_leads", {"lead_id": lead_id},
              {"count": len(matches)}, lead_id)
    return matches


@router.post("/merge")
def merge_leads(body: MergeIn, request: Request = None):
    if body.primary_id == body.duplicate_id:
        raise HTTPException(400, "primary_id and duplicate_id must differ")
    if is_agent_write(request):
        return queue_pending_change(
            "merge_leads", body.primary_id, body.model_dump(), summarize_merge_leads(body))
    return _apply_merge_leads(body)


def _apply_merge_leads(body: MergeIn, *, conn=None):
    if conn is None:
        with get_conn() as owned_conn:
            return _apply_merge_leads(body, conn=owned_conn)

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
async def process_lead(lead_id: int, source_event_id: int | None = None):
    """Extract (if raw events exist) → score → draft. The core pipeline.

    Never hold a connection across the driver awaits: in openclaw mode each can
    run minutes, and an open write transaction would lock out every other
    writer (chat inserts, bookings) past busy_timeout."""
    driver = get_driver()
    with get_conn() as conn:
        lead = fetch_lead(conn, lead_id)
        event_count = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE lead_id = ?", (lead_id,)).fetchone()["c"]
        if source_event_id is None:
            source_event = conn.execute(
                "SELECT id, content FROM events WHERE lead_id = ? "
                "AND type IN ('note','form','text','email') "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (lead_id,)).fetchone()
        else:
            source_event = conn.execute(
                "SELECT id, content FROM events WHERE id = ? AND lead_id = ? "
                "AND type IN ('note','form','text','email')",
                (source_event_id, lead_id),
            ).fetchone()
            if not source_event:
                raise HTTPException(
                    404, f"source event {source_event_id} not found for lead {lead_id}"
                )

    # Extract from the selected source even when CRM fields are populated: a
    # reply can replace old contact or qualification facts. Keep all candidate
    # changes in memory until every agent response is confirmed to be real
    # agent output. A fallback must never auto-apply CRM fields or a score.
    fills: dict = {}
    if source_event:
        extracted = await driver.extract(source_event["content"])
        fallback = extracted.pop("_fallback_used", None)
        if fallback:
            raise HTTPException(
                409,
                "The local agent used its backup parser. No CRM fields were changed. "
                "Try again after OpenClaw is ready, then review the result.",
            )
        fills = _changed_process_fields(lead, extracted)

    candidate = {**lead, **fills}
    score = score_lead(candidate, event_count)
    reason = await driver.explain_score(candidate, score)
    draft = await driver.draft_followup(candidate)
    if (
        reason.startswith(DETERMINISTIC_FALLBACK_PREFIX)
        or draft.startswith(DETERMINISTIC_FALLBACK_PREFIX)
    ):
        raise HTTPException(
            409,
            "The local agent used a backup response. No CRM fields were changed. "
            "Try again after OpenClaw is ready, then review the result.",
        )

    proposed_fields = {**fills, "score": score, "score_reason": reason}
    proposed_fields = {
        key: value for key, value in proposed_fields.items() if lead.get(key) != value
    }
    response_lead = {**candidate, "score": score, "score_reason": reason}

    with get_conn() as conn:
        score_inputs = {"lead_id": lead_id}
        if source_event:
            score_inputs["source_event_id"] = int(source_event["id"])
        audit(conn, "agent", "score_lead", score_inputs,
              {"score": score, "reason": reason, "pending": bool(proposed_fields)},
              lead_id)
        audit(conn, "agent", "draft_followup", {"lead_id": lead_id},
              {"draft": draft}, lead_id)
        proposal = None
        if proposed_fields:
            if source_event:
                legacy_key = (
                    f"lead-process:{lead_id}:event:{source_event['id']}"
                )
                legacy_row = conn.execute(
                    "SELECT operation, lead_id, payload FROM pending_changes "
                    "WHERE dedupe_key = ?",
                    (legacy_key,),
                ).fetchone()
                legacy_payload = None
                if legacy_row:
                    if (
                        legacy_row["operation"] != "update_lead"
                        or legacy_row["lead_id"] != lead_id
                    ):
                        raise RuntimeError(
                            "pending change dedupe key conflicts with another proposal"
                        )
                    try:
                        legacy_payload = json.loads(legacy_row["payload"])
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise RuntimeError(
                            "legacy pending change payload is not a valid JSON object"
                        ) from exc
                    if not isinstance(legacy_payload, dict):
                        raise RuntimeError(
                            "legacy pending change payload is not a valid JSON object"
                        )
                proposal_identity = _proposal_identity(proposed_fields)
                if (
                    isinstance(legacy_payload, dict)
                    and _proposal_identity(legacy_payload) == proposal_identity
                ):
                    dedupe_key = legacy_key
                else:
                    serialized_proposal = json.dumps(
                        proposal_identity,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                    proposal_digest = hashlib.sha256(serialized_proposal).hexdigest()
                    dedupe_key = (
                        f"{legacy_key}:candidate:{proposal_digest}"
                    )
            else:
                # Preserve the serializer used by existing no-source keys.
                serialized_proposal = json.dumps(
                    proposed_fields, sort_keys=True, default=str
                ).encode()
                proposal_digest = hashlib.sha256(serialized_proposal).hexdigest()
                dedupe_key = f"lead-process:{lead_id}:candidate:{proposal_digest}"
            proposal = insert_pending_change(
                conn,
                "update_lead",
                lead_id,
                proposed_fields,
                _summarize_update_fields(lead, proposed_fields),
                dedupe_key=dedupe_key,
            )
    return {
        "lead": response_lead,
        "followup_draft": draft,
        "pending_change": proposal,
    }


@router.delete("/{lead_id}")
def delete_lead(lead_id: int, request: Request = None, body: LeadDelete = None):
    if is_agent_write(request):
        return queue_pending_change(
            "delete_lead", lead_id, (body.model_dump() if body else {"reason": ""}),
            summarize_delete_lead(lead_id, body))
    return _apply_delete_lead(lead_id, body)


def _apply_delete_lead(lead_id: int, body: LeadDelete = None, *, conn=None):
    """Delete a lead and its linked rows. events/appointments/reminders have
    NOT NULL lead_id columns, so they are removed with the lead; audit_log rows
    are kept (lead_id set NULL) so the paper trail survives the delete."""
    if conn is None:
        with get_conn() as owned_conn:
            return _apply_delete_lead(lead_id, body, conn=owned_conn)

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
