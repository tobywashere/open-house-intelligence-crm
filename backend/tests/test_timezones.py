"""One timestamp convention end to end: naive local wall-clock at every API
boundary. Aware inputs get CONVERTED to local then made naive — never
stripped. `parse_ts` is exercised under a TZ explicitly pinned away from
whatever the host/CI defaults to (CI runs UTC), so the assertion is
meaningful everywhere, not just on a Pacific dev machine.
"""
import time

import pytest

from datetime import datetime

from app.calendar_adapter.local_calendar import parse_ts


@pytest.fixture()
def pacific_tz(monkeypatch):
    """Pin TZ to America/Los_Angeles for the duration of the test, regardless
    of the host's default (CI runs UTC, where an unpinned test would be a
    silent no-op since UTC-aware and UTC-naive-stripped happen to match)."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    time.tzset()  # restore host TZ after monkeypatch un-sets the env var


def test_parse_ts_converts_aware_to_local_not_strips(pacific_tz):
    utc = "2026-07-28T17:00:00Z"
    local = datetime.fromisoformat("2026-07-28T17:00:00+00:00").astimezone().replace(tzinfo=None)
    # Sanity: with TZ pinned to Pacific, "converted" and "stripped" must
    # actually differ — otherwise this test would pass even with the old,
    # buggy strip-only implementation.
    assert local != datetime.fromisoformat("2026-07-28T17:00:00")
    assert parse_ts(utc) == local


def test_parse_ts_naive_input_still_passes_through(pacific_tz):
    # Naive input (already local, no tzinfo) is returned unchanged.
    assert parse_ts("2026-07-28T09:00:00") == datetime(2026, 7, 28, 9, 0, 0)


def test_reminder_rejects_garbage_due_ts(client):
    lead = client.post("/api/leads", json={"name": "T", "source": "note"}).json()
    r = client.post("/api/reminders",
                     json={"lead_id": lead["id"], "due_ts": "not a date", "note": "x"})
    assert r.status_code == 422


def test_reminder_accepts_aware_due_ts_and_normalizes_to_local(pacific_tz, client):
    lead = client.post("/api/leads", json={"name": "T2", "source": "note"}).json()
    r = client.post("/api/reminders", json={
        "lead_id": lead["id"], "due_ts": "2026-07-28T17:00:00Z", "note": "x",
    })
    assert r.status_code == 200
    body = r.json()
    # Stored value is naive local wall-clock, not the raw UTC string.
    assert body["due_ts"] == "2026-07-28T10:00:00"
