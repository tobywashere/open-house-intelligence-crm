import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

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

    async def no_process(_lead_id, source_event_id=None):
        return {}

    monkeypatch.setattr(leads_router, "process_lead", no_process)
    real_get_conn = poller.get_conn
    transaction_entry_barrier = threading.Barrier(2)

    @contextmanager
    def coordinated_get_conn():
        # Both workers must reach the transaction boundary before either can
        # proceed. In the old check-then-insert code this barrier ran once for
        # both seen-check transactions and again for both insert transactions,
        # deterministically allowing duplicate events. The fixed path reaches
        # it once, then BEGIN IMMEDIATE serializes the in-transaction recheck.
        transaction_entry_barrier.wait(timeout=5)
        with real_get_conn() as conn:
            yield conn

    monkeypatch.setattr(poller, "get_conn", coordinated_get_conn)

    def log_reply():
        return poller._log_reply(
            lead,
            "concurrent-reply-1",
            "Sounds good, let's talk Tuesday",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(log_reply), executor.submit(log_reply)]
        results = [future.result(timeout=10) for future in futures]

    event_ids = {result[0] for result in results}
    assert len(event_ids) == 1
    assert sorted(result[1] for result in results) == [False, True]
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

    async def no_process(_lead_id, source_event_id=None):
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

    event_id, inserted = poller._log_reply(*args)
    assert event_id > 0
    assert inserted is True
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


class _ReviewableExtractionDriver:
    async def extract(self, raw_text):
        return {
            "phone": "+14255550199",
            "budget": 925_000,
            "area": "Kirkland",
            "timeline": "2 months",
            "intent": "buy",
        }

    async def explain_score(self, lead, score):
        return f"Derived score {score} from the proposed details."

    async def draft_followup(self, lead):
        return "Hi Test, would you like to discuss Kirkland homes this week?"


def test_reply_processing_uses_exact_inserted_or_existing_event_id(client, monkeypatch):
    lead = make_lead(client)
    processed = []

    async def capture_process(lead_id, source_event_id=None):
        processed.append((lead_id, source_event_id))
        return {}

    monkeypatch.setattr(leads_router, "process_lead", capture_process)

    first_event_id, first_inserted = poller._log_reply(
        lead, "exact-reply-1", "First processing attempt"
    )
    retry_event_id, retry_inserted = poller._log_reply(
        lead, "exact-reply-1", "A changed preview must not change the source"
    )

    assert first_event_id > 0
    assert retry_event_id == first_event_id
    assert first_inserted is True
    assert retry_inserted is False
    assert processed == [
        (lead["id"], first_event_id),
        (lead["id"], first_event_id),
    ]


def test_process_proposes_changes_to_every_populated_business_field(client, monkeypatch):
    lead = make_lead(
        client,
        phone="+1 (425) 555-0100",
        email="old@example.com",
        budget=800_000,
        area="Seattle",
        timeline="6 months",
        intent="browse",
    )
    event = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "email", "content": "My plans and contact details changed."},
    ).json()

    class ChangedFieldsDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            return {
                "phone": "+1 425 555 0199",
                "email": " NEW@EXAMPLE.COM ",
                "budget": "$925,000",
                "area": " Kirkland ",
                "timeline": " 2 months ",
                "intent": " BUY ",
            }

    monkeypatch.setattr(leads_router, "get_driver", lambda: ChangedFieldsDriver())

    response = client.post(
        f"/api/leads/{lead['id']}/process?source_event_id={event['id']}"
    )

    assert response.status_code == 200, response.text
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["phone"] == "+1 (425) 555-0100"
    assert current["email"] == "old@example.com"
    assert current["budget"] == 800_000
    assert current["area"] == "Seattle"
    assert current["timeline"] == "6 months"
    assert current["intent"] == "browse"
    assert current["score"] is None
    assert current["score_reason"] is None

    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["payload"] == {
        "phone": "+14255550199",
        "email": "new@example.com",
        "budget": 925_000,
        "area": "Kirkland",
        "timeline": "2 months",
        "intent": "buy",
        "score": response.json()["lead"]["score"],
        "score_reason": response.json()["lead"]["score_reason"],
    }

    approved = client.post(
        f"/api/pending-changes/{pending[0]['id']}/approve",
        json={"fields": {"email": "reviewed@example.com", "budget": 950_000}},
    )
    assert approved.status_code == 200, approved.text
    applied = approved.json()
    assert applied["phone"] == "+14255550199"
    assert applied["email"] == "reviewed@example.com"
    assert applied["budget"] == 950_000
    assert applied["area"] == "Kirkland"
    assert applied["timeline"] == "2 months"
    assert applied["intent"] == "buy"
    assert applied["score"] == response.json()["lead"]["score"]
    assert applied["score_reason"] == response.json()["lead"]["score_reason"]


def test_process_omits_normalized_unchanged_and_blank_extraction(client, monkeypatch):
    lead = make_lead(
        client,
        phone="+1 (425) 555-0100",
        email="lead@example.com",
        budget=900_000,
        area="Bellevue",
        timeline="6 weeks",
        intent="buy",
    )
    event = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Only the preferred area changed."},
    ).json()

    class SparseChangedFieldsDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            return {
                "phone": " +1 425-555-0100 ",
                "email": " LEAD@EXAMPLE.COM ",
                "budget": "900,000",
                "area": " Redmond ",
                "timeline": "   ",
                "intent": " ",
            }

    monkeypatch.setattr(
        leads_router, "get_driver", lambda: SparseChangedFieldsDriver()
    )

    response = client.post(
        f"/api/leads/{lead['id']}/process?source_event_id={event['id']}"
    )

    assert response.status_code == 200, response.text
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["payload"] == {
        "area": "Redmond",
        "score": response.json()["lead"]["score"],
        "score_reason": response.json()["lead"]["score_reason"],
    }
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["area"] == "Bellevue"
    assert current["timeline"] == "6 weeks"
    assert current["intent"] == "buy"


def test_process_queues_extracted_fields_and_derived_score_until_approval(client, monkeypatch):
    lead = make_lead(
        client,
        phone=None,
        budget=None,
        area=None,
        timeline=None,
        intent="unknown",
    )
    client.post(
        f"/api/leads/{lead['id']}/events",
        json={
            "type": "note",
            "content": "Budget is $925k, Kirkland, moving in two months.",
        },
    )
    monkeypatch.setattr(
        leads_router, "get_driver", lambda: _ReviewableExtractionDriver()
    )

    response = client.post(f"/api/leads/{lead['id']}/process")

    assert response.status_code == 200, response.text
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["phone"] is None
    assert current["budget"] is None
    assert current["area"] is None
    assert current["timeline"] is None
    assert current["intent"] == "unknown"
    assert current["score"] is None
    assert current["score_reason"] is None
    assert response.json()["lead"]["score"] > 0
    assert response.json()["lead"]["score_reason"].startswith("Derived score")
    assert response.json()["followup_draft"].startswith("Hi Test")

    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["operation"] == "update_lead"
    assert pending[0]["lead_id"] == lead["id"]
    assert "dedupe_key" not in pending[0]
    assert pending[0]["payload"] == {
        "phone": "+14255550199",
        "budget": 925_000,
        "area": "Kirkland",
        "timeline": "2 months",
        "intent": "buy",
        "score": response.json()["lead"]["score"],
        "score_reason": response.json()["lead"]["score_reason"],
    }

    approved = client.post(
        f"/api/pending-changes/{pending[0]['id']}/approve",
        json={"fields": {"budget": 950_000, "area": "Redmond"}},
    )
    assert approved.status_code == 200, approved.text
    applied = approved.json()
    assert applied["phone"] == "+14255550199"
    assert applied["budget"] == 950_000
    assert applied["area"] == "Redmond"
    assert applied["timeline"] == "2 months"
    assert applied["intent"] == "buy"
    assert applied["score"] == response.json()["lead"]["score"]
    assert applied["score_reason"] == response.json()["lead"]["score_reason"]


def test_process_uses_highest_event_id_when_timestamps_tie(client, monkeypatch):
    lead = make_lead(client, budget=None, area=None, timeline=None)
    older = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Older qualification details."},
    ).json()
    newer = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Newer qualification details."},
    ).json()
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute(
            "UPDATE events SET created_at = '2026-08-06T12:00:00' "
            "WHERE id IN (?, ?)",
            (older["id"], newer["id"]),
        )
        conn.commit()

    seen = []

    class EventSelectingDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            seen.append(raw_text)
            return {
                "budget": 925_000,
                "timeline": "2 months",
                "area": "Kirkland" if raw_text.startswith("Newer") else "Old area",
            }

    monkeypatch.setattr(leads_router, "get_driver", lambda: EventSelectingDriver())

    response = client.post(f"/api/leads/{lead['id']}/process")

    assert response.status_code == 200, response.text
    assert seen == ["Newer qualification details."]
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["payload"]["area"] == "Kirkland"


def test_process_rejects_source_event_owned_by_another_lead(client):
    lead = make_lead(client, email="one@example.com")
    other = make_lead(client, email="two@example.com")
    foreign_event = client.post(
        f"/api/leads/{other['id']}/events",
        json={"type": "note", "content": "This belongs to the other lead."},
    ).json()

    response = client.post(
        f"/api/leads/{lead['id']}/process?source_event_id={foreign_event['id']}"
    )

    assert response.status_code == 404
    assert "source event" in response.json()["detail"].lower()
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["score"] is None
    assert client.get("/api/pending-changes").json() == []


def test_known_lead_reply_queues_fields_and_score_once(client, monkeypatch):
    lead = make_lead(
        client,
        phone=None,
        budget=None,
        area=None,
        timeline=None,
        intent="unknown",
    )
    monkeypatch.setattr(
        leads_router, "get_driver", lambda: _ReviewableExtractionDriver()
    )
    fetch = _fake_fetch(
        [{
            "messageId": "review-reply-1",
            "sender": "Test Lead <lead@example.com>",
            "preview": {
                "body": "I can spend $925k in Kirkland and move in two months."
            },
        }]
    )
    monkeypatch.setattr("app.integrations.poller.cc.execute", fetch)

    assert poller.check_inbox() == {"replies": 1, "intake": 0}
    assert poller.check_inbox() == {"replies": 0, "intake": 0}

    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["phone"] is None
    assert current["budget"] is None
    assert current["area"] is None
    assert current["timeline"] is None
    assert current["intent"] == "unknown"
    assert current["score"] is None
    assert current["score_reason"] is None
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["operation"] == "update_lead"
    assert pending[0]["payload"]["score"] > 0
    assert pending[0]["payload"]["score_reason"].startswith("Derived score")
    tools = [row["tool"] for row in client.get("/api/audit?limit=50").json()]
    assert "score_lead" in tools
    assert "draft_followup" in tools


def test_concurrent_processing_deduplicates_same_source_proposal(client, monkeypatch):
    lead = make_lead(
        client,
        phone=None,
        budget=None,
        area=None,
        timeline=None,
        intent="unknown",
    )
    event = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Kirkland, $925k, moving in two months."},
    ).json()
    extraction_barrier = threading.Barrier(2)

    class CoordinatedDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            extraction_barrier.wait(timeout=5)
            return await super().extract(raw_text)

    monkeypatch.setattr(leads_router, "get_driver", lambda: CoordinatedDriver())

    def process():
        return asyncio.run(
            leads_router.process_lead(lead["id"], source_event_id=event["id"])
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in (executor.submit(process), executor.submit(process))
        ]

    assert len(results) == 2
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["operation"] == "update_lead"
    current = client.get(f"/api/leads/{lead['id']}").json()
    assert current["budget"] is None
    assert current["timeline"] is None
    assert current["score"] is None
    assert pending[0]["payload"]["score"] > 0


def test_concurrent_distinct_source_events_create_distinct_proposals(client, monkeypatch):
    lead = make_lead(client, area="Bellevue")
    first = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "email", "content": "First reply prefers Redmond."},
    ).json()
    second = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "email", "content": "Second reply prefers Kirkland."},
    ).json()
    extraction_barrier = threading.Barrier(2)

    class EventSpecificDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            extraction_barrier.wait(timeout=5)
            return {
                "area": "Redmond" if raw_text.startswith("First") else "Kirkland"
            }

    monkeypatch.setattr(leads_router, "get_driver", lambda: EventSpecificDriver())

    def process(event_id):
        return asyncio.run(
            leads_router.process_lead(lead["id"], source_event_id=event_id)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in (
                executor.submit(process, first["id"]),
                executor.submit(process, second["id"]),
            )
        ]

    assert len(results) == 2
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 2
    assert {row["payload"]["area"] for row in pending} == {"Redmond", "Kirkland"}
    assert all(row["operation"] == "update_lead" for row in pending)


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
