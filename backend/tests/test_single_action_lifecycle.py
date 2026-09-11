"""Lifecycle evidence for the one-completion CRM experiment.

Only the model-selection completion is scripted here.  Tool calls run through
the installed CRM skill dispatcher, with its urllib HTTP boundary bridged to
FastAPI's TestClient, so approval and SQLite behavior stays production-real.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.crm_chat import CRM_REQUEST_TOOL
from app.agent.single_action import run_single_action
from app.main import app


def _proposal(call_id: str, operation: str, arguments: dict) -> dict:
    return {
        "choices": [{"message": {"content": "untrusted model prose", "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": CRM_REQUEST_TOOL,
                "arguments": json.dumps({"operation": operation, "arguments": arguments}),
            },
        }]}}],
    }


@pytest.fixture()
def lifecycle_client_factory(tmp_path, monkeypatch):
    """Build clients against one isolated database, including after a restart."""
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "single-action-lifecycle.db")
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    return lambda: TestClient(app)


@pytest.fixture()
def lifecycle_client(lifecycle_client_factory):
    with lifecycle_client_factory() as client:
        yield client


@pytest.fixture()
def skill_dispatch(monkeypatch):
    """Run the real CRM dispatcher while replacing only its HTTP transport."""
    skill_dir = Path(__file__).resolve().parents[2] / "skills" / "crm-db-operations"
    monkeypatch.syspath_prepend(str(skill_dir))
    import cli as skill_cli
    import tools as skill_tools

    monkeypatch.setattr(skill_tools, "BASE_URL", "http://crm.test/api")
    monkeypatch.setattr(skill_tools, "API_TOKEN", "")

    def build(client):
        def open_request(request, *, timeout):
            parsed = urllib.parse.urlsplit(request.full_url)
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            response = client.request(
                request.get_method(), path, content=request.data,
                headers=dict(request.header_items()),
            )
            if response.status_code >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, response.status_code, "test CRM response",
                    response.headers, io.BytesIO(response.content),
                )
            return io.BytesIO(response.content)

        monkeypatch.setattr(skill_tools, "_open_request", open_request)

        def dispatch(operation: str, arguments: dict) -> dict:
            result = skill_cli.dispatch(operation, arguments)
            effect = skill_cli.CONTRACT["operations"][operation]["effect"]
            return {
                "ok": True,
                "operation": operation,
                "kind": "proposal" if result.get("pending") is True else effect,
                "result": result,
            }

        return dispatch

    return build


class SelectionGateway:
    """Scripts one model selection; dispatcher-backed invocation stays real."""

    def __init__(self, response: dict, dispatch):
        self.response = response
        self.dispatch = dispatch
        self.model_calls = []
        self.tool_calls = []

    async def chat_completion(self, payload, *, channel=None, timeout=None):
        self.model_calls.append((payload, channel, timeout))
        return self.response

    async def invoke_tool(self, name, args, *, agent_id, session_key, idempotency_key, timeout=None):
        assert name == "openhouse_crm"
        self.tool_calls.append((args, agent_id, session_key, idempotency_key, timeout))
        return self.dispatch(args["operation"], args["arguments"])


def _run(gateway):
    return asyncio.run(run_single_action(gateway, "do this", "lifecycle-session", "agent"))


def _lead(client, name="Lifecycle Lead"):
    response = client.post("/api/leads", json={"name": name, "source": "form"})
    assert response.status_code == 200, response.text
    return response.json()


def _pending(client, pending_id):
    rows = client.get("/api/pending-changes?status=pending").json()
    return next(row for row in rows if row["id"] == pending_id)


def _row_count(table: str) -> int:
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_create_stays_pending_then_edited_approval_persists_across_restart(
    lifecycle_client_factory, skill_dispatch
):
    with lifecycle_client_factory() as initial:
        gateway = SelectionGateway(
            _proposal("create", "create_lead", {"name": "Original Name", "area": "Kirkland"}),
            skill_dispatch(initial),
        )
        proposed = _run(gateway)
        assert proposed.status == "pending"
        assert proposed.receipt.result["pending"] is True
        pending_id = proposed.receipt.result["id"]
        assert initial.get("/api/leads").json() == []
        assert _pending(initial, pending_id)["payload"]["name"] == "Original Name"

        approved = initial.post(
            f"/api/pending-changes/{pending_id}/approve",
            json={"fields": {"name": "Edited Name", "area": "Bellevue"}},
        )
        assert approved.status_code == 200, approved.text
        lead_id = approved.json()["id"]
        assert initial.get(f"/api/leads/{lead_id}").json()["name"] == "Edited Name"
        assert initial.post(f"/api/pending-changes/{pending_id}/approve").status_code == 400

    with lifecycle_client_factory() as restarted:
        restored = restarted.get(f"/api/leads/{lead_id}")
        assert restored.status_code == 200
        assert restored.json()["area"] == "Bellevue"
        rows = restarted.get("/api/pending-changes?status=approved").json()
        assert [row["id"] for row in rows] == [pending_id]


@pytest.mark.parametrize(
    ("operation", "arguments", "table"),
    [
        ("add_note", {"content": "Call after inspection"}, "events"),
        ("schedule_followup", {"due_ts": "2026-09-21T09:00:00", "note": "Send comps"}, "reminders"),
    ],
)
def test_denied_note_or_followup_never_mutates_business_rows(
    lifecycle_client, skill_dispatch, operation, arguments, table
):
    skill_dispatch = skill_dispatch(lifecycle_client)
    lead = _lead(lifecycle_client)
    gateway = SelectionGateway(
        _proposal("proposal", operation, {"lead_id": lead["id"], **arguments}),
        skill_dispatch,
    )

    proposed = _run(gateway)
    assert proposed.status == "pending", proposed
    pending_id = proposed.receipt.result["id"]
    assert lifecycle_client.get(f"/api/leads/{lead['id']}").json()["events"] == []
    assert _row_count(table) == 0

    denied = lifecycle_client.post(f"/api/pending-changes/{pending_id}/deny")
    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == "denied"
    assert lifecycle_client.get(f"/api/leads/{lead['id']}").json()["events"] == []
    assert _row_count(table) == 0


def test_approved_note_and_followup_use_real_handlers(lifecycle_client, skill_dispatch):
    skill_dispatch = skill_dispatch(lifecycle_client)
    lead = _lead(lifecycle_client)
    note = _run(SelectionGateway(
        _proposal("note", "add_note", {"lead_id": lead["id"], "content": "Requested Saturday tour"}),
        skill_dispatch,
    ))
    followup = _run(SelectionGateway(
        _proposal("followup", "schedule_followup", {
            "lead_id": lead["id"], "due_ts": "2026-09-22T09:00:00", "note": "Send listings",
        }),
        skill_dispatch,
    ))
    assert note.status == followup.status == "pending", (note, followup)

    note_result = lifecycle_client.post(
        f"/api/pending-changes/{note.receipt.result['id']}/approve"
    )
    followup_result = lifecycle_client.post(
        f"/api/pending-changes/{followup.receipt.result['id']}/approve"
    )
    assert note_result.status_code == followup_result.status_code == 200
    assert note_result.json()["content"] == "Requested Saturday tour"
    assert followup_result.json()["note"] == "Send listings"


def test_booking_conflict_is_checked_at_approval_and_valid_booking_persists(
    lifecycle_client, skill_dispatch
):
    skill_dispatch = skill_dispatch(lifecycle_client)
    conflict_lead = _lead(lifecycle_client, "Conflict Lead")
    conflict = _run(SelectionGateway(
        _proposal("conflict", "book_appointment", {
            "lead_id": conflict_lead["id"],
            "start_ts": "2026-09-23T10:00:00",
            "end_ts": "2026-09-23T10:45:00",
        }),
        skill_dispatch,
    ))
    lifecycle_client.post("/api/appointments", json={
        "lead_id": conflict_lead["id"],
        "start_ts": "2026-09-23T10:00:00", "end_ts": "2026-09-23T10:45:00",
    })

    rejected = lifecycle_client.post(
        f"/api/pending-changes/{conflict.receipt.result['id']}/approve"
    )
    assert rejected.status_code == 409
    assert _pending(lifecycle_client, conflict.receipt.result["id"])["status"] == "pending"

    good_lead = _lead(lifecycle_client, "Booked Lead")
    successful = _run(SelectionGateway(
        _proposal("book", "book_appointment", {
            "lead_id": good_lead["id"],
            "start_ts": "2026-09-23T11:00:00",
            "end_ts": "2026-09-23T11:45:00", "location": "Model home",
        }),
        skill_dispatch,
    ))
    approved = lifecycle_client.post(
        f"/api/pending-changes/{successful.receipt.result['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["location"] == "Model home"
    assert lifecycle_client.get(f"/api/leads/{good_lead['id']}").json()["status"] == "meeting_booked"
