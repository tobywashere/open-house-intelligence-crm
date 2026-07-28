"""One timestamp convention end to end: naive local wall-clock at every API
boundary. Aware inputs get CONVERTED to local then made naive — never
stripped. `parse_ts` is exercised under a TZ explicitly pinned away from
whatever the host/CI defaults to (CI runs UTC), so the assertion is
meaningful everywhere, not just on a Pacific dev machine.
"""
import contextlib
import os
import time

import pytest

from datetime import datetime

from app.calendar_adapter.local_calendar import parse_ts


@contextlib.contextmanager
def _pinned_tz(tz: str):
    """Pin TZ to `tz` for the duration of the `with` block, restoring the
    host's original TZ (env var + libc tzset() state) on exit — including
    the un-set case (host had no TZ env var at all).

    Deliberately does NOT use `monkeypatch.setenv`: a `monkeypatch`-dependent
    fixture tears down BEFORE monkeypatch itself restores the env var
    (pytest tears fixtures down in reverse dependency order), so a final
    `time.tzset()` in that fixture's body would run while TZ is still the
    pinned value, re-pinning libc's tzset() state to it and never re-reading
    the host's real TZ for the rest of the test session — the exact
    restoration bug this contextmanager exists to avoid. Save/restore the
    env var ourselves, in our own finalizer, so tzset() is always the last
    thing to run and always runs against the value we intend."""
    original = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


@pytest.fixture()
def pacific_tz():
    """Pin TZ to America/Los_Angeles for the duration of the test, regardless
    of the host's default (CI runs UTC, where an unpinned test would be a
    silent no-op since UTC-aware and UTC-naive-stripped happen to match)."""
    with _pinned_tz("America/Los_Angeles"):
        yield


def test_pinned_tz_restores_host_tz_on_exit():
    """Proves _pinned_tz's finalizer actually restores state (the bug this
    guards against: TZ silently left pinned to Pacific for every later
    test), independent of whatever the pacific_tz-using tests above do.

    Pins to Tokyo, not Pacific — this dev box's own host TZ already IS
    Pacific, which would make a before/after `time.tzname` comparison a
    false pass even with the original bug (tzset() never re-called after
    env restore just happens to leave the same PST/PDT tzname behind)."""
    before_env = os.environ.get("TZ")
    before_tzname = time.tzname

    with _pinned_tz("Asia/Tokyo"):
        assert os.environ.get("TZ") == "Asia/Tokyo"
        assert time.tzname == ("JST", "JST")

    assert os.environ.get("TZ") == before_env
    assert time.tzname == before_tzname
    assert time.tzname != ("JST", "JST")  # would false-pass if tzset() weren't re-run


def test_pinned_tz_tzset_is_last_operation_in_finalizer(monkeypatch):
    """Directly proves the finalizer's operation ORDER — env restored, then
    tzset() re-run against the restored value — rather than inferring it
    from tzname, which can coincidentally match on a Pacific host even with
    the buggy order (restore env, call tzset() before that, or not at all)."""
    calls = []
    real_tzset = time.tzset
    monkeypatch.setattr(time, "tzset", lambda: calls.append(os.environ.get("TZ")))

    with _pinned_tz("Asia/Tokyo"):
        pass

    real_tzset()  # actually restore libc state now that the spy is done
    # First call: entering the pin, TZ already set to Tokyo. Second call:
    # the finalizer's tzset(), which must run AFTER TZ was put back — i.e.
    # its recorded env value must NOT be "Asia/Tokyo".
    assert len(calls) == 2
    assert calls[0] == "Asia/Tokyo"
    assert calls[1] != "Asia/Tokyo"


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
