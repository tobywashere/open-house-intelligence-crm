"""Verified dashboard CRM orchestration and evidence rendering."""

import asyncio
import json

import pytest

from app.agent.crm_chat import (
    CRM_REQUEST_TOOL,
    DASHBOARD_CHANNEL,
    FINISH_TOOL,
    MAX_CRM_CALLS,
    MAX_MODEL_ROUNDS,
    CrmCallReceipt,
    FinishDecision,
    render_verified_reply,
    run_verified_crm_chat,
)
from app.agent.openclaw_gateway import OpenClawGatewayError
from app.db import get_conn
from app.routers import chat as chat_router


def _completion(*calls):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": list(calls),
            }
        }]
    }


def tool_call(call_id, name, params, *, raw=False):
    arguments = params if raw else json.dumps(params)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def finish_call(classification, message, evidence_call_ids, pending_id=None, *, call_id="finish"):
    params = {
        "classification": classification,
        "message": message,
        "evidence_call_ids": evidence_call_ids,
    }
    if pending_id is not None:
        params["pending_id"] = pending_id
    return _completion(tool_call(call_id, FINISH_TOOL, params))


def request_call(call_id, operation, arguments):
    return _completion(tool_call(call_id, CRM_REQUEST_TOOL, {
        "operation": operation,
        "arguments": arguments,
    }))


class ScriptedGateway:
    def __init__(self, chat_responses, invoke_responses=()):
        self.chat_responses = list(chat_responses)
        self.invoke_responses = list(invoke_responses)
        self.chat_calls = []
        self.invoke_calls = []

    async def chat_completion(self, payload, *, channel=None):
        self.chat_calls.append({"payload": payload, "channel": channel})
        if not self.chat_responses:
            raise AssertionError("unexpected model round")
        response = self.chat_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def invoke_tool(self, name, args, *, agent_id, session_key, idempotency_key):
        self.invoke_calls.append({
            "name": name,
            "args": args,
            "agent_id": agent_id,
            "session_key": session_key,
            "idempotency_key": idempotency_key,
        })
        if not self.invoke_responses:
            raise AssertionError("unexpected CRM invocation")
        response = self.invoke_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def run(gateway, message="question", session_id="dashboard"):
    return asyncio.run(run_verified_crm_chat(
        gateway, message, session_id, "openhouse-crm"
    ))


def read_receipt(operation, result):
    return {"ok": True, "operation": operation, "kind": "read", "result": result}


def proposal_receipt(operation, pending_id, summary):
    return {
        "ok": True,
        "operation": operation,
        "kind": "proposal",
        "result": {
            "pending": True,
            "id": pending_id,
            "operation": operation,
            "status": "pending",
            "summary": summary,
        },
    }


def error_receipt(operation, code, message, retryable=False):
    return {
        "ok": False,
        "operation": operation,
        "kind": "error",
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def test_lead_directory_call_renders_exact_total_and_current_page():
    gateway = ScriptedGateway(
        [
            request_call("call-1", "list_lead_directory", {"offset": 0, "limit": 2}),
            finish_call("answered", "There are probably fourteen.", ["call-1"]),
        ],
        [read_receipt("list_lead_directory", {
            "total": 15,
            "offset": 0,
            "limit": 2,
            "leads": [
                {"id": 4, "name": "Jordan Ellis", "status": "new", "score": 72,
                 "area": "Kirkland", "intent": "buy", "is_neglected": 0},
                {"id": 8, "name": "Alex Rivera", "status": "contacted", "score": 61,
                 "area": "Bellevue", "intent": "sell", "is_neglected": 1},
            ],
        })],
    )

    reply = run(gateway, "How many leads do I have?")

    assert reply == (
        "15 leads total. Showing 2 (offset 0): Jordan Ellis (ID 4, new, score 72, "
        "Kirkland, buy); Alex Rivera (ID 8, contacted, score 61, Bellevue, sell, neglected)."
    )
    assert gateway.invoke_calls[0]["idempotency_key"] == "ohi:dashboard:call-1"
    assert gateway.invoke_calls[0]["session_key"] == "dashboard:dashboard"


def test_create_lead_renders_exact_pending_id_without_applied_claim():
    gateway = ScriptedGateway(
        [
            request_call("call-create", "create_lead", {"name": "Jordan Ellis"}),
            finish_call("queued", "Jordan was created and saved.", ["call-create"], 41),
        ],
        [proposal_receipt("create_lead", 41, "Create lead Jordan Ellis")],
    )

    reply = run(gateway, "Add Jordan Ellis")

    assert reply == (
        "Queued Pending approval #41: Create lead Jordan Ellis. Status: pending; "
        "the change has not been applied."
    )
    assert "created" not in reply.lower()
    assert len(gateway.invoke_calls) == 1


def test_failed_write_cannot_claim_pending_or_reach_gateway():
    gateway = ScriptedGateway([
        request_call("call-1", "create_lead", {"name": "Jordan", "source_note": "open house"}),
        finish_call("failed", "I created it", ["call-1"]),
    ])

    reply = run(gateway, "Add Jordan")

    assert reply == (
        "Nothing was queued or changed. [invalid_arguments] Unsupported argument: source_note."
    )
    assert "created" not in reply.lower()
    assert gateway.invoke_calls == []


def test_false_queued_finish_is_returned_as_tool_error_and_never_becomes_reply():
    gateway = ScriptedGateway([
        finish_call("queued", "Created it.", [], 9, call_id="bad-finish"),
        finish_call("needs_clarification", "What name should I use?", [], call_id="good-finish"),
    ])

    reply = run(gateway, "Add a lead")

    assert reply == "What name should I use?"
    followup_messages = gateway.chat_calls[1]["payload"]["messages"]
    tool_message = next(message for message in followup_messages if message.get("tool_call_id") == "bad-finish")
    assert json.loads(tool_message["content"])["ok"] is False
    assert "pending" in json.loads(tool_message["content"])["error"].lower()


def test_mismatched_pending_id_is_rejected():
    gateway = ScriptedGateway(
        [
            request_call("call-book", "book_appointment", {
                "lead_id": 4, "start_ts": "2026-08-24T17:00:00",
                "end_ts": "2026-08-24T17:30:00",
            }),
            finish_call("queued", "Queued.", ["call-book"], 99, call_id="bad-finish"),
            finish_call("queued", "Queued.", ["call-book"], 12, call_id="good-finish"),
        ],
        [proposal_receipt("book_appointment", 12, "Book Jordan at 5:00 PM")],
    )

    reply = run(gateway)

    assert "#12" in reply
    assert "#99" not in reply
    correction = next(
        message for message in gateway.chat_calls[2]["payload"]["messages"]
        if message.get("tool_call_id") == "bad-finish"
    )
    assert "does not match" in json.loads(correction["content"])["error"]


def test_verified_proposal_takes_precedence_if_later_model_round_fails():
    gateway = ScriptedGateway(
        [
            request_call("write", "create_lead", {"name": "Jordan"}),
            finish_call("answered", "Jordan was created.", [], call_id="false-finish"),
            OpenClawGatewayError("gateway timeout"),
        ],
        [proposal_receipt("create_lead", 17, "Create lead Jordan")],
    )

    reply = run(gateway)

    assert reply == (
        "Queued Pending approval #17: Create lead Jordan. Status: pending; "
        "the change has not been applied."
    )
    assert len(gateway.invoke_calls) == 1


def test_three_round_booking_uses_one_pending_receipt():
    gateway = ScriptedGateway(
        [
            request_call("read-lead", "get_lead_context", {"lead_id": 4}),
            request_call("read-slots", "check_availability", {"date": "2026-08-24"}),
            request_call("write-book", "book_appointment", {
                "lead_id": 4, "start_ts": "2026-08-24T17:00:00",
                "end_ts": "2026-08-24T17:30:00",
            }),
            finish_call("queued", "Booked and completed.", ["write-book"], 23),
        ],
        [
            read_receipt("get_lead_context", {"id": 4, "name": "Jordan Ellis"}),
            read_receipt("check_availability", [{
                "start_ts": "2026-08-24T17:00:00", "end_ts": "2026-08-24T17:30:00"
            }]),
            proposal_receipt("book_appointment", 23, "Book Jordan Ellis from 5:00 to 5:30 PM"),
        ],
    )

    reply = run(gateway, "Book Jordan Monday at five")

    assert reply.startswith("Queued Pending approval #23:")
    assert len(gateway.invoke_calls) == 3
    assert sum(call["args"]["operation"] == "book_appointment" for call in gateway.invoke_calls) == 1


def test_multiple_client_calls_execute_none_and_get_bounded_correction():
    two_calls = _completion(
        tool_call("one", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
        tool_call("two", CRM_REQUEST_TOOL, {"operation": "create_lead", "arguments": {"name": "Nope"}}),
    )
    gateway = ScriptedGateway([
        two_calls,
        finish_call("needs_clarification", "Which action should I take?", []),
    ])

    assert run(gateway) == "Which action should I take?"
    assert gateway.invoke_calls == []
    corrections = [m for m in gateway.chat_calls[1]["payload"]["messages"] if m["role"] == "tool"]
    assert len(corrections) == 2
    assert all(len(m["content"]) <= 500 for m in corrections)
    assert all("exactly one" in m["content"].lower() for m in corrections)


@pytest.mark.parametrize("bad_response", [
    _completion(tool_call("bad-json", CRM_REQUEST_TOOL, "{", raw=True)),
    _completion(tool_call("unknown", "invented_function", {})),
    _completion(),
])
def test_bad_model_calls_execute_nothing_and_can_be_corrected(bad_response):
    gateway = ScriptedGateway([
        bad_response,
        finish_call("needs_clarification", "Could you clarify what you want?", []),
    ])

    assert run(gateway) == "Could you clarify what you want?"
    assert gateway.invoke_calls == []


def test_repeated_malformed_calls_hit_round_limit_with_truthful_failure():
    malformed = _completion(tool_call("bad", CRM_REQUEST_TOOL, "{", raw=True))
    gateway = ScriptedGateway([malformed] * MAX_MODEL_ROUNDS)

    reply = run(gateway)

    assert reply == "I couldn't verify a CRM answer within the allowed model rounds. No unverified action was reported."
    assert len(gateway.chat_calls) == MAX_MODEL_ROUNDS
    assert gateway.invoke_calls == []


def test_invalid_write_at_round_limit_still_reports_nothing_changed():
    malformed = _completion(tool_call("bad", CRM_REQUEST_TOOL, "{", raw=True))
    gateway = ScriptedGateway([
        request_call("invalid-write", "create_lead", {
            "name": "Jordan", "status": "new",
        }),
        *([malformed] * (MAX_MODEL_ROUNDS - 1)),
    ])

    reply = run(gateway)

    assert reply == "Nothing was queued or changed. [invalid_arguments] Unsupported argument: status."
    assert gateway.invoke_calls == []


def test_model_timeout_returns_readable_unavailable_response():
    gateway = ScriptedGateway([OpenClawGatewayError("gateway timeout")])

    reply = run(gateway)

    assert "unavailable" in reply.lower()
    assert "saved" in reply.lower()


def test_crm_call_limit_never_executes_the_seventh_request():
    chat_responses = []
    invoke_responses = []
    for index in range(MAX_CRM_CALLS + 1):
        chat_responses.append(request_call(f"read-{index}", "list_leads", {}))
        invoke_responses.append(read_receipt("list_leads", []))
    gateway = ScriptedGateway(chat_responses, invoke_responses)

    reply = run(gateway)

    assert "CRM call limit" in reply
    assert len(gateway.invoke_calls) == MAX_CRM_CALLS


def test_ambiguous_invoke_failure_is_not_retried():
    gateway = ScriptedGateway(
        [request_call("write", "create_lead", {"name": "Jordan"})],
        [OpenClawGatewayError("gateway timeout")],
    )

    reply = run(gateway)

    assert len(gateway.invoke_calls) == 1
    assert "did not retry" in reply.lower()
    assert "check pending approvals" in reply.lower()
    assert "created" not in reply.lower()


def test_repeated_tool_call_id_is_never_executed_twice():
    repeated = request_call("same-call", "list_leads", {})
    gateway = ScriptedGateway(
        [
            repeated,
            repeated,
            finish_call("answered", "One lead.", ["same-call"]),
        ],
        [read_receipt("list_leads", [{"id": 4, "name": "Jordan", "status": "new"}])],
    )

    reply = run(gateway)

    assert reply == "1 leads found: Jordan (ID 4, new)."
    assert len(gateway.invoke_calls) == 1
    duplicate_error = next(
        message for message in gateway.chat_calls[2]["payload"]["messages"]
        if message.get("tool_call_id") == "same-call" and "already" in message["content"]
    )
    assert "no CRM call was executed" in duplicate_error["content"]


def test_second_reviewed_write_in_one_turn_is_not_executed():
    gateway = ScriptedGateway(
        [
            request_call("first-write", "create_lead", {"name": "Jordan"}),
            request_call("second-write", "create_lead", {"name": "Alex"}),
            finish_call("queued", "Queued Jordan.", ["first-write"], 21),
        ],
        [proposal_receipt("create_lead", 21, "Create lead Jordan")],
    )

    reply = run(gateway)

    assert reply.startswith("Queued Pending approval #21: Create lead Jordan.")
    assert len(gateway.invoke_calls) == 1
    second_result = next(
        message for message in gateway.chat_calls[2]["payload"]["messages"]
        if message.get("tool_call_id") == "second-write"
    )
    assert "one reviewed proposal" in second_result["content"]


def test_every_model_round_has_exact_dashboard_channel_tools_and_required_choice():
    gateway = ScriptedGateway([
        request_call("read", "list_leads", {}),
        finish_call("answered", "No leads.", ["read"]),
    ], [read_receipt("list_leads", [])])

    run(gateway)

    for call in gateway.chat_calls:
        assert call["channel"] == DASHBOARD_CHANNEL == "openhouse-dashboard"
        payload = call["payload"]
        assert payload["tool_choice"] == "required"
        assert [tool["function"]["name"] for tool in payload["tools"]] == [
            CRM_REQUEST_TOOL, FINISH_TOOL,
        ]
        assert payload["model"] == "openclaw/openhouse-crm"


def test_request_tool_schema_is_strict_and_finish_schema_is_exact():
    gateway = ScriptedGateway([
        finish_call("needs_clarification", "What should I look up?", []),
    ])

    run(gateway)

    tools = gateway.chat_calls[0]["payload"]["tools"]
    request_schema = tools[0]["function"]["parameters"]
    create = next(branch for branch in request_schema["oneOf"]
                  if branch["properties"]["operation"]["const"] == "create_lead")
    assert create["additionalProperties"] is False
    assert create["required"] == ["operation", "arguments"]
    assert create["properties"]["arguments"]["additionalProperties"] is False
    assert "source_note" not in create["properties"]["arguments"]["properties"]
    assert "status" not in create["properties"]["arguments"]["properties"]
    assert tools[1]["function"]["parameters"] == {
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
    }


def _receipt(call_id, operation, result=None, *, ok=True, kind="read", error=None):
    return CrmCallReceipt(call_id, operation, ok, kind, result, error)


def _decision(classification="answered", message="Model narrative", evidence=("read",), pending_id=None):
    return FinishDecision(classification, message, tuple(evidence), pending_id)


def test_renderer_formats_dashboard_metrics_including_null_average():
    receipts = [_receipt("read", "generate_dashboard_insights", {
        "active_leads": 12, "high_priority": 3, "followups_due": 4,
        "appointments_booked": 2, "avg_response_minutes": None,
        "agent_mode": "openclaw", "cloud_llm_requests": 0,
    })]

    assert render_verified_reply(_decision(), receipts) == (
        "Dashboard metrics: 12 active leads; 3 high priority; 4 follow-ups due; "
        "2 appointments booked; average response unavailable; agent mode openclaw; "
        "0 cloud LLM requests."
    )


def test_renderer_formats_availability_slots():
    receipts = [_receipt("read", "check_availability", [
        {"start_ts": "2026-08-24T17:00:00", "end_ts": "2026-08-24T17:30:00"},
        {"start_ts": "2026-08-24T18:00:00", "end_ts": "2026-08-24T18:30:00"},
    ])]

    assert render_verified_reply(_decision(), receipts) == (
        "Available slots: 2026-08-24T17:00:00 to 2026-08-24T17:30:00; "
        "2026-08-24T18:00:00 to 2026-08-24T18:30:00."
    )


def test_renderer_formats_appointments_with_lead_names_and_times():
    receipts = [_receipt("read", "list_appointments", [{
        "id": 2, "lead_id": 4, "lead_name": "Jordan Ellis",
        "start_ts": "2026-08-24T17:00:00", "end_ts": "2026-08-24T17:30:00",
        "location": "Kirkland office",
    }])]

    assert render_verified_reply(_decision(), receipts) == (
        "Appointments: Jordan Ellis (lead ID 4), 2026-08-24T17:00:00 to "
        "2026-08-24T17:30:00 at Kirkland office."
    )


def test_renderer_formats_lead_context_identity_and_stored_fields():
    receipts = [_receipt("read", "get_lead_context", {
        "id": 4, "name": "Jordan Ellis", "status": "contacted", "score": 72,
        "phone": "+14255550111", "email": "jordan@example.com", "budget": 950000,
        "area": "Kirkland", "timeline": "6 weeks", "intent": "buy",
        "is_neglected": 0, "events": [], "appointments": [],
    })]

    assert render_verified_reply(_decision(), receipts) == (
        "Jordan Ellis (lead ID 4): status contacted; score 72; phone +14255550111; "
        "email jordan@example.com; budget $950,000; area Kirkland; timeline 6 weeks; intent buy; not neglected."
    )


def test_renderer_formats_pending_proposal_from_receipt_only():
    receipts = [_receipt("write", "create_lead", {
        "pending": True, "id": 31, "operation": "create_lead", "status": "pending",
        "summary": "Create lead Jordan Ellis",
    }, kind="proposal")]

    reply = render_verified_reply(
        _decision("queued", "Created and applied.", ("write",), 31), receipts
    )

    assert reply == (
        "Queued Pending approval #31: Create lead Jordan Ellis. Status: pending; "
        "the change has not been applied."
    )


def test_renderer_formats_only_sanitized_error_code_and_message():
    receipts = [_receipt(
        "write", "book_appointment", ok=False, kind="error", result=None,
        error={"code": "schedule_conflict", "message": "Requested schedule conflicts with an existing appointment", "retryable": False},
    )]

    assert render_verified_reply(
        _decision("failed", "I booked it.", ("write",)), receipts
    ) == (
        "Nothing was queued or changed. [schedule_conflict] Requested schedule conflicts with an existing appointment."
    )


def test_narrative_message_is_bounded_and_strips_unsupported_mutation_success():
    receipts = [_receipt("narrative", "draft_followup", "Hi Jordan, ...", kind="narrative")]
    decision = _decision(
        "answered",
        "Here is the draft. I also created and saved a new lead. " + ("x" * 5000),
        ("narrative",),
    )

    reply = render_verified_reply(decision, receipts)

    assert reply.startswith("Here is the draft.")
    assert "created" not in reply.lower()
    assert "saved" not in reply.lower()
    assert len(reply) <= 4000


def test_clarification_is_one_question_and_has_no_success_claim():
    gateway = ScriptedGateway([
        finish_call(
            "needs_clarification",
            "I created it. Which Jordan do you mean? Should I use the email match?",
            [],
            call_id="bad",
        ),
        finish_call("needs_clarification", "Which Jordan do you mean?", []),
    ])

    reply = run(gateway)

    assert reply == "Which Jordan do you mean?"
    assert reply.count("?") == 1


def test_chat_route_persists_only_final_verified_reply(client, monkeypatch):
    class Driver:
        name = "openclaw"

        async def chat(self, message, session_id):
            return "Queued Pending approval #7: Create lead Jordan. Status: pending; the change has not been applied."

    monkeypatch.setattr(chat_router, "get_driver", lambda: Driver())

    response = client.post("/api/chat", json={"message": "Add Jordan", "session_id": "verified"})

    assert response.json() == {
        "reply": "Queued Pending approval #7: Create lead Jordan. Status: pending; the change has not been applied.",
        "session_id": "verified",
    }
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id",
            ("verified",),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("user", "Add Jordan"),
        ("agent", "Queued Pending approval #7: Create lead Jordan. Status: pending; the change has not been applied."),
    ]
