import threading
import time

import pytest

from tests.conftest import make_lead


AGENT = {"X-Actor": "agent"}


def _queue_reminder(client):
    lead = make_lead(client)
    queued = client.post(
        "/api/reminders",
        json={
            "lead_id": lead["id"],
            "due_ts": "2026-08-21T09:00:00",
            "note": "Durable follow-up",
        },
        headers=AGENT,
    )
    assert queued.status_code == 202, queued.text
    return lead, queued.json()


def _outbox_rows():
    from app.db import get_conn

    with get_conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM hook_outbox ORDER BY id")]


def _approved(client, pending_id):
    return [
        row
        for row in client.get("/api/pending-changes?status=approved").json()
        if row["id"] == pending_id
    ]


def _wait_until(predicate, *, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition was not reached before timeout"


def _approve_then_crash_before_dispatch(client, monkeypatch, pending_id):
    from app.integrations import hook_outbox
    from app.routers import pending_changes as pending_router

    assert hook_outbox.stop_worker(timeout=2)
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test-key")

    async def process_crash(*_args):
        raise RuntimeError("simulated process crash before hook dispatch")

    with monkeypatch.context() as patch:
        patch.setattr(
            pending_router,
            "_dispatch_committed_hook",
            process_crash,
            raising=False,
        )
        with pytest.raises(RuntimeError, match="process crash"):
            client.post(f"/api/pending-changes/{pending_id}/approve")


def test_startup_drains_hook_committed_before_dispatch(client, monkeypatch):
    from app.integrations import hook_outbox, hooks
    from app.main import startup

    lead, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])

    rows = _outbox_rows()
    assert len(rows) == 1
    assert rows[0]["pending_change_id"] == queued["id"]
    assert rows[0]["idempotency_key"] == f"pending-change:{queued['id']}"
    assert rows[0]["hook_type"] == "reminder_created"
    assert rows[0]["lead_id"] == lead["id"]
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempts"] == 0
    assert "payload" not in rows[0]
    assert len(_approved(client, queued["id"])) == 1

    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder: calls.append(reminder) or hooks.HookOutcome.LIVE_DELIVERED,
    )
    startup()
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered")

    assert len(calls) == 1
    assert calls[0]["lead_id"] == lead["id"]
    delivered = _outbox_rows()[0]
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 1
    assert delivered["delivered_at"] is not None
    assert hook_outbox.stop_worker(timeout=2)


def test_startup_recovery_does_not_block_application_startup(client, monkeypatch):
    from app.integrations import hook_outbox, hooks
    from app.main import startup

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    started = threading.Event()
    release = threading.Event()

    def slow_hook(_reminder):
        started.set()
        release.wait(timeout=3)
        return hooks.HookOutcome.LIVE_DELIVERED

    monkeypatch.setattr(hooks, "on_reminder_created", slow_hook)
    before = time.monotonic()
    startup()
    elapsed = time.monotonic() - before

    assert elapsed < 0.5
    assert started.wait(timeout=2)
    release.set()
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered")
    assert hook_outbox.stop_worker(timeout=2)


def test_failed_hook_stays_retryable_and_retry_does_not_replay_approval(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    assert hook_outbox.stop_worker(timeout=2)
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test-key")
    calls = []

    def flaky_hook(reminder):
        calls.append(reminder)
        return (
            hooks.HookOutcome.LIVE_DELIVERED
            if len(calls) > 1
            else hooks.HookOutcome.FAILED
        )

    monkeypatch.setattr(hooks, "on_reminder_created", flaky_hook)
    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")

    assert approved.status_code == 200, approved.text
    outbox_id = _outbox_rows()[0]["id"]
    hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0)
    failed = _outbox_rows()[0]
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert "reported failure" in failed["last_error"]
    assert len(_approved(client, queued["id"])) == 1

    hook_outbox.drain_hook_outbox(retry_base_seconds=0)

    delivered = _outbox_rows()[0]
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 2
    assert delivered["last_error"] is None
    assert len(calls) == 2
    approval_audits = [
        row
        for row in client.get("/api/audit?limit=100").json()
        if row["tool"] == "approve_pending_change"
    ]
    assert len(approval_audits) == 1


def test_concurrent_drains_claim_one_delivery(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])

    started = threading.Event()
    release = threading.Event()
    calls = []
    errors = []

    def blocking_hook(reminder):
        calls.append(reminder)
        started.set()
        release.wait(timeout=3)
        return hooks.HookOutcome.LIVE_DELIVERED

    monkeypatch.setattr(hooks, "on_reminder_created", blocking_hook)

    def drain():
        try:
            hook_outbox.drain_hook_outbox()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=drain)
    second = threading.Thread(target=drain)
    first.start()
    assert started.wait(timeout=2)
    second.start()
    second.join(timeout=2)
    release.set()
    first.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(calls) == 1
    assert _outbox_rows()[0]["status"] == "delivered"
    assert _outbox_rows()[0]["attempts"] == 1


def test_stale_processing_claim_is_recovered(client, monkeypatch):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'processing', claim_token = 'dead-worker', "
            "claimed_at = '2000-01-01T00:00:00'"
        )

    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder: calls.append(reminder) or hooks.HookOutcome.LIVE_DELIVERED,
    )
    hook_outbox.drain_hook_outbox()

    row = _outbox_rows()[0]
    assert len(calls) == 1
    assert row["status"] == "delivered"
    assert row["attempts"] == 1
    assert row["claim_token"] is None


def test_dispatch_does_not_report_delivery_after_claim_is_replaced(
    client, monkeypatch
):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])

    def replace_claim(_reminder):
        with get_conn() as conn:
            conn.execute(
                "UPDATE hook_outbox SET claim_token = 'replacement-worker' "
                "WHERE pending_change_id = ?",
                (queued["id"],),
            )
        return hooks.HookOutcome.LIVE_DELIVERED

    monkeypatch.setattr(hooks, "on_reminder_created", replace_claim)
    delivered = hook_outbox.dispatch_hook(_outbox_rows()[0]["id"])

    row = _outbox_rows()[0]
    assert delivered is False
    assert row["status"] == "processing"
    assert row["claim_token"] == "replacement-worker"
