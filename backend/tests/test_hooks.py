import json

from .conftest import make_lead


def _audit_tools(client):
    return [a["tool"] for a in client.get("/api/audit?limit=30").json()]


def _draft_body(client):
    for a in client.get("/api/audit?limit=30").json():
        if a["tool"] == "gmail_create_draft (simulated)":
            return json.loads(a["input"])["body"]
    raise AssertionError("no simulated gmail draft in audit log")


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


def test_intro_draft_unsigned_when_display_name_unset(client, monkeypatch):
    monkeypatch.delenv("AGENT_DISPLAY_NAME", raising=False)
    make_lead(client)
    body = _draft_body(client)
    assert "Best," not in body


def test_intro_draft_signed_when_display_name_set(client, monkeypatch):
    monkeypatch.setenv("AGENT_DISPLAY_NAME", "Alex Rivera")
    make_lead(client)
    body = _draft_body(client)
    assert "Best,\nAlex Rivera" in body


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


def test_reminder_hook_reports_integration_failure(client, monkeypatch):
    from app.integrations import hooks
    from app.integrations.composio_client import IntegrationError

    lead = make_lead(client)
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *_: (_ for _ in ()).throw(IntegrationError("network down")),
    )

    delivered = hooks.on_reminder_created(
        {
            "id": 91,
            "lead_id": lead["id"],
            "due_ts": "2026-08-20T09:00:00",
            "note": "Call",
        }
    )

    assert delivered is hooks.HookOutcome.FAILED


def test_lead_calendar_failure_does_not_create_gmail_draft(client, monkeypatch):
    from app.integrations import hooks
    from app.integrations.composio_client import IntegrationError

    lead = make_lead(client, email="buyer@example.com")
    calls = []

    def execute(slug, _args):
        calls.append(slug)
        raise IntegrationError("calendar unavailable")

    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(hooks.cc, "execute", execute)

    outcome = hooks.on_lead_created(lead, delivery_key="pending-change:41")

    assert outcome is hooks.HookOutcome.FAILED
    assert calls == ["GOOGLECALENDAR_CREATE_EVENT"]


def test_existing_appointment_event_id_skips_provider(client, monkeypatch):
    from app.integrations import hooks

    lead = make_lead(client)
    appointment = {
        "id": 91,
        "lead_id": lead["id"],
        "start_ts": "2026-08-20T10:00:00",
        "end_ts": "2026-08-20T10:45:00",
        "location": "Kirkland",
        "gcal_event_id": "already-created",
    }
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *_: (_ for _ in ()).throw(AssertionError("provider replayed")),
    )

    assert hooks.on_tour_booked(
        lead, appointment, delivery_key="pending-change:42"
    ) is hooks.HookOutcome.LIVE_DELIVERED


def test_existing_reminder_event_id_skips_provider(client, monkeypatch):
    from app.integrations import hooks

    lead = make_lead(client)
    reminder = {
        "id": 92,
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Call",
        "gcal_event_id": "already-created",
    }
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *_: (_ for _ in ()).throw(AssertionError("provider replayed")),
    )

    assert hooks.on_reminder_created(
        reminder, delivery_key="pending-change:43"
    ) is hooks.HookOutcome.LIVE_DELIVERED


def test_hook_blanket_guard_non_integration_error(client, monkeypatch):
    """Verify hooks never raise even when _create_event raises non-IntegrationError."""
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected error")

    monkeypatch.setattr("app.integrations.hooks._create_event", boom)
    lead = make_lead(client)                      # hook must swallow the error
    assert lead["id"] > 0
    # The blanket guard caught the RuntimeError and logged it
    tools = _audit_tools(client)
    assert any(t.endswith("(failed)") for t in tools)
