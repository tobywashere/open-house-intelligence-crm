"""Truthful configured-versus-verified integration status."""

import pytest

from app.integrations import composio_client as cc


@pytest.fixture(autouse=True)
def clear_integration_status(monkeypatch):
    monkeypatch.setattr(cc, "_LAST_OPERATION", None, raising=False)
    monkeypatch.setattr(cc, "_LAST_DETAIL", None, raising=False)


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
