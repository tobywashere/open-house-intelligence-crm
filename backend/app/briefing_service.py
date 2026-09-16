"""Build a morning briefing from canonical CRM rows.

The agent may contribute bounded preparation advice, but it never supplies the
schedule or factual lead fields rendered by the dashboard.
"""

import json
from datetime import datetime

from .briefing_contract import require_briefing_response
from .report_models import BriefingPost, MeetingAdvice


def _preferences(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _factual_summary(row) -> str:
    facts: list[str] = []
    if row["intent"] and row["intent"] != "unknown":
        facts.append(f"Intent: {row['intent']}.")
    if row["area"]:
        facts.append(f"Area: {row['area']}.")
    if row["budget"] is not None:
        facts.append(f"Budget: ${row['budget']:,}.")
    if row["timeline"]:
        facts.append(f"Timeline: {row['timeline']}.")
    preferences = _preferences(row["preferences"])
    if preferences:
        facts.append(f"Preferences: {', '.join(preferences)}.")
    return " ".join(facts) or "No additional CRM details have been recorded."


def _schedule_block(row) -> dict:
    return {
        "appointment_id": row["id"],
        "start": row["start_ts"][11:16],
        "end": row["end_ts"][11:16],
        "kind": "meeting",
        "title": f"Meeting — {row['name']}",
        "lead_id": row["lead_id"],
    }


def _meeting_brief(row, advice: MeetingAdvice | None) -> dict:
    assistant_advice = None
    if advice and (advice.prepare or advice.recommendation):
        assistant_advice = {
            "prepare": advice.prepare,
            "recommendation": advice.recommendation,
        }
    return {
        "appointment_id": row["id"],
        "lead_id": row["lead_id"],
        "name": row["name"],
        "area": row["area"],
        "budget": row["budget"],
        "timeline": row["timeline"],
        "intent": row["intent"],
        "preferences": _preferences(row["preferences"]),
        "persona": row["persona"],
        "score": row["score"],
        "summary": _factual_summary(row),
        "assistant_advice": assistant_advice,
    }


def _deterministic_actions(conn, date_key: str) -> list[dict]:
    actions: list[dict] = []
    seen: set[int] = set()
    due_rows = conn.execute(
        "SELECT r.id AS reminder_id, r.lead_id, r.due_ts, r.note, "
        "l.name, l.phone, l.email "
        "FROM reminders r JOIN leads l ON l.id = r.lead_id "
        "WHERE r.done = 0 AND substr(r.due_ts, 1, 10) <= ? "
        "AND l.status != 'closed' ORDER BY r.due_ts",
        (date_key,),
    ).fetchall()
    for row in due_rows:
        channel = "email" if row["email"] else "call" if row["phone"] else "text"
        reason = row["note"] or f"Follow-up reminder due {row['due_ts'][:16].replace('T', ' ')}."
        actions.append(
            {
                "lead_id": row["lead_id"],
                "name": row["name"],
                "channel": channel,
                "action": f"Follow up with {row['name']}",
                "reason": reason,
                "evidence": {"kind": "reminder", "id": row["reminder_id"]},
            }
        )
        seen.add(row["lead_id"])

    neglected = conn.execute(
        "SELECT id, name, phone, email, last_activity_at FROM leads "
        "WHERE is_neglected = 1 AND status != 'closed' ORDER BY last_activity_at",
    ).fetchall()
    for row in neglected:
        if row["id"] in seen:
            continue
        channel = "email" if row["email"] else "call" if row["phone"] else "text"
        actions.append(
            {
                "lead_id": row["id"],
                "name": row["name"],
                "channel": channel,
                "action": f"Reconnect with {row['name']}",
                "reason": f"CRM marked this lead neglected after the last activity on {row['last_activity_at'][:10]}.",
                "evidence": {"kind": "lead", "id": row["id"]},
            }
        )
    return actions


def build_briefing(conn, date_key: str, advice: BriefingPost | None) -> dict:
    appointments = conn.execute(
        "SELECT a.*, l.name, l.area, l.budget, l.timeline, l.intent, "
        "l.preferences, l.persona, l.score "
        "FROM appointments a JOIN leads l ON l.id = a.lead_id "
        "WHERE substr(a.start_ts, 1, 10) = ? ORDER BY a.start_ts",
        (date_key,),
    ).fetchall()
    advice_by_lead = (
        {item.lead_id: item for item in advice.meeting_briefs}
        if advice is not None
        else {}
    )
    count = len(appointments)
    greeting = (
        "Good morning — no appointments are scheduled today."
        if count == 0
        else f"Good morning — {count} appointment{'s' if count != 1 else ''} scheduled today."
    )
    response = {
        "date": date_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "crm",
        "greeting": greeting,
        "schedule": [_schedule_block(row) for row in appointments],
        "meeting_briefs": [
            _meeting_brief(row, advice_by_lead.get(row["lead_id"]))
            for row in appointments
        ],
        "suggested_actions": _deterministic_actions(conn, date_key),
    }
    return require_briefing_response(response, date_key)
