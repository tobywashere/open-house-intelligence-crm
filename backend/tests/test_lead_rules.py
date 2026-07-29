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
    client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "lost"})
    r = client.patch(f"/api/leads/{lead['id']}", json={"status": "new"})
    assert r.status_code == 400
    assert "closed" in r.json()["detail"] and "new" in r.json()["detail"]


def test_forward_transitions_ok(client):
    lead = _mk(client)
    for status in ("contacted", "meeting_booked"):
        assert client.patch(f"/api/leads/{lead['id']}",
                            json={"status": status}).status_code == 200
    assert client.post(
        f"/api/leads/{lead['id']}/close",
        json={"outcome": "won", "reason": "Offer accepted"},
    ).status_code == 200


def test_booking_cannot_reopen_closed_lead(client):
    lead = _mk(client)
    client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "lost"})
    r = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-08-05T10:00:00",
        "end_ts": "2026-08-05T10:45:00", "location": "X"})
    assert r.status_code == 400
    assert client.get(f"/api/leads/{lead['id']}").json()["status"] == "closed"


def test_direct_patch_cannot_close_without_an_outcome(client):
    lead = _mk(client)

    response = client.patch(
        f"/api/leads/{lead['id']}", json={"status": "closed"}
    )

    assert response.status_code == 400
    assert "close endpoint" in response.json()["detail"].lower()
    assert client.get(f"/api/leads/{lead['id']}").json()["status"] == "new"


def test_close_requires_won_or_lost_and_records_reason(client):
    lead = _mk(client)

    invalid = client.post(
        f"/api/leads/{lead['id']}/close", json={"outcome": "unknown"}
    )
    assert invalid.status_code == 422

    response = client.post(
        f"/api/leads/{lead['id']}/close",
        json={"outcome": "won", "reason": "  Offer accepted  "},
    )

    assert response.status_code == 200, response.text
    closed = response.json()
    assert closed["status"] == "closed"
    assert closed["outcome"] == "won"
    assert closed["close_reason"] == "Offer accepted"
    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert any(
        event["type"] == "status_change"
        and "won" in event["content"]
        and "Offer accepted" in event["content"]
        for event in profile["events"]
    )


def test_close_is_forward_only(client):
    lead = _mk(client)
    first = client.post(
        f"/api/leads/{lead['id']}/close", json={"outcome": "lost"}
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/leads/{lead['id']}/close", json={"outcome": "won"}
    )
    assert second.status_code == 400
    assert client.get(f"/api/leads/{lead['id']}").json()["outcome"] == "lost"
