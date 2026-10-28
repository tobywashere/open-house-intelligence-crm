import os
import sys
from pathlib import Path

import pytest

HUMAN_TOKEN = 'test-human-' + 'h' * 40
AGENT_TOKEN = 'test-agent-' + 'a' * 40

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ importable

TEST_DB = Path(__file__).resolve().parent / "test.db"
os.environ["DB_PATH"] = str(TEST_DB)          # must be set BEFORE importing app
os.environ["AGENT_MODE"] = "mock"
os.environ["INTEGRATIONS_MODE"] = "off"   # hard-set: ambient live env must not leak into tests
os.environ['OHI_DEMO_MODE'] = '1'  # Tests explicitly opt into the synthetic demo.
os.environ.pop("COMPOSIO_API_KEY", None)  # scrub ambient key to prevent real API calls

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def client():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB) + suffix)
        if p.exists():
            p.unlink()
    with TestClient(app, client=('127.0.0.1', 50000), base_url='http://localhost') as c:  # startup event runs init_db()
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


@pytest.fixture()
def client_factory():
    """Returns a callable that builds a fresh TestClient(app) on demand, so
    tests can monkeypatch env vars (e.g. OHI_API_TOKEN) before the client —
    and the middleware that reads them per-request — comes into play."""
    made = []

    def _make():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        c = TestClient(app, client=('127.0.0.1', 50000), base_url='http://localhost')
        c.__enter__()  # run startup event (init_db())
        made.append(c)
        return c

    yield _make
    for c in made:
        c.__exit__(None, None, None)


@pytest.fixture()
def capability_client(monkeypatch, client_factory):
    """Integration tests authenticate as a human; agents never share this key."""
    monkeypatch.setenv('OHI_API_TOKEN', HUMAN_TOKEN)
    monkeypatch.setenv('OHI_AGENT_API_TOKEN', AGENT_TOKEN)
    c = client_factory()
    c.headers['X-API-Token'] = HUMAN_TOKEN
    return c


def queued_automation(operation, payload):
    """Seed a real automation proposal, like the mailbox poller, without HTTP spoofing."""
    import httpx
    from app.approvals import queue_pending_change
    result = queue_pending_change(operation, payload.get('lead_id'), payload, 'Test automation proposal')
    return httpx.Response(result.status_code, content=result.body)


def make_lead(client, **overrides) -> dict:
    body = {"name": "Test Lead", "email": "lead@example.com",
            "phone": "+14255550100", "area": "Bellevue", "budget": 900000,
            "timeline": "6 weeks", "source": "note"}
    body.update(overrides)
    res = client.post("/api/leads", json=body)
    assert res.status_code == 200, res.text
    return res.json()
