"""Every write through the REST layer must leave an audit_log row (Task 11
fix round 1 — docs/CONTRACT.md §3 previously claimed this while three write
endpoints silently skipped it). Pins the fix so it can't regress."""
import json


AGENT = {"X-Actor": "agent"}


def _mk(client, **kw):
    body = {"name": "T", "source": "note", "status": "new"} | kw
    return client.post("/api/leads", json=body).json()


def _last_audit_row(client, tool: str) -> dict:
    rows = client.get("/api/audit?limit=50").json()
    matches = [r for r in rows if r["tool"] == tool]
    assert matches, f"no audit_log row found for tool={tool!r}"
    return matches[0]  # newest first


def test_add_event_is_audited(client):
    lead = _mk(client)
    r = client.post(f"/api/leads/{lead['id']}/events",
                     json={"type": "call", "content": "left a voicemail"})
    assert r.status_code == 200
    row = _last_audit_row(client, "add_event")
    assert row["actor"] == "user"
    assert row["lead_id"] == lead["id"]


def test_complete_reminder_is_audited(client):
    lead = _mk(client)
    reminder = client.post("/api/reminders", json={
        "lead_id": lead["id"], "due_ts": "2026-08-01T09:00:00", "note": "follow up",
    }).json()
    r = client.patch(f"/api/reminders/{reminder['id']}")
    assert r.status_code == 200
    row = _last_audit_row(client, "complete_reminder")
    assert row["actor"] == "user"
    assert row["lead_id"] == lead["id"]


def test_clear_chat_history_is_audited(client):
    session_id = "test-audit-clear"
    client.post("/api/chat", json={"message": "hi", "session_id": session_id})
    r = client.delete(f"/api/chat/history?session_id={session_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1
    row = _last_audit_row(client, "clear_chat_history")
    assert row["actor"] == "user"
    assert row["lead_id"] is None


def test_advance_time_is_audited_even_without_neglect(client):
    # NEGLECT_AFTER_DAYS is 2 (backend/app/routers/misc.py). A fresh lead's
    # last_activity_at is "now", so backdating it by 1 day still leaves it
    # short of the 2-day threshold: run_neglect_check() flags nothing, and
    # its conditional `if neglected:` audit never fires. The unconditional
    # advance_time audit must still land — advance_time backdates EVERY
    # open lead's last_activity_at regardless of whether anyone crosses the
    # threshold, so that mutation needs its own trail.
    lead = _mk(client)
    r = client.post("/api/demo/advance-time", json={"days": 1})
    assert r.status_code == 200
    assert r.json()["neglected"] == []  # confirms the conditional audit path did NOT fire
    row = _last_audit_row(client, "advance_time")
    assert row["actor"] == "user"
    assert json.loads(row["input"]) == {"days": 1}


def test_agent_cannot_complete_reminder_or_advance_demo_time(client):
    lead = _mk(client)
    reminder = client.post("/api/reminders", json={
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Call",
    }).json()
    audit_before = client.get("/api/audit?limit=50").json()

    complete = client.patch(
        f"/api/reminders/{reminder['id']}", headers=AGENT
    )
    advance = client.post(
        "/api/demo/advance-time", json={"days": 1}, headers=AGENT
    )

    assert complete.status_code == 403
    assert advance.status_code == 403
    pending_reminders = client.get("/api/reminders").json()
    assert any(row["id"] == reminder["id"] for row in pending_reminders)
    assert client.get(f"/api/leads/{lead['id']}").json()["last_activity_at"] == lead[
        "last_activity_at"
    ]
    assert client.get("/api/audit?limit=50").json() == audit_before


def test_direct_note_booking_and_reminder_are_user_audited(client):
    lead = _mk(client)
    note = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Direct note"},
    )
    booking = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-08-11T10:00:00",
            "end_ts": "2026-08-11T10:45:00",
        },
    )
    reminder = client.post(
        "/api/reminders",
        json={"lead_id": lead["id"], "due_ts": "2026-08-12T09:00:00", "note": "Call"},
    )

    assert note.status_code == booking.status_code == reminder.status_code == 200
    assert _last_audit_row(client, "add_event")["actor"] == "user"
    assert _last_audit_row(client, "book_appointment")["actor"] == "user"
    assert _last_audit_row(client, "schedule_followup")["actor"] == "user"


def test_approved_note_booking_and_reminder_have_agent_and_user_audits(client):
    lead = _mk(client)
    proposals = [
        client.post(
            f"/api/leads/{lead['id']}/events",
            json={"type": "note", "content": "Agent note"},
            headers=AGENT,
        ),
        client.post(
            "/api/appointments",
            json={
                "lead_id": lead["id"],
                "start_ts": "2026-08-13T10:00:00",
                "end_ts": "2026-08-13T10:45:00",
            },
            headers=AGENT,
        ),
        client.post(
            "/api/reminders",
            json={"lead_id": lead["id"], "due_ts": "2026-08-14T09:00:00", "note": "Call"},
            headers=AGENT,
        ),
    ]
    assert [proposal.status_code for proposal in proposals] == [202, 202, 202]

    for proposal in proposals:
        approved = client.post(f"/api/pending-changes/{proposal.json()['id']}/approve")
        assert approved.status_code == 200

    assert _last_audit_row(client, "add_event")["actor"] == "agent"
    assert _last_audit_row(client, "book_appointment")["actor"] == "agent"
    assert _last_audit_row(client, "schedule_followup")["actor"] == "agent"
    approvals = [
        row for row in client.get("/api/audit?limit=50").json()
        if row["tool"] == "approve_pending_change"
    ]
    assert len(approvals) == 3
    assert {row["actor"] for row in approvals} == {"user"}
