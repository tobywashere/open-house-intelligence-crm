"""Evidence-verified CRM orchestration for dashboard chat."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any

from .openclaw_gateway import OpenClawGatewayError


DASHBOARD_CHANNEL = "openhouse-dashboard"
MAX_MODEL_ROUNDS = 8
MAX_CRM_CALLS = 6
MAX_RECEIPT_BYTES = 32 * 1024
DEFAULT_DEADLINE_SECONDS = 120.0
DEADLINE_ENV = "CRM_CHAT_DEADLINE_SECONDS"

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
    "The CRM change may have reached the backend, but its outcome is unknown. "
    "I did not retry it. Do not retry automatically. "
    "Check Pending approvals before trying again."
)
DEADLINE_REPLY = (
    "I couldn't verify a CRM answer within the total time limit. "
    "No unverified action was reported."
)
CLARIFICATION_REPLY = "What information should I use to continue?"

_SAFE_ERROR_CODES = frozenset({
    "invalid_arguments", "not_found", "ambiguous_match", "schedule_conflict",
    "backend_unavailable", "timeout", "result_too_large", "operation_failed",
    "outcome_unknown",
})
_SAFE_ERROR_MESSAGES = {
    "invalid_arguments": "Invalid CRM arguments",
    "not_found": "CRM record was not found",
    "ambiguous_match": "CRM record match is ambiguous",
    "schedule_conflict": "Requested schedule conflicts with an existing appointment",
    "backend_unavailable": "CRM backend is unavailable",
    "timeout": "CRM operation timed out",
    "result_too_large": "CRM operation returned too much data",
    "operation_failed": "CRM operation failed",
    "outcome_unknown": "CRM mutation outcome is unknown",
}
_INVALID_RECEIPT_MESSAGE = "CRM operation returned an invalid receipt"
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


def _load_client_tools_module():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "skills" / "crm-db-operations" / "client_tools.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_openhouse_dashboard_client_tools", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("CRM client-tool contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONTRACT_MODULE = _load_contract_module()
_CONTRACT = _CONTRACT_MODULE.CONTRACT
_CLIENT_TOOLS_MODULE = _load_client_tools_module()
CRM_REQUEST_TOOL = _CLIENT_TOOLS_MODULE.CRM_REQUEST_TOOL
FINISH_TOOL = _CLIENT_TOOLS_MODULE.FINISH_TOOL
_MUTATING_EFFECTS = frozenset({"proposal", "validated_write"})


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
    return _CLIENT_TOOLS_MODULE.build_dashboard_client_tools(_CONTRACT)[0]


def _finish_tool() -> dict:
    return _CLIENT_TOOLS_MODULE.build_dashboard_client_tools(_CONTRACT)[1]


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


def _trusted_error_message(code: str, message: object = None) -> str:
    if (
        code == "invalid_arguments"
        and isinstance(message, str)
        and len(message) <= 256
        and _SAFE_ARGUMENT_ERROR_RE.fullmatch(message)
    ):
        return message
    if code == "operation_failed" and message == _INVALID_RECEIPT_MESSAGE:
        return _INVALID_RECEIPT_MESSAGE
    return _SAFE_ERROR_MESSAGES.get(code, _SAFE_ERROR_MESSAGES["operation_failed"])


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


def _is_mutating_operation(operation: str) -> bool:
    entry = _CONTRACT["operations"].get(operation)
    return bool(entry and entry["effect"] in _MUTATING_EFFECTS)


def _unknown_outcome_receipt(call_id: str, operation: str) -> CrmCallReceipt:
    return CrmCallReceipt(
        call_id,
        operation,
        False,
        "error",
        None,
        {
            "code": "outcome_unknown",
            "message": _SAFE_ERROR_MESSAGES["outcome_unknown"],
            "retryable": False,
        },
    )


def _definite_invoke_failure_receipt(
    call_id: str, operation: str
) -> CrmCallReceipt:
    return CrmCallReceipt(
        call_id,
        operation,
        False,
        "error",
        None,
        {
            "code": "operation_failed",
            "message": _SAFE_ERROR_MESSAGES["operation_failed"],
            "retryable": False,
        },
    )


def _is_unknown_outcome(receipt: CrmCallReceipt) -> bool:
    return (
        not receipt.ok
        and isinstance(receipt.error, dict)
        and receipt.error.get("code") == "outcome_unknown"
    )


def _json_size(value: object) -> int | None:
    try:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
    except (TypeError, ValueError, OverflowError):
        return None


def _is_int(value: object, *, minimum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (minimum is None or value >= minimum)
    )


def _is_number_or_none(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_nonblank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_lead_row(row: object, *, require_status: bool = True) -> bool:
    return (
        isinstance(row, dict)
        and _is_int(row.get("id"), minimum=1)
        and _is_nonblank_text(row.get("name"))
        and (not require_status or _is_nonblank_text(row.get("status")))
        and (
            "score" not in row
            or row["score"] is None
            or (_is_int(row["score"], minimum=0) and row["score"] <= 100)
        )
        and ("area" not in row or row["area"] is None or _is_nonblank_text(row["area"]))
        and ("timeline" not in row or row["timeline"] is None or _is_nonblank_text(row["timeline"]))
        and ("intent" not in row or row["intent"] is None or _is_nonblank_text(row["intent"]))
        and (
            "is_neglected" not in row
            or (
                _is_int(row["is_neglected"], minimum=0)
                and row["is_neglected"] <= 1
            )
        )
    )


def _valid_common_result(operation: str, result: object) -> bool:
    if operation == "list_lead_directory":
        return (
            isinstance(result, dict)
            and _is_int(result.get("total"), minimum=0)
            and _is_int(result.get("offset"), minimum=0)
            and _is_int(result.get("limit"), minimum=1)
            and result["limit"] <= 50
            and isinstance(result.get("leads"), list)
            and len(result["leads"]) <= result["limit"]
            and len(result["leads"]) <= result["total"]
            and all(_valid_lead_row(item) for item in result["leads"])
        )
    if operation == "list_leads":
        return isinstance(result, list) and all(_valid_lead_row(item) for item in result)
    if operation == "generate_dashboard_insights":
        count_keys = (
            "active_leads", "high_priority", "followups_due",
            "appointments_booked", "cloud_llm_requests",
        )
        return (
            isinstance(result, dict)
            and all(_is_int(result.get(key), minimum=0) for key in count_keys)
            and _is_number_or_none(result.get("avg_response_minutes"))
            and _is_nonblank_text(result.get("agent_mode"))
        )
    if operation == "check_availability":
        return (
            isinstance(result, list)
            and all(
                isinstance(item, dict)
                and _is_nonblank_text(item.get("start_ts"))
                and _is_nonblank_text(item.get("end_ts"))
                for item in result
            )
        )
    if operation == "list_appointments":
        return (
            isinstance(result, list)
            and all(
                isinstance(item, dict)
                and _is_int(item.get("lead_id"), minimum=1)
                and _is_nonblank_text(item.get("lead_name"))
                and _is_nonblank_text(item.get("start_ts"))
                and _is_nonblank_text(item.get("end_ts"))
                and (
                    "location" not in item
                    or item["location"] is None
                    or isinstance(item["location"], str)
                )
                for item in result
            )
        )
    if operation == "get_lead_context":
        return (
            _valid_lead_row(result, require_status=False)
            and all(
                key not in result
                or result[key] is None
                or _is_nonblank_text(result[key])
                for key in ("status", "phone", "email")
            )
            and (
                "budget" not in result
                or _is_number_or_none(result["budget"])
            )
            and ("events" not in result or isinstance(result["events"], list))
            and (
                "appointments" not in result
                or isinstance(result["appointments"], list)
            )
        )
    if operation == "draft_followup":
        return _is_nonblank_text(result)
    if operation == "score_lead":
        return (
            isinstance(result, dict)
            and _is_int(result.get("lead_id"), minimum=1)
            and _is_int(result.get("score"), minimum=0)
            and result["score"] <= 100
            and _is_nonblank_text(result.get("score_reason"))
        )
    return True


def _normalize_gateway_receipt(call_id: str, operation: str, payload: object) -> CrmCallReceipt:
    if not isinstance(payload, dict) or payload.get("operation") != operation:
        return _invalid_gateway_receipt(call_id, operation)
    if payload.get("ok") is False and set(payload) == {"ok", "operation", "kind", "error"}:
        error = payload["error"]
        if (
            payload.get("kind") == "error"
            and isinstance(error, dict)
            and set(error) == {"code", "message", "retryable"}
            and error.get("code") in _SAFE_ERROR_CODES
            and isinstance(error.get("retryable"), bool)
            and error["retryable"] == (error["code"] in {"backend_unavailable", "timeout"})
        ):
            code = error["code"]
            return CrmCallReceipt(
                call_id,
                operation,
                False,
                "error",
                None,
                {
                    "code": code,
                    "message": _trusted_error_message(code, error.get("message")),
                    "retryable": error["retryable"],
                },
            )
        return _invalid_gateway_receipt(call_id, operation)
    if payload.get("ok") is not True or set(payload) != {"ok", "operation", "kind", "result"}:
        return _invalid_gateway_receipt(call_id, operation)
    payload_size = _json_size(payload)
    if payload_size is None:
        return _invalid_gateway_receipt(call_id, operation)
    if payload_size > MAX_RECEIPT_BYTES:
        return _result_too_large_receipt(call_id, operation)

    expected_effect = _CONTRACT["operations"][operation]["effect"]
    kind = payload.get("kind")
    result = payload["result"]
    pending = _is_pending_result(result, operation)
    if pending:
        if expected_effect not in {"proposal", "validated_write"} or kind != "proposal":
            return _invalid_gateway_receipt(call_id, operation)
    elif kind != expected_effect or expected_effect == "proposal":
        return _invalid_gateway_receipt(call_id, operation)
    if not _valid_common_result(operation, result):
        return _invalid_gateway_receipt(call_id, operation)
    return CrmCallReceipt(call_id, operation, True, kind, result, None)


def _invalid_gateway_receipt(call_id: str, operation: str) -> CrmCallReceipt:
    if _is_mutating_operation(operation):
        return _unknown_outcome_receipt(call_id, operation)
    return CrmCallReceipt(
        call_id,
        operation,
        False,
        "error",
        None,
        {
            "code": "operation_failed",
            "message": _INVALID_RECEIPT_MESSAGE,
            "retryable": False,
        },
    )


def _result_too_large_receipt(call_id: str, operation: str) -> CrmCallReceipt:
    if _is_mutating_operation(operation):
        return _unknown_outcome_receipt(call_id, operation)
    return CrmCallReceipt(
        call_id,
        operation,
        False,
        "error",
        None,
        {
            "code": "result_too_large",
            "message": _SAFE_ERROR_MESSAGES["result_too_large"],
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
    mutation_receipts = [
        receipt for receipt in receipts
        if _is_mutating_operation(receipt.operation)
    ]
    missing_mutation_ids = [
        receipt.call_id
        for receipt in mutation_receipts
        if receipt.call_id not in evidence_ids
    ]
    if missing_mutation_ids:
        return FinishDecision(
            classification,
            message,
            evidence_ids,
            pending_id,
            "Finish evidence must include every mutation receipt; missing: "
            + ", ".join(missing_mutation_ids),
        )
    all_proposals = [
        receipt for receipt in receipts
        if receipt.ok and receipt.kind == "proposal"
        and _is_pending_result(receipt.result, receipt.operation)
    ]
    if all_proposals and classification != "queued":
        return FinishDecision(
            classification,
            message,
            evidence_ids,
            pending_id,
            "A verified pending proposal must be reported as queued",
        )

    if classification == "queued":
        proposals = [
            receipt for receipt in evidence
            if receipt.ok and receipt.kind == "proposal" and _is_pending_result(receipt.result, receipt.operation)
        ]
        if len(all_proposals) != 1 or len(proposals) != 1:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Queued requires exactly one successful pending proposal receipt")
        if pending_id != proposals[0].result["id"]:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Queued pending ID does not match the proposal receipt")
    elif classification == "answered":
        if not any(receipt.ok and receipt.kind in {"read", "narrative", "validated_write"} for receipt in evidence):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Answered requires successful read or narrative evidence")
    elif classification == "failed":
        if not any(not receipt.ok and receipt.kind == "error" for receipt in evidence):
            return FinishDecision(classification, message, evidence_ids, pending_id, "Failed requires structured error evidence")
    else:
        if not message or "?" not in message:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification requires one question")
        if message.count("?") != 1:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification must contain exactly one question")
        if re.fullmatch(
            r"(?is)(?:what|which|who|when|where|why|how|could|would|should|"
            r"do|does|did|is|are|can|may)\b[^?]*\?",
            message,
        ) is None:
            return FinishDecision(classification, message, evidence_ids, pending_id, "Clarification must be a single direct question")

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
    fields = []
    for key in ("status", "score", "phone", "email"):
        if result.get(key) is not None:
            fields.append(f"{key} {result[key]}")
    budget = result.get("budget")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool):
        fields.append(f"budget ${budget:,.0f}")
    for key in ("area", "timeline", "intent"):
        if result.get(key) is not None:
            fields.append(f"{key} {result[key]}")
    if result.get("is_neglected") in {0, 1}:
        fields.append("neglected" if result["is_neglected"] == 1 else "not neglected")
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


def _format_narrative(receipt: CrmCallReceipt) -> str:
    if receipt.operation == "draft_followup":
        return f"Draft follow-up:\n{receipt.result.strip()}"[:4000]
    if receipt.operation == "score_lead":
        result = receipt.result
        return (
            f"Lead ID {result['lead_id']} scored {result['score']}: "
            f"{result['score_reason'].rstrip('.')}."
        )[:4000]
    bounded = json.dumps(
        receipt.result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )[:3000]
    return f"Verified {receipt.operation} result: {bounded}"


def _pending_mutation_line(receipt: CrmCallReceipt) -> str:
    result = receipt.result
    prefix = f"Queued Pending approval #{result['id']}: "
    suffix = (
        f". Status: {result['status']}; the change has not been applied."
    )
    summary_limit = max(0, 600 - len(prefix) - len(suffix))
    summary = result["summary"].strip()[:summary_limit]
    return prefix + summary + suffix


def _trusted_failure_parts(receipt: CrmCallReceipt) -> tuple[str, str]:
    error = receipt.error or {}
    code = error.get("code")
    if code not in _SAFE_ERROR_CODES:
        code = "operation_failed"
    return code, _trusted_error_message(code, error.get("message"))


def _legacy_failure_reply(receipt: CrmCallReceipt) -> str:
    code, message = _trusted_failure_parts(receipt)
    if code == "outcome_unknown":
        return AMBIGUOUS_INVOKE_REPLY
    return (
        "Nothing was queued or changed. "
        f"[{code}] {message.rstrip('.')}."
    )[:4000]


def _mutation_status_line(receipt: CrmCallReceipt) -> str:
    if (
        receipt.ok
        and receipt.kind == "proposal"
        and _is_pending_result(receipt.result, receipt.operation)
    ):
        return _pending_mutation_line(receipt)
    if receipt.ok and receipt.kind == "validated_write":
        rendered = json.dumps(
            receipt.result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )[:480]
        return f"Applied {receipt.operation}: {rendered}."
    code, message = _trusted_failure_parts(receipt)
    if code == "outcome_unknown":
        return (
            f"Outcome unknown for {receipt.operation}. The CRM change may have "
            "reached the backend; it was not retried. Check Pending approvals "
            "before trying again."
        )
    return f"Failed {receipt.operation}: [{code}] {message.rstrip('.')}."


def _mutation_sequence_reply(receipts: list[CrmCallReceipt]) -> str | None:
    mutations = [
        receipt for receipt in receipts
        if _is_mutating_operation(receipt.operation)
    ]
    if not mutations:
        return None
    if len(mutations) == 1:
        only = mutations[0]
        if not only.ok:
            return _legacy_failure_reply(only)
    return "\n".join(_mutation_status_line(receipt) for receipt in mutations)[:4000]


def _evidence_failure_line(receipt: CrmCallReceipt) -> str:
    code, message = _trusted_failure_parts(receipt)
    return f"Failed {receipt.operation}: [{code}] {message.rstrip('.')}."


def render_verified_reply(decision: FinishDecision, receipts: list[CrmCallReceipt]) -> str:
    """Render critical facts and every mutation status from receipts only."""
    by_id = {receipt.call_id: receipt for receipt in receipts}
    evidence = [by_id[item] for item in decision.evidence_call_ids if item in by_id]
    mutation_receipts = [
        receipt for receipt in receipts
        if _is_mutating_operation(receipt.operation)
    ]
    if mutation_receipts:
        if (
            len(receipts) == 1
            and len(mutation_receipts) == 1
            and not mutation_receipts[0].ok
        ):
            return _legacy_failure_reply(mutation_receipts[0])
        evidence_ids = set(decision.evidence_call_ids)
        rendered = []
        for receipt in receipts:
            if _is_mutating_operation(receipt.operation):
                rendered.append(_mutation_status_line(receipt))
            elif receipt.call_id in evidence_ids:
                if decision.classification == "failed" and not receipt.ok:
                    rendered.append(_evidence_failure_line(receipt))
                elif (
                    decision.classification == "answered"
                    and receipt.ok
                    and receipt.kind in {"read", "validated_write"}
                ):
                    rendered.append(_format_read(receipt))
                elif (
                    decision.classification == "answered"
                    and receipt.ok
                    and receipt.kind == "narrative"
                ):
                    rendered.append(_format_narrative(receipt))
        if rendered:
            return "\n".join(line[:600] for line in rendered)[:4000]
    if decision.classification == "queued":
        proposal = next((receipt for receipt in evidence if receipt.ok and receipt.kind == "proposal"), None)
        if proposal and _is_pending_result(proposal.result, proposal.operation):
            return _pending_mutation_line(proposal)
    if decision.classification == "failed":
        failure = next((receipt for receipt in reversed(evidence) if not receipt.ok and receipt.error), None)
        if failure:
            return _legacy_failure_reply(failure)
        return "Nothing was queued or changed. The CRM request could not be verified."
    if decision.classification == "needs_clarification":
        return CLARIFICATION_REPLY
    rendered = []
    for receipt in evidence:
        if receipt.ok and receipt.kind in {"read", "validated_write"}:
            rendered.append(_format_read(receipt))
        elif receipt.ok and receipt.kind == "narrative":
            rendered.append(_format_narrative(receipt))
    if rendered:
        return "\n".join(rendered)[:4000]
    return "I couldn't produce a verified answer from the CRM evidence."


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


def _valid_tool_call_shape(call: object) -> bool:
    if not isinstance(call, dict):
        return False
    function = call.get("function")
    return (
        isinstance(call.get("id"), str)
        and bool(call["id"])
        and call.get("type") == "function"
        and isinstance(function, dict)
        and isinstance(function.get("name"), str)
        and bool(function["name"])
        and isinstance(function.get("arguments"), str)
    )


def _assistant_tool_message(calls: list[dict]) -> dict:
    sanitized = [
        {
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["function"]["name"],
                "arguments": call["function"]["arguments"],
            },
        }
        for call in calls
    ]
    return {"role": "assistant", "content": None, "tool_calls": sanitized}


def _append_cardinality_correction(messages: list[dict], calls: list) -> None:
    correction = "Exactly one structured client-tool call is required per model round; no calls were executed."
    ids = [call.get("id") for call in calls if isinstance(call, dict)]
    valid_ids = (
        bool(calls)
        and all(_valid_tool_call_shape(call) for call in calls)
        and len(ids) == len(calls)
        and len(set(ids)) == len(ids)
    )
    if not valid_ids:
        messages.append({"role": "user", "content": correction})
        return
    messages.append(_assistant_tool_message(calls))
    for call_id in ids:
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": _tool_error(correction),
        })


def _idempotency_key(
    agent_id: str,
    session_id: str,
    turn_nonce: str,
    call_id: str,
) -> str:
    identity = json.dumps(
        [agent_id, session_id, turn_nonce, call_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return "ohi:v1:" + hashlib.sha256(identity).hexdigest()


def _terminal_reply(receipts: list[CrmCallReceipt], fallback: str) -> str:
    return _mutation_sequence_reply(receipts) or fallback


def _deadline_seconds(configured: float | None) -> float | None:
    raw: object = (
        os.environ.get(DEADLINE_ENV, str(DEFAULT_DEADLINE_SECONDS))
        if configured is None
        else configured
    )
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


async def run_verified_crm_chat(
    gateway,
    message: str,
    session_id: str,
    agent_id: str,
    *,
    deadline_seconds: float | None = None,
    monotonic=None,
) -> str:
    """Run a bounded required-client-tool loop and return only verified prose."""
    total_budget = _deadline_seconds(deadline_seconds)
    if total_budget is None:
        return DEADLINE_REPLY
    clock = time.monotonic if monotonic is None else monotonic
    deadline_at = clock() + total_budget
    turn_nonce = secrets.token_hex(16)
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
        remaining = deadline_at - clock()
        if remaining <= 0:
            return _terminal_reply(receipts, DEADLINE_REPLY)
        payload = {
            "model": model,
            "user": session_id,
            "messages": deepcopy(messages),
            "tools": tools,
            "tool_choice": "required",
        }
        try:
            data = await asyncio.wait_for(
                gateway.chat_completion(
                    payload,
                    channel=DASHBOARD_CHANNEL,
                    timeout=remaining,
                ),
                timeout=remaining,
            )
            assistant = _model_message(data)
        except TimeoutError:
            return _terminal_reply(receipts, DEADLINE_REPLY)
        except Exception:
            return _terminal_reply(receipts, UNAVAILABLE_REPLY)
        calls = assistant.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) != 1:
            _append_cardinality_correction(messages, calls if isinstance(calls, list) else [])
            continue
        call = calls[0]
        if not _valid_tool_call_shape(call):
            messages.append({
                "role": "user",
                "content": "Malformed function call; no CRM call was executed.",
            })
            continue
        call_id = call["id"]
        function = call["function"]
        function_name = function["name"]
        try:
            params = json.loads(function["arguments"])
        except (TypeError, ValueError, json.JSONDecodeError):
            messages.append(_assistant_tool_message([call]))
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": _tool_error(
                    "Malformed function call arguments; no CRM call was executed"
                ),
            })
            continue

        messages.append(_assistant_tool_message([call]))
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
                        return _terminal_reply(receipts, CALL_LIMIT_REPLY)
                    else:
                        crm_calls += 1
                        remaining = deadline_at - clock()
                        if remaining <= 0:
                            return _terminal_reply(receipts, DEADLINE_REPLY)
                        try:
                            raw_receipt = await asyncio.wait_for(
                                gateway.invoke_tool(
                                    "openhouse_crm",
                                    {"operation": operation, "arguments": validated},
                                    agent_id=agent_id,
                                    session_key=f"dashboard:{session_id}",
                                    idempotency_key=_idempotency_key(
                                        agent_id,
                                        session_id,
                                        turn_nonce,
                                        call_id,
                                    ),
                                    timeout=remaining,
                                ),
                                timeout=remaining,
                            )
                        except TimeoutError:
                            if _is_mutating_operation(operation):
                                receipt = _unknown_outcome_receipt(
                                    call_id, operation
                                )
                                receipts.append(receipt)
                                return render_verified_reply(
                                    FinishDecision(
                                        "failed", "", (receipt.call_id,)
                                    ),
                                    receipts,
                                )
                            return _terminal_reply(receipts, DEADLINE_REPLY)
                        except OpenClawGatewayError as exc:
                            if (
                                _is_mutating_operation(operation)
                                and exc.definite_pre_dispatch
                            ):
                                receipt = _definite_invoke_failure_receipt(
                                    call_id, operation
                                )
                            elif _is_mutating_operation(operation):
                                receipt = _unknown_outcome_receipt(
                                    call_id, operation
                                )
                                receipts.append(receipt)
                                return render_verified_reply(
                                    FinishDecision(
                                        "failed", "", (receipt.call_id,)
                                    ),
                                    receipts,
                                )
                            else:
                                return _terminal_reply(receipts, UNAVAILABLE_REPLY)
                        except Exception:
                            if _is_mutating_operation(operation):
                                receipt = _unknown_outcome_receipt(
                                    call_id, operation
                                )
                                receipts.append(receipt)
                                return render_verified_reply(
                                    FinishDecision(
                                        "failed", "", (receipt.call_id,)
                                    ),
                                    receipts,
                                )
                            return _terminal_reply(receipts, UNAVAILABLE_REPLY)
                        else:
                            receipt = _normalize_gateway_receipt(
                                call_id, operation, raw_receipt
                            )
        receipts.append(receipt)
        if _is_unknown_outcome(receipt):
            return render_verified_reply(
                FinishDecision("failed", "", (receipt.call_id,)), receipts
            )
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(
                _receipt_payload(receipt), separators=(",", ":"), ensure_ascii=False
            ),
        })
    mutation_reply = _mutation_sequence_reply(receipts)
    if mutation_reply:
        return mutation_reply
    last_error = next(
        (receipt for receipt in reversed(receipts) if not receipt.ok and receipt.error),
        None,
    )
    if last_error:
        return render_verified_reply(
            FinishDecision("failed", "", (last_error.call_id,)), receipts
        )
    return ROUND_LIMIT_REPLY
