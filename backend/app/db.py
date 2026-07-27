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
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column backfill for DBs created before a column existed —
    lets an existing GB10/demo DB pick up new fields without a reseed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    for col in ("persona", "relationship_summary"):
        if col not in cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    for table in ("appointments", "reminders"):
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "gcal_event_id" not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN gcal_event_id TEXT")


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
