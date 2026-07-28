import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ importable

from conftest import TEST_DB


def test_gcal_columns_migrated(client):
    conn = sqlite3.connect(TEST_DB)
    appt_cols = {r[1] for r in conn.execute("PRAGMA table_info(appointments)")}
    rem_cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
    conn.close()
    assert "gcal_event_id" in appt_cols
    assert "gcal_event_id" in rem_cols


def test_legacy_timestamps_backfilled_to_naive_local(tmp_path, monkeypatch):
    """A pre-Task-7 DB has Z-suffixed UTC rows (old DEFAULT) and legacy
    space-separated naive rows sitting next to each other. init_db()'s
    backfill (app.db._migrate_timestamps) must normalize every one of them
    to naive-local T-form, converting (not stripping) the Z rows so the
    instant they represent is preserved, and re-running init_db() a second
    time must be a no-op (idempotent)."""
    import app.db as db

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Build the DB with the current schema, then hand-write legacy-shaped
    # values directly (bypassing the app's own naive-local write path) to
    # simulate rows written before this task.
    conn = sqlite3.connect(db_path)
    conn.executescript(db.SCHEMA_PATH.read_text())
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

    def sqlite_local(z_ts: str) -> str:
        # Compute the expected converted instant the same way SQLite does,
        # so assertions don't depend on the test host's TZ either.
        return sqlite3.connect(":memory:").execute(
            "select strftime('%Y-%m-%dT%H:%M:%S', datetime(?, 'localtime'))", (z_ts,),
        ).fetchone()[0]

    expected_z_converted = sqlite_local("2026-07-27T00:04:27Z")
    assert not expected_z_converted.endswith("Z")
    # The conversion must actually move the clock (proves it's astimezone,
    # not a strip) — same digits would mean this assertion is a no-op.
    assert expected_z_converted != "2026-07-27T00:04:27"
    expected_reminder_due = sqlite_local("2026-07-25T09:00:00Z")

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
