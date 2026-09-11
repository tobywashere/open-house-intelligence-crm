"""The live experiment cannot dispatch writes or leak replies in its report."""
import asyncio
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def comparison():
    path = Path(__file__).resolve().parents[2] / "scripts/compare_crm_chat.py"
    spec = importlib.util.spec_from_file_location("compare_crm_chat", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GatewaySpy:
    def __init__(self):
        self.invocations = []
        self.completions = []

    async def invoke_tool(self, name, args, **kwargs):
        self.invocations.append((name, args, kwargs))
        return {"ok": True, "operation": "list_lead_directory", "kind": "read",
                "result": {"total": 15, "leads": [], "offset": 0, "limit": 20}}

    async def chat_completion(self, payload, **kwargs):
        self.completions.append((payload, kwargs))
        return {"choices": [{"message": {"content": "Private Person lead@example.com"}}]}


@pytest.mark.parametrize("operation", ["create_lead", "delete_lead", "post_briefing", "approve_pending_change"])
def test_read_only_boundary_rejects_mutations_before_dispatch(comparison, operation):
    delegate = GatewaySpy()
    gateway = comparison.ReadOnlyGateway(delegate)
    with pytest.raises(comparison.OpenClawGatewayError) as exc:
        asyncio.run(gateway.invoke_tool("openhouse_crm", {"operation": operation, "arguments": {}},
                                       agent_id="openhouse-crm", session_key="test", idempotency_key="test"))
    assert exc.value.definite_pre_dispatch is True
    assert delegate.invocations == []


def test_read_only_boundary_validates_arguments_and_native_tool(comparison):
    delegate = GatewaySpy()
    gateway = comparison.ReadOnlyGateway(delegate)
    for name, args in [("exec", {}), ("openhouse_crm", {"operation": "list_lead_directory", "arguments": {"sql": "DELETE"}})]:
        with pytest.raises(comparison.OpenClawGatewayError):
            asyncio.run(gateway.invoke_tool(name, args))
    assert delegate.invocations == []
    receipt = asyncio.run(gateway.invoke_tool("openhouse_crm", {"operation": "list_lead_directory", "arguments": {}}))
    assert receipt["result"]["total"] == 15
    assert gateway.tool_calls == 1


def test_model_request_keeps_protected_channel_and_read_only_catalog(comparison):
    delegate = GatewaySpy()
    gateway = comparison.ReadOnlyGateway(delegate)
    payload = {"tools": comparison.crm_chat._CLIENT_TOOLS_MODULE.build_dashboard_client_tools(comparison.crm_chat._CONTRACT)}
    asyncio.run(gateway.chat_completion(payload, channel="openhouse-dashboard"))
    forwarded, kwargs = delegate.completions[0]
    assert kwargs["channel"] == "openhouse-dashboard"
    branches = forwarded["tools"][0]["function"]["parameters"]["oneOf"]
    assert {b["properties"]["operation"]["const"] for b in branches} == {"list_lead_directory", "get_lead_context"}
    assert len(payload["tools"][0]["function"]["parameters"]["oneOf"]) > 2
    with pytest.raises(comparison.OpenClawGatewayError):
        asyncio.run(gateway.chat_completion(payload, channel="discord"))
    assert len(delegate.completions) == 1


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost.evil.test", "http://user:secret@localhost:8080", "file:///tmp/db", "http://localhost:bad", "http://localhost/?token=secret", "http://localhost/#token", "http://localhost/admin"])
def test_nonlocal_or_credential_urls_are_rejected(comparison, url):
    with pytest.raises(ValueError):
        comparison.validate_local_url(url)


@pytest.mark.parametrize("url", ["http://localhost:18789", "http://127.0.0.1:8080/api", "http://[::1]:8080/api"])
def test_loopback_urls_are_accepted(comparison, url):
    assert comparison.validate_local_url(url) == url


def test_default_report_contains_no_raw_reply_or_record_values(comparison):
    reply = "15 leads total. Private Person lead@example.com Bearer secret-value"
    result = comparison.summarize_reply("baseline", reply, 15, 3, 1, 0.4)
    rendered = json.dumps(result)
    assert result["passed"] is True
    assert result["count"] == 15
    for private in ("Private Person", "lead@example.com", "secret-value", reply):
        assert private not in rendered


def test_missing_count_is_failure_with_classification_not_raw_text(comparison):
    reply = "Private Person: I created your lead, trust me!"
    result = comparison.summarize_reply("baseline", reply, 15, 1, 0, 0.2)
    assert result["passed"] is False
    assert result["count"] is None
    assert result["failure_stage"] == "no_verified_count"
    assert "Private Person" not in json.dumps(result)


def test_private_capture_is_exclusive_and_owner_only(comparison, tmp_path):
    target = tmp_path / "private.json"
    comparison.write_private_capture(target, [{"reply": "private exact response"}])
    assert json.loads(target.read_text())[0]["reply"] == "private exact response"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        comparison.write_private_capture(target, [{"reply": "overwritten"}])
    assert "overwritten" not in target.read_text()


def test_cli_requires_explicit_live_opt_in(comparison, capsys):
    assert comparison.main([]) == 2
    assert "--live-read-only" in capsys.readouterr().err


def test_comparison_exposes_finish_dependency_without_leaking_directory(comparison, monkeypatch):
    class SelectOnceGateway(GatewaySpy):
        async def chat_completion(self, payload, **kwargs):
            self.completions.append((payload, kwargs))
            if len(self.completions) > 1:
                return {"choices": [{"message": {"content": "Private Person secret@example.com"}}]}
            return {"choices": [{"message": {"tool_calls": [{
                "id": "call-one", "type": "function", "function": {
                    "name": "openhouse_crm_request",
                    "arguments": json.dumps({"operation": "list_lead_directory", "arguments": {}}),
                },
            }]}}]}

    async def count(*args):
        return 15

    monkeypatch.setattr(comparison, "OpenClawGateway", lambda **kwargs: SelectOnceGateway())
    monkeypatch.setattr(comparison, "_api_count", count)
    args = SimpleNamespace(api_url="http://localhost:8080/api", gateway_url="http://localhost:18789",
                           timeout=1.0, agent_id="openhouse-crm")
    report, private = asyncio.run(comparison.compare(args))
    baseline = [row for row in report["results"] if row["path"] == "baseline"]
    candidate = [row for row in report["results"] if row["path"] == "single_action"]
    assert len(baseline) == len(candidate) == 3
    assert all(not row["passed"] and row["model_calls"] > 1 for row in baseline)
    assert all(row["passed"] and row["model_calls"] == 1 and row["tool_calls"] == 1 for row in candidate)
    assert all(row["failure_stage"] == "finish_or_render" for row in baseline)
    assert all("reply" in entry for entry in private)
    assert "secret@example.com" not in json.dumps(report)


def test_api_snapshot_failure_preserves_completed_case_diagnostics(comparison, monkeypatch):
    counts = iter([15, ConnectionError("private-hostname")])

    async def count(*args):
        result = next(counts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(comparison, "OpenClawGateway", lambda **kwargs: GatewaySpy())
    monkeypatch.setattr(comparison, "_api_count", count)
    args = SimpleNamespace(api_url="http://localhost:8080/api", gateway_url="http://localhost:18789",
                           timeout=1.0, agent_id="openhouse-crm")
    report, captures = asyncio.run(comparison.compare(args))
    assert report["passed"] is False
    assert report["api_count_stable"] is False
    assert len(report["results"]) == len(captures) == 6
    assert report["failure_stage"] == "final_api_snapshot_unavailable"
    assert "private-hostname" not in json.dumps(report)
