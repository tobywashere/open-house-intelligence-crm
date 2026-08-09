import json
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
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
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

    def slow_hook(_reminder, **_kwargs):
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

    def flaky_hook(reminder, **_kwargs):
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


def test_failed_hook_exhausts_on_fifth_attempt(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.FAILED,
    )
    outbox_id = _outbox_rows()[0]["id"]

    for attempt in range(5):
        assert hook_outbox.dispatch_hook(
            outbox_id, retry_base_seconds=0
        ) is False
        row = _outbox_rows()[0]
        assert row["attempts"] == attempt + 1
        assert row["status"] == (
            "exhausted" if attempt == 4 else "failed"
        )

    assert row["next_attempt_at"] is None
    assert row["claim_token"] is None
    assert row["claimed_at"] is None
    assert hook_outbox.drain_hook_outbox(retry_base_seconds=0) == 0
    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is False
    assert len(calls) == 5
    assert _outbox_rows()[0]["attempts"] == 5


def test_exhausted_hook_sanitizes_error_and_terminal_audit(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    secret = "top-secret-provider-token"
    monkeypatch.setenv("OHI_API_TOKEN", secret)

    def fail_with_secret(_reminder, **_kwargs):
        raise RuntimeError(f"provider rejected {secret}")

    monkeypatch.setattr(hooks, "on_reminder_created", fail_with_secret)
    outbox_id = _outbox_rows()[0]["id"]

    for _ in range(5):
        hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0)

    exhausted = _outbox_rows()[0]
    assert exhausted["status"] == "exhausted"
    assert exhausted["last_error"] == "provider rejected [redacted]"
    matching = [
        row
        for row in client.get(
            "/api/audit?limit=100", headers={"X-API-Token": secret}
        ).json()
        if json.loads(row["input"]).get("outbox_id") == outbox_id
    ]
    terminal = matching[0]
    output = json.loads(terminal["output"])
    assert terminal["tool"] == "gcal_create_event (failed)"
    assert output == {
        "error": "provider rejected [redacted]",
        "retryable": False,
        "status": "exhausted",
        "attempts": 5,
    }
    assert secret not in json.dumps(matching)


def test_real_provider_failure_keeps_actionable_sanitized_error(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    secret = "expired-provider-secret"
    provider_reason = (
        f"Google Calendar connection expired for {secret}; reconnect the account"
    )
    monkeypatch.setenv("COMPOSIO_API_KEY", secret)
    provider_args = []

    def disconnected_provider(slug, args):
        assert slug == "GOOGLECALENDAR_CREATE_EVENT"
        provider_args.append(args)
        raise hooks.cc.IntegrationError(provider_reason)

    monkeypatch.setattr(hooks.cc, "execute", disconnected_provider)
    outbox_id = _outbox_rows()[0]["id"]

    for _ in range(5):
        assert hook_outbox.dispatch_hook(
            outbox_id, retry_base_seconds=0
        ) is False

    expected = (
        "Google Calendar connection expired for [redacted]; reconnect the account"
    )
    exhausted = _outbox_rows()[0]
    assert exhausted["status"] == "exhausted"
    assert exhausted["last_error"] == expected
    audits = client.get("/api/audit?limit=100").json()
    terminal = next(
        row
        for row in audits
        if json.loads(row["input"]).get("outbox_id") == outbox_id
    )
    assert json.loads(terminal["output"])["error"] == expected
    assert secret not in json.dumps(audits)
    assert len(provider_args) == 5
    assert all("delivery_step" not in args for args in provider_args)


def test_blanket_hook_failure_is_tied_to_exact_delivery(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    secret = "blanket-failure-secret"
    monkeypatch.setenv("OPENCLAW_API_TOKEN", secret)

    def unexpected_provider_failure(_slug, _args):
        raise RuntimeError(f"provider adapter crashed with {secret}; reconnect it")

    monkeypatch.setattr(hooks.cc, "execute", unexpected_provider_failure)
    outbox_id = _outbox_rows()[0]["id"]

    assert hook_outbox.dispatch_hook(
        outbox_id, retry_base_seconds=0
    ) is False

    failed = _outbox_rows()[0]
    expected = "provider adapter crashed with [redacted]; reconnect it"
    assert failed["last_error"] == expected
    matching = [
        row
        for row in client.get("/api/audit?limit=100").json()
        if row["tool"] == "gcal_create_event (failed)"
    ]
    assert any(
        json.loads(row["input"]).get("delivery_key")
        == f"pending-change:{queued['id']}"
        for row in matching
    )
    assert secret not in json.dumps(matching)


def test_exhaustion_state_and_audit_are_atomic(
    client, monkeypatch, caplog
):
    import logging

    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'failed', attempts = 4 "
            "WHERE id = ?",
            (outbox_id,),
        )
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda _reminder, **_kwargs: hooks.HookOutcome.FAILED,
    )
    monkeypatch.setattr(
        hook_outbox,
        "audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit database unavailable")
        ),
    )

    with caplog.at_level(logging.ERROR):
        hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0)

    row = _outbox_rows()[0]
    assert row["status"] == "processing"
    assert row["attempts"] == 5
    assert row["claim_token"] is not None
    assert "could not persist exhausted state" in caplog.text


def test_stale_fifth_claim_exhausts_without_sixth_provider_attempt(
    client, monkeypatch
):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'processing', attempts = 5, "
            "claim_token = 'dead-worker', claimed_at = '2000-01-01T00:00:00' "
            "WHERE id = ?",
            (outbox_id,),
        )
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
    )

    assert hook_outbox.dispatch_hook(
        outbox_id, stale_after_seconds=1
    ) is False

    exhausted = _outbox_rows()[0]
    assert exhausted["status"] == "exhausted"
    assert exhausted["attempts"] == 5
    assert exhausted["claim_token"] is None
    assert calls == []


def test_stale_fifth_claim_for_deleted_object_is_cancelled_not_exhausted(
    client, monkeypatch
):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    lead, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'processing', attempts = 5, "
            "claim_token = 'dead-worker', claimed_at = '2000-01-01T00:00:00' "
            "WHERE id = ?",
            (outbox_id,),
        )
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
    )
    deleted = client.delete(f"/api/leads/{lead['id']}")
    assert deleted.status_code == 200, deleted.text

    assert hook_outbox.dispatch_hook(
        outbox_id, stale_after_seconds=1
    ) is False

    cancelled = _outbox_rows()[0]
    assert cancelled["status"] == "cancelled"
    assert cancelled["attempts"] == 5
    assert cancelled["claim_token"] is None
    assert cancelled["claimed_at"] is None
    assert cancelled["next_attempt_at"] is None
    assert "reminder no longer exists" in cancelled["last_error"]
    assert calls == []
    matching = [
        row
        for row in client.get("/api/audit?limit=100").json()
        if json.loads(row["input"]).get("outbox_id") == outbox_id
    ]
    assert [row["tool"] for row in matching] == [
        "gcal_create_event (cancelled)"
    ]
    output = json.loads(matching[0]["output"])
    assert output["retryable"] is False
    assert output["status"] == "cancelled"
    assert "reminder no longer exists" in output["reason"]
    assert matching[0]["lead_id"] is None


def test_stale_fifth_claim_rechecks_deleted_object_during_terminalization(
    client, monkeypatch
):
    from app.db import get_conn
    from app.integrations import hook_outbox, hooks

    lead, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'processing', attempts = 5, "
            "claim_token = 'dead-worker', claimed_at = '2000-01-01T00:00:00' "
            "WHERE id = ?",
            (outbox_id,),
        )
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
    )
    terminalize = hook_outbox._exhaust_stale_claim_at_limit

    def delete_at_terminal_boundary(*args, **kwargs):
        deleted = client.delete(f"/api/leads/{lead['id']}")
        assert deleted.status_code == 200, deleted.text
        return terminalize(*args, **kwargs)

    monkeypatch.setattr(
        hook_outbox,
        "_exhaust_stale_claim_at_limit",
        delete_at_terminal_boundary,
    )

    assert hook_outbox.dispatch_hook(
        outbox_id, stale_after_seconds=1
    ) is False

    terminal = _outbox_rows()[0]
    assert terminal["status"] == "cancelled"
    assert terminal["attempts"] == 5
    assert "reminder no longer exists" in terminal["last_error"]
    assert calls == []


def test_operator_can_list_and_retry_exhausted_hook(client, monkeypatch):
    from app.db import get_conn
    from app.integrations import hook_outbox

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = 'exhausted', attempts = 5, "
            "last_error = 'provider unavailable', claim_token = NULL, "
            "claimed_at = NULL, next_attempt_at = NULL WHERE id = ?",
            (outbox_id,),
        )

    listed = client.get("/api/integrations/outbox?status=exhausted")

    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [outbox_id]
    assert set(listed.json()[0]) == {
        "id",
        "pending_change_id",
        "hook_type",
        "object_id",
        "lead_id",
        "delivery_mode",
        "status",
        "attempts",
        "last_error",
        "next_attempt_at",
        "created_at",
        "updated_at",
        "delivered_at",
    }

    retried = client.post(f"/api/integrations/outbox/{outbox_id}/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending"
    assert retried.json()["attempts"] == 0
    assert retried.json()["last_error"] is None
    assert retried.json()["next_attempt_at"] is None
    assert set(retried.json()) == set(listed.json()[0])
    audits = client.get("/api/audit?limit=100").json()
    retry_audit = next(
        row for row in audits if row["tool"] == "retry_hook_delivery"
    )
    assert retry_audit["actor"] == "user"
    assert json.loads(retry_audit["input"]) == {"outbox_id": outbox_id}
    assert json.loads(retry_audit["output"]) == {
        "status": "pending",
        "attempts": 0,
    }
    assert hook_outbox.stop_worker(timeout=2)

    agent_retry = client.post(
        f"/api/integrations/outbox/{outbox_id}/retry",
        headers=AGENT,
    )
    assert agent_retry.status_code == 403


@pytest.mark.parametrize(
    "status", ["pending", "failed", "processing", "delivered", "cancelled"]
)
def test_only_exhausted_hook_can_be_retried(client, monkeypatch, status):
    from app.db import get_conn

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    outbox_id = _outbox_rows()[0]["id"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = ? WHERE id = ?",
            (status, outbox_id),
        )

    conflict = client.post(f"/api/integrations/outbox/{outbox_id}/retry")

    assert conflict.status_code == 409
    assert client.post("/api/integrations/outbox/999999/retry").status_code == 404


def test_outbox_listing_rejects_unknown_status(client):
    response = client.get("/api/integrations/outbox?status=unknown")

    assert response.status_code == 422


def test_agent_retry_is_rejected_before_outbox_lookup(client):
    response = client.post(
        "/api/integrations/outbox/999999/retry",
        headers=AGENT,
    )

    assert response.status_code == 403


def test_lead_created_retry_resumes_after_calendar_when_gmail_failed(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    queued = client.post(
        "/api/leads",
        json={
            "name": "Partial Delivery",
            "email": "partial@example.com",
            "source": "form",
        },
        headers=AGENT,
    )
    assert queued.status_code == 202, queued.text
    _approve_then_crash_before_dispatch(client, monkeypatch, queued.json()["id"])

    calls = []
    gmail_attempts = 0

    def calendar_then_flaky_gmail(slug, _args):
        nonlocal gmail_attempts
        calls.append(slug)
        if slug == "GOOGLECALENDAR_CREATE_EVENT":
            return {"response_data": {"id": "event-once"}}
        if slug == "GMAIL_CREATE_EMAIL_DRAFT":
            gmail_attempts += 1
            if gmail_attempts == 1:
                raise hooks.cc.IntegrationError("gmail temporarily unavailable")
            return {"response_data": {"id": "draft-after-retry"}}
        raise AssertionError(f"unexpected Composio tool: {slug}")

    monkeypatch.setattr(hooks.cc, "execute", calendar_then_flaky_gmail)
    outbox_id = _outbox_rows()[0]["id"]

    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is False
    assert _outbox_rows()[0]["status"] == "failed"
    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is True

    delivered = _outbox_rows()[0]
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 2
    assert calls.count("GOOGLECALENDAR_CREATE_EVENT") == 1
    assert calls.count("GMAIL_CREATE_EMAIL_DRAFT") == 2


def test_concurrent_drains_claim_one_delivery(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])

    started = threading.Event()
    release = threading.Event()
    calls = []
    errors = []

    def blocking_hook(reminder, **_kwargs):
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
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
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

    def replace_claim(_reminder, **_kwargs):
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


def test_deleted_reminder_hook_is_cancelled_once_and_never_retried(
    client, monkeypatch
):
    from app.integrations import hook_outbox, hooks

    lead, queued = _queue_reminder(client)
    _approve_then_crash_before_dispatch(client, monkeypatch, queued["id"])
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda reminder, **_kwargs: calls.append(reminder)
        or hooks.HookOutcome.LIVE_DELIVERED,
    )

    deleted = client.delete(f"/api/leads/{lead['id']}")
    assert deleted.status_code == 200, deleted.text
    outbox_id = _outbox_rows()[0]["id"]

    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is False

    cancelled = _outbox_rows()[0]
    assert cancelled["status"] == "cancelled"
    assert cancelled["claim_token"] is None
    assert cancelled["claimed_at"] is None
    assert cancelled["next_attempt_at"] is None
    assert "reminder no longer exists" in cancelled["last_error"]
    assert calls == []
    assert hook_outbox.drain_hook_outbox(retry_base_seconds=0) == 0
    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is False
    assert _outbox_rows()[0]["attempts"] == 1

    matching = [
        row
        for row in client.get("/api/audit?limit=100").json()
        if json.loads(row["input"]).get("outbox_id") == outbox_id
    ]
    assert [row["tool"] for row in matching] == ["gcal_create_event (cancelled)"]
    output = json.loads(matching[0]["output"])
    assert output["retryable"] is False
    assert output["status"] == "cancelled"
    assert "reminder no longer exists" in output["reason"]
    assert matching[0]["lead_id"] is None


def test_merged_appointment_hook_delivers_for_surviving_lead(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    primary = make_lead(client, name="Primary", email="primary@example.com")
    duplicate = make_lead(client, name="Duplicate", email="duplicate@example.com")
    queued = client.post(
        "/api/appointments",
        json={
            "lead_id": duplicate["id"],
            "start_ts": "2026-08-22T10:00:00",
            "end_ts": "2026-08-22T10:45:00",
            "location": "Kirkland",
        },
        headers=AGENT,
    )
    assert queued.status_code == 202, queued.text
    _approve_then_crash_before_dispatch(client, monkeypatch, queued.json()["id"])

    merged = client.post(
        "/api/leads/merge",
        json={"primary_id": primary["id"], "duplicate_id": duplicate["id"]},
    )
    assert merged.status_code == 200, merged.text
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_tour_booked",
        lambda lead, appointment, **_kwargs: calls.append((lead, appointment))
        or hooks.HookOutcome.LIVE_DELIVERED,
    )

    outbox_id = _outbox_rows()[0]["id"]
    assert hook_outbox.dispatch_hook(outbox_id) is True

    delivered = _outbox_rows()[0]
    assert delivered["status"] == "delivered"
    assert delivered["lead_id"] == primary["id"]
    assert len(calls) == 1
    assert calls[0][0]["id"] == primary["id"]
    assert calls[0][1]["lead_id"] == primary["id"]


def test_merged_away_lead_created_hook_is_cancelled_not_retried(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    primary = make_lead(client, name="Primary", email="primary@example.com")
    queued = client.post(
        "/api/leads",
        json={"name": "Duplicate", "email": "duplicate@example.com", "source": "form"},
        headers=AGENT,
    )
    assert queued.status_code == 202, queued.text
    _approve_then_crash_before_dispatch(client, monkeypatch, queued.json()["id"])
    approved = _approved(client, queued.json()["id"])[0]
    duplicate_id = approved["result"]["id"]

    merged = client.post(
        "/api/leads/merge",
        json={"primary_id": primary["id"], "duplicate_id": duplicate_id},
    )
    assert merged.status_code == 200, merged.text
    calls = []
    monkeypatch.setattr(
        hooks,
        "on_lead_created",
        lambda lead, **_kwargs: calls.append(lead)
        or hooks.HookOutcome.LIVE_DELIVERED,
    )

    outbox_id = _outbox_rows()[0]["id"]
    assert hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0) is False

    cancelled = _outbox_rows()[0]
    assert cancelled["status"] == "cancelled"
    assert "lead no longer exists" in cancelled["last_error"]
    assert calls == []
    assert hook_outbox.drain_hook_outbox(retry_base_seconds=0) == 0
