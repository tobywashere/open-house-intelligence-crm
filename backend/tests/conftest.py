import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ importable

TEST_DB = Path(__file__).resolve().parent / "test.db"
os.environ["DB_PATH"] = str(TEST_DB)          # must be set BEFORE importing app
os.environ["AGENT_MODE"] = "mock"
os.environ["INTEGRATIONS_MODE"] = "off"   # hard-set: ambient live env must not leak into tests
os.environ.pop("COMPOSIO_API_KEY", None)  # scrub ambient key to prevent real API calls

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB) + suffix)
        if p.exists():
            p.unlink()
    with TestClient(app) as c:  # startup event runs init_db()
        # Seed availability windows for testing
        from app.db import get_conn
        with get_conn() as conn:
            for weekday in range(5):  # Mon-Fri 5pm-8pm
                conn.execute(
                    "INSERT INTO availability (weekday, start_time, end_time) VALUES (?,?,?)",
                    (weekday, "17:00", "20:00"))
            conn.execute(
                "INSERT INTO availability (weekday, start_time, end_time) VALUES (?,?,?)",
                (5, "10:00", "16:00"))  # Sat 10am-4pm
        yield c


def make_lead(client, **overrides) -> dict:
    body = {"name": "Test Lead", "email": "lead@example.com",
            "phone": "+14255550100", "area": "Bellevue", "budget": 900000,
            "timeline": "6 weeks", "source": "note"}
    body.update(overrides)
    res = client.post("/api/leads", json=body)
    assert res.status_code == 200, res.text
    return res.json()
