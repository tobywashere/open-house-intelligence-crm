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
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


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
