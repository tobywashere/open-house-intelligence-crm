from .conftest import make_lead
from app.integrations import poller


def _fake_fetch(messages):
    def fake_execute(slug, arguments):
        assert slug == "GMAIL_FETCH_EMAILS"
        return {"response_data": {"messages": messages}}
    return fake_execute


def test_reply_logged_and_reminder_done(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})  # creates reply-check reminder

    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "reply-1",
        "sender": "Test Lead <lead@example.com>",
        "preview": {"body": "Sounds great, let's talk Tuesday"},
    }]))
    assert poller.check_replies() == 1

    profile = client.get(f"/api/leads/{lead['id']}").json()
    reply_evs = [e for e in profile["events"] if "[gmail:reply-1]" in e["content"]]
    assert len(reply_evs) == 1 and reply_evs[0]["type"] == "email"
    reminders = client.get("/api/reminders").json()   # only done=0 returned
    assert not any(r["note"].startswith("Check for a reply") for r in reminders)


def test_reply_dedupe_second_pass_noop(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    fake = _fake_fetch([{"messageId": "reply-2",
                         "sender": "lead@example.com",
                         "preview": {"body": "hi"}}])
    monkeypatch.setattr("app.integrations.poller.cc.execute", fake)
    assert poller.check_replies() == 1
    assert poller.check_replies() == 0   # marker dedupe


def test_no_active_leads_no_call(client, monkeypatch):
    def explode(slug, arguments):
        raise AssertionError("should not be called")
    monkeypatch.setattr("app.integrations.poller.cc.execute", explode)
    assert poller.check_replies() == 0   # fresh DB: no contacted leads with email
