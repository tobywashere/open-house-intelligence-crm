"""Truthful configured-versus-verified integration status."""

import pytest

from app.agent import status as agent_status
from app.integrations import composio_client as cc


@pytest.fixture(autouse=True)
def clear_integration_status(monkeypatch):
    monkeypatch.setattr(cc, "_LAST_OPERATION", None, raising=False)
    monkeypatch.setattr(cc, "_LAST_DETAIL", None, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_CHAT_OK", None, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_DETAIL", None, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_OK", None, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_DETAIL", None, raising=False)
    monkeypatch.setattr(agent_status, "_EVENT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_CHAT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_FALLBACKS", {}, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_FALLBACK_SEQUENCE", 0, raising=False)


def test_chat_success_does_not_mean_crm_verified():
    agent_status.record_chat(True)
    agent_status.record_crm_capability(False, "no audited CRM call")

    assert agent_status.resolved_status(
        gateway_reachable=True,
        endpoint_enabled=True,
    ) == "chat_verified"


def test_crm_success_is_distinct():
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)

    assert agent_status.resolved_status(
        gateway_reachable=True,
        endpoint_enabled=True,
    ) == "crm_verified"


def test_newer_chat_failure_degrades_previously_verified_crm():
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    agent_status.record_chat(False, "timeout")

    assert agent_status.resolved_status(
        gateway_reachable=True,
        endpoint_enabled=True,
    ) == "degraded"


def test_fallback_after_crm_verification_marks_status_degraded():
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    agent_status.record_fallback("extract")

    assert agent_status.resolved_status(
        gateway_reachable=True,
        endpoint_enabled=True,
    ) == "degraded"
    assert agent_status.fallback_counts() == {"extract": 1}


def test_successful_crm_verification_resets_fallback_degradation_epoch():
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    agent_status.record_fallback("extract")
    agent_status.record_crm_capability(True)

    assert agent_status.resolved_status(
        gateway_reachable=True,
        endpoint_enabled=True,
    ) == "crm_verified"
    assert agent_status.fallback_counts() == {"extract": 1}


def test_configured_integration_is_not_reported_as_verified(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "api")
    monkeypatch.setenv("COMPOSIO_API_KEY", "configured-key")

    body = client.get("/api/integrations/status").json()

    assert body == {
        "mode": "live",
        "configured": True,
        "last_operation": None,
        "detail": None,
    }


def test_successful_operation_records_verified_status(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "api")
    monkeypatch.setenv("COMPOSIO_API_KEY", "configured-key")

    class Response:
        status_code = 200

        def json(self):
            return {"successful": True, "data": {"messages": []}}

    monkeypatch.setattr(cc.httpx, "post", lambda *args, **kwargs: Response())

    cc.execute("GMAIL_FETCH_EMAILS", {})

    assert cc.status() == {
        "mode": "live",
        "configured": True,
        "last_operation": "succeeded",
        "detail": None,
    }


def test_failed_operation_records_sanitized_error_status(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "api")
    monkeypatch.setenv("COMPOSIO_API_KEY", "configured-key")

    class Response:
        status_code = 403

        def json(self):
            return {"successful": False, "error": "secret provider response"}

    monkeypatch.setattr(cc.httpx, "post", lambda *args, **kwargs: Response())

    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})

    status = cc.status()
    assert status["last_operation"] == "failed"
    assert status["detail"] == "provider_error"
    assert "secret provider response" not in str(status)
