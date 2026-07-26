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
