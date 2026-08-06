import json
import logging
import time

from fastapi.testclient import TestClient

from tests.conftest import make_lead


AGENT = {"X-Actor": "agent"}


def _wait_until(predicate, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition was not reached before timeout"


def _stop_worker():
    from app.integrations import hook_outbox

    stop = getattr(hook_outbox, "stop_worker", None)
    if stop is not None:
        assert stop(timeout=2)


def _outbox_rows():
    from app.db import get_conn

    with get_conn() as conn:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM hook_outbox ORDER BY id")
        ]


def _seed_live_reminder_rows(client, count):
    from app.db import get_conn

    lead = make_lead(client, email=None)
    outbox_ids = []
    with get_conn() as conn:
        for index in range(count):
            reminder_id = conn.execute(
                "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
                (lead["id"], "2026-08-21T09:00:00", f"Follow up {index}"),
            ).lastrowid
            pending_id = conn.execute(
                "INSERT INTO pending_changes "
                "(operation, lead_id, payload, summary, status) VALUES (?,?,?,?,?)",
                (
                    "schedule_followup",
                    lead["id"],
                    "{}",
                    f"Follow up {index}",
                    "approved",
                ),
            ).lastrowid
            outbox_ids.append(
                conn.execute(
                    "INSERT INTO hook_outbox "
                    "(pending_change_id, idempotency_key, hook_type, object_id, "
                    "lead_id, delivery_mode) VALUES (?,?,?,?,?,?)",
                    (
                        pending_id,
                        f"pending-change:{pending_id}",
                        "reminder_created",
                        reminder_id,
                        lead["id"],
                        "live",
                    ),
                ).lastrowid
            )
    return outbox_ids


def _configure_live(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test-key")


def test_worker_drains_more_than_one_batch(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    ids = _seed_live_reminder_rows(client, 25)
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda _reminder: hooks.HookOutcome.LIVE_DELIVERED,
    )

    hook_outbox.start_worker(batch_size=10, poll_seconds=30)
    _wait_until(
        lambda: all(row["status"] == "delivered" for row in _outbox_rows()),
        timeout=3,
    )

    assert [row["id"] for row in _outbox_rows()] == ids
    assert all(row["attempts"] == 1 for row in _outbox_rows())
    _stop_worker()


def test_worker_retries_failed_row_without_restart(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    _seed_live_reminder_rows(client, 1)
    calls = []

    def flaky(reminder):
        calls.append(reminder)
        return (
            hooks.HookOutcome.FAILED
            if len(calls) == 1
            else hooks.HookOutcome.LIVE_DELIVERED
        )

    monkeypatch.setattr(hooks, "on_reminder_created", flaky)
    worker = hook_outbox.start_worker(
        batch_size=10, poll_seconds=0.05, retry_base_seconds=1
    )
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered", timeout=3)

    assert worker.is_alive()
    assert len(calls) == 2
    assert _outbox_rows()[0]["attempts"] == 2
    _stop_worker()


def test_one_failed_delivery_does_not_stop_the_rest_of_the_batch(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    _seed_live_reminder_rows(client, 2)

    def deliver_by_note(reminder):
        return (
            hooks.HookOutcome.FAILED
            if reminder["note"] == "Follow up 0"
            else hooks.HookOutcome.LIVE_DELIVERED
        )

    monkeypatch.setattr(hooks, "on_reminder_created", deliver_by_note)
    selected = hook_outbox.drain_hook_outbox(
        max_items=10, retry_base_seconds=30
    )

    assert selected == 2
    assert [row["status"] for row in _outbox_rows()] == ["failed", "delivered"]


def test_approval_enqueue_wakes_sleeping_worker(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    lead = make_lead(client, email=None)
    _configure_live(monkeypatch)
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder: calls.append(reminder) or hooks.HookOutcome.LIVE_DELIVERED,
    )
    hook_outbox.start_worker(batch_size=10, poll_seconds=30)
    time.sleep(0.05)

    queued = client.post(
        "/api/reminders",
        json={
            "lead_id": lead["id"],
            "due_ts": "2026-08-22T09:00:00",
            "note": "Wake the worker",
        },
        headers=AGENT,
    ).json()
    before = time.monotonic()
    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")
    assert approved.status_code == 200, approved.text
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered", timeout=1)

    assert time.monotonic() - before < 1
    assert len(calls) == 1
    _stop_worker()


def test_duplicate_worker_start_returns_the_one_live_worker(client):
    from app.integrations import hook_outbox

    _stop_worker()
    first = hook_outbox.start_worker(poll_seconds=30)
    second = hook_outbox.start_worker(poll_seconds=30)

    assert first is second
    assert first.is_alive()
    _stop_worker()


def test_fastapi_shutdown_stops_and_joins_worker():
    from app.integrations import hook_outbox
    from app.main import app

    _stop_worker()
    with TestClient(app):
        worker = hook_outbox.start_worker()
        assert worker.is_alive()

    assert not worker.is_alive()


def test_restart_waits_for_inflight_shutdown_then_starts_fresh_worker(
    client, monkeypatch
):
    import threading

    from app.integrations import hook_outbox, hooks
    from app.main import shutdown

    _stop_worker()
    _configure_live(monkeypatch)
    _seed_live_reminder_rows(client, 1)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_hook(reminder):
        calls.append(reminder)
        started.set()
        release.wait(timeout=3)
        return hooks.HookOutcome.LIVE_DELIVERED

    monkeypatch.setattr(hooks, "on_reminder_created", blocking_hook)
    old_worker = hook_outbox.start_worker(poll_seconds=30)
    assert started.wait(timeout=2)
    assert hook_outbox.stop_worker(timeout=0.05) is False

    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()
    time.sleep(0.05)
    restarted = []
    restart_thread = threading.Thread(
        target=lambda: restarted.append(hook_outbox.start_worker(poll_seconds=30))
    )
    restart_thread.start()
    try:
        time.sleep(0.05)
        assert shutdown_thread.is_alive(), "shutdown must join the in-flight hook"
        assert restart_thread.is_alive(), "restart must not reuse a stopping worker"
    finally:
        release.set()

    shutdown_thread.join(timeout=2)
    restart_thread.join(timeout=2)
    assert not shutdown_thread.is_alive()
    assert not restart_thread.is_alive()
    assert not old_worker.is_alive()
    assert len(restarted) == 1
    assert restarted[0] is not old_worker
    assert restarted[0].is_alive()
    assert len(calls) == 1
    assert _outbox_rows()[0]["status"] == "delivered"
    assert _outbox_rows()[0]["attempts"] == 1
    _stop_worker()


def test_worker_recovers_stale_processing_claim(client, monkeypatch):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    _seed_live_reminder_rows(client, 1)
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'processing', "
            "claim_token = 'dead-worker', claimed_at = '2000-01-01T00:00:00'"
        )
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda _reminder: hooks.HookOutcome.LIVE_DELIVERED,
    )

    hook_outbox.start_worker(
        poll_seconds=30, stale_after_seconds=1, retry_base_seconds=1
    )
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered")

    assert _outbox_rows()[0]["attempts"] == 1
    _stop_worker()


def test_live_intent_waits_for_integrations_then_delivers(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    lead = make_lead(client, email=None)
    _configure_live(monkeypatch)
    queued = client.post(
        "/api/reminders",
        json={
            "lead_id": lead["id"],
            "due_ts": "2026-08-23T09:00:00",
            "note": "Wait for credentials",
        },
        headers=AGENT,
    ).json()
    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert _outbox_rows()[0]["delivery_mode"] == "live"

    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder: calls.append(reminder) or hooks.HookOutcome.LIVE_DELIVERED,
    )
    hook_outbox.start_worker(
        poll_seconds=0.05, retry_base_seconds=1, stale_after_seconds=1
    )
    _wait_until(lambda: _outbox_rows()[0]["status"] == "failed")

    unavailable = _outbox_rows()[0]
    assert calls == []
    assert unavailable["delivered_at"] is None
    assert "live integrations unavailable" in unavailable["last_error"]
    assert unavailable["next_attempt_at"] is not None

    _configure_live(monkeypatch)
    hook_outbox.wake_worker()
    _wait_until(lambda: _outbox_rows()[0]["status"] == "delivered", timeout=3)

    assert len(calls) == 1
    assert _outbox_rows()[0]["attempts"] == 2
    _stop_worker()


def test_off_mode_approval_never_becomes_later_live_delivery(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    lead = make_lead(client, email=None)
    calls = []
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *args: calls.append(args) or {"response_data": {"id": "unexpected"}},
    )
    queued = client.post(
        "/api/reminders",
        json={
            "lead_id": lead["id"],
            "due_ts": "2026-08-24T09:00:00",
            "note": "Demo-only reminder",
        },
        headers=AGENT,
    ).json()

    approved = client.post(f"/api/pending-changes/{queued['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert _outbox_rows() == []

    _configure_live(monkeypatch)
    hook_outbox.start_worker(poll_seconds=0.05)
    hook_outbox.wake_worker()
    time.sleep(0.1)

    assert calls == []
    assert _outbox_rows() == []
    _stop_worker()


def test_live_outbox_rejects_simulated_hook_result(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    outbox_id = _seed_live_reminder_rows(client, 1)[0]
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda _reminder: hooks.HookOutcome.SIMULATED,
    )

    delivered = hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=1)

    assert delivered is False
    assert _outbox_rows()[0]["status"] == "failed"
    assert "simulated" in _outbox_rows()[0]["last_error"]
    assert _outbox_rows()[0]["delivered_at"] is None


def test_lead_created_failure_uses_composite_audit_label(client, monkeypatch):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    lead = make_lead(client, email=None)
    with get_conn() as conn:
        pending_id = conn.execute(
            "INSERT INTO pending_changes "
            "(operation, lead_id, payload, summary, status) VALUES (?,?,?,?,?)",
            ("create_lead", None, "{}", "Create lead", "approved"),
        ).lastrowid
        outbox_id = conn.execute(
            "INSERT INTO hook_outbox "
            "(pending_change_id, idempotency_key, hook_type, object_id, "
            "lead_id, delivery_mode) VALUES (?,?,?,?,?,?)",
            (
                pending_id,
                f"pending-change:{pending_id}",
                "lead_created",
                lead["id"],
                lead["id"],
                "live",
            ),
        ).lastrowid
    monkeypatch.setattr(
        hooks, "on_lead_created", lambda _lead: hooks.HookOutcome.FAILED
    )

    hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=1)
    audits = client.get("/api/audit?limit=100").json()
    matching = [
        row
        for row in audits
        if json.loads(row["input"]).get("outbox_id") == outbox_id
    ]

    assert [row["tool"] for row in matching] == ["lead_created_hook (failed)"]


def test_outbox_failure_survives_failure_audit_error(
    client, monkeypatch, caplog
):
    from app.integrations import hook_outbox, hooks

    _stop_worker()
    _configure_live(monkeypatch)
    outbox_id = _seed_live_reminder_rows(client, 1)[0]
    monkeypatch.setattr(
        hooks, "on_reminder_created", lambda _reminder: hooks.HookOutcome.FAILED
    )
    monkeypatch.setattr(
        hook_outbox,
        "audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit database unavailable")
        ),
    )

    with caplog.at_level(logging.ERROR):
        delivered = hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=1)

    failed = _outbox_rows()[0]
    assert delivered is False
    assert failed["status"] == "failed"
    assert failed["last_error"]
    assert "could not persist failure audit" in caplog.text
