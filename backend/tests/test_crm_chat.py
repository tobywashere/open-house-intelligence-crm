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

    async def chat_completion(self, payload, *, channel=None, timeout=None):
        self.chat_calls.append({
            "payload": payload,
            "channel": channel,
            "timeout": timeout,
        })
        if not self.chat_responses:
            raise AssertionError("unexpected model round")
        response = self.chat_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def invoke_tool(
        self, name, args, *, agent_id, session_key, idempotency_key, timeout=None
    ):
        self.invoke_calls.append({
            "name": name,
            "args": args,
            "agent_id": agent_id,
            "session_key": session_key,
            "idempotency_key": idempotency_key,
            "timeout": timeout,
        })
        if not self.invoke_responses:
            raise AssertionError("unexpected CRM invocation")
        response = self.invoke_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def run(gateway, message="question", session_id="dashboard", **kwargs):
    return asyncio.run(run_verified_crm_chat(
        gateway, message, session_id, "openhouse-crm", **kwargs
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
    assert gateway.invoke_calls[0]["idempotency_key"].startswith("ohi:v1:")
    assert len(gateway.invoke_calls[0]["idempotency_key"]) == len("ohi:v1:") + 64
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

    assert reply == "What information should I use to continue?"
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

    assert run(gateway) == "What information should I use to continue?"
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

    assert run(gateway) == "What information should I use to continue?"
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


def test_read_invoke_transport_failure_does_not_claim_a_mutation_may_exist():
    gateway = ScriptedGateway(
        [request_call("read", "list_leads", {})],
        [OpenClawGatewayError("gateway request failed")],
    )

    reply = run(gateway)

    assert len(gateway.invoke_calls) == 1
    assert "unavailable" in reply.lower()
    assert "crm change may have reached" not in reply.lower()
    assert "pending approvals" not in reply.lower()


def test_structured_unknown_mutation_outcome_stops_the_turn_without_retry():
    gateway = ScriptedGateway(
        [request_call("write", "create_lead", {"name": "Jordan"})],
        [error_receipt(
            "create_lead",
            "outcome_unknown",
            "private transport detail",
            retryable=False,
        )],
    )

    reply = run(gateway)

    assert len(gateway.chat_calls) == 1
    assert len(gateway.invoke_calls) == 1
    assert "outcome" in reply.lower() and "unknown" in reply.lower()
    assert "check pending approvals" in reply.lower()
    assert "do not retry" in reply.lower()
    assert "nothing was queued or changed" not in reply.lower()


def test_malformed_mutation_receipt_is_unknown_and_stops_without_retry():
    gateway = ScriptedGateway(
        [request_call("write", "create_lead", {"name": "Jordan"})],
        [{"ok": True, "truncated": True}],
    )

    reply = run(gateway)

    assert len(gateway.chat_calls) == 1
    assert len(gateway.invoke_calls) == 1
    assert "outcome" in reply.lower() and "unknown" in reply.lower()
    assert "check pending approvals" in reply.lower()
    assert "nothing was queued or changed" not in reply.lower()


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("create_lead", {"name": "Jordan"}),
        ("book_appointment", {
            "lead_id": 4,
            "start_ts": "2026-08-24T17:00:00",
            "end_ts": "2026-08-24T17:30:00",
        }),
    ],
    ids=["child_spawn", "http_403"],
)
def test_definite_mutation_failure_reaches_dashboard_as_failed(
    operation, arguments
):
    gateway = ScriptedGateway(
        [
            request_call("write", operation, arguments),
            finish_call("failed", "Failed.", ["write"]),
        ],
        [error_receipt(
            operation,
            "operation_failed",
            "CRM operation failed",
            retryable=False,
        )],
    )

    reply = run(gateway)

    assert len(gateway.chat_calls) == 2
    assert len(gateway.invoke_calls) == 1
    assert reply == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation failed."
    )
    assert "unknown" not in reply.lower()


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

    assert reply == "Draft follow-up:\nHi Jordan, ..."
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

    assert reply == "What information should I use to continue?"
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


# Fix round 1: receipt/evidence hardening regressions.


def test_failed_finish_after_proposal_cannot_hide_pending_item():
    gateway = ScriptedGateway(
        [
            request_call("proposal", "create_lead", {"name": "Jordan"}),
            request_call("rejected-write", "create_lead", {"name": "Alex"}),
            finish_call("failed", "Nothing happened.", ["rejected-write"], call_id="bad-finish"),
            finish_call("queued", "Queued.", ["proposal"], 51),
        ],
        [proposal_receipt("create_lead", 51, "Create lead Jordan")],
    )

    reply = run(gateway)

    assert reply.startswith("Queued Pending approval #51: Create lead Jordan.")
    assert len(gateway.chat_calls) == 4
    correction = next(
        message for message in gateway.chat_calls[3]["payload"]["messages"]
        if message.get("tool_call_id") == "bad-finish"
    )
    assert "proposal" in correction["content"].lower()


def test_clarification_after_proposal_cannot_hide_pending_item():
    gateway = ScriptedGateway(
        [
            request_call("proposal", "create_lead", {"name": "Jordan"}),
            finish_call("needs_clarification", "What email should I add?", [], call_id="bad-finish"),
            finish_call("queued", "Queued.", ["proposal"], 52),
        ],
        [proposal_receipt("create_lead", 52, "Create lead Jordan")],
    )

    reply = run(gateway)

    assert reply.startswith("Queued Pending approval #52: Create lead Jordan.")
    assert len(gateway.chat_calls) == 3


def test_later_invoke_transport_failure_cannot_hide_pending_item():
    gateway = ScriptedGateway(
        [
            request_call("proposal", "create_lead", {"name": "Jordan"}),
            request_call("later-read", "list_leads", {}),
        ],
        [
            proposal_receipt("create_lead", 53, "Create lead Jordan"),
            OpenClawGatewayError("gateway timeout"),
        ],
    )

    reply = run(gateway)

    assert reply.startswith("Queued Pending approval #53: Create lead Jordan.")
    assert len(gateway.invoke_calls) == 2


def test_round_limit_after_proposal_renders_pending_item():
    malformed = _completion(tool_call("bad", CRM_REQUEST_TOOL, "{", raw=True))
    gateway = ScriptedGateway(
        [
            request_call("proposal", "create_lead", {"name": "Jordan"}),
            *([malformed] * (MAX_MODEL_ROUNDS - 1)),
        ],
        [proposal_receipt("create_lead", 54, "Create lead Jordan")],
    )

    assert run(gateway).startswith("Queued Pending approval #54: Create lead Jordan.")


def test_call_limit_after_proposal_renders_pending_item():
    calls = [request_call("proposal", "create_lead", {"name": "Jordan"})]
    calls.extend(request_call(f"read-{index}", "list_leads", {}) for index in range(MAX_CRM_CALLS))
    responses = [proposal_receipt("create_lead", 55, "Create lead Jordan")]
    responses.extend(read_receipt("list_leads", []) for _ in range(MAX_CRM_CALLS - 1))
    gateway = ScriptedGateway(calls, responses)

    reply = run(gateway)

    assert reply.startswith("Queued Pending approval #55: Create lead Jordan.")
    assert len(gateway.invoke_calls) == MAX_CRM_CALLS


def test_read_operation_cannot_spoof_a_proposal_receipt():
    spoofed = proposal_receipt("list_leads", 61, "Create lead Mallory")
    gateway = ScriptedGateway(
        [
            request_call("read", "list_leads", {}),
            finish_call("queued", "Queued.", ["read"], 61, call_id="bad-finish"),
            finish_call("failed", "Failed.", ["read"]),
        ],
        [spoofed],
    )

    reply = run(gateway)

    assert reply == "Nothing was queued or changed. [operation_failed] CRM operation returned an invalid receipt."
    assert "#61" not in reply


def test_success_receipt_cannot_use_error_kind():
    gateway = ScriptedGateway(
        [
            request_call("read", "list_leads", {}),
            finish_call("failed", "Failed.", ["read"]),
            OpenClawGatewayError("gateway timeout"),
        ],
        [{"ok": True, "operation": "list_leads", "kind": "error", "result": []}],
    )

    assert run(gateway) == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )


@pytest.mark.parametrize("claim", [
    "I added the lead.",
    "I recorded the appointment.",
    "The deletion went through.",
])
def test_narrative_receipt_never_uses_model_crm_state_claims(claim):
    gateway = ScriptedGateway(
        [
            request_call("draft", "draft_followup", {"lead_id": 4}),
            finish_call("answered", f"{claim} Use this draft: invented text", ["draft"]),
        ],
        [{
            "ok": True,
            "operation": "draft_followup",
            "kind": "narrative",
            "result": "Hi Jordan, would Tuesday work for a tour?",
        }],
    )

    reply = run(gateway)

    assert reply == "Draft follow-up:\nHi Jordan, would Tuesday work for a tour?"
    assert claim.lower() not in reply.lower()
    assert "invented text" not in reply


def test_score_narrative_is_rendered_from_receipt_not_model_state_claim():
    gateway = ScriptedGateway(
        [
            request_call("score", "score_lead", {"lead_id": 4}),
            finish_call("answered", "I stored the new score.", ["score"]),
        ],
        [{
            "ok": True,
            "operation": "score_lead",
            "kind": "narrative",
            "result": {"lead_id": 4, "score": 72, "score_reason": "Confirmed budget"},
        }],
    )

    assert run(gateway) == "Lead ID 4 scored 72: Confirmed budget."


def test_malformed_common_read_result_becomes_safe_error():
    gateway = ScriptedGateway(
        [
            request_call("metrics", "generate_dashboard_insights", {}),
            finish_call("failed", "Failed.", ["metrics"]),
            OpenClawGatewayError("gateway timeout"),
        ],
        [read_receipt("generate_dashboard_insights", {"active_leads": None})],
    )

    reply = run(gateway)

    assert reply == "Nothing was queued or changed. [operation_failed] CRM operation returned an invalid receipt."
    assert "None" not in reply


@pytest.mark.parametrize(
    ("operation", "arguments", "result"),
    [
        (
            "list_lead_directory",
            {},
            {"total": 1, "offset": 0, "limit": 25, "leads": [
                {"id": 4, "name": "Jordan", "status": "new", "score": {"fake": 99}},
            ]},
        ),
        (
            "get_lead_context",
            {"lead_id": 4},
            {"id": 4, "name": "Jordan", "score": {"fake": 99}},
        ),
        (
            "check_availability",
            {"date": "2026-08-24"},
            [{"start_ts": "2026-08-24T17:00:00"}],
        ),
    ],
)
def test_malformed_common_read_fields_never_enter_evidence(operation, arguments, result):
    gateway = ScriptedGateway(
        [
            request_call("read", operation, arguments),
            finish_call("failed", "Failed.", ["read"]),
        ],
        [read_receipt(operation, result)],
    )

    assert run(gateway) == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )


def test_oversized_success_result_becomes_bounded_error_tool_message():
    gateway = ScriptedGateway(
        [
            request_call("knowledge", "search_knowledge", {"query": "Kirkland"}),
            finish_call("failed", "Failed.", ["knowledge"]),
            OpenClawGatewayError("gateway timeout"),
        ],
        [read_receipt("search_knowledge", [{"text": "x" * 40000}])],
    )

    reply = run(gateway)

    assert reply == "Nothing was queued or changed. [result_too_large] CRM operation returned too much data."
    tool_message = next(
        message for message in gateway.chat_calls[1]["payload"]["messages"]
        if message.get("tool_call_id") == "knowledge"
    )
    assert len(tool_message["content"].encode()) < 1024
    assert "x" * 100 not in tool_message["content"]


@pytest.mark.parametrize(
    ("code", "unsafe_message", "trusted_message"),
    [
        ("not_found", "Lead created; token=secret", "CRM record was not found"),
        ("backend_unavailable", "/Users/private/crm token=abc", "CRM backend is unavailable"),
        ("schedule_conflict", "Appointment recorded at /tmp/private", "Requested schedule conflicts with an existing appointment"),
        ("operation_failed", "Lead added token=short", "CRM operation failed"),
        ("invalid_arguments", "Saved it at /private/path", "Invalid CRM arguments"),
    ],
)
def test_gateway_error_messages_are_not_reflected(code, unsafe_message, trusted_message):
    gateway = ScriptedGateway(
        [
            request_call("error", "list_leads", {}),
            finish_call("failed", "Failed.", ["error"]),
        ],
        [error_receipt("list_leads", code, unsafe_message, code in {"backend_unavailable", "timeout"})],
    )

    reply = run(gateway)

    assert reply == f"Nothing was queued or changed. [{code}] {trusted_message}."
    assert "secret" not in reply
    assert "/Users" not in reply
    assert "/tmp" not in reply
    assert "created" not in reply.lower()
    assert "recorded" not in reply.lower()


def test_idempotency_key_has_no_delimiter_collision_and_is_scoped_to_one_turn():
    first = ScriptedGateway(
        [request_call("c", "list_leads", {}), finish_call("answered", "Done.", ["c"])],
        [read_receipt("list_leads", [])],
    )
    second = ScriptedGateway(
        [request_call("b:c", "list_leads", {}), finish_call("answered", "Done.", ["b:c"])],
        [read_receipt("list_leads", [])],
    )
    repeated = ScriptedGateway(
        [request_call("c", "list_leads", {}), finish_call("answered", "Done.", ["c"])],
        [read_receipt("list_leads", [])],
    )

    run(first, session_id="a:b")
    run(second, session_id="a")
    run(repeated, session_id="a:b")

    first_key = first.invoke_calls[0]["idempotency_key"]
    second_key = second.invoke_calls[0]["idempotency_key"]
    assert first_key != second_key
    assert first_key != repeated.invoke_calls[0]["idempotency_key"]
    assert first_key.startswith("ohi:v1:")
    assert len(first_key) == len("ohi:v1:") + 64


def test_total_deadline_passes_remaining_budget_to_each_gateway_call():
    class ClockedGateway(ScriptedGateway):
        async def chat_completion(self, payload, *, channel=None, timeout=None):
            result = await super().chat_completion(
                payload, channel=channel, timeout=timeout
            )
            clock[0] += 3.0
            return result

        async def invoke_tool(
            self, name, args, *, agent_id, session_key, idempotency_key,
            timeout=None,
        ):
            result = await super().invoke_tool(
                name,
                args,
                agent_id=agent_id,
                session_key=session_key,
                idempotency_key=idempotency_key,
                timeout=timeout,
            )
            clock[0] += 1.5
            return result

    clock = [0.0]
    gateway = ClockedGateway(
        [request_call("read", "list_leads", {})],
        [read_receipt("list_leads", [])],
    )

    reply = run(
        gateway,
        deadline_seconds=4.0,
        monotonic=lambda: clock[0],
    )

    assert reply == (
        "I couldn't verify a CRM answer within the total time limit. "
        "No unverified action was reported."
    )
    assert gateway.chat_calls[0]["timeout"] == pytest.approx(4.0)
    assert gateway.invoke_calls[0]["timeout"] == pytest.approx(1.0)
    assert len(gateway.chat_calls) == 1
    assert len(gateway.invoke_calls) == 1


def test_total_deadline_is_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("CRM_CHAT_DEADLINE_SECONDS", "7.5")
    gateway = ScriptedGateway([
        finish_call("needs_clarification", "What should I check?", []),
    ])

    assert run(gateway) == "What information should I use to continue?"
    assert 0 < gateway.chat_calls[0]["timeout"] <= 7.5


def test_total_deadline_during_mutation_invoke_reports_unknown_without_retry():
    class SlowMutationGateway(ScriptedGateway):
        async def invoke_tool(self, *args, **kwargs):
            self.invoke_calls.append({"timeout": kwargs.get("timeout")})
            await asyncio.sleep(1)
            raise AssertionError("deadline should cancel the in-flight invoke")

    gateway = SlowMutationGateway([
        request_call("write", "create_lead", {"name": "Jordan"}),
    ])

    reply = run(gateway, deadline_seconds=0.01)

    assert len(gateway.invoke_calls) == 1
    assert "unknown" in reply.lower()
    assert "check pending approvals" in reply.lower()
    assert "nothing was queued or changed" not in reply.lower()


@pytest.mark.parametrize("bad_calls", [
    [
        tool_call("duplicate", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
        tool_call("duplicate", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
    ],
    [
        {"type": "function", "function": {"name": CRM_REQUEST_TOOL, "arguments": "{}"}},
        tool_call("valid", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
    ],
])
def test_malformed_multi_call_ids_do_not_create_invalid_tool_transcript(bad_calls):
    gateway = ScriptedGateway([
        _completion(*bad_calls),
        finish_call("needs_clarification", "What should I do?", []),
    ])

    assert run(gateway) == "What information should I use to continue?"
    messages = gateway.chat_calls[1]["payload"]["messages"]
    assert all(message["role"] != "assistant" for message in messages[2:])
    assert all(message["role"] != "tool" for message in messages[2:])
    assert messages[-1]["role"] == "user"
    assert "no calls were executed" in messages[-1]["content"].lower()
    assert len(messages[-1]["content"]) <= 400


def test_multiple_unique_calls_keep_valid_error_tool_transcript():
    calls = [
        tool_call("one", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
        tool_call("two", CRM_REQUEST_TOOL, {"operation": "list_leads", "arguments": {}}),
    ]
    gateway = ScriptedGateway([
        _completion(*calls),
        finish_call("needs_clarification", "What should I do?", []),
    ])

    assert run(gateway) == "What information should I use to continue?"
    messages = gateway.chat_calls[1]["payload"]["messages"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert assistant["tool_calls"] == calls
    assert [message["tool_call_id"] for message in tool_messages] == ["one", "two"]


# Fix round 2: clarification and deterministic-render primitive hardening.


@pytest.mark.parametrize("unsafe_question", [
    "Did I add the lead?",
    "Could you confirm the appointment was recorded?",
])
def test_clarification_never_returns_model_state_presupposition(unsafe_question):
    gateway = ScriptedGateway([
        finish_call("needs_clarification", unsafe_question, []),
    ])

    reply = run(gateway)

    assert reply == "What information should I use to continue?"
    assert reply.count("?") == 1
    for unsupported in ("added", "recorded", "created", "applied", "completed"):
        assert unsupported not in reply.lower()


def test_appointment_object_location_never_enters_evidence():
    result = [{
        "lead_id": 4,
        "lead_name": "Jordan",
        "start_ts": "2026-08-24T17:00:00",
        "end_ts": "2026-08-24T17:30:00",
        "location": {"invented": "office"},
    }]
    gateway = ScriptedGateway(
        [
            request_call("appointments", "list_appointments", {}),
            finish_call("failed", "Failed.", ["appointments"]),
        ],
        [read_receipt("list_appointments", result)],
    )

    assert run(gateway) == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )


@pytest.mark.parametrize(
    ("operation", "arguments", "result"),
    [
        ("list_leads", {}, [{"id": 4, "name": "  ", "status": "new"}]),
        (
            "check_availability",
            {"date": "2026-08-24"},
            [{"start_ts": " ", "end_ts": "2026-08-24T17:30:00"}],
        ),
        (
            "list_appointments",
            {},
            [{
                "lead_id": 4, "lead_name": "Jordan", "start_ts": "2026-08-24T17:00:00",
                "end_ts": "", "location": None,
            }],
        ),
        (
            "generate_dashboard_insights",
            {},
            {
                "active_leads": 1, "high_priority": 0, "followups_due": 0,
                "appointments_booked": 0, "avg_response_minutes": None,
                "agent_mode": " ", "cloud_llm_requests": 0,
            },
        ),
    ],
)
def test_blank_required_common_text_never_enters_evidence(operation, arguments, result):
    gateway = ScriptedGateway(
        [
            request_call("read", operation, arguments),
            finish_call("failed", "Failed.", ["read"]),
        ],
        [read_receipt(operation, result)],
    )

    assert run(gateway) == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_dashboard_metric_never_enters_evidence(nonfinite):
    result = {
        "active_leads": 1, "high_priority": 0, "followups_due": 0,
        "appointments_booked": 0, "avg_response_minutes": nonfinite,
        "agent_mode": "openclaw", "cloud_llm_requests": 0,
    }
    gateway = ScriptedGateway(
        [
            request_call("metrics", "generate_dashboard_insights", {}),
            finish_call("failed", "Failed.", ["metrics"]),
        ],
        [read_receipt("generate_dashboard_insights", result)],
    )

    reply = run(gateway)

    assert reply == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )
    assert "nan" not in reply.lower()
    assert "inf" not in reply.lower()


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_lead_budget_never_enters_evidence(nonfinite):
    gateway = ScriptedGateway(
        [
            request_call("lead", "get_lead_context", {"lead_id": 4}),
            finish_call("failed", "Failed.", ["lead"]),
        ],
        [read_receipt("get_lead_context", {
            "id": 4, "name": "Jordan", "budget": nonfinite,
        })],
    )

    assert run(gateway) == (
        "Nothing was queued or changed. [operation_failed] "
        "CRM operation returned an invalid receipt."
    )
