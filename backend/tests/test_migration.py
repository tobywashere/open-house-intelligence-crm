import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ importable

from conftest import TEST_DB


def _fresh_legacy_db(tmp_path, monkeypatch, name="legacy.db"):
    """Point app.db at a throwaway DB file with the current schema applied,
    but nothing else — callers hand-write legacy-shaped rows directly
    (bypassing the app's own naive-local write path) before calling
    db.init_db() to exercise the backfill."""
    import app.db as db

    db_path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(db.SCHEMA_PATH.read_text())
    conn.commit()
    return db, conn


def _sqlite_local(z_ts: str) -> str:
    # Compute the expected converted instant the same way SQLite does, so
    # assertions don't depend on the test host's TZ either.
    return sqlite3.connect(":memory:").execute(
        "select strftime('%Y-%m-%dT%H:%M:%S', datetime(?, 'localtime'))", (z_ts,),
    ).fetchone()[0]


@pytest.fixture
def non_utc_local_timezone():
    """Run timezone-sensitive assertions in a known non-UTC locale."""
    if not hasattr(time, "tzset"):
        pytest.skip("process timezone changes require time.tzset()")

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        yield
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_gcal_columns_migrated(client):
    conn = sqlite3.connect(TEST_DB)
    appt_cols = {r[1] for r in conn.execute("PRAGMA table_info(appointments)")}
    rem_cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
    conn.close()
    assert "gcal_event_id" in appt_cols
    assert "gcal_event_id" in rem_cols


def test_hook_outbox_schema_is_additive_and_idempotent(tmp_path, monkeypatch):
    import app.db as db

    db_path = tmp_path / "pre-hook-outbox.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE hook_outbox ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "pending_change_id INTEGER NOT NULL UNIQUE,"
        "idempotency_key TEXT NOT NULL UNIQUE,"
        "hook_type TEXT NOT NULL, object_id INTEGER NOT NULL, lead_id INTEGER,"
        "status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,"
        "last_error TEXT, claim_token TEXT, claimed_at TEXT,"
        "created_at TEXT NOT NULL DEFAULT '2026-08-01T00:00:00',"
        "updated_at TEXT NOT NULL DEFAULT '2026-08-01T00:00:00', delivered_at TEXT)"
    )
    conn.execute(
        "INSERT INTO hook_outbox "
        "(pending_change_id, idempotency_key, hook_type, object_id) "
        "VALUES (1, 'pending-change:1', 'reminder_created', 1)"
    )
    conn.commit()
    conn.close()

    db.init_db()
    db.init_db()

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(hook_outbox)")}
    indexes = conn.execute("PRAGMA index_list(hook_outbox)").fetchall()
    legacy = conn.execute(
        "SELECT delivery_mode, next_attempt_at FROM hook_outbox WHERE id = 1"
    ).fetchone()
    conn.close()

    assert {
        "pending_change_id",
        "idempotency_key",
        "hook_type",
        "object_id",
        "lead_id",
        "delivery_mode",
        "status",
        "attempts",
        "last_error",
        "claim_token",
        "claimed_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
        "delivered_at",
    } <= cols
    assert any(index[2] for index in indexes), "outbox must enforce a unique key"
    assert legacy == ("simulated", None)


def test_hook_outbox_status_migration_preserves_rows_and_allows_terminal_states(
    tmp_path, monkeypatch
):
    import app.db as db

    db_path = tmp_path / "pre-cancelled-status.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE hook_outbox ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "pending_change_id INTEGER NOT NULL UNIQUE,"
        "idempotency_key TEXT NOT NULL UNIQUE,"
        "hook_type TEXT NOT NULL CHECK (hook_type IN "
        "('lead_created','tour_booked','reminder_created')),"
        "object_id INTEGER NOT NULL, lead_id INTEGER,"
        "delivery_mode TEXT NOT NULL DEFAULT 'simulated' CHECK (delivery_mode IN "
        "('live','simulated')),"
        "status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN "
        "('pending','processing','failed','delivered')),"
        "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, claim_token TEXT,"
        "claimed_at TEXT, next_attempt_at TEXT,"
        "created_at TEXT NOT NULL DEFAULT '2026-08-01T00:00:00',"
        "updated_at TEXT NOT NULL DEFAULT '2026-08-01T00:00:00', delivered_at TEXT)"
    )
    conn.execute(
        "INSERT INTO hook_outbox "
        "(id, pending_change_id, idempotency_key, hook_type, object_id, "
        "delivery_mode, status, attempts, last_error) "
        "VALUES (7, 11, 'pending-change:11', 'reminder_created', 42, "
        "'live', 'failed', 3, 'temporary failure')"
    )
    conn.commit()
    conn.close()

    db.init_db()
    db.init_db()

    conn = sqlite3.connect(db_path)
    preserved = conn.execute(
        "SELECT id, pending_change_id, status, attempts, last_error "
        "FROM hook_outbox WHERE id = 7"
    ).fetchone()
    conn.execute("UPDATE hook_outbox SET status = 'cancelled' WHERE id = 7")
    conn.execute("UPDATE hook_outbox SET status = 'exhausted' WHERE id = 7")
    conn.commit()
    migrated = conn.execute(
        "SELECT status FROM hook_outbox WHERE id = 7"
    ).fetchone()[0]
    conn.close()

    assert preserved == (7, 11, "failed", 3, "temporary failure")
    assert migrated == "exhausted"


def test_pending_change_dedupe_key_migration_is_additive(tmp_path, monkeypatch):
    import app.db as db

    db_path = tmp_path / "pre-pending-dedupe.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE pending_changes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, lead_id INTEGER,"
        "payload TEXT NOT NULL, summary TEXT NOT NULL,"
        "status TEXT NOT NULL DEFAULT 'pending', result TEXT, deny_reason TEXT,"
        "created_at TEXT NOT NULL DEFAULT '2026-08-01T00:00:00', decided_at TEXT)"
    )
    conn.execute(
        "INSERT INTO pending_changes (operation, lead_id, payload, summary) "
        "VALUES ('update_lead', 3, '{}', 'Existing proposal')"
    )
    conn.commit()
    conn.close()

    db.init_db()
    db.init_db()

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_changes)")}
    existing = conn.execute(
        "SELECT operation, summary, dedupe_key FROM pending_changes WHERE id = 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO pending_changes "
        "(operation, lead_id, payload, summary, dedupe_key) VALUES (?,?,?,?,?)",
        ("update_lead", 3, "{}", "First", "lead-process:3:event:9"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pending_changes "
            "(operation, lead_id, payload, summary, dedupe_key) VALUES (?,?,?,?,?)",
            ("update_lead", 3, "{}", "Duplicate", "lead-process:3:event:9"),
        )
    conn.close()

    assert "dedupe_key" in columns
    assert existing == ("update_lead", "Existing proposal", None)


def test_legacy_leads_table_gains_outcome_columns(tmp_path, monkeypatch):
    import app.db as db

    db_path = tmp_path / "pre-outcomes.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE leads ("
        "id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,"
        "created_at TEXT, last_activity_at TEXT)"
    )
    conn.execute(
        "INSERT INTO leads VALUES "
        "(1, 'Legacy closed', 'closed', '2026-07-01T10:00:00', '2026-07-02T10:00:00')"
    )
    conn.commit()
    conn.close()

    db.init_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
    legacy = conn.execute("SELECT * FROM leads WHERE id = 1").fetchone()
    conn.close()
    assert {"outcome", "close_reason"} <= columns
    assert legacy["outcome"] is None
    assert legacy["close_reason"] is None


def test_legacy_timestamps_backfilled_to_naive_local(
    tmp_path, monkeypatch, non_utc_local_timezone
):
    """A pre-Task-7 DB has Z-suffixed UTC rows (old DEFAULT) and legacy
    space-separated naive rows sitting next to each other. init_db()'s
    backfill (app.db._migrate_timestamps) must normalize every one of them
    to naive-local T-form, converting (not stripping) the Z rows so the
    instant they represent is preserved, and re-running init_db() a second
    time must be a no-op (idempotent)."""
    db, conn = _fresh_legacy_db(tmp_path, monkeypatch)
    db_path = db.DB_PATH
    conn.execute(
        "INSERT INTO leads (id, name, created_at, last_activity_at) VALUES "
        "(1, 'Legacy Z', '2026-07-27T00:04:27Z', '2026-07-27T00:04:27Z')"
    )
    conn.execute(
        "INSERT INTO leads (id, name, created_at, last_activity_at) VALUES "
        "(2, 'Legacy space', '2026-07-06 18:16:11', '2026-07-06 18:16:11')"
    )
    conn.execute(
        "INSERT INTO reminders (lead_id, due_ts, created_at) VALUES "
        "(1, '2026-07-25T09:00:00Z', '2026-07-25T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO audit_log (ts, actor, tool) VALUES "
        "('2026-07-25T09:00:00Z', 'agent', 'x')"
    )
    conn.commit()
    conn.close()

    expected_z_converted = _sqlite_local("2026-07-27T00:04:27Z")
    assert not expected_z_converted.endswith("Z")
    # The conversion must actually move the clock (proves it's astimezone,
    # not a strip) — same digits would mean this assertion is a no-op.
    assert expected_z_converted != "2026-07-27T00:04:27"
    expected_reminder_due = _sqlite_local("2026-07-25T09:00:00Z")

    db.init_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM leads")}
    assert rows[1]["created_at"] == expected_z_converted
    assert rows[1]["last_activity_at"] == expected_z_converted
    assert rows[2]["created_at"] == "2026-07-06T18:16:11"  # T-form, same instant (no shift)
    assert rows[2]["last_activity_at"] == "2026-07-06T18:16:11"

    reminder = conn.execute("SELECT * FROM reminders").fetchone()
    assert reminder["due_ts"] == expected_reminder_due
    audit_row = conn.execute("SELECT * FROM audit_log").fetchone()

    for val in (reminder["due_ts"], reminder["created_at"], audit_row["ts"]):
        assert not val.endswith("Z")
        assert " " not in val

    conn.close()

    # Idempotent: running the backfill again must not change anything further.
    db.init_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows_again = {r["id"]: r for r in conn.execute("SELECT * FROM leads")}
    conn.close()
    assert rows_again[1]["created_at"] == expected_z_converted
    assert rows_again[2]["created_at"] == "2026-07-06T18:16:11"


def test_migration_skips_unparseable_row_instead_of_crashing(tmp_path, monkeypatch):
    """A garbage value ending in a (lowercase) z is a candidate for the
    Z-suffix branch since SQLite LIKE is ASCII-case-insensitive, but
    datetime() can't parse it. Without a NOT NULL guard, the UPDATE would
    set the column to NULL and immediately violate its NOT NULL constraint
    — raising IntegrityError on every future init_db() call, i.e. bricking
    startup permanently. Reachable in practice: pre-Task-7 `ReminderIn` had
    no validator, so arbitrary client strings could land in due_ts."""
    db, conn = _fresh_legacy_db(tmp_path, monkeypatch)
    lead = conn.execute(
        "INSERT INTO leads (name, created_at, last_activity_at) VALUES "
        "('Garbage', 'not-a-real-timestampz', 'not-a-real-timestampz')"
    )
    lead_id = lead.lastrowid
    conn.execute(
        "INSERT INTO reminders (lead_id, due_ts) VALUES (?, 'also garbage Z')",
        (lead_id,),
    )
    conn.commit()
    conn.close()

    db.init_db()  # must not raise

    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    reminder = conn.execute("SELECT * FROM reminders WHERE lead_id = ?", (lead_id,)).fetchone()
    conn.close()

    # Left untouched, not nulled, not crashed.
    assert row["created_at"] == "not-a-real-timestampz"
    assert row["last_activity_at"] == "not-a-real-timestampz"
    assert reminder["due_ts"] == "also garbage Z"


def test_migration_converts_offset_suffixed_legacy_rows(tmp_path, monkeypatch):
    """A row stored with an explicit numeric UTC offset (e.g. from an
    unvalidated pre-Task-7 client write) isn't `Z`-suffixed, so the
    Z-only predicate leaves it untouched — it still breaks string
    comparisons the same way Z rows did. Must be converted the same way."""
    db, conn = _fresh_legacy_db(tmp_path, monkeypatch)
    lead = conn.execute(
        "INSERT INTO leads (name, created_at, last_activity_at) VALUES "
        "('Offset', '2026-07-27T00:04:27+00:00', '2026-07-27T00:04:27+00:00')"
    )
    lead_id = lead.lastrowid
    conn.commit()
    conn.close()

    expected = _sqlite_local("2026-07-27T00:04:27+00:00")
    assert not expected.endswith("+00:00")

    db.init_db()
    db.init_db()  # idempotent: a second run must not change it further

    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    assert row["created_at"] == expected
    assert row["last_activity_at"] == expected


def test_migration_space_branch_does_not_mangle_trailing_text(tmp_path, monkeypatch):
    """The space->T reformat must replace only the single date/time
    delimiter (position 11), not every space in the string — a value like
    '2026-07-06 18:16:11 PDT' must not become '...T18:16:11TPDT'. Since
    that shape isn't cleanly parseable as naive local either (the trailing
    zone abbreviation isn't valid to datetime()), the guard also means it's
    left untouched rather than partially transformed."""
    db, conn = _fresh_legacy_db(tmp_path, monkeypatch)
    lead = conn.execute(
        "INSERT INTO leads (name, created_at, last_activity_at) VALUES "
        "('Trailing zone', '2026-07-06 18:16:11 PDT', '2026-07-06 18:16:11')"
    )
    lead_id = lead.lastrowid
    conn.commit()
    conn.close()

    db.init_db()

    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()

    # Untouched: not mangled into '...T18:16:11TPDT', and not silently
    # truncated either.
    assert row["created_at"] == "2026-07-06 18:16:11 PDT"
    # The clean sibling column (no trailing garbage) still converts normally.
    assert row["last_activity_at"] == "2026-07-06T18:16:11"
