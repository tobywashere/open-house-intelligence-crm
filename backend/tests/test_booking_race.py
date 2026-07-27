"""Booking must be atomic: N concurrent identical bookings -> exactly 1 appointment.

Uses a live uvicorn server (TestClient serializes requests in-process, which
would mask the race) and real threads."""
import threading

import httpx
import pytest
from .live_server import live_server, seeded_lead  # see Step 2


def test_concurrent_bookings_yield_one_appointment(live_server, seeded_lead):
    url = f"{live_server}/api/appointments"
    body = {"lead_id": seeded_lead, "start_ts": "2026-08-03T18:00:00",
            "end_ts": "2026-08-03T18:45:00", "location": "123 Main St"}
    results = []
    barrier = threading.Barrier(8)

    def book():
        barrier.wait()
        r = httpx.post(url, json=body, timeout=10)
        results.append(r.status_code)

    threads = [threading.Thread(target=book) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(results).count(200) == 1, f"expected exactly one 200, got {results}"
    assert all(code in (200, 409) for code in results)
    appts = httpx.get(f"{live_server}/api/appointments").json()
    assert len([a for a in appts if a["start_ts"].startswith("2026-08-03T18:00")]) == 1
