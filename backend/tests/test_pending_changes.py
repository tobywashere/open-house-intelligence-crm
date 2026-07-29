"""Agent-initiated lead writes (X-Actor: agent) must queue for approval
instead of applying; the dashboard's untagged calls must be unaffected."""
from tests.conftest import make_lead

AGENT = {"X-Actor": "agent"}


def _pending(client, status="pending"):
    res = client.get(f"/api/pending-changes?status={status}")
    assert res.status_code == 200, res.text
    return res.json()


def test_dashboard_create_is_unaffected(client):
    """No X-Actor header: applies immediately, exactly as before this feature."""
    res = client.post("/api/leads", json={"name": "Direct Dana", "source": "form"})
    assert res.status_code == 200
    assert res.json()["name"] == "Direct Dana"
    assert _pending(client) == []


def test_agent_create_lead_queues_not_applies(client):
    before = len(client.get("/api/leads").json())
    res = client.post("/api/leads", json={"name": "Agent Andy", "source": "form"}, headers=AGENT)
    assert res.status_code == 202
    body = res.json()
    assert body["pending"] is True
    assert body["operation"] == "create_lead"
    assert body["status"] == "pending"
    assert "Agent Andy" in body["summary"]

    after = len(client.get("/api/leads").json())
    assert after == before, "queued create_lead must not insert a lead row"

    pending = _pending(client)
    assert len(pending) == 1
    assert pending[0]["id"] == body["id"]
    assert pending[0]["lead_id"] is None


def test_agent_update_lead_queues_and_approve_applies(client):
    lead = make_lead(client, budget=900_000)
    res = client.patch(f"/api/leads/{lead['id']}", json={"budget": 1_100_000}, headers=AGENT)
    assert res.status_code == 202
    pending_id = res.json()["id"]
    assert "900,000" in res.json()["summary"]
    assert "1,100,000" in res.json()["summary"]

    unchanged = client.get(f"/api/leads/{lead['id']}").json()
    assert unchanged["budget"] == 900_000, "queued update_lead must not touch the row"

    approve = client.post(f"/api/pending-changes/{pending_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["budget"] == 1_100_000

    applied = client.get(f"/api/leads/{lead['id']}").json()
    assert applied["budget"] == 1_100_000

    assert _pending(client) == []
    approved = _pending(client, status="approved")
    assert len(approved) == 1
    assert approved[0]["status"] == "approved"


def test_agent_close_lead_queues_and_deny_leaves_unchanged(client):
    lead = make_lead(client)
    res = client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "won"}, headers=AGENT)
    assert res.status_code == 202
    pending_id = res.json()["id"]
    assert "won" in res.json()["summary"]

    deny = client.post(f"/api/pending-changes/{pending_id}/deny", json={"reason": "not actually closing"})
    assert deny.status_code == 200
    assert deny.json()["status"] == "denied"
    assert deny.json()["deny_reason"] == "not actually closing"

    still_open = client.get(f"/api/leads/{lead['id']}").json()
    assert still_open["status"] != "closed"

    denied = _pending(client, status="denied")
    assert len(denied) == 1 and denied[0]["id"] == pending_id


def test_agent_delete_lead_queues(client):
    lead = make_lead(client)
    res = client.delete(f"/api/leads/{lead['id']}", headers=AGENT)
    assert res.status_code == 202
    assert res.json()["operation"] == "delete_lead"

    still_there = client.get(f"/api/leads/{lead['id']}")
    assert still_there.status_code == 200, "queued delete_lead must not remove the row"


def test_agent_merge_leads_queues(client):
    primary = make_lead(client, name="Primary Pat", phone="+14255550111")
    dup = make_lead(client, name="Dup Dana", phone="+14255550112")
    res = client.post("/api/leads/merge",
                       json={"primary_id": primary["id"], "duplicate_id": dup["id"]},
                       headers=AGENT)
    assert res.status_code == 202
    assert res.json()["operation"] == "merge_leads"

    both_exist = client.get("/api/leads").json()
    ids = {lead_row["id"] for lead_row in both_exist}
    assert primary["id"] in ids and dup["id"] in ids, "queued merge must not delete the duplicate"


def test_approve_already_decided_is_400(client):
    lead = make_lead(client)
    res = client.post(f"/api/leads/{lead['id']}/close", json={"outcome": "lost"}, headers=AGENT)
    pending_id = res.json()["id"]
    client.post(f"/api/pending-changes/{pending_id}/deny")

    retry = client.post(f"/api/pending-changes/{pending_id}/approve")
    assert retry.status_code == 400


def test_pending_changes_status_filter_defaults_to_pending(client):
    lead = make_lead(client)
    client.patch(f"/api/leads/{lead['id']}", json={"area": "Kirkland"}, headers=AGENT)
    assert len(_pending(client)) == 1
    assert _pending(client, status="approved") == []
    assert _pending(client, status="denied") == []


def test_direct_edits_still_apply_instantly_alongside_agent_writes(client):
    """Regression: a human editing a lead directly must never be gated,
    even while an agent-proposed change on the same lead is pending."""
    lead = make_lead(client, budget=500_000)
    client.patch(f"/api/leads/{lead['id']}", json={"budget": 999_999}, headers=AGENT)

    direct = client.patch(f"/api/leads/{lead['id']}", json={"area": "Redmond"})
    assert direct.status_code == 200
    assert direct.json()["area"] == "Redmond"
    assert direct.json()["budget"] == 500_000, "the agent's queued budget change must not have leaked in"
