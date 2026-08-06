"""Agent-initiated lead writes (X-Actor: agent) must queue for approval
instead of applying; the dashboard's untagged calls must be unaffected."""
import asyncio
import threading

from tests.conftest import make_lead

AGENT = {"X-Actor": "agent"}


def _pending(client, status="pending"):
    res = client.get(f"/api/pending-changes?status={status}")
    assert res.status_code == 200, res.text
    return res.json()


def _audit_rows(client, tool):
    return [row for row in client.get("/api/audit?limit=100").json() if row["tool"] == tool]


def test_dashboard_create_is_unaffected(client):
    """No X-Actor header: applies immediately, exactly as before this feature."""
    res = client.post("/api/leads", json={"name": "Direct Dana", "source": "form"})
    assert res.status_code == 200
    assert res.json()["name"] == "Direct Dana"
    assert _pending(client) == []


def test_agent_create_lead_queues_not_applies(client):
    before = len(client.get("/api/leads").json())
    res = client.post("/api/leads", json={"name": "Agent Andy", "source": "form"}, headers=AGENT)
    assert res.status_code == 202
    body = res.json()
    assert body["pending"] is True
    assert body["operation"] == "create_lead"
    assert body["status"] == "pending"
    assert "Agent Andy" in body["summary"]

    after = len(client.get("/api/leads").json())
    assert after == before, "queued create_lead must not insert a lead row"

    pending = _pending(client)
    assert len(pending) == 1
    assert pending[0]["id"] == body["id"]
    assert pending[0]["lead_id"] is None


def test_agent_update_lead_queues_and_approve_applies(client):
    lead = make_lead(client, budget=900_000)
    res = client.patch(f"/api/leads/{lead['id']}", json={"budget": 1_100_000}, headers=AGENT)
    assert res.status_code == 202
    pending_id = res.json()["id"]
    assert "900,000" in res.json()["summary"]
    assert "1,100,000" in res.json()["summary"]

    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["budget"] == 900_000, "queued update_lead must not touch the row"

    approve = client.post(f"/api/pending-changes/{pending_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["budget"] == 1_100_000

    applied = client.get(f"/api/leads/{lead['id']}").json()
    assert applied["budget"] == 1_100_000

    assert _pending(client) == []
    approved = _pending(client, status="approved")
    assert len(approved) == 1
    assert approved[0]["status"] == "approved"


def test_agent_close_lead_queues_and_deny_leaves_unchanged(client):
    lead = make_lead(client)
    res = client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "won"}, headers=AGENT)
    assert res.status_code == 202
    pending_id = res.json()["id"]
    assert "won" in res.json()["summary"]

    deny = client.post(f"/api/pending-changes/{pending_id}/deny", json={"reason": "not actually closing"})
    assert deny.status_code == 200
    assert deny.json()["status"] == "denied"
    assert deny.json()["deny_reason"] == "not actually closing"

    still_open = client.get(f"/api/leads/{lead['id']}").json()
    assert still_open["status"] != "closed"

    denied = _pending(client, status="denied")
    assert len(denied) == 1 and denied[0]["id"] == pending_id


def test_agent_delete_lead_queues(client):
    lead = make_lead(client)
    res = client.delete(f"/api/leads/{lead['id']}", headers=AGENT)
    assert res.status_code == 202
    assert res.json()["operation"] == "delete_lead"

    still_there = client.get(f"/api/leads/{lead['id']}")
    assert still_there.status_code == 200, "queued delete_lead must not remove the row"


def test_agent_merge_leads_queues(client):
    primary = make_lead(client, name="Primary Pat", phone="+14255550111")
    dup = make_lead(client, name="Dup Dana", phone="+14255550112")
    res = client.post("/api/leads/merge",
                       json={"primary_id": primary["id"], "duplicate_id": dup["id"]},
                       headers=AGENT)
    assert res.status_code == 202
    assert res.json()["operation"] == "merge_leads"

    both_exist = client.get("/api/leads").json()
    ids = {lead_row["id"] for lead_row in both_exist}
    assert primary["id"] in ids and dup["id"] in ids, "queued merge must not delete the duplicate"


def test_approve_already_decided_is_400(client):
    lead = make_lead(client)
    res = client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "lost"}, headers=AGENT)
    pending_id = res.json()["id"]
    client.post(f"/api/pending-changes/{pending_id}/deny")

    retry = client.post(f"/api/pending-changes/{pending_id}/approve")
    assert retry.status_code == 400


def test_pending_changes_status_filter_defaults_to_pending(client):
    lead = make_lead(client)
    client.patch(f"/api/leads/{lead['id']}", json={"area": "Kirkland"}, headers=AGENT)
    assert len(_pending(client)) == 1
    assert _pending(client, status="approved") == []
    assert _pending(client, status="denied") == []


def test_agent_create_lead_from_raw_text_resolves_fields_at_queue_time(client):
    """The operator needs real fields to look at and edit, not a raw note —
    extraction must happen before queuing, not deferred to approval."""
    res = client.post("/api/leads", json={
        "raw_text": "Met Jordan Ellis, Kirkland, budget 850k, timeline 2 months",
        "source": "note",
    }, headers=AGENT)
    assert res.status_code == 202
    pending_id = res.json()["id"]

    pending = _pending(client)[0]
    payload = pending["payload"]
    assert payload["name"] == "Jordan Ellis"
    assert payload["area"] == "Kirkland"
    assert payload["budget"] == 850_000
    assert payload["raw_text"].startswith("Met Jordan Ellis")

    approved = client.post(f"/api/pending-changes/{pending_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["area"] == "Kirkland"
    assert approved.json()["budget"] == 850_000


def test_agent_create_fallback_warns_for_review_without_leaking_marker(client, monkeypatch):
    from app.routers import leads as leads_router

    class FallbackDriver:
        async def extract(self, raw_text):
            return {
                "name": "Jordan Ellis",
                "area": "Kirkland",
                "budget": 850_000,
                "preferences": [],
                "missing_fields": ["phone"],
                "_fallback_used": "deterministic_parser",
            }

    monkeypatch.setattr(leads_router, "get_driver", lambda: FallbackDriver())
    queued = client.post(
        "/api/leads",
        json={"raw_text": "Met Jordan Ellis in Kirkland", "source": "note"},
        headers=AGENT,
    )

    assert queued.status_code == 202
    assert "backup parser" in queued.json()["summary"].lower()
    pending = _pending(client)[0]
    assert "backup parser" in pending["summary"].lower()
    assert "_fallback_used" not in str(queued.json())
    assert "_fallback_used" not in str(pending)


def test_approve_with_edited_fields_overrides_the_queued_payload(client):
    """The operator can edit a field in the dialog before approving —
    the applied write must reflect the edit, not the agent's original value."""
    res = client.post("/api/leads", json={
        "raw_text": "Met Jordan Ellis, Kirkland, budget 850k",
        "source": "note",
    }, headers=AGENT)
    pending_id = res.json()["id"]

    approved = client.post(f"/api/pending-changes/{pending_id}/approve",
                            json={"fields": {"budget": 900_000, "area": "Bellevue"}})
    assert approved.status_code == 200
    assert approved.json()["budget"] == 900_000
    assert approved.json()["area"] == "Bellevue"
    assert approved.json()["name"] == "Jordan Ellis", "unedited fields must pass through unchanged"


def test_approve_update_lead_with_edited_fields(client):
    lead = make_lead(client, budget=900_000, area="Bellevue")
    res = client.patch(f"/api/leads/{lead['id']}", json={"budget": 1_100_000}, headers=AGENT)
    pending_id = res.json()["id"]

    # operator edits to a different budget than the agent proposed, and adds
    # a field the agent never touched
    approved = client.post(f"/api/pending-changes/{pending_id}/approve",
                            json={"fields": {"budget": 1_050_000, "timeline": "ASAP"}})
    assert approved.status_code == 200
    assert approved.json()["budget"] == 1_050_000
    assert approved.json()["timeline"] == "ASAP"
    assert approved.json()["area"] == "Bellevue", "field the operator didn't touch must be untouched"


def test_direct_edits_still_apply_instantly_alongside_agent_writes(client):
    """Regression: a human editing a lead directly must never be gated,
    even while an agent-proposed change on the same lead is pending."""
    lead = make_lead(client, budget=500_000)
    client.patch(f"/api/leads/{lead['id']}", json={"budget": 999_999}, headers=AGENT)

    direct = client.patch(f"/api/leads/{lead['id']}", json={"area": "Redmond"})
    assert direct.status_code == 200
    assert direct.json()["area"] == "Redmond"
    assert direct.json()["budget"] == 500_000, "the agent's queued budget change must not have leaked in"


def test_agent_note_queues_and_approval_applies(client):
    lead = make_lead(client)
    queued = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Requested Saturday tour"},
        headers=AGENT,
    )

    assert queued.status_code == 202
    assert queued.json()["operation"] == "add_event"
    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["events"] == []
    assert unchanged["status"] == lead["status"]
    assert unchanged["last_activity_at"] == lead["last_activity_at"]
    assert _audit_rows(client, "add_event") == []

    approved = client.post(f"/api/pending-changes/{queued.json()['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["content"] == "Requested Saturday tour"


def test_agent_booking_does_not_run_hook_before_approval(client, monkeypatch):
    from app.routers import calendar as calendar_router

    lead = make_lead(client)
    calls = []
    monkeypatch.setattr(calendar_router.hooks, "on_tour_booked", lambda *args: calls.append(args))
    queued = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-08-08T10:00:00",
            "end_ts": "2026-08-08T10:45:00",
        },
        headers=AGENT,
    )

    assert queued.status_code == 202
    assert calls == []
    assert client.get("/api/appointments").json() == []
    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["status"] == lead["status"]
    assert unchanged["last_activity_at"] == lead["last_activity_at"]
    assert unchanged["events"] == []
    assert _audit_rows(client, "book_appointment") == []

    approved = client.post(f"/api/pending-changes/{queued.json()['id']}/approve")
    assert approved.status_code == 200
    assert len(calls) == 1


def test_agent_reminder_denial_has_no_local_or_external_effect(client, monkeypatch):
    from app.routers import misc

    lead = make_lead(client)
    original_status = lead["status"]
    original_activity = lead["last_activity_at"]
    calls = []
    monkeypatch.setattr(misc.hooks, "on_reminder_created", lambda value: calls.append(value))
    queued = client.post(
        "/api/reminders",
        json={"lead_id": lead["id"], "due_ts": "2026-08-09T09:00:00", "note": "Call"},
        headers=AGENT,
    )

    assert queued.status_code == 202
    denied = client.post(f"/api/pending-changes/{queued.json()['id']}/deny")
    assert denied.status_code == 200
    assert client.get("/api/reminders").json() == []
    assert calls == []
    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["status"] == original_status
    assert unchanged["last_activity_at"] == original_activity
    assert unchanged["events"] == []
    assert _audit_rows(client, "schedule_followup") == []


def test_booking_conflict_during_approval_leaves_change_pending(client, monkeypatch):
    from app.routers import calendar as calendar_router

    first = make_lead(client, name="First Buyer")
    second = make_lead(client, name="Second Buyer")
    calls = []
    monkeypatch.setattr(calendar_router.hooks, "on_tour_booked", lambda *args: calls.append(args))
    payload = {
        "start_ts": "2026-08-10T10:00:00",
        "end_ts": "2026-08-10T10:45:00",
    }
    queued = client.post(
        "/api/appointments",
        json={"lead_id": first["id"], **payload},
        headers=AGENT,
    )
    assert queued.status_code == 202

    direct = client.post(
        "/api/appointments",
        json={"lead_id": second["id"], **payload},
    )
    assert direct.status_code == 200
    assert len(calls) == 1

    approved = client.post(f"/api/pending-changes/{queued.json()['id']}/approve")
    assert approved.status_code == 409
    assert [item["id"] for item in _pending(client)] == [queued.json()["id"]]
    unchanged = client.get(f"/api/leads/{first['id']}").json()
    assert unchanged["status"] == first["status"]
    assert unchanged["last_activity_at"] == first["last_activity_at"]
    assert unchanged["events"] == []
    assert len(calls) == 1
    operation_rows = _audit_rows(client, "book_appointment")
    assert len(operation_rows) == 1
    assert operation_rows[0]["lead_id"] == second["id"]


def test_edited_blank_note_is_rejected_and_remains_pending(client):
    lead = make_lead(client)
    queued = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Valid proposal"},
        headers=AGENT,
    )

    approved = client.post(
        f"/api/pending-changes/{queued.json()['id']}/approve",
        json={"fields": {"content": "   "}},
    )

    assert approved.status_code == 422
    assert [item["id"] for item in _pending(client)] == [queued.json()["id"]]
    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["events"] == []
    assert unchanged["last_activity_at"] == lead["last_activity_at"]
    assert _audit_rows(client, "add_event") == []


def test_concurrent_approvals_apply_reminder_once(client, monkeypatch):
    from app.routers import pending_changes as pending_router

    lead = make_lead(client)
    queued = client.post(
        "/api/reminders",
        json={"lead_id": lead["id"], "due_ts": "2026-08-15T09:00:00", "note": "Call"},
        headers=AGENT,
    ).json()
    original_operation = pending_router._operation
    barrier = threading.Barrier(2)

    def gated_operation(operation):
        model_cls, apply_fn, needs_lead_id = original_operation(operation)
        if operation != "schedule_followup":
            return model_cls, apply_fn, needs_lead_id

        def gated_apply(body, **kwargs):
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
            return apply_fn(body, **kwargs)

        return model_cls, gated_apply, needs_lead_id

    monkeypatch.setattr(pending_router, "_operation", gated_operation)
    statuses = []

    def approve():
        from fastapi import HTTPException

        try:
            asyncio.run(pending_router.approve_pending(queued["id"]))
            statuses.append(200)
        except HTTPException as exc:
            statuses.append(exc.status_code)

    workers = [threading.Thread(target=approve) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(statuses) == [200, 400]
    reminders = client.get("/api/reminders").json()
    assert len(reminders) == 1
    assert reminders[0]["note"] == "Call"
    assert len(_audit_rows(client, "schedule_followup")) == 1
    assert len(_audit_rows(client, "approve_pending_change")) == 1


def test_booking_hook_exception_cannot_leave_applied_proposal_pending(client, monkeypatch):
    from app.routers import calendar as calendar_router

    lead = make_lead(client)
    queued = client.post(
        "/api/appointments",
        json={
            "lead_id": lead["id"],
            "start_ts": "2026-08-16T10:00:00",
            "end_ts": "2026-08-16T10:45:00",
        },
        headers=AGENT,
    ).json()
    monkeypatch.setattr(
        calendar_router.hooks,
        "on_tour_booked",
        lambda *args: (_ for _ in ()).throw(RuntimeError("calendar unavailable")),
    )

    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")

    assert approved.status_code == 200
    assert len(client.get("/api/appointments").json()) == 1
    assert _pending(client) == []
    assert [item["id"] for item in _pending(client, "approved")] == [queued["id"]]
    assert len(_audit_rows(client, "book_appointment")) == 1
    failures = _audit_rows(client, "gcal_create_event (failed)")
    assert len(failures) == 1
    assert failures[0]["actor"] == "user"
    assert failures[0]["lead_id"] == lead["id"]


def test_create_lead_hook_failure_audit_uses_new_lead_id(client, monkeypatch):
    from app.routers import leads as leads_router

    queued = client.post(
        "/api/leads",
        json={"name": "Hook Failure", "source": "form"},
        headers=AGENT,
    ).json()
    monkeypatch.setattr(
        leads_router.hooks,
        "on_lead_created",
        lambda *_: (_ for _ in ()).throw(RuntimeError("mail unavailable")),
    )

    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")

    assert approved.status_code == 200
    failures = _audit_rows(client, "gmail_create_draft (failed)")
    assert len(failures) == 1
    assert failures[0]["lead_id"] == approved.json()["id"]


def test_reminder_hook_exception_cannot_leave_applied_proposal_pending(client, monkeypatch):
    from app.routers import misc

    lead = make_lead(client)
    queued = client.post(
        "/api/reminders",
        json={"lead_id": lead["id"], "due_ts": "2026-08-17T09:00:00", "note": "Call"},
        headers=AGENT,
    ).json()
    monkeypatch.setattr(
        misc.hooks,
        "on_reminder_created",
        lambda *args: (_ for _ in ()).throw(RuntimeError("calendar unavailable")),
    )

    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")

    assert approved.status_code == 200
    assert len(client.get("/api/reminders").json()) == 1
    assert _pending(client) == []
    assert [item["id"] for item in _pending(client, "approved")] == [queued["id"]]
    assert len(_audit_rows(client, "schedule_followup")) == 1
    failures = _audit_rows(client, "gcal_create_event (failed)")
    assert len(failures) == 1
    assert failures[0]["actor"] == "user"
    assert failures[0]["lead_id"] == lead["id"]
