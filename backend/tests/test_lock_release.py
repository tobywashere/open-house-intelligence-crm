"""Regression for the Critical fix (Task 1 review round 1): get_conn() now
holds an exclusive BEGIN IMMEDIATE write lock for its whole block, so any
Composio network call (15-30s in live mode) made while a get_conn() block is
still open would serialize every other writer behind it on busy_timeout and
eventually 500 them with "database is locked". These tests assert cc.execute
always runs with zero get_conn() blocks open, across every call site that
was fixed: hooks.py (lead-created gcal call-block + gmail draft, tour-booked
gcal event, reminder-created gcal event) and calendar.py (availability's
free/busy lookup)."""
from contextlib import contextmanager

from .conftest import make_lead


def _track(monkeypatch, target: str):
    """Patch `target` (a "module.get_conn" dotted path) with a wrapper that
    records the current nesting depth, returning a list the caller can read
    depths from via the returned recorder + the real get_conn's module."""
    import importlib
    module_path, _, _ = target.rpartition(".")
    module = importlib.import_module(module_path)
    real_get_conn = module.get_conn
    depth = {"n": 0}

    @contextmanager
    def tracking_get_conn():
        depth["n"] += 1
        try:
            with real_get_conn() as conn:
                yield conn
        finally:
            depth["n"] -= 1

    monkeypatch.setattr(target, tracking_get_conn)
    return depth


def test_lead_created_hooks_release_lock_before_composio_call(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    depth = _track(monkeypatch, "app.integrations.hooks.get_conn")

    calls = []

    def fake_execute(slug, args):
        calls.append((slug, depth["n"]))
        return {"response_data": {"id": "evt-1"}}

    monkeypatch.setattr("app.integrations.hooks.cc.execute", fake_execute)

    make_lead(client)  # fires on_lead_created -> gcal call-block + gmail draft

    slugs = [c[0] for c in calls]
    assert "GOOGLECALENDAR_CREATE_EVENT" in slugs
    assert "GMAIL_CREATE_EMAIL_DRAFT" in slugs
    assert all(d == 0 for _, d in calls), (
        f"cc.execute ran while a get_conn() block was open: {calls}")


def test_tour_booked_hook_releases_lock_before_composio_call(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    depth = _track(monkeypatch, "app.integrations.hooks.get_conn")

    calls = []

    def fake_execute(slug, args):
        calls.append((slug, depth["n"]))
        return {"response_data": {"id": "evt-2"}}

    monkeypatch.setattr("app.integrations.hooks.cc.execute", fake_execute)

    lead = make_lead(client)
    res = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-07-28T12:00:00",
        "end_ts": "2026-07-28T12:45:00", "location": "123 Main St"})
    assert res.status_code == 200

    assert any(slug == "GOOGLECALENDAR_CREATE_EVENT" for slug, _ in calls)
    assert all(d == 0 for _, d in calls), (
        f"cc.execute ran while a get_conn() block was open: {calls}")


def test_reminder_created_hook_releases_lock_before_composio_call(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    depth = _track(monkeypatch, "app.integrations.hooks.get_conn")

    calls = []

    def fake_execute(slug, args):
        calls.append((slug, depth["n"]))
        return {"response_data": {"id": "evt-3"}}

    monkeypatch.setattr("app.integrations.hooks.cc.execute", fake_execute)

    lead = make_lead(client)
    res = client.post("/api/reminders", json={
        "lead_id": lead["id"], "due_ts": "2026-07-29T09:00:00", "note": "call back"})
    assert res.status_code == 200

    assert any(slug == "GOOGLECALENDAR_CREATE_EVENT" for slug, _ in calls)
    assert all(d == 0 for _, d in calls), (
        f"cc.execute ran while a get_conn() block was open: {calls}")


def test_availability_releases_lock_before_busy_query(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    depth = _track(monkeypatch, "app.routers.calendar.get_conn")

    calls = []

    def fake_execute(slug, args):
        calls.append((slug, depth["n"]))
        return {"response_data": {"calendars": {"primary": {"busy": []}}}}

    monkeypatch.setattr("app.integrations.poller.cc.execute", fake_execute)

    res = client.get("/api/availability?date=2026-11-20")
    assert res.status_code == 200

    assert calls, "busy_blocks() should have called cc.execute"
    assert all(d == 0 for _, d in calls), (
        f"cc.execute ran while a get_conn() block was open: {calls}")
