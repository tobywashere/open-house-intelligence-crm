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
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    calls = []
    captured_kwargs = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"successful": True, "data": {"response_data": {"id": "evt1"}}}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        captured_kwargs["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(cc.httpx, "post", fake_post)
    data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", {"summary": "x"})
    assert data == {"response_data": {"id": "evt1"}}
    url, headers, body = calls[0]
    assert url.endswith("/api/v3/tools/execute/GOOGLECALENDAR_CREATE_EVENT")
    assert headers["x-api-key"] == "k"
    assert body["arguments"] == {"summary": "x"}
    assert body["user_id"] == "default"
    assert captured_kwargs["timeout"] == 15


def test_non_idempotent_send_is_not_retried_after_ambiguous_failure(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
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
    assert len(attempts) == 1


def test_read_only_fetch_retries_once_on_transient_failure(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    attempts = []

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return FakeResp(503, {})
        return FakeResp(
            200,
            {"successful": True, "data": {"messages": []}},
        )

    monkeypatch.setattr(cc.httpx, "post", fake_post)

    assert cc.execute("GMAIL_FETCH_EMAILS", {}) == {"messages": []}
    assert len(attempts) == 2


def test_execute_without_key_raises(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})


def test_cli_transport_is_live_without_key(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")
    assert cc.is_live()


def test_cli_transport_executes_via_subprocess(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    calls = []

    class FakeProc:
        returncode = 0
        stdout = '{"successful": true, "data": {"response_data": {"id": "evt9"}}, "error": null}'
        stderr = ""

    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/composio")
    monkeypatch.setattr(cc.os.path, "exists", lambda _: True)
    monkeypatch.setattr(cc.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or FakeProc())
    data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", {"summary": "x"})
    assert data == {"response_data": {"id": "evt9"}}
    assert calls[0][1:3] == ["execute", "GOOGLECALENDAR_CREATE_EVENT"]
    assert '"summary": "x"' in calls[0][4]


def test_cli_transport_failure_raises(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")

    class FakeProc:
        returncode = 1
        stdout = '{"successful": false, "error": "no connected account"}'
        stderr = ""

    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/composio")
    monkeypatch.setattr(cc.os.path, "exists", lambda _: True)
    monkeypatch.setattr(cc.subprocess, "run", lambda argv, **kw: FakeProc())
    with pytest.raises(cc.IntegrationError) as exc_info:
        cc.execute("GMAIL_SEND_EMAIL", {})
    assert "no connected account" in str(exc_info.value)


def test_execute_in_off_mode_raises_without_network_call(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")

    def no_network_allowed(*a, **kw):
        raise AssertionError("network call made in off mode")

    monkeypatch.setattr(cc.httpx, "post", no_network_allowed)
    with pytest.raises(cc.IntegrationError) as exc_info:
        cc.execute("GMAIL_SEND_EMAIL", {})
    assert "integrations disabled" in str(exc_info.value)
