import json
import time

from .conftest import TEST_DB, make_lead
from app.integrations import poller
from app.routers import leads as leads_router


def _fake_fetch(messages):
    def fake_execute(slug, arguments):
        assert slug == "GMAIL_FETCH_EMAILS"
        assert arguments["query"] == "in:inbox newer_than:2d"
        return {"response_data": {"messages": messages}}
    return fake_execute


def test_reply_logged_reminder_done_and_reprocessed(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})  # creates reply-check reminder

    # backdate last_activity_at so a later bump from the reply is unambiguous
    import sqlite3
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute(
            "UPDATE leads SET last_activity_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (lead["id"],))
        conn.commit()
    before = client.get(f"/api/leads/{lead['id']}").json()["last_activity_at"]

    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "reply-1",
        "sender": "Test Lead <lead@example.com>",
        "preview": {"body": "Sounds great, let's talk Tuesday"},
    }]))
    assert poller.check_inbox() == {"replies": 1, "intake": 0}

    profile = client.get(f"/api/leads/{lead['id']}").json()
    reply_evs = [e for e in profile["events"] if "[gmail:reply-1]" in e["content"]]
    assert len(reply_evs) == 1 and reply_evs[0]["type"] == "email"
    reminders = client.get("/api/reminders").json()   # only done=0 returned
    assert not any(r["note"].startswith("Check for a reply") for r in reminders)
    # reply triggered re-processing (extract → score) — wiring, not extraction quality
    tools = [a["tool"] for a in client.get("/api/audit?limit=30").json()]
    assert "score_lead" in tools
    # reply detection must bump last_activity_at so the lead stops looking neglected
    assert profile["last_activity_at"] > before


def test_reply_dedupe_second_pass_noop(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    fake = _fake_fetch([{"messageId": "reply-2",
                         "sender": "lead@example.com",
                         "preview": {"body": "hi"}}])
    monkeypatch.setattr("app.integrations.poller.cc.execute", fake)
    assert poller.check_inbox()["replies"] == 1
    assert poller.check_inbox()["replies"] == 0   # marker dedupe


def test_unknown_sender_becomes_lead(client, monkeypatch):
    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "new-1",
        "sender": "Maria Lopez <maria@example.net>",
        "subject": "Looking for a home in Kirkland",
        "preview": {"body": "Hi! My budget is around $800k, hoping to move in 3 months."},
    }]))
    assert poller.check_inbox()["intake"] == 1
    leads = client.get("/api/leads").json()
    maria = next(l for l in leads if l["email"] == "maria@example.net")
    assert maria["source"] == "email"
    # second pass: same message id is not intaken twice
    assert poller.check_inbox()["intake"] == 0
    assert len([l for l in client.get("/api/leads").json()
                if l["email"] == "maria@example.net"]) == 1


def test_noise_senders_ignored(client, monkeypatch):
    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([
        {"messageId": "n1", "sender": "no-reply@zillow.com",
         "preview": {"body": "Your weekly listings digest"}},
        {"messageId": "n2", "sender": "newsletter@redfin.com",
         "preview": {"body": "Market trends this week"}},
    ]))
    assert poller.check_inbox() == {"replies": 0, "intake": 0}


def test_process_fallback_returns_retryable_result_without_mutating_lead(client, monkeypatch):
    lead = make_lead(client, budget=None, timeline=None, intent="unknown")
    client.post(f"/api/leads/{lead['id']}/events", json={
        "type": "note", "content": "Budget is $900k and timeline is 6 weeks.",
    })

    class FallbackDriver:
        async def extract(self, raw_text):
            return {
                "budget": 900000,
                "timeline": "6 weeks",
                "intent": "buy",
                "_fallback_used": "deterministic_parser",
            }

        async def explain_score(self, lead, score):
            raise AssertionError("fallback extraction must stop processing before score")

        async def draft_followup(self, lead):
            raise AssertionError("fallback extraction must stop processing before drafting")

    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackDriver())

    response = client.post(f"/api/leads/{lead['id']}/process")

    assert response.status_code == 409
    assert "backup parser" in response.json()["detail"].lower()
    assert "_fallback_used" not in response.text
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["budget"] is None
    assert current["timeline"] is None
    assert current["intent"] == "unknown"
    assert current["score"] is None
    audit = client.get("/api/audit?limit=30").json()
    assert "score_lead" not in [entry["tool"] for entry in audit]
    assert "_fallback_used" not in json.dumps(audit)


def test_reply_poller_does_not_auto_apply_deterministic_fallback_fields(client, monkeypatch):
    lead = make_lead(client, budget=None, timeline=None, intent="unknown")

    class FallbackDriver:
        async def extract(self, raw_text):
            return {
                "budget": 900000,
                "timeline": "6 weeks",
                "intent": "buy",
                "_fallback_used": "deterministic_parser",
            }

        async def explain_score(self, lead, score):
            raise AssertionError("fallback extraction must stop processing before score")

        async def draft_followup(self, lead):
            raise AssertionError("fallback extraction must stop processing before drafting")

    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackDriver())
    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "fallback-reply-1",
        "sender": "Test Lead <lead@example.com>",
        "preview": {"body": "I can spend $900k and move in 6 weeks."},
    }]))

    assert poller.check_inbox() == {"replies": 1, "intake": 0}

    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["budget"] is None
    assert current["timeline"] is None
    assert current["intent"] == "unknown"
    assert current["score"] is None
    audit = client.get("/api/audit?limit=30").json()
    assert "score_lead" not in [entry["tool"] for entry in audit]
    assert "_fallback_used" not in json.dumps(audit)
    assert "agent_processing_deferred" in [entry["tool"] for entry in audit]


def test_process_deterministic_draft_does_not_write_score_or_reason(client, monkeypatch):
    lead = make_lead(client)

    class FallbackDraftDriver:
        async def extract(self, raw_text):
            raise AssertionError("complete lead should not need extraction")

        async def explain_score(self, lead, score):
            return "The lead has a confirmed budget and timeline."

        async def draft_followup(self, lead):
            return "[deterministic fallback] Hi Test, can we talk Tuesday?"

    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackDraftDriver())

    response = client.post(f"/api/leads/{lead['id']}/process")

    assert response.status_code == 409
    assert "backup response" in response.json()["detail"].lower()
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["score"] is None
    assert current["score_reason"] is None
    audit = client.get("/api/audit?limit=30").json()
    assert "score_lead" not in [entry["tool"] for entry in audit]
