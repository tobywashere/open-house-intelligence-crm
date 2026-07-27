from fastapi.testclient import TestClient


def _mk(client, **kw):
    body = {"name": "T", "source": "note", "status": "new"} | kw
    return client.post("/api/leads", json=body).json()


def test_self_merge_is_400(client):
    lead = _mk(client)
    r = client.post("/api/leads/merge",
                    json={"primary_id": lead["id"], "duplicate_id": lead["id"]})
    assert r.status_code == 400


def test_backward_status_transition_is_400(client):
    lead = _mk(client)
    client.patch(f"/api/leads/{lead['id']}", json={"status": "closed"})
    r = client.patch(f"/api/leads/{lead['id']}", json={"status": "new"})
    assert r.status_code == 400
    assert "closed" in r.json()["detail"] and "new" in r.json()["detail"]


def test_forward_transitions_ok(client):
    lead = _mk(client)
    for status in ("contacted", "meeting_booked", "closed"):
        assert client.patch(f"/api/leads/{lead['id']}",
                            json={"status": status}).status_code == 200
