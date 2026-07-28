def test_token_required_when_set(monkeypatch, client_factory):
    monkeypatch.setenv("OHI_API_TOKEN", "s3cret")
    client = client_factory()          # builds a fresh TestClient after env is set
    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/leads", headers={"X-API-Token": "s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200   # probe stays open


def test_open_when_unset(monkeypatch, client_factory):
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    assert client_factory().get("/api/leads").status_code == 200


def test_cors_preflight_bypasses_token_guard(monkeypatch, client_factory):
    # Browsers never attach custom headers (like X-API-Token) to a preflight
    # OPTIONS request. CORSMiddleware must sit outside the auth guard and
    # answer the preflight itself — with CORS headers — or the guard would
    # 401 it with no CORS headers and the browser would block the real
    # request that follows.
    monkeypatch.setenv("OHI_API_TOKEN", "s3cret")
    client = client_factory()
    res = client.options(
        "/api/leads",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code in (200, 204)
    assert "access-control-allow-origin" in {k.lower() for k in res.headers.keys()}


def test_cors_rejects_foreign_origin(monkeypatch, client_factory):
    # Wildcard CORS would let ANY web page the operator has open read/write
    # this API from the browser — localhost binding does not stop that.
    # Only an explicit allowlist does.
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    client = client_factory()
    res = client.get("/api/leads", headers={"Origin": "https://evil.example"})
    assert res.status_code == 200  # request itself succeeds (no server-side origin check)
    assert "access-control-allow-origin" not in {k.lower() for k in res.headers.keys()}


def test_cors_allows_default_origin(monkeypatch, client_factory):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    client = client_factory()
    res = client.get("/api/leads", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
