import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_concurrent_reply_logging_inserts_one_event_and_updates_activity(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/reminders", json={
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Check for a reply to concurrent test",
    })
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute(
            "UPDATE leads SET last_activity_at = '2000-01-01T00:00:00' WHERE id = ?",
            (lead["id"],),
        )
        conn.commit()

    async def no_process(_lead_id):
        return {}

    monkeypatch.setattr(leads_router, "process_lead", no_process)
    original_seen = poller._seen
    seen_barrier = threading.Barrier(2)

    def coordinated_seen(msg_id):
        result = original_seen(msg_id)
        seen_barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(poller, "_seen", coordinated_seen)
    start_barrier = threading.Barrier(2)

    def log_reply():
        start_barrier.wait(timeout=5)
        return poller._log_reply(
            lead,
            "concurrent-reply-1",
            "Sounds good, let's talk Tuesday",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(log_reply), executor.submit(log_reply)]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(results) == [0, 1]
    profile = client.get(f"/api/leads/{lead['id']}").json()
    reply_events = [
        event for event in profile["events"]
        if "[gmail:concurrent-reply-1]" in event["content"]
    ]
    assert len(reply_events) == 1
    assert profile["last_activity_at"] > "2000-01-01T00:00:00"
    assert not any(
        reminder["note"].startswith("Check for a reply")
        for reminder in client.get("/api/reminders").json()
    )
    audits = [
        row for row in client.get("/api/audit?limit=30").json()
        if row["tool"] == "gmail_reply_detected"
    ]
    assert len(audits) == 1


def test_reply_audit_failure_rolls_back_event_and_allows_retry(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/reminders", json={
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Check for a reply to rollback test",
    })
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute(
            "UPDATE leads SET last_activity_at = '2000-01-01T00:00:00' WHERE id = ?",
            (lead["id"],),
        )
        conn.commit()

    async def no_process(_lead_id):
        return {}

    monkeypatch.setattr(leads_router, "process_lead", no_process)
    real_audit = poller.audit
    fail_reply_audit = True

    def flaky_audit(conn, actor, tool, inputs, result, lead_id=None):
        nonlocal fail_reply_audit
        if tool == "gmail_reply_detected" and fail_reply_audit:
            fail_reply_audit = False
            raise RuntimeError("audit unavailable")
        return real_audit(conn, actor, tool, inputs, result, lead_id)

    monkeypatch.setattr(poller, "audit", flaky_audit)
    args = (lead, "retry-reply-1", "Sounds good, let's talk Tuesday")

    with pytest.raises(RuntimeError, match="audit unavailable"):
        poller._log_reply(*args)

    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["last_activity_at"] == "2000-01-01T00:00:00"
    assert not any("[gmail:retry-reply-1]" in event["content"] for event in unchanged["events"])
    assert any(
        reminder["note"].startswith("Check for a reply")
        for reminder in client.get("/api/reminders").json()
    )
    assert not any(
        row["tool"] == "gmail_reply_detected"
        for row in client.get("/api/audit?limit=30").json()
    )

    assert poller._log_reply(*args) == 1
    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert len([
        event for event in profile["events"]
        if "[gmail:retry-reply-1]" in event["content"]
    ]) == 1
    assert profile["last_activity_at"] > "2000-01-01T00:00:00"
    assert len([
        row for row in client.get("/api/audit?limit=30").json()
        if row["tool"] == "gmail_reply_detected"
    ]) == 1


def test_unknown_sender_queues_lead_for_review_without_mutating_crm(client, monkeypatch):
    hook_calls = []
    monkeypatch.setattr(
        leads_router.hooks, "on_lead_created", lambda lead: hook_calls.append(lead)
    )
    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "new-1",
        "sender": "Maria Lopez <maria@example.net>",
        "subject": "Looking for a home in Kirkland",
        "preview": {"body": "Hi! My budget is around $800k, hoping to move in 3 months."},
    }]))
    assert poller.check_inbox()["intake"] == 1

    assert client.get("/api/leads").json() == []
    assert hook_calls == []
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["operation"] == "create_lead"
    assert pending[0]["payload"]["email"] == "maria@example.net"
    assert "My budget is around $800k" in pending[0]["payload"]["raw_text"]
    assert "[gmail:new-1]" in pending[0]["payload"]["raw_text"]
    assert "_fallback_used" not in json.dumps(pending[0])

    audit = client.get("/api/audit?limit=30").json()
    tools = [entry["tool"] for entry in audit]
    assert "create_lead" not in tools
    assert "score_lead" not in tools
    assert "email_lead_intake" not in tools
    assert "email_intake_review_required" in tools

    # The pending payload retains the Gmail marker, so another poll pass must
    # not create a duplicate proposal while the first one awaits review.
    assert poller.check_inbox()["intake"] == 0
    assert len(client.get("/api/pending-changes").json()) == 1


def test_unknown_sender_fallback_is_reviewable_and_leaks_no_internal_marker(client, monkeypatch):
    class FallbackDriver:
        async def extract(self, raw_text):
            return {
                "name": "Maria Lopez",
                "budget": 800_000,
                "timeline": "3 months",
                "intent": "buy",
                "preferences": [],
                "missing_fields": ["phone"],
                "_fallback_used": "deterministic_parser",
            }

    hook_calls = []
    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackDriver())
    monkeypatch.setattr(
        leads_router.hooks, "on_lead_created", lambda lead: hook_calls.append(lead)
    )
    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "fallback-new-1",
        "sender": "Maria Lopez <maria@example.net>",
        "subject": "Looking for a home",
        "preview": {"body": "My budget is $800k and I hope to move in 3 months."},
    }]))

    assert poller.check_inbox() == {"replies": 0, "intake": 1}

    assert client.get("/api/leads").json() == []
    assert hook_calls == []
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert "backup parser" in pending[0]["summary"].lower()
    assert "My budget is $800k" in pending[0]["payload"]["raw_text"]
    assert "[gmail:fallback-new-1]" in pending[0]["payload"]["raw_text"]

    audit = client.get("/api/audit?limit=30").json()
    tools = [entry["tool"] for entry in audit]
    assert "create_lead" not in tools
    assert "score_lead" not in tools
    assert "email_lead_intake" not in tools
    assert "email_intake_review_required" in tools
    assert "_fallback_used" not in json.dumps({"pending": pending, "audit": audit})


def test_concurrent_unknown_sender_intake_queues_and_audits_once(client, monkeypatch):
    extraction_barrier = threading.Barrier(2)

    class CoordinatedDriver:
        async def extract(self, raw_text):
            extraction_barrier.wait(timeout=5)
            return {
                "name": "Maria Lopez",
                "budget": 800_000,
                "preferences": [],
                "missing_fields": ["phone"],
            }

    monkeypatch.setattr(leads_router, "get_driver", lambda: CoordinatedDriver())

    def intake():
        return poller._intake_lead(
            "maria@example.net",
            "Looking for a home",
            "My budget is $800k.",
            "concurrent-new-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=10) for future in [
            executor.submit(intake),
            executor.submit(intake),
        ]]

    assert sorted(results) == [0, 1]
    assert client.get("/api/leads").json() == []
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert "[gmail:concurrent-new-1]" in pending[0]["payload"]["raw_text"]
    review_audits = [
        row for row in client.get("/api/audit?limit=30").json()
        if row["tool"] == "email_intake_review_required"
    ]
    assert len(review_audits) == 1


def test_intake_audit_failure_rolls_back_proposal_and_allows_retry(client, monkeypatch):
    real_audit = poller.audit
    fail_review_audit = True

    def flaky_audit(conn, actor, tool, inputs, result, lead_id=None):
        nonlocal fail_review_audit
        if tool == "email_intake_review_required" and fail_review_audit:
            fail_review_audit = False
            raise RuntimeError("audit unavailable")
        return real_audit(conn, actor, tool, inputs, result, lead_id)

    monkeypatch.setattr(poller, "audit", flaky_audit)
    args = (
        "maria@example.net",
        "Looking for a home",
        "My budget is $800k.",
        "retry-new-1",
    )

    assert poller._intake_lead(*args) == 0
    assert client.get("/api/leads").json() == []
    assert client.get("/api/pending-changes").json() == []
    review_audits = [
        row for row in client.get("/api/audit?limit=30").json()
        if row["tool"] == "email_intake_review_required"
    ]
    assert review_audits == []

    assert poller._intake_lead(*args) == 1
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert "[gmail:retry-new-1]" in pending[0]["payload"]["raw_text"]
    review_audits = [
        row for row in client.get("/api/audit?limit=30").json()
        if row["tool"] == "email_intake_review_required"
    ]
    assert len(review_audits) == 1


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


def test_process_deterministic_score_explanation_does_not_write_score_or_reason(client, monkeypatch):
    lead = make_lead(client)

    class FallbackScoreDriver:
        async def extract(self, raw_text):
            raise AssertionError("complete lead should not need extraction")

        async def explain_score(self, lead, score):
            return "[deterministic fallback] Scored from stored CRM fields."

        async def draft_followup(self, lead):
            return "Hi Test, can we talk Tuesday?"

    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackScoreDriver())

    response = client.post(f"/api/leads/{lead['id']}/process")

    assert response.status_code == 409
    assert "backup response" in response.json()["detail"].lower()
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["score"] is None
    assert current["score_reason"] is None
    audit = client.get("/api/audit?limit=30").json()
    assert "score_lead" not in [entry["tool"] for entry in audit]
