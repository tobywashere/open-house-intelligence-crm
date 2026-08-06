import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", BACKEND_DIR / "data" / "crm.db"))
SCHEMA_PATH = BACKEND_DIR / "schema.sql"

JSON_FIELDS = {"preferences", "missing_fields"}


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """`with get_conn() as conn:` — one BEGIN IMMEDIATE transaction per block:
    commits on success, rolls back on error, and ALWAYS closes. sqlite3's own
    connection context manager does not close, which leaked an fd per request
    until the process hit its NOFILE limit.

    Every block holds the exclusive write lock for its full duration and
    other writers serialize behind it on busy_timeout — so callers MUST NOT
    do slow work (network calls, `await`s that can take seconds) inside a
    `with get_conn()` block. Do that work first/after and keep the block to
    just the DB reads/writes."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # autocommit off at the driver level: WE own transaction boundaries so a
    # read-check + write (e.g. conflict check -> INSERT) is one atomic unit.
    # BEGIN IMMEDIATE takes the write lock up front; a concurrent writer blocks
    # on busy_timeout instead of both reading an empty calendar and double-booking.
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    # journal_mode is WAL for read throughput (readers never block on a writer's
    # WAL frames), but every writer here takes the same exclusive BEGIN
    # IMMEDIATE lock and queues behind it up to busy_timeout — writers are
    # fully serialized, not concurrent.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Runs on its own plain (non-transactional) connection, not get_conn():
    conn.executescript() implicitly COMMITs any open transaction before it
    runs (sqlite3 stdlib behavior), which would silently end get_conn()'s
    BEGIN IMMEDIATE early and make its closing COMMIT/ROLLBACK a lie. Schema
    creation only ever runs once at startup, single-threaded — it doesn't
    need get_conn()'s atomicity guarantee."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate(conn)
        _migrate_timestamps(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column backfill for DBs created before a column existed —
    lets an existing GB10/demo DB pick up new fields without a reseed."""
    # Additive TABLE too: an install predating the settings table gets it here,
    # since init_db()'s executescript only creates what schema.sql declares at
    # first run. Mirrors the CREATE in schema.sql exactly.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        " key TEXT PRIMARY KEY,"
        " payload TEXT NOT NULL,"
        " updated_at TEXT NOT NULL DEFAULT"
        " (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending_changes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " operation TEXT NOT NULL,"
        " lead_id INTEGER,"
        " payload TEXT NOT NULL,"
        " summary TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " result TEXT,"
        " deny_reason TEXT,"
        " created_at TEXT NOT NULL DEFAULT"
        " (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),"
        " decided_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hook_outbox ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " pending_change_id INTEGER NOT NULL UNIQUE,"
        " idempotency_key TEXT NOT NULL UNIQUE,"
        " hook_type TEXT NOT NULL CHECK (hook_type IN"
        " ('lead_created','tour_booked','reminder_created')),"
        " object_id INTEGER NOT NULL,"
        " lead_id INTEGER,"
        " delivery_mode TEXT NOT NULL DEFAULT 'simulated' CHECK (delivery_mode IN"
        " ('live','simulated')),"
        " status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN"
        " ('pending','processing','failed','delivered')),"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " last_error TEXT,"
        " claim_token TEXT,"
        " claimed_at TEXT,"
        " next_attempt_at TEXT,"
        " created_at TEXT NOT NULL DEFAULT"
        " (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),"
        " updated_at TEXT NOT NULL DEFAULT"
        " (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),"
        " delivered_at TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hook_outbox_delivery "
        "ON hook_outbox (status, claimed_at, id)"
    )
    outbox_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(hook_outbox)")
    }
    if "delivery_mode" not in outbox_cols:
        # Existing rows predate explicit intent metadata. Default them to the
        # safe simulated mode so an upgrade cannot unexpectedly contact a
        # provider for an intent created while integrations were off.
        conn.execute(
            "ALTER TABLE hook_outbox ADD COLUMN delivery_mode TEXT NOT NULL "
            "DEFAULT 'simulated' CHECK (delivery_mode IN ('live','simulated'))"
        )
    if "next_attempt_at" not in outbox_cols:
        conn.execute("ALTER TABLE hook_outbox ADD COLUMN next_attempt_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hook_outbox_retry "
        "ON hook_outbox (status, next_attempt_at, claimed_at, id)"
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    for col in ("persona", "relationship_summary", "close_reason"):
        if col not in cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    if "outcome" not in cols:
        conn.execute(
            "ALTER TABLE leads ADD COLUMN outcome TEXT "
            "CHECK (outcome IN ('won','lost'))"
        )
    for table in ("appointments", "reminders"):
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "gcal_event_id" not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN gcal_event_id TEXT")


# Every timestamp column in schema.sql (Task 7's one convention: naive local
# wall-clock). Keep in sync with
# schema.sql — availability has no timestamp columns (start_time/end_time
# are HH:MM only) so it's absent on purpose.
TIMESTAMP_COLUMNS = {
    "leads": ["created_at", "last_activity_at"],
    "events": ["created_at"],
    "appointments": ["start_ts", "end_ts", "created_at"],
    "reminders": ["due_ts", "created_at"],
    "audit_log": ["ts"],
    "chat_messages": ["created_at"],
    "briefing": ["generated_at"],
    "insights": ["computed_at"],
    "daily_summary": ["generated_at"],
    "hook_outbox": [
        "claimed_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
        "delivered_at",
    ],
}


def _migrate_timestamps(conn: sqlite3.Connection) -> None:
    """One-shot, idempotent backfill: normalizes every row written before
    Task 7 (naive-local-everywhere) to the new convention. Changing a
    column's DEFAULT only affects rows inserted after the change — an
    existing GB10/demo DB is left with old aware-UTC rows sitting next to
    new naive-local rows. That mix is silently wrong: for the same
    wall-clock instant, the aware string sorts AFTER the naive-local string
    (e.g. '2026-07-27T00:04:27Z' > '2026-07-06T18:16:11'), so old rows read
    as hours "newer" than they are — due reminders fire late, the neglect
    check misfires by a timezone, free_slots' range filter mis-includes rows.

    Runs on every boot (init_db() does), full-scanning all 13 configured
    columns — fine at demo/GB10 scale; a real-scale deployment would want a
    schema-version marker to skip this once converged.

    Three legacy shapes get fixed, per column:
      1. `...Z` (aware UTC) -> converted (not stripped) to naive local, via
         SQLite's own datetime(): the actual instant is preserved.
      2. `...+HH:MM` / `...-HH:MM` (aware with an explicit numeric offset,
         e.g. from an unvalidated pre-Task-7 client write) -> same
         conversion; datetime() parses numeric offsets natively.
      3. `YYYY-MM-DD HH:MM:SS` (space-separated, already-naive-local from an
         older code path) -> reformatted to the `T`-separated form only, by
         replacing just the date/time delimiter (position 11) rather than
         every space in the string — a value like
         '2026-07-06 18:16:11 PDT' must become
         '2026-07-06T18:16:11 PDT', not '...T18:16:11TPDT'. No zone shift,
         since it was already local wall-clock.

    Every UPDATE's WHERE clause also requires
    `datetime(col,'localtime') IS NOT NULL` before touching a row: SQLite's
    LIKE is ASCII-case-insensitive ('junkz' LIKE '%Z' is true), and
    `datetime()` on anything it can't parse returns NULL — without this
    guard, an unparseable garbage value (reachable pre-Task-7, when
    ReminderIn had no validator and stored whatever a client sent) would get
    UPDATEd to NULL and immediately violate the column's NOT NULL
    constraint, raising IntegrityError. Because init_db() runs on every
    startup, that's not a one-time failure — it's a permanent boot-failure
    loop until someone hand-edits the row. The guard makes unparseable rows
    a no-op (left as-is) instead of a crash.

    All UPDATEs are no-ops on a second run: after conversion no row matches
    any of the WHERE clauses anymore.
    """
    for table, cols in TIMESTAMP_COLUMNS.items():
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col not in tcols:
                continue  # column itself predates this DB; _migrate() above adds it first
            # Aware: Z-suffixed or explicit numeric offset -> converted to naive local.
            conn.execute(
                f"UPDATE {table} SET {col} = "
                f"strftime('%Y-%m-%dT%H:%M:%S', datetime({col}, 'localtime')) "
                f"WHERE ({col} LIKE '%Z' OR {col} GLOB '*[+-][0-9][0-9]:[0-9][0-9]') "
                f"AND datetime({col}, 'localtime') IS NOT NULL"
            )
            # Legacy space-separated naive -> T-separated, delimiter only (not every space).
            conn.execute(
                f"UPDATE {table} SET {col} = substr({col}, 1, 10) || 'T' || substr({col}, 12) "
                f"WHERE {col} LIKE '____-__-__ __:__:__%' "
                f"AND {col} NOT LIKE '%Z' AND {col} NOT GLOB '*[+-][0-9][0-9]:[0-9][0-9]' "
                f"AND datetime({col}, 'localtime') IS NOT NULL"
            )


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in JSON_FIELDS & d.keys():
        try:
            d[f] = json.loads(d[f]) if d[f] else []
        except (TypeError, json.JSONDecodeError):
            d[f] = []
    return d


def audit(conn: sqlite3.Connection, actor: str, tool: str,
          input_: dict | None = None, output: dict | None = None,
          lead_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, tool, input, output, lead_id) VALUES (?,?,?,?,?)",
        (actor, tool, json.dumps(input_ or {}), json.dumps(output or {}), lead_id),
    )
