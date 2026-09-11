"""Bounded one-tool-call CRM experiment; production chat remains unchanged."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
import re
import secrets
import time

from . import crm_chat
from .openclaw_gateway import OpenClawGatewayError


_OPERATIONS = frozenset({
    "list_lead_directory", "get_lead_context", "check_availability",
    "create_lead", "add_note", "schedule_followup", "book_appointment",
})
_CLARIFICATION_REPLY = "I need valid CRM operation details before I can continue."
_AGENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class SingleActionResult:
    reply: str
    status: str
    operation: str | None
    receipt: crm_chat.CrmCallReceipt | None
    model_calls: int
    tool_calls: int


def _result(reply, status, operation=None, receipt=None, model_calls=0, tool_calls=0):
    return SingleActionResult(reply, status, operation, receipt, model_calls, tool_calls)


def _single_action_tool(operations: frozenset[str]) -> dict:
    tool = deepcopy(crm_chat._crm_request_tool())
    branches = tool["function"]["parameters"]["oneOf"]
    tool["function"]["parameters"]["oneOf"] = [
        branch for branch in branches
        if branch["properties"]["operation"]["const"] in operations
    ]
    return tool


def _bridge_native_note_receipt(payload: object) -> object:
    """Translate the native event label to the public add_note contract label."""
    if (
        isinstance(payload, dict)
        and payload.get("operation") == "add_note"
        and payload.get("kind") == "proposal"
        and isinstance(payload.get("result"), dict)
        and payload["result"].get("operation") == "add_event"
    ):
        bridged = dict(payload)
        bridged["result"] = {**payload["result"], "operation": "add_note"}
        return bridged
    return payload


def _failure(receipt: crm_chat.CrmCallReceipt, *, model_calls: int, tool_calls: int):
    status = "unknown" if crm_chat._is_unknown_outcome(receipt) else "failed"
    reply = crm_chat.render_verified_reply(
        crm_chat.FinishDecision("failed", "", (receipt.call_id,)), [receipt]
    )
    return _result(reply, status, receipt.operation, receipt, model_calls, tool_calls)


async def run_single_action(
    gateway,
    message: str,
    session_id: str,
    agent_id: str,
    *,
    allowed_operations=None,
    deadline_seconds=120.0,
) -> SingleActionResult:
    """Select and execute exactly one validated CRM operation without a finish turn."""
    total_budget = crm_chat._deadline_seconds(deadline_seconds)
    if total_budget is None:
        return _result(crm_chat.DEADLINE_REPLY, "failed")
    try:
        allowed = _OPERATIONS if allowed_operations is None else frozenset(allowed_operations)
    except TypeError:
        return _result(_CLARIFICATION_REPLY, "needs_clarification")
    if not allowed or not allowed.issubset(_OPERATIONS):
        return _result(_CLARIFICATION_REPLY, "needs_clarification")
    if not isinstance(agent_id, str) or _AGENT_ID_RE.fullmatch(agent_id) is None:
        return _result(crm_chat.UNAVAILABLE_REPLY, "failed")

    deadline = time.monotonic() + total_budget
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _result(crm_chat.DEADLINE_REPLY, "failed")
    model = f"openclaw/{agent_id}"
    payload = {
        "model": model,
        "user": session_id,
        "messages": [
            {"role": "system", "content": (
                "Select exactly one CRM operation. CRM writes are reviewed proposals, "
                "never applied changes."
            )},
            {"role": "user", "content": message},
        ],
        "tools": [_single_action_tool(allowed)],
        "tool_choice": "required",
    }
    try:
        response = await asyncio.wait_for(
            gateway.chat_completion(payload, channel=crm_chat.DASHBOARD_CHANNEL, timeout=remaining),
            timeout=remaining,
        )
        assistant = crm_chat._model_message(response)
    except TimeoutError:
        return _result(crm_chat.DEADLINE_REPLY, "failed", model_calls=1)
    except Exception:
        return _result(crm_chat.UNAVAILABLE_REPLY, "failed", model_calls=1)

    calls = assistant.get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) != 1 or not crm_chat._valid_tool_call_shape(calls[0]):
        return _result(_CLARIFICATION_REPLY, "needs_clarification", model_calls=1)
    call = calls[0]
    try:
        params = json.loads(call["function"]["arguments"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return _result(_CLARIFICATION_REPLY, "needs_clarification", model_calls=1)
    if call["function"]["name"] != crm_chat.CRM_REQUEST_TOOL or not isinstance(params, dict):
        return _result(_CLARIFICATION_REPLY, "needs_clarification", model_calls=1)
    operation = params.get("operation")
    if (
        not isinstance(operation, str)
        or operation not in allowed
        or set(params) != {"operation", "arguments"}
    ):
        return _result(_CLARIFICATION_REPLY, "needs_clarification", operation if isinstance(operation, str) else None, model_calls=1)
    try:
        arguments = crm_chat._CONTRACT_MODULE.validate_arguments(operation, params["arguments"])
    except (TypeError, ValueError):
        return _result(_CLARIFICATION_REPLY, "needs_clarification", operation, model_calls=1)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _result(crm_chat.DEADLINE_REPLY, "failed", operation, model_calls=1)
    try:
        raw_receipt = await asyncio.wait_for(
            gateway.invoke_tool(
                "openhouse_crm", {"operation": operation, "arguments": arguments},
                agent_id=agent_id, session_key=f"dashboard:{session_id}",
                idempotency_key=crm_chat._idempotency_key(agent_id, session_id, secrets.token_hex(16), call["id"]),
                timeout=remaining,
            ),
            timeout=remaining,
        )
    except TimeoutError:
        receipt = (crm_chat._unknown_outcome_receipt(call["id"], operation)
                   if crm_chat._is_mutating_operation(operation)
                   else crm_chat._definite_invoke_failure_receipt(call["id"], operation))
        return _failure(receipt, model_calls=1, tool_calls=1)
    except OpenClawGatewayError as exc:
        receipt = (crm_chat._definite_invoke_failure_receipt(call["id"], operation)
                   if exc.definite_pre_dispatch or not crm_chat._is_mutating_operation(operation)
                   else crm_chat._unknown_outcome_receipt(call["id"], operation))
        return _failure(receipt, model_calls=1, tool_calls=1)
    except Exception:
        receipt = (crm_chat._unknown_outcome_receipt(call["id"], operation)
                   if crm_chat._is_mutating_operation(operation)
                   else crm_chat._definite_invoke_failure_receipt(call["id"], operation))
        return _failure(receipt, model_calls=1, tool_calls=1)

    receipt = crm_chat._normalize_gateway_receipt(
        call["id"], operation, _bridge_native_note_receipt(raw_receipt)
    )
    if not receipt.ok:
        return _failure(receipt, model_calls=1, tool_calls=1)
    if receipt.kind == "proposal":
        reply = crm_chat.render_verified_reply(
            crm_chat.FinishDecision("queued", "", (receipt.call_id,), receipt.result["id"]), [receipt]
        )
        return _result(reply, "pending", operation, receipt, 1, 1)
    reply = crm_chat.render_verified_reply(
        crm_chat.FinishDecision("answered", "", (receipt.call_id,)), [receipt]
    )
    return _result(reply, "answered", operation, receipt, 1, 1)
