from .conftest import make_lead
from app.integrations import composio_client as cc


def test_status_off_by_default(client):
    res = client.get("/api/integrations/status").json()
    assert res == {
        "mode": "off",
        "configured": False,
        "last_operation": None,
        "detail": None,
    }


def test_send_simulated_runs_closed_loop(client):
    lead = make_lead(client)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "Homes in Bellevue", "body": "Hi!"})
    assert res.status_code == 200
    assert res.json() == {"sent": True, "simulated": True}

    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert profile["status"] == "contacted"
    types = [e["type"] for e in profile["events"]]
    assert "email" in types and "status_change" in types

    reminders = client.get("/api/reminders").json()
    assert any(r["lead_id"] == lead["id"] and
               r["note"].startswith("Check for a reply") for r in reminders)

    audit = client.get("/api/audit?limit=10").json()
    assert any(a["tool"] == "gmail_send (simulated)" for a in audit)


def test_send_no_email_400(client):
    lead = make_lead(client, email=None, name="No Email")
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.status_code == 400


def test_send_live_calls_gmail(client, monkeypatch):
    lead = make_lead(client)          # create BEFORE going live: the lead hook must not hit the network
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    sent = {}

    def fake_execute(slug, arguments):
        sent["slug"], sent["args"] = slug, arguments
        return {"response_data": {"id": "msg123"}}

    monkeypatch.setattr("app.integrations.router.cc.execute", fake_execute)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.json() == {"sent": True, "simulated": False}
    assert sent["slug"] == "GMAIL_SEND_EMAIL"
    assert sent["args"]["recipient_email"] == "lead@example.com"

    profile = client.get(f"/api/leads/{lead['id']}").json()
    email_ev = next(e for e in profile["events"] if e["type"] == "email")
    assert "[gmail:msg123]" in email_ev["content"]


def test_send_live_failure_502(client, monkeypatch):
    lead = make_lead(client)          # create BEFORE going live (see above)
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")

    def boom(slug, arguments):
        raise cc.IntegrationError("scope missing")

    monkeypatch.setattr("app.integrations.router.cc.execute", boom)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.status_code == 502
    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert profile["status"] == "new"          # closed loop did NOT run
    assert not any(e["type"] == "email" for e in profile["events"])
