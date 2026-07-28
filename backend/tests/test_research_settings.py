"""Daily market-research scope: pack defaults, operator overrides, rendering.

The research scope is the one part of a vertical operators tune repeatedly, so
it is editable at runtime rather than living only in pack.json. A stored row
wins over the pack; the rendered prompt is returned alongside so the UI can
show exactly what the agent will be asked.
"""
import pytest


def _valid(**over) -> dict:
    body = {
        "role": "a commercial insurance broker's research analyst",
        "audience": "a commercial insurance broker",
        "lookback_days": 14,
        "regions": ["Ontario", "Quebec"],
        "topics": ["carrier appetite shifts"],
        "exclusions": ["personal lines"],
        "national_scope_note": "",
    }
    body.update(over)
    return body


def test_defaults_come_from_the_pack_when_nothing_is_stored(client):
    body = client.get("/api/research-settings").json()
    assert "Seattle" in body["regions"]
    assert body["lookback_days"] == 7
    # the rendered prompt is the point of the endpoint — it must be filled,
    # not a template with unsubstituted tokens
    assert "Seattle" in body["rendered_prompt"]
    assert "{regions}" not in body["rendered_prompt"]
    assert "{lookback_days}" not in body["rendered_prompt"]


def test_put_persists_and_rerenders(client):
    client.put("/api/research-settings", json=_valid())
    body = client.get("/api/research-settings").json()
    assert body["regions"] == ["Ontario", "Quebec"]
    assert body["lookback_days"] == 14
    assert "Ontario" in body["rendered_prompt"]
    # the stored row must fully replace the pack’s scope — a recruiter must
    # never see Seattle in their prompt
    assert "Seattle" not in body["rendered_prompt"]


def test_put_is_audited(client):
    client.put("/api/research-settings", json=_valid())
    tools = [a["tool"] for a in client.get("/api/audit?limit=50").json()]
    assert "update_research_settings" in tools


def test_get_is_not_audited(client):
    """CONTRACT §3 names exactly two audited reads; this is not one of them."""
    before = len(client.get("/api/audit?limit=500").json())
    client.get("/api/research-settings")
    assert len(client.get("/api/audit?limit=500").json()) == before


@pytest.mark.parametrize("bad", [
    {"lookback_days": 0},
    {"lookback_days": 400},
    {"regions": []},
    {"role": ""},
])
def test_bounds_rejected(client, bad):
    assert client.put("/api/research-settings", json=_valid(**bad)).status_code == 422


def test_research_is_replaced_wholesale_not_merged(monkeypatch, tmp_path):
    """A pack supplying only `role` must NOT inherit Seattle's regions/topics —
    the daily web search would go hunting for ADU legislation on a recruiter's
    behalf. Same content-leak class as mock_summary."""
    import json as _json

    from app import vertical

    d = tmp_path / "recruiting"
    d.mkdir()
    (d / "pack.json").write_text(_json.dumps(
        {"research": {"role": "a technical recruiter's research analyst",
                      "audience": "a technical recruiter",
                      "lookback_days": 7, "regions": ["Toronto"]}}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "recruiting")
    vertical.clear_cache()
    research = vertical.load_pack()["research"]
    assert research["regions"] == ["Toronto"]
    assert "topics" not in research or not research["topics"]
    vertical.clear_cache()
