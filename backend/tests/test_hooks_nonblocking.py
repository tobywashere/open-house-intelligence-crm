import anyio
import time
from unittest.mock import patch

import httpx
import pytest


@pytest.mark.anyio
async def test_slow_hook_does_not_block_event_loop(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    from app.main import app
    from app.integrations import hooks

    def slow_hook(lead):
        time.sleep(2)

    with patch.object(hooks, "on_lead_created", side_effect=slow_hook):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            # t0 starts BEFORE the 0.3s settle sleep, not after: when the loop
            # is frozen by the blocking hook, that same `anyio.sleep(0.3)`
            # can't return until the freeze ends either, so a t0 taken after
            # it (as in the original brief draft) silently measures 0 --
            # the freeze already happened before t0 was ever stamped.
            t0 = time.monotonic()
            async with anyio.create_task_group() as tg:
                async def create():
                    await ac.post("/api/leads", json={"name": "Slow", "source": "note"})
                tg.start_soon(create)
                await anyio.sleep(0.3)          # let create reach the hook
                r = await ac.get("/api/health")
                elapsed = time.monotonic() - t0
        assert r.status_code == 200
        assert elapsed < 1.0, f"/health blocked {elapsed:.1f}s behind the hook"
