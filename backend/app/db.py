import json
import os
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", BACKEND_DIR / "data" / "crm.db"))
SCHEMA_PATH = BACKEND_DIR / "schema.sql"

JSON_FIELDS = {"preferences", "missing_fields"}


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the agent's tool calls write while dashboard reads are in flight
    # (default rollback journal throws "database is locked" under that overlap).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        _migrate(conn)


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
