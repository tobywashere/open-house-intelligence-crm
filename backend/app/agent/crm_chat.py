"""Evidence-verified CRM orchestration for dashboard chat."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
from typing import Any


CRM_REQUEST_TOOL = "openhouse_crm_request"
FINISH_TOOL = "finish_crm_response"
DASHBOARD_CHANNEL = "openhouse-dashboard"
MAX_MODEL_ROUNDS = 8
MAX_CRM_CALLS = 6

UNAVAILABLE_REPLY = (
    "⚠ The local agent is unavailable or returned an invalid response. "
    "Your message is saved — check agent readiness and try again."
)
ROUND_LIMIT_REPLY = (
    "I couldn't verify a CRM answer within the allowed model rounds. "
    "No unverified action was reported."
)
CALL_LIMIT_REPLY = (
    "I reached the verified CRM call limit before a final answer. "
    "No unverified action was reported."
)
AMBIGUOUS_INVOKE_REPLY = (
    "I couldn't verify the CRM request because the local agent became unavailable. "
    "I did not retry it. Check Pending approvals before trying again."
)

_SAFE_ERROR_CODES = frozenset({
    "invalid_arguments", "not_found", "ambiguous_match", "schedule_conflict",
    "backend_unavailable", "timeout", "result_too_large", "operation_failed",
})
_MUTATION_SUCCESS_RE = re.compile(
    r"\b(?:applied|booked|completed|created|deleted|merged|queued|saved|scheduled|submitted|updated)\b",
    re.I,
)
_SAFE_ARGUMENT_ERROR_RE = re.compile(
    r"^(?:(?:Unsupported|Missing|Invalid) argument: [a-z][a-z0-9_]*|"
    r"Invalid CRM arguments: [a-z][a-z0-9_]*|CRM arguments must be an object|"
    r"Unknown CRM operation: [a-z][a-z0-9_]*)$"
)


def _load_contract_module():
    contract_path = (
        Path(__file__).resolve().parents[3]
        / "skills" / "crm-db-operations" / "contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_openhouse_dashboard_crm_contract", contract_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("CRM operation contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONTRACT_MODULE = _load_contract_module()
_CONTRACT = _CONTRACT_MODULE.CONTRACT


@dataclass(frozen=True)
class CrmCallReceipt:
    call_id: str
    operation: str
    ok: bool
    kind: str
    result: Any
    error: dict[str, Any] | None


@dataclass(frozen=True)
class FinishDecision:
    classification: str
    message: str
    evidence_call_ids: tuple[str, ...]
    pending_id: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _crm_request_tool() -> dict:
    branches = []
    for operation, entry in _CONTRACT["operations"].items():
        branches.append({
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "arguments"],
            "properties": {
                "operation": {
                    "const": operation,
                    "description": entry["description"],
                },
                "arguments": deepcopy(entry["arguments"]),
            },
        })
    return {
        "type": "function",
        "function": {
            "name": CRM_REQUEST_TOOL,
            "description": (
                "Read the local CRM or propose one reviewed CRM change. "
                "Use only contract arguments and never invent CRM facts."
            ),
            "parameters": {"oneOf": branches},
        },
    }


def _finish_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": FINISH_TOOL,
            "description": "Finish with a classification supported by collected CRM evidence.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["classification", "message", "evidence_call_ids"],
                "properties": {
                    "classification": {
                        "type": "string",
                        "enum": ["answered", "queued", "needs_clarification", "failed"],
                    },
                    "message": {"type": "string", "maxLength": 4000},
                    "evidence_call_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                        "maxItems": 6,
                    },
                    "pending_id": {"type": "integer", "minimum": 1},
                },
            },
        },
    }


def _tool_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message[:400]}, separators=(",", ":"))


def _receipt_payload(receipt: CrmCallReceipt) -> dict:
    payload = {
        "ok": receipt.ok,
        "operation": receipt.operation,
        "kind": receipt.kind,
    }
    if receipt.ok:
        payload["result"] = receipt.result
    else:
        payload["error"] = receipt.error
    return payload


def _safe_local_argument_message(exc: Exception) -> str:
    message = str(exc)
    return message if len(message) <= 256 and _SAFE_ARGUMENT_ERROR_RE.fullmatch(message) else "Invalid CRM arguments"


def _local_error(call_id: str, operation: str, message: str) -> CrmCallReceipt:
    safe_operation = operation if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", operation) else "unknown"
    return CrmCallReceipt(
        call_id,
        safe_operation,
        False,
        "error",
        None,
        {"code": "invalid_arguments", "message": message[:256], "retryable": False},
    )


def _normalize_gateway_receipt(call_id: str, operation: str, payload: object) -> CrmCallReceipt:
    if not isinstance(payload, dict):
        return _invalid_gateway_receipt(call_id, operation)
    if payload.get("operation") != operation or payload.get("kind") not in {
        "read", "proposal", "narrative", "validated_write", "error",
    }:
        return _invalid_gateway_receipt(call_id, operation)
    if payload.get("ok") is True and set(payload) == {"ok", "operation", "kind", "result"}:
        kind = payload["kind"]
        result = payload["result"]
        if kind == "proposal" and not _is_pending_result(result, operation):
            return _invalid_gateway_receipt(call_id, operation)
        return CrmCallReceipt(call_id, operation, True, kind, result, None)
    if payload.get("ok") is False and set(payload) == {"ok", "operation", "kind", "error"}:
        error = payload["error"]
        if (
            payload["kind"] == "error"
            and isinstance(error, dict)
            and set(error) == {"code", "message", "retryable"}
            and error.get("code") in _SAFE_ERROR_CODES
            and isinstance(error.get("message"), str)
            and len(error["message"]) <= 256
            and isinstance(error.get("retryable"), bool)
        ):
            return CrmCallReceipt(call_id, operation, False, "error", None, dict(error))
    return _invalid_gateway_receipt(call_id, operation)


def _invalid_gateway_receipt(call_id: str, operation: str) -> CrmCallReceipt:
    return CrmCallReceipt(
        call_id,
        operation,
        False,
        "error",
        None,
        {
            "code": "operation_failed",
            "message": "CRM operation returned an invalid receipt",
            "retryable": False,
        },
    )


def _is_pending_result(result: object, operation: str | None = None) -> bool:
    return (
        isinstance(result, dict)
        and result.get("pending") is True
        and isinstance(result.get("id"), int)
        and not isinstance(result.get("id"), bool)
        and result["id"] >= 1
        and result.get("status") == "pending"
        and isinstance(result.get("summary"), str)
        and bool(result["summary"].strip())
        and (operation is None or result.get("operation", operation) == operation)
    )


def _validate_finish_shape(params: object) -> str | None:
    if not isinstance(params, dict):
        return "Finish arguments must be an object"
    allowed = {"classification", "message", "evidence_call_ids", "pending_id"}
    required = {"classification", "message", "evidence_call_ids"}
    if set(params) - allowed or not required.issubset(params):
        return "Finish arguments do not match the required schema"
    if params["classification"] not in {"answered", "queued", "needs_clarification", "failed"}:
        return "Finish classification is invalid"
    if not isinstance(params["message"], str) or len(params["message"]) > 4000:
        return "Finish message is invalid"
    evidence = params["evidence_call_ids"]
    if (
        not isinstance(evidence, list)
        or len(evidence) > 6
        or any(not isinstance(item, str) or not item for item in evidence)
        or len(set(evidence)) != len(evidence)
    ):
        return "Finish evidence is invalid"
    if "pending_id" in params and (
        not isinstance(params["pending_id"], int)
        or isinstance(params["pending_id"], bool)
        or params["pending_id"] < 1
    ):
        return "Finish pending ID is invalid"
    return None


def validate_finish(params: object, receipts: list[CrmCallReceipt]) -> FinishDecision:
    """Validate a model's finish request against exact call-scoped evidence."""
    shape_error = _validate_finish_shape(params)
    if shape_error:
        return FinishDecision("failed", "", (), error=shape_error)
    assert isinstance(params, dict)
    classification = params["classification"]
    message = params["message"].strip()
    evidence_ids = tuple(params["evidence_call_ids"])
    pending_id = params.get("pending_id")
    by_id = {receipt.call_id: receipt for receipt in receipts}
    if any(call_id not in by_id for call_id in evidence_ids):
        return FinishDecision(classification, message, evidence_ids, pending_id, "Finish evidence references an unknown call")
    evidence = [by_id[call_id] for call_id in evidence_ids]

    if classification == "queued":
        proposals = [
            receipt for receipt in evidence
            if receipt.ok and receipt.kind == "proposal" and _is_pending_result(receipt.result, receipt.operation)
        ]
        if len(proposals) != 1:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Queued requires exactly one successful pending proposal receipt")
        if pending_id != proposals[0].result["id"]:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Queued pending ID does not match the proposal receipt")
    elif classification == "answered":
        if not any(receipt.ok and receipt.kind in {"read", "narrative", "validated_write"} for receipt in evidence):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Answered requires successful read or narrative evidence")
        if any(receipt.ok and receipt.kind == "proposal" for receipt in receipts):
            return FinishDecision(classification, message, evidence_ids, pending_id, "A pending proposal must be reported as queued")
        if _MUTATION_SUCCESS_RE.search(message):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Answered cannot claim a CRM mutation succeeded")
    elif classification == "failed":
        if not any(not receipt.ok and receipt.kind == "error" for receipt in evidence):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Failed requires structured error evidence")
    else:
        if not message or "?" not in message:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification requires one question")
        if message.count("?") != 1:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification must contain exactly one question")
        if _MUTATION_SUCCESS_RE.search(message):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification cannot claim a CRM mutation succeeded")

    return FinishDecision(classification, message, evidence_ids, pending_id)


def _format_directory(result: object) -> str:
    if not isinstance(result, dict):
        return "Lead directory unavailable."
    total = result.get("total")
    offset = result.get("offset")
    leads = result.get("leads")
    if not isinstance(total, int) or not isinstance(offset, int) or not isinstance(leads, list):
        return "Lead directory unavailable."
    rendered = []
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        bits = [f"ID {lead.get('id')}"]
        for key in ("status", "score", "area", "intent"):
            value = lead.get(key)
            if value is not None:
                bits.append(f"score {value}" if key == "score" else str(value))
        if lead.get("is_neglected") == 1:
            bits.append("neglected")
        rendered.append(f"{lead.get('name') or 'Unnamed lead'} ({', '.join(bits)})")
    page = "; ".join(rendered) if rendered else "none on this page"
    return f"{total} leads total. Showing {len(rendered)} (offset {offset}): {page}."


def _format_leads(result: object) -> str:
    if not isinstance(result, list):
        return "Lead list unavailable."
    if not result:
        return "0 leads found."
    names = [
        f"{lead.get('name') or 'Unnamed lead'} (ID {lead.get('id')}, {lead.get('status') or 'status unknown'})"
        for lead in result if isinstance(lead, dict)
    ]
    return f"{len(result)} leads found: " + "; ".join(names) + "."


def _format_metrics(result: object) -> str:
    if not isinstance(result, dict):
        return "Dashboard metrics unavailable."
    average = result.get("avg_response_minutes")
    average_text = "average response unavailable" if average is None else f"average response {average} minutes"
    return (
        f"Dashboard metrics: {result.get('active_leads')} active leads; "
        f"{result.get('high_priority')} high priority; {result.get('followups_due')} follow-ups due; "
        f"{result.get('appointments_booked')} appointments booked; {average_text}; "
        f"agent mode {result.get('agent_mode')}; {result.get('cloud_llm_requests')} cloud LLM requests."
    )


def _format_availability(result: object) -> str:
    if not isinstance(result, list) or not result:
        return "No available slots were returned."
    slots = [
        f"{slot.get('start_ts')} to {slot.get('end_ts')}"
        for slot in result if isinstance(slot, dict)
    ]
    return "Available slots: " + "; ".join(slots) + "."


def _format_appointments(result: object) -> str:
    if not isinstance(result, list) or not result:
        return "No appointments found."
    rows = []
    for appointment in result:
        if not isinstance(appointment, dict):
            continue
        row = (
            f"{appointment.get('lead_name') or 'Unknown lead'} (lead ID {appointment.get('lead_id')}), "
            f"{appointment.get('start_ts')} to {appointment.get('end_ts')}"
        )
        if appointment.get("location"):
            row += f" at {appointment['location']}"
        rows.append(row)
    return "Appointments: " + "; ".join(rows) + "."


def _format_lead_context(result: object) -> str:
    if not isinstance(result, dict):
        return "Lead context unavailable."
    fields = [
        f"status {result.get('status')}",
        f"score {result.get('score')}",
        f"phone {result.get('phone')}",
        f"email {result.get('email')}",
    ]
    budget = result.get("budget")
    fields.append(f"budget ${budget:,.0f}" if isinstance(budget, (int, float)) else "budget unavailable")
    fields.extend([
        f"area {result.get('area')}",
        f"timeline {result.get('timeline')}",
        f"intent {result.get('intent')}",
        "neglected" if result.get("is_neglected") == 1 else "not neglected",
    ])
    return f"{result.get('name') or 'Unnamed lead'} (lead ID {result.get('id')}): " + "; ".join(fields) + "."


def _format_read(receipt: CrmCallReceipt) -> str:
    formatters = {
        "list_lead_directory": _format_directory,
        "list_leads": _format_leads,
        "generate_dashboard_insights": _format_metrics,
        "check_availability": _format_availability,
        "list_appointments": _format_appointments,
        "get_lead_context": _format_lead_context,
    }
    formatter = formatters.get(receipt.operation)
    if formatter:
        return formatter(receipt.result)
    bounded = json.dumps(receipt.result, sort_keys=True, separators=(",", ":"), default=str)[:3000]
    return f"Verified {receipt.operation}: {bounded}"


def _safe_narrative(message: str) -> str:
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", message.strip()):
        if sentence and not _MUTATION_SUCCESS_RE.search(sentence):
            kept.append(sentence)
    return " ".join(kept)[:4000].strip()


def render_verified_reply(decision: FinishDecision, receipts: list[CrmCallReceipt]) -> str:
    """Render critical facts and every mutation status from receipts only."""
    by_id = {receipt.call_id: receipt for receipt in receipts}
    evidence = [by_id[item] for item in decision.evidence_call_ids if item in by_id]
    if decision.classification == "queued":
        proposal = next((receipt for receipt in evidence if receipt.ok and receipt.kind == "proposal"), None)
        if proposal and _is_pending_result(proposal.result, proposal.operation):
            result = proposal.result
            return (
                f"Queued Pending approval #{result['id']}: {result['summary'].strip()}. "
                f"Status: {result['status']}; the change has not been applied."
            )[:4000]
    if decision.classification == "failed":
        failure = next((receipt for receipt in reversed(evidence) if not receipt.ok and receipt.error), None)
        if failure:
            return (
                "Nothing was queued or changed. "
                f"[{failure.error['code']}] {failure.error['message'].rstrip('.')}."
            )[:4000]
        return "Nothing was queued or changed. The CRM request could not be verified."
    if decision.classification == "needs_clarification":
        question = decision.message.split("?", 1)[0].strip()
        return (question + "?")[:4000]
    rendered = []
    for receipt in evidence:
        if receipt.ok and receipt.kind in {"read", "validated_write"}:
            rendered.append(_format_read(receipt))
    if rendered:
        return "\n".join(rendered)[:4000]
    narrative = _safe_narrative(decision.message)
    return narrative or "I couldn't produce a verified answer from the CRM evidence."


def _single_proposal_reply(receipts: list[CrmCallReceipt]) -> str | None:
    proposals = [
        receipt for receipt in receipts
        if receipt.ok and receipt.kind == "proposal"
        and _is_pending_result(receipt.result, receipt.operation)
    ]
    if len(proposals) != 1:
        return None
    proposal = proposals[0]
    return render_verified_reply(
        FinishDecision(
            "queued", "", (proposal.call_id,), proposal.result["id"]
        ),
        receipts,
    )


def _model_message(data: object) -> dict:
    try:
        choices = data["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise ValueError
        return message
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("invalid completion response") from None


def _append_cardinality_correction(messages: list[dict], assistant: dict, calls: list) -> None:
    correction = "Exactly one structured client-tool call is required per model round; no calls were executed."
    messages.append(assistant)
    if calls:
        for index, call in enumerate(calls):
            call_id = call.get("id") if isinstance(call, dict) else None
            if isinstance(call_id, str) and call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_error(correction)})
            elif index == 0:
                messages.append({"role": "user", "content": correction})
    else:
        messages.append({"role": "user", "content": correction})


async def run_verified_crm_chat(
    gateway,
    message: str,
    session_id: str,
    agent_id: str,
) -> str:
    """Run a bounded required-client-tool loop and return only verified prose."""
    messages = [
        {
            "role": "system",
            "content": (
                "For each round call exactly one provided function. Use CRM requests for facts/actions "
                "and finish only with evidence. CRM writes are reviewed proposals, never applied changes."
            ),
        },
        {"role": "user", "content": message},
    ]
    tools = [_crm_request_tool(), _finish_tool()]
    receipts: list[CrmCallReceipt] = []
    crm_calls = 0
    seen_call_ids: set[str] = set()
    model = f"openclaw/{agent_id.strip()}" if agent_id.strip() else "openclaw"

    for _round in range(MAX_MODEL_ROUNDS):
        payload = {
            "model": model,
            "user": session_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
        }
        try:
            data = await gateway.chat_completion(payload, channel=DASHBOARD_CHANNEL)
            assistant = _model_message(data)
        except Exception:
            return _single_proposal_reply(receipts) or UNAVAILABLE_REPLY
        calls = assistant.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) != 1:
            _append_cardinality_correction(messages, assistant, calls if isinstance(calls, list) else [])
            continue
        call = calls[0]
        try:
            call_id = call["id"]
            function = call["function"]
            function_name = function["name"]
            params = json.loads(function["arguments"])
            if (
                call.get("type") != "function"
                or not isinstance(call_id, str)
                or not call_id
                or not isinstance(function_name, str)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            messages.append(assistant)
            call_id = call.get("id") if isinstance(call, dict) else None
            if isinstance(call_id, str) and call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_error("Malformed function call arguments; no CRM call was executed")})
            else:
                messages.append({"role": "user", "content": "Malformed function call; no CRM call was executed."})
            continue

        messages.append(assistant)
        if call_id in seen_call_ids:
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": _tool_error(
                    "This tool call ID was already handled; no CRM call was executed"
                ),
            })
            continue
        seen_call_ids.add(call_id)
        if function_name == FINISH_TOOL:
            decision = validate_finish(params, receipts)
            if decision.ok:
                return render_verified_reply(decision, receipts)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_error(decision.error or "Finish evidence is invalid")})
            continue
        if function_name != CRM_REQUEST_TOOL:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_error("Unknown client function; no CRM call was executed")})
            continue
        if not isinstance(params, dict) or set(params) != {"operation", "arguments"}:
            receipt = _local_error(call_id, "unknown", "Invalid CRM arguments")
        else:
            operation = params.get("operation")
            arguments = params.get("arguments")
            if not isinstance(operation, str):
                receipt = _local_error(call_id, "unknown", "Invalid CRM arguments")
            else:
                try:
                    validated = _CONTRACT_MODULE.validate_arguments(operation, arguments)
                except (TypeError, ValueError) as exc:
                    receipt = _local_error(call_id, operation, _safe_local_argument_message(exc))
                else:
                    already_has_proposal = any(
                        item.ok and item.kind == "proposal" for item in receipts
                    )
                    if (
                        _CONTRACT["operations"][operation]["effect"] == "proposal"
                        and already_has_proposal
                    ):
                        receipt = CrmCallReceipt(
                            call_id,
                            operation,
                            False,
                            "error",
                            None,
                            {
                                "code": "invalid_arguments",
                                "message": "Only one reviewed proposal is allowed per dashboard turn",
                                "retryable": False,
                            },
                        )
                    elif crm_calls >= MAX_CRM_CALLS:
                        return _single_proposal_reply(receipts) or CALL_LIMIT_REPLY
                    else:
                        crm_calls += 1
                        try:
                            raw_receipt = await gateway.invoke_tool(
                                "openhouse_crm",
                                {"operation": operation, "arguments": validated},
                                agent_id=agent_id,
                                session_key=f"dashboard:{session_id}",
                                idempotency_key=f"ohi:{session_id}:{call_id}",
                            )
                        except Exception:
                            return AMBIGUOUS_INVOKE_REPLY
                        receipt = _normalize_gateway_receipt(call_id, operation, raw_receipt)
        receipts.append(receipt)
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(_receipt_payload(receipt), separators=(",", ":"), default=str),
        })
    proposal_reply = _single_proposal_reply(receipts)
    if proposal_reply:
        return proposal_reply
    last_error = next(
        (receipt for receipt in reversed(receipts) if not receipt.ok and receipt.error),
        None,
    )
    if last_error:
        return render_verified_reply(
            FinishDecision("failed", "", (last_error.call_id,)), receipts
        )
    return ROUND_LIMIT_REPLY
