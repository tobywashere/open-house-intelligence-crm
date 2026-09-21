import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.auth import require_agent, require_human
from app.main import app


HUMAN_TOKEN = "human-" + "h" * 40
AGENT_TOKEN = "agent-" + "a" * 40


def _capabilities(monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", HUMAN_TOKEN)
    monkeypatch.setenv("OHI_AGENT_API_TOKEN", AGENT_TOKEN)


def _token(value: str) -> dict[str, str]:
    return {"X-API-Token": value}


def _request(role=None, mode=None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/"})
    if role is not None:
        request.state.auth_role = role
    if mode is not None:
        request.state.auth_mode = mode
    return request


def test_auth_status_reports_public_mode_and_trusted_role(monkeypatch, client_factory):
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    monkeypatch.delenv("OHI_AGENT_API_TOKEN", raising=False)
    local = client_factory()
    assert local.get("/api/auth/status").json() == {"mode": "local", "role": "human"}

    monkeypatch.setenv("OHI_API_TOKEN", "legacy-secret")
    token = client_factory()
    assert token.get("/api/auth/status").json() == {"mode": "token", "role": None}
    assert token.get("/api/auth/status", headers=_token("legacy-secret")).json() == {
        "mode": "token",
        "role": "human",
    }

    _capabilities(monkeypatch)
    capabilities = client_factory()
    assert capabilities.get("/api/auth/status").json() == {
        "mode": "capabilities",
        "role": None,
    }
    assert capabilities.get("/api/auth/status", headers=_token(HUMAN_TOKEN)).json() == {
        "mode": "capabilities",
        "role": "human",
    }
    assert capabilities.get("/api/auth/status", headers=_token(AGENT_TOKEN)).json() == {
        "mode": "capabilities",
        "role": "agent",
    }
    wrong = capabilities.get("/api/auth/status", headers=_token("wrong-token"))
    assert wrong.status_code == 200
    assert wrong.json() == {"mode": "capabilities", "role": None}
    assert HUMAN_TOKEN not in wrong.text
    assert AGENT_TOKEN not in wrong.text


def test_capability_mode_authenticates_human_and_restricts_agent(monkeypatch, client_factory):
    _capabilities(monkeypatch)
    client = client_factory()

    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/leads", headers=_token("wrong-token")).status_code == 401
    assert client.get("/api/leads", headers=_token(HUMAN_TOKEN)).status_code == 200
    assert client.get("/api/leads", headers=_token(AGENT_TOKEN)).status_code == 200

    spoofed = client.post(
        "/api/leads",
        headers={**_token(AGENT_TOKEN), "X-Actor": "user"},
        json={"name": "Must Not Exist", "source": "note"},
    )
    assert spoofed.status_code == 403

    human = client.post(
        "/api/leads",
        headers={**_token(HUMAN_TOKEN), "X-Actor": "agent"},
        json={"name": "Human Created", "source": "note"},
    )
    assert human.status_code == 200
    assert human.json()["name"] == "Human Created"


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", "/api/leads", {"name": "Blocked", "source": "note"}),
        ("POST", "/api/chat", {"message": "hello", "session_id": "auth-test"}),
        ("POST", "/api/chat/directory", {"message": "List leads"}),
        ("PUT", "/api/research-settings", {}),
        ("POST", "/api/health/agent-check", None),
        ("POST", "/api/email/send", {}),
        ("POST", "/api/pending-changes/1/approve", {}),
        ("POST", "/api/pending-changes/1/deny", {}),
        ("POST", "/api/pending-changes/1/%61pprove", {}),
        ("POST", "/api/pending-changes/1/%64eny", {}),
    ],
)
def test_agent_is_denied_every_non_allowlisted_api_route(
    monkeypatch, client_factory, method, path, json_body
):
    _capabilities(monkeypatch)
    client = client_factory()
    response = client.request(
        method,
        path,
        headers={**_token(AGENT_TOKEN), "X-Actor": "user"},
        json=json_body,
    )
    assert response.status_code == 403


def test_future_agent_proposal_route_reaches_normal_router_404(monkeypatch, client_factory):
    _capabilities(monkeypatch)
    client = client_factory()
    response = client.post(
        "/api/agent/lead-proposals",
        headers=_token(AGENT_TOKEN),
        json={"request_id": "a" * 32, "name": "Future route"},
    )
    assert response.status_code == 404


def test_native_role_dependencies_require_capability_mode(monkeypatch):
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    monkeypatch.delenv("OHI_AGENT_API_TOKEN", raising=False)
    for dependency in (require_human, require_agent):
        with pytest.raises(HTTPException) as exc:
            dependency(_request("human", "local"))
        assert exc.value.status_code == 403

    monkeypatch.setenv("OHI_API_TOKEN", "legacy-secret")
    for dependency in (require_human, require_agent):
        with pytest.raises(HTTPException) as exc:
            dependency(_request("human", "token"))
        assert exc.value.status_code == 403


def test_native_role_dependencies_use_middleware_identity(monkeypatch):
    _capabilities(monkeypatch)
    assert require_human(_request("human", "capabilities")) == "human"
    assert require_agent(_request("agent", "capabilities")) == "agent"
    with pytest.raises(HTTPException) as stale_mode_exc:
        require_human(_request("human", "token"))
    assert stale_mode_exc.value.status_code == 403
    with pytest.raises(HTTPException) as human_exc:
        require_human(_request("agent", "capabilities"))
    assert human_exc.value.status_code == 403
    with pytest.raises(HTTPException) as agent_exc:
        require_agent(_request("human", "capabilities"))
    assert agent_exc.value.status_code == 403


def test_local_and_single_token_modes_keep_explicit_legacy_actor_behavior(
    monkeypatch, client_factory
):
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    monkeypatch.delenv("OHI_AGENT_API_TOKEN", raising=False)
    local = client_factory()
    queued = local.post(
        "/api/leads",
        headers={"X-Actor": "agent"},
        json={"name": "Local Legacy", "source": "note"},
    )
    assert queued.status_code == 202

    monkeypatch.setenv("OHI_API_TOKEN", "legacy-short-secret")
    token = client_factory()
    queued = token.post(
        "/api/leads",
        headers={**_token("legacy-short-secret"), "X-Actor": "agent"},
        json={"name": "Token Legacy", "source": "note"},
    )
    assert queued.status_code == 202


@pytest.mark.parametrize(
    ("human", "agent"),
    [
        (HUMAN_TOKEN, HUMAN_TOKEN),
        ("short", AGENT_TOKEN),
        (HUMAN_TOKEN, "short"),
        (HUMAN_TOKEN, "agent key with spaces" + "a" * 32),
        ("human-é" + "h" * 32, AGENT_TOKEN),
    ],
)
def test_invalid_capability_configuration_fails_at_startup(monkeypatch, human, agent):
    monkeypatch.setenv("OHI_API_TOKEN", human)
    monkeypatch.setenv("OHI_AGENT_API_TOKEN", agent)
    with pytest.raises(RuntimeError, match="capability authentication configuration"):
        with TestClient(app):
            pass


def test_invalid_runtime_configuration_fails_closed_except_health_and_cors(
    monkeypatch, client_factory
):
    _capabilities(monkeypatch)
    client = client_factory()
    monkeypatch.setenv("OHI_AGENT_API_TOKEN", HUMAN_TOKEN)

    protected = client.get("/api/leads", headers=_token(HUMAN_TOKEN))
    assert protected.status_code == 503
    assert protected.json() == {"detail": "invalid capability authentication configuration"}

    status = client.get("/api/auth/status", headers=_token(HUMAN_TOKEN))
    assert status.status_code == 503
    assert status.json() == {"detail": "invalid capability authentication configuration"}

    assert client.get("/api/health").status_code == 200
    preflight = client.options(
        "/api/leads",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code in (200, 204)
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
