"""Tests for the bounded one-model-request CRM experiment."""
import asyncio
import json

from app.agent.crm_chat import CRM_REQUEST_TOOL, DASHBOARD_CHANNEL
from app.agent.openclaw_gateway import OpenClawGatewayError
from app.agent.single_action import run_single_action


def completion(*calls, content="model prose must never be rendered"):
    return {"choices": [{"message": {"content": content, "tool_calls": list(calls)}}]}


def call(call_id, operation, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": CRM_REQUEST_TOOL, "arguments": json.dumps({
            "operation": operation, "arguments": arguments,
        })},
    }


def directory_receipt():
    return {
        "ok": True, "operation": "list_lead_directory", "kind": "read",
        "result": {"total": 1, "offset": 0, "limit": 10,
                   "leads": [{"id": 4, "name": "Jordan", "status": "new"}]},
    }


class Gateway:
    def __init__(self, model_response, invocation=directory_receipt()):
        self.model_response = model_response
        self.invocation = invocation
        self.chat_calls = []
        self.invoke_calls = []

    async def chat_completion(self, payload, *, channel=None, timeout=None):
        self.chat_calls.append((payload, channel, timeout))
        return self.model_response

    async def invoke_tool(self, name, args, *, agent_id, session_key, idempotency_key, timeout=None):
        self.invoke_calls.append((name, args, agent_id, session_key, idempotency_key, timeout))
        if isinstance(self.invocation, Exception):
            raise self.invocation
        return self.invocation


def run(gateway, **kwargs):
    return asyncio.run(run_single_action(gateway, "show leads", "session-1", "agent-1", **kwargs))


def test_renders_verified_directory_after_exactly_one_completion_without_finish_tool():
    gateway = Gateway(completion(call("call-1", "list_lead_directory", {"limit": 10})))

    result = run(gateway)

    assert result.reply == "1 leads total. Showing 1 (offset 0): Jordan (ID 4, new)."
    assert (result.status, result.operation, result.model_calls, result.tool_calls) == (
        "answered", "list_lead_directory", 1, 1)
    payload, channel, _ = gateway.chat_calls[0]
    assert channel == DASHBOARD_CHANNEL
    assert len(payload["tools"]) == 1
    assert "finish" not in json.dumps(payload["tools"])


def test_narrow_allowed_operations_rejects_before_network_dispatch():
    gateway = Gateway(completion(call("call-1", "create_lead", {"name": "Jordan"})))

    result = run(gateway, allowed_operations={"list_lead_directory"})

    assert result.status == "needs_clarification"
    assert result.operation == "create_lead"
    assert result.tool_calls == 0
    assert not gateway.invoke_calls


def test_absent_multiple_or_malformed_calls_do_not_dispatch_or_repeat_model_request():
    cases = [
        completion(),
        completion(call("one", "list_lead_directory", {}), call("two", "list_lead_directory", {})),
        completion({"id": "bad", "type": "function", "function": {"name": CRM_REQUEST_TOOL, "arguments": "{}"}}),
    ]
    for response in cases:
        gateway = Gateway(response)
        result = run(gateway)
        assert result.status == "needs_clarification"
        assert result.model_calls == 1
        assert result.tool_calls == 0
        assert len(gateway.chat_calls) == 1
        assert not gateway.invoke_calls


def test_invalid_arguments_need_clarification_without_dispatch():
    gateway = Gateway(completion(call("call-1", "get_lead_context", {})))

    result = run(gateway)

    assert result.status == "needs_clarification"
    assert result.operation == "get_lead_context"
    assert result.tool_calls == 0
    assert not gateway.invoke_calls


def test_invalid_mutation_receipt_and_post_dispatch_timeout_are_unknown():
    for invocation in ({"ok": True, "operation": "create_lead", "kind": "proposal", "result": {}}, TimeoutError()):
        gateway = Gateway(completion(call("write", "create_lead", {"name": "Jordan"})), invocation)
        result = run(gateway)
        assert result.status == "unknown"
        assert result.tool_calls == 1
        assert "unknown" in result.reply.casefold()


def test_definite_pre_dispatch_write_failure_is_failed_not_unknown():
    gateway = Gateway(
        completion(call("write", "create_lead", {"name": "Jordan"})),
        OpenClawGatewayError("rejected", definite_pre_dispatch=True),
    )

    result = run(gateway)

    assert result.status == "failed"
    assert "unknown" not in result.reply.casefold()


def test_uncertain_read_transport_is_failed_not_unknown():
    gateway = Gateway(
        completion(call("read", "list_lead_directory", {"limit": 10})),
        OpenClawGatewayError("connection lost"),
    )

    result = run(gateway)

    assert result.status == "failed"
    assert "unknown" not in result.reply.casefold()


def test_note_proposal_bridges_the_native_add_event_receipt_name():
    gateway = Gateway(
        completion(call("note", "add_note", {"lead_id": 4, "content": "Called Jordan."})),
        {
            "ok": True, "operation": "add_note", "kind": "proposal",
            "result": {"pending": True, "id": 7, "operation": "add_event",
                       "status": "pending", "summary": "Add note to Jordan"},
        },
    )

    result = run(gateway)

    assert result.status == "pending"
    assert result.receipt is not None and result.receipt.operation == "add_note"
    assert "Pending approval #7" in result.reply


def test_unhashable_operation_needs_clarification_without_dispatch():
    response = completion(call("bad", "list_lead_directory", {"limit": 10}))
    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "operation": [], "arguments": {},
    })
    gateway = Gateway(response)

    result = run(gateway)

    assert result.status == "needs_clarification"
    assert result.operation is None
    assert not gateway.invoke_calls


def test_invalid_allowed_set_rejects_before_the_model_network_call():
    gateway = Gateway(completion(call("read", "list_lead_directory", {"limit": 10})))

    result = run(gateway, allowed_operations={"unsupported_operation"})

    assert result.status == "needs_clarification"
    assert result.model_calls == result.tool_calls == 0
    assert not gateway.chat_calls


def test_read_timeout_is_failed_without_unknown_outcome_language():
    gateway = Gateway(
        completion(call("read", "list_lead_directory", {"limit": 10})),
        TimeoutError(),
    )

    result = run(gateway)

    assert result.status == "failed"
    assert result.tool_calls == 1
    assert "unknown" not in result.reply.casefold()


def test_model_deadline_stops_before_tool_dispatch():
    class SlowGateway(Gateway):
        async def chat_completion(self, payload, *, channel=None, timeout=None):
            self.chat_calls.append((payload, channel, timeout))
            await asyncio.sleep(0.02)
            return self.model_response

    gateway = SlowGateway(completion(call("read", "list_lead_directory", {"limit": 10})))

    result = run(gateway, deadline_seconds=0.001)

    assert result.status == "failed"
    assert result.model_calls == 1
    assert result.tool_calls == 0
    assert not gateway.invoke_calls


def test_invalid_agent_ids_fail_before_any_gateway_call():
    for agent_id in (None, 4, object(), "", "bad agent", "x" * 65):
        gateway = Gateway(completion(call("read", "list_lead_directory", {"limit": 10})))

        result = asyncio.run(run_single_action(
            gateway, "show leads", "session-1", agent_id,
        ))

        assert result.status == "failed"
        assert result.model_calls == result.tool_calls == 0
        assert not gateway.chat_calls
        assert not gateway.invoke_calls
