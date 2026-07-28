def test_token_required_when_set(monkeypatch, client_factory):
    monkeypatch.setenv("OHI_API_TOKEN", "s3cret")
    client = client_factory()          # builds a fresh TestClient after env is set
    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/leads", headers={"X-API-Token": "s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200   # probe stays open


def test_open_when_unset(monkeypatch, client_factory):
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    assert client_factory().get("/api/leads").status_code == 200
