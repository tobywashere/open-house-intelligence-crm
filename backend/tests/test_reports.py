"""Generated report contracts.

These routes are an agent-to-UI trust boundary: generated payloads must be
structurally valid and may only refer to CRM records that actually exist.
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app
from app.routers import reports
from tests.conftest import make_lead


def test_briefing_post_rejects_unknown_lead_reference(client):
    response = client.post(
        "/api/briefing",
        json={
            "date": "2026-07-28",
            "meeting_briefs": [
                {
                    "lead_id": 999,
                    "prepare": ["Review history"],
                    "recommendation": "Ask about timing",
                }
            ],
        },
    )

    assert response.status_code == 422


def test_summary_post_requires_a_real_source_url(client):
    response = client.post(
        "/api/summary",
        json={
            "date": "2026-07-28",
            "greeting": "Good morning",
            "market_watch": [
                {
                    "title": "Rates changed",
                    "source": "Unknown",
                    "url": "not-a-url",
                    "takeaway": "Check the source",
                }
            ],
            "ai_insights": [],
        },
    )

    assert response.status_code == 422


def test_summary_rejects_market_item_without_supported_source_fields(client):
    payload = {
        "date": "2026-08-05",
        "generated_at": "2026-08-05T07:00:00",
        "greeting": "Daily brief",
        "market_watch": [
            {
                "title": "Seattle employment",
                "source": "U.S. Bureau of Labor Statistics",
                "url": "https://www.bls.gov/eag/eag.wa_seattle_msa.htm",
                "takeaway": "Review the published figures.",
                "date": "2026-08-05",
                "summary": "Source-backed summary.",
                "geo": "Seattle",
            }
        ],
        "ai_insights": [],
    }
    del payload["market_watch"][0]["url"]

    response = client.post("/api/summary", json=payload)

    assert response.status_code == 422


def test_summary_rejects_market_item_from_an_unconfigured_source(client):
    response = client.post(
        "/api/summary",
        json={
            "date": "2026-08-05",
            "generated_at": "2026-08-05T07:00:00",
            "greeting": "Daily brief",
            "market_watch": [
                {
                    "title": "Unsupported source",
                    "source": "Example",
                    "url": "https://example.com/unsupported",
                    "takeaway": "This must not be shown as a daily brief fact.",
                    "date": "2026-08-05",
                    "summary": "This URL is not one of the configured sources.",
                    "geo": "Seattle",
                }
            ],
            "ai_insights": [],
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["source", "title", "takeaway", "summary", "geo"])
def test_summary_rejects_blank_required_market_source_fields(client, field):
    item = {
        "title": "Seattle employment",
        "source": "U.S. Bureau of Labor Statistics",
        "url": "https://www.bls.gov/eag/eag.wa_seattle_msa.htm",
        "takeaway": "Review the published figures.",
        "date": "2026-08-05",
        "summary": "Source-backed summary.",
        "geo": "Seattle",
    }
    item[field] = "   "

    response = client.post(
        "/api/summary",
        json={
            "date": "2026-08-05",
            "generated_at": "2026-08-05T07:00:00",
            "greeting": "Daily brief",
            "market_watch": [item],
            "ai_insights": [],
        },
    )

    assert response.status_code == 422


def test_summary_storage_failure_returns_503_without_replacing_prior_report(
    client, monkeypatch
):
    payload = {
        "date": "2026-08-05",
        "generated_at": "2026-08-05T07:00:00",
        "greeting": "Daily brief",
        "market_watch": [
            {
                "title": "Seattle employment",
                "source": "U.S. Bureau of Labor Statistics",
                "url": "https://www.bls.gov/eag/eag.wa_seattle_msa.htm",
                "takeaway": "Review the published figures.",
                "date": "2026-08-05",
                "summary": "Source-backed summary.",
                "geo": "Seattle",
            }
        ],
        "ai_insights": [],
    }
    assert client.post("/api/summary", json=payload).status_code == 200
    prior = client.get("/api/summary?date=2026-08-05").json()
    with get_conn() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE tool = 'post_daily_summary'"
        ).fetchone()[0]

    def unavailable_audit(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(reports, "audit", unavailable_audit)
    changed = {**payload, "greeting": "This must not replace the saved report"}
    with TestClient(app, raise_server_exceptions=False) as nonthrowing_client:
        response = nonthrowing_client.post("/api/summary", json=changed)

    assert response.status_code == 503
    assert "could not save" in response.json()["detail"].lower()
    assert client.get("/api/summary?date=2026-08-05").json() == prior
    with get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE tool = 'post_daily_summary'"
        ).fetchone()[0] == audit_count


def test_briefing_never_promotes_a_lead_to_a_fake_meeting(client):
    lead = make_lead(client, name="No Appointment")

    response = client.get("/api/briefing?date=2026-07-28")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "crm"
    assert body["schedule"] == []
    assert body["meeting_briefs"] == []
    assert all(action["lead_id"] != lead["id"] for action in body["suggested_actions"])


def test_empty_briefing_contains_no_sample_people_or_appointments(client):
    body = client.get("/api/briefing?date=2026-08-05").json()

    assert body["schedule"] == []
    assert body["meeting_briefs"] == []
    serialized = json.dumps(body).lower()
    assert "sample" not in serialized
    assert "sarah chen" not in serialized


def test_briefing_rehydrates_facts_from_real_appointment(client):
    lead = make_lead(
        client,
        name="Canonical Name",
        area="Bellevue",
        budget=900_000,
        timeline="6 weeks",
        intent="buy",
    )
    appointment = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-07-28T17:00:00",
            "end_ts": "2026-07-28T17:45:00",
            "location": "Main Street",
        },
    ).json()

    body = client.get("/api/briefing?date=2026-07-28").json()

    assert body["schedule"] == [
        {
            "appointment_id": appointment["id"],
            "start": "17:00",
            "end": "17:45",
            "kind": "meeting",
            "title": "Meeting — Canonical Name",
            "lead_id": lead["id"],
        }
    ]
    assert body["meeting_briefs"][0]["appointment_id"] == appointment["id"]
    assert body["meeting_briefs"][0]["name"] == "Canonical Name"
    assert body["meeting_briefs"][0]["area"] == "Bellevue"
    assert body["meeting_briefs"][0]["budget"] == 900_000


def test_briefing_preserves_a_persisted_whitespace_only_lead_name(client):
    lead = make_lead(client, name="   ")
    appointment = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-07-28T17:00:00",
            "end_ts": "2026-07-28T17:45:00",
            "location": "Main Street",
        },
    ).json()

    response = client.get("/api/briefing?date=2026-07-28")

    assert response.status_code == 200
    body = response.json()
    assert body["schedule"][0] == {
        "appointment_id": appointment["id"],
        "start": "17:00",
        "end": "17:45",
        "kind": "meeting",
        "title": "Meeting —    ",
        "lead_id": lead["id"],
    }
    assert body["meeting_briefs"][0]["name"] == "   "


def test_briefing_preserves_a_persisted_whitespace_only_reminder_note(client):
    lead = make_lead(client, name="Reminder Lead")
    reminder = client.post(
        "/api/reminders",
        json={
            "lead_id": lead["id"],
            "due_ts": "2026-07-28T09:00:00",
            "note": "   ",
        },
    )
    assert reminder.status_code == 200

    response = client.get("/api/briefing?date=2026-07-28")

    assert response.status_code == 200
    action = response.json()["suggested_actions"][0]
    assert action["name"] == "Reminder Lead"
    assert action["reason"] == "   "
    assert action["evidence"] == {
        "kind": "reminder",
        "id": reminder.json()["id"],
    }


def test_briefing_post_cannot_override_canonical_crm_facts(client):
    lead = make_lead(client, name="Real Name", area="Redmond", budget=750_000)
    appointment = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-07-28T18:00:00",
            "end_ts": "2026-07-28T18:45:00",
            "location": "Real Location",
        },
    ).json()
    posted = client.post(
        "/api/briefing",
        json={
            "date": "2026-07-28",
            "greeting": "Three invented meetings",
            "schedule": [
                {
                    "start": "09:00",
                    "end": "10:00",
                    "kind": "meeting",
                    "title": "Invented meeting",
                    "lead_id": lead["id"],
                }
            ],
            "meeting_briefs": [
                {
                    "lead_id": lead["id"],
                    "name": "Invented Name",
                    "area": "Invented Area",
                    "score": 100,
                    "summary": "Invented facts",
                    "prepare": ["Bring the verified CRM notes"],
                    "recommendation": "Ask an open question.",
                }
            ],
        },
    )
    assert posted.status_code == 200

    body = client.get("/api/briefing?date=2026-07-28").json()

    assert body["schedule"][0]["appointment_id"] == appointment["id"]
    assert body["schedule"][0]["start"] == "18:00"
    assert body["meeting_briefs"][0]["name"] == "Real Name"
    assert body["meeting_briefs"][0]["area"] == "Redmond"
    assert body["meeting_briefs"][0]["budget"] == 750_000
    assert body["meeting_briefs"][0]["assistant_advice"] == {
        "prepare": ["Bring the verified CRM notes"],
        "recommendation": "Ask an open question.",
    }


def test_summary_get_rejects_legacy_payload_with_invalid_source(client):
    payload = {
        "date": "2026-07-28",
        "greeting": "Good morning",
        "market_watch": [
            {
                "title": "Unsupported market claim",
                "source": "Unknown",
                "url": "not-a-url",
                "takeaway": "This must not be displayed.",
            }
        ],
        "ai_insights": [],
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_summary (date, payload) VALUES (?, ?)",
            ("2026-07-28", json.dumps(payload)),
        )

    response = client.get("/api/summary?date=2026-07-28")

    assert response.status_code == 422
    assert "invalid" in response.json()["detail"].lower()


def test_summary_get_rejects_malformed_stored_json(client):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_summary (date, payload) VALUES (?, ?)",
            ("2026-07-28", "{not-json"),
        )

    try:
        response = client.get("/api/summary?date=2026-07-28")
    except json.JSONDecodeError:
        pytest.fail("malformed stored summary escaped as an unhandled JSON error")

    assert response.status_code == 422
    assert "invalid" in response.json()["detail"].lower()
