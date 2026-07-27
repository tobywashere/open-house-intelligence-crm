from .conftest import make_lead


def _audit_tools(client):
    return [a["tool"] for a in client.get("/api/audit?limit=30").json()]


def test_booking_hook_simulated(client):
    lead = make_lead(client)
    res = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-07-28T10:00:00",
        "end_ts": "2026-07-28T10:45:00", "location": "123 Main St"})
    assert res.status_code == 200
    assert "gcal_create_event (simulated)" in _audit_tools(client)


def test_new_lead_hook_simulated_event_and_draft(client):
    make_lead(client)
    tools = _audit_tools(client)
    assert "gcal_create_event (simulated)" in tools     # call block
    assert "gmail_create_draft (simulated)" in tools    # intro draft


def test_new_lead_without_email_no_draft(client):
    make_lead(client, email=None, name="Phone Only")
    assert "gmail_create_draft (simulated)" not in _audit_tools(client)


def test_reminder_hook_simulated(client):
    lead = make_lead(client)
    client.post("/api/reminders", json={
        "lead_id": lead["id"], "due_ts": "2026-07-29T09:00:00", "note": "call back"})
    assert "gcal_create_event (simulated)" in _audit_tools(client)


def test_booking_hook_live_stores_event_id(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    monkeypatch.setattr("app.integrations.hooks.cc.execute",
                        lambda slug, args: {"response_data": {"id": "gcal-evt-9"}})
    lead = make_lead(client)
    appt = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-07-28T11:00:00",
        "end_ts": "2026-07-28T11:45:00", "location": None}).json()
    profile = client.get(f"/api/leads/{lead['id']}").json()
    stored = next(a for a in profile["appointments"] if a["id"] == appt["id"])
    assert stored["gcal_event_id"] == "gcal-evt-9"


def test_hook_failure_never_breaks_request(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")

    def boom(slug, args):
        from app.integrations.composio_client import IntegrationError
        raise IntegrationError("network down")

    monkeypatch.setattr("app.integrations.hooks.cc.execute", boom)
    lead = make_lead(client)                      # hook fails silently
    assert lead["id"] > 0
    assert any(t.endswith("(failed)") for t in _audit_tools(client))
