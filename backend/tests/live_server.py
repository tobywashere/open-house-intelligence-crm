"""Session fixture: uvicorn on a random port against a temp DB, for tests that
need real concurrency (TestClient runs requests in-process, serialized)."""
import socket
import threading
import time

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    db_path = tmp_path / "race.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MODE", "mock")
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    import uvicorn
    from app import db as db_module
    from app.main import app
    # app.db.DB_PATH is a module-level constant bound at first import (which
    # already happened via conftest.py before this fixture ever runs), so the
    # env var above only affects processes that import app.db fresh — it does
    # NOT retarget the already-bound attribute. Patch it directly so get_conn()
    # actually points at this test's isolated DB instead of the shared
    # tests/test.db every other test uses.
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture()
def seeded_lead(live_server):
    r = httpx.post(f"{live_server}/api/leads",
                   json={"name": "Race Test", "source": "note", "status": "new"})
    return r.json()["id"]
