from app.db import get_conn
from app.integrations import composio_client


def test_avg_response_minutes_null_with_no_qualifying_leads(client, monkeypatch):
    monkeypatch.setattr(composio_client, "_REQUEST_COUNT", 0)
    client.post("/api/leads", json={"name": "M", "source": "note"})
    m = client.get("/api/metrics").json()
    assert m["avg_response_minutes"] is None
    assert m["cloud_llm_requests"] == 0  # off mode makes no calls


def test_avg_response_minutes_computed_from_seeded_deltas(client, monkeypatch):
    monkeypatch.setattr(composio_client, "_REQUEST_COUNT", 0)

    lead_a = client.post("/api/leads", json={"name": "A", "source": "note"}).json()
    client.post(f"/api/leads/{lead_a['id']}/events", json={"type": "text", "content": "hi"})
    lead_b = client.post("/api/leads", json={"name": "B", "source": "note"}).json()
    client.post(f"/api/leads/{lead_b['id']}/events", json={"type": "text", "content": "hi"})
    # C has no events — must not count toward the average.
    client.post("/api/leads", json={"name": "C", "source": "note"})

    with get_conn() as conn:
        conn.execute("UPDATE leads SET created_at = ? WHERE id = ?",
                     ("2026-01-01T10:00:00", lead_a["id"]))
        conn.execute("UPDATE events SET created_at = ? WHERE lead_id = ?",
                     ("2026-01-01T10:05:00", lead_a["id"]))  # +5 min
        conn.execute("UPDATE leads SET created_at = ? WHERE id = ?",
                     ("2026-01-01T10:00:00", lead_b["id"]))
        conn.execute("UPDATE events SET created_at = ? WHERE lead_id = ?",
                     ("2026-01-01T10:15:00", lead_b["id"]))  # +15 min

    m = client.get("/api/metrics").json()
    assert m["avg_response_minutes"] == 10.0  # mean of 5 and 15, rounded server-side to 1dp
    assert m["cloud_llm_requests"] == 0


def test_cloud_llm_requests_reflects_real_counter(client, monkeypatch):
    monkeypatch.setattr(composio_client, "_REQUEST_COUNT", 3)
    m = client.get("/api/metrics").json()
    assert m["cloud_llm_requests"] == 3
