"""Every write through the REST layer must leave an audit_log row (Task 11
fix round 1 — docs/CONTRACT.md §3 previously claimed this while three write
endpoints silently skipped it). Pins the fix so it can't regress."""


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
