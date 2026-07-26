import pytest

from app.integrations import composio_client as cc


def test_mode_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    assert cc.mode() == "off"
    assert not cc.is_live()
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    assert not cc.is_live()          # live without a key is not live
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    assert cc.is_live()


def test_execute_success(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    calls = []

    class FakeResp:
        status_code = 200
        def json(self):
            return {"successful": True, "data": {"response_data": {"id": "evt1"}}}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return FakeResp()

    monkeypatch.setattr(cc.httpx, "post", fake_post)
    data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", {"summary": "x"})
    assert data == {"response_data": {"id": "evt1"}}
    url, headers, body = calls[0]
    assert url.endswith("/api/v3/tools/execute/GOOGLECALENDAR_CREATE_EVENT")
    assert headers["x-api-key"] == "k"
    assert body["arguments"] == {"summary": "x"}


def test_execute_retries_once_then_raises(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    attempts = []

    class FakeResp:
        status_code = 200
        def json(self):
            return {"successful": False, "error": "boom"}

    monkeypatch.setattr(cc.httpx, "post",
                        lambda *a, **kw: attempts.append(1) or FakeResp())
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})
    assert len(attempts) == 2


def test_execute_without_key_raises(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})
