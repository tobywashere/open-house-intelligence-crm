"""One-command OpenClaw acceptance behavior."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

import scripts.acceptance_openclaw as acceptance


DEPENDENCIES = {
    "python": "3.13.7",
    "node": "v24.1.0",
    "npm": "11.0.0",
    "openclaw": "OpenClaw 2026.8.1-beta.2",
    "ollama": "ollama version 0.32.15",
}


class FakeAPI:
    """Stateful HTTP-boundary fake with complete response shapes."""

    def __init__(self):
        self.leads = [
            {"id": 1, "name": "Jordan Ellis", "status": "new"},
            {"id": 2, "name": "Alex Rivera", "status": "contacted"},
        ]
        self.pending: dict[int, dict] = {}
        self.pending_posts: list[int] = []
        self.denied: list[int] = []
        self.deleted_sessions: list[str] = []
        self.directory_count = 2
        self.crm_status = "crm_verified"
        self.chat_status = "chat_verified"
        self.create_pending = True
        self.invalid_creates_pending = False
        self.fail_deny = False
        self.fail_delete = False
        self.health_error: Exception | None = None
        self.briefing_extra: dict = {}
        self.briefing_payload: dict | None = None
        self.pending_get_failures = 0
        self.pending_get_calls = 0
        self.pending_fail_from_call: int | None = None
        self.chat_messages: list[str] = []
        self.chat_session_override: str | None = None
        self.delete_session_override: str | None = None
        self.delete_count = 6
        self.reviewed_reply_pending_id = 17
        self.concurrent_pending: dict | None = None

    def request(self, method: str, path: str, payload: dict | None = None):
        if path == "/health" and method == "GET":
            if self.health_error:
                raise self.health_error
            return {
                "ok": True,
                "agent_status": {
                    "status": "endpoint_enabled",
                    "gateway_reachable": True,
                    "endpoint_enabled": True,
                },
            }
        if path == "/health/agent-check" and method == "POST":
            return {"status": self.chat_status}
        if path == "/health/crm-check" and method == "POST":
            return {"status": self.crm_status, "crm_verified": self.crm_status == "crm_verified"}
        if path == "/leads" and method == "GET":
            return [dict(lead) for lead in self.leads]
        if path == "/pending-changes?status=pending" and method == "GET":
            self.pending_get_calls += 1
            if (
                self.pending_fail_from_call is not None
                and self.pending_get_calls >= self.pending_fail_from_call
            ):
                raise acceptance.ApiError(503, "pending unavailable")
            if self.pending_get_failures:
                self.pending_get_failures -= 1
                raise acceptance.ApiError(503, "pending unavailable")
            return [dict(row) for row in self.pending.values()]
        if path.startswith("/pending-changes/") and path.endswith("/deny") and method == "POST":
            pending_id = int(path.split("/")[2])
            if self.fail_deny:
                raise acceptance.ApiError(503, "deny failed")
            row = self.pending.pop(pending_id)
            row["status"] = "denied"
            self.denied.append(pending_id)
            return row
        if path.startswith("/summary?") and method == "GET":
            raise acceptance.ApiError(404, "no daily summary")
        if path.startswith("/briefing?") and method == "GET":
            if self.briefing_payload is not None:
                return json.loads(json.dumps(self.briefing_payload))
            return {
                "date": "2099-12-31",
                "generated_at": "2099-12-31T08:00:00",
                "source": "crm",
                "greeting": "Good morning, no appointments are scheduled today.",
                "schedule": [],
                "meeting_briefs": [],
                "suggested_actions": [],
                **self.briefing_extra,
            }
        if path == "/chat" and method == "POST":
            assert payload is not None
            message = payload["message"]
            self.chat_messages.append(message)
            response_session = self.chat_session_override or payload["session_id"]
            if "unsupported arguments" in message:
                if self.invalid_creates_pending:
                    self._add_pending(29, self._name_from_message(message))
                    return {
                        "reply": (
                            "Queued Pending approval #29: Create lead acceptance. "
                            "Status: pending; the change has not been applied."
                        ),
                        "session_id": response_session,
                    }
                return {
                    "reply": (
                        "Nothing was queued or changed. [invalid_arguments] "
                        "Unsupported argument: status."
                    ),
                    "session_id": response_session,
                }
            if "Create exactly one disposable" in message:
                name = self._name_from_message(message)
                if self.create_pending:
                    self._add_pending(17, name)
                    if self.concurrent_pending is not None:
                        row = dict(self.concurrent_pending)
                        self.pending[row["id"]] = row
                    return {
                        "reply": (
                            f"Queued Pending approval #{self.reviewed_reply_pending_id}: "
                            f"Create lead {name}. "
                            "Status: pending; the change has not been applied."
                        ),
                        "session_id": response_session,
                    }
                return {
                    "reply": "Nothing was queued or changed. The CRM tool failed.",
                    "session_id": response_session,
                }
            return {
                "reply": (
                    f"{self.directory_count} leads total. Showing 2 (offset 0): "
                    "Jordan Ellis (ID 1, new); Alex Rivera (ID 2, contacted)."
                ),
                "session_id": response_session,
            }
        if path.startswith("/chat/history?session_id=") and method == "DELETE":
            session_id = path.split("=", 1)[1]
            if self.fail_delete:
                raise acceptance.ApiError(503, "delete failed")
            self.deleted_sessions.append(session_id)
            return {
                "session_id": self.delete_session_override or session_id,
                "deleted": self.delete_count,
            }
        raise AssertionError((method, path, payload))

    @staticmethod
    def _name_from_message(message: str) -> str:
        marker = "NAME="
        return message.split(marker, 1)[1].split("\n", 1)[0].strip()

    def _add_pending(self, pending_id: int, name: str) -> None:
        self.pending[pending_id] = {
            "id": pending_id,
            "operation": "create_lead",
            "status": "pending",
            "payload": {"name": name, "source": "note"},
            "summary": f"Create lead {name}",
        }
        self.pending_posts.append(pending_id)


def run(api: FakeAPI, *, allow_test_write: bool = False) -> dict:
    return acceptance.run_acceptance(
        api,
        allow_test_write=allow_test_write,
        revision="abc1234",
        dependencies=DEPENDENCIES,
        discord_bound=False,
        session_id="openhouse-acceptance-test-session",
        test_id="fixed123",
        briefing_date="2099-12-31",
    )


def test_discord_binding_inspection_uses_the_runtime_agent_id(monkeypatch):
    captured: list[str] = []
    monkeypatch.setenv("AGENT_ID", "custom-crm")
    monkeypatch.setattr(
        acceptance,
        "_capture_discord_binding",
        lambda agent_id: captured.append(agent_id) or False,
    )

    result = acceptance.run_acceptance(
        FakeAPI(),
        revision="abc123",
        dependencies=DEPENDENCIES,
        discord_bound=None,
        session_id="accept-agent-env",
        test_id="agent-env",
    )

    assert acceptance.exit_code(result) == 0
    assert captured == ["custom-crm"]


def test_discord_binding_inspection_loads_agent_id_from_repo_env(
    monkeypatch, tmp_path
):
    captured: list[str] = []
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setattr(acceptance, "REPO", tmp_path)
    (tmp_path / ".env").write_text("AGENT_ID=portable-crm\n", encoding="utf-8")
    monkeypatch.setattr(
        acceptance,
        "_capture_discord_binding",
        lambda agent_id: captured.append(agent_id) or False,
    )

    result = acceptance.run_acceptance(
        FakeAPI(),
        revision="abc123",
        dependencies=DEPENDENCIES,
        discord_bound=None,
        session_id="accept-agent-file",
        test_id="agent-file",
    )

    assert acceptance.exit_code(result) == 0
    assert captured == ["portable-crm"]


def test_discord_binding_inspection_defaults_only_when_agent_id_is_absent(
    monkeypatch, tmp_path
):
    captured: list[str] = []
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setattr(acceptance, "REPO", tmp_path)
    monkeypatch.setattr(
        acceptance,
        "_capture_discord_binding",
        lambda agent_id: captured.append(agent_id) or False,
    )

    result = acceptance.run_acceptance(
        FakeAPI(),
        revision="abc123",
        dependencies=DEPENDENCIES,
        discord_bound=None,
        session_id="accept-agent-default",
        test_id="agent-default",
    )

    assert acceptance.exit_code(result) == 0
    assert captured == ["openhouse-crm"]


@pytest.mark.parametrize("agent_id", ["", "   ", "Custom CRM", "main"])
def test_discord_binding_inspection_rejects_explicit_invalid_environment_agent_id(
    monkeypatch, agent_id
):
    captured: list[str] = []
    api = FakeAPI()
    monkeypatch.setenv("AGENT_ID", agent_id)
    monkeypatch.setattr(
        acceptance,
        "_capture_discord_binding",
        lambda selected: captured.append(selected) or False,
    )

    result = acceptance.run_acceptance(
        api,
        allow_test_write=True,
        revision="abc123",
        dependencies=DEPENDENCIES,
        discord_bound=None,
        session_id="accept-agent-invalid-env",
        test_id="agent-invalid-env",
    )

    assert acceptance.exit_code(result) == 1
    assert captured == []
    assert by_name(result, "CRM agent configuration")["level"] == "FAIL"
    assert api.chat_messages == []
    assert api.pending_get_calls == 0
    assert api.pending_posts == []
    assert api.deleted_sessions == []


@pytest.mark.parametrize("agent_id", ["", "   ", "Custom CRM", "main"])
def test_discord_binding_inspection_rejects_explicit_invalid_repo_agent_id(
    monkeypatch, tmp_path, agent_id
):
    captured: list[str] = []
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setattr(acceptance, "REPO", tmp_path)
    (tmp_path / ".env").write_text(f"AGENT_ID={agent_id}\n", encoding="utf-8")
    monkeypatch.setattr(
        acceptance,
        "_capture_discord_binding",
        lambda selected: captured.append(selected) or False,
    )

    result = acceptance.run_acceptance(
        FakeAPI(),
        revision="abc123",
        dependencies=DEPENDENCIES,
        discord_bound=None,
        session_id="accept-agent-invalid-file",
        test_id="agent-invalid-file",
    )

    assert acceptance.exit_code(result) == 1
    assert captured == []
    assert by_name(result, "CRM agent configuration")["level"] == "FAIL"


def by_name(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["name"] == name)


def cleanup_by_name(result: dict, name: str) -> dict:
    return next(check for check in result["cleanup"] if check["name"] == name)


def canonical_briefing() -> dict:
    return {
        "date": "2099-12-31",
        "generated_at": "2099-12-31T08:00:00",
        "source": "crm",
        "greeting": "Good morning, one appointment is scheduled today.",
        "schedule": [
            {
                "appointment_id": 8,
                "start": "17:00",
                "end": "17:30",
                "kind": "meeting",
                "title": "Meeting, Jordan Ellis",
                "lead_id": 1,
            }
        ],
        "meeting_briefs": [
            {
                "appointment_id": 8,
                "lead_id": 1,
                "name": "Jordan Ellis",
                "area": "Kirkland",
                "budget": 850000,
                "timeline": "90 days",
                "intent": "buy",
                "preferences": ["quiet street"],
                "persona": "Home Buyer",
                "score": 72,
                "summary": "Intent: buy. Area: Kirkland.",
                "assistant_advice": {
                    "prepare": ["Review the saved CRM notes."],
                    "recommendation": "Confirm the recorded timeline.",
                },
            }
        ],
        "suggested_actions": [
            {
                "lead_id": 1,
                "name": "Jordan Ellis",
                "channel": "email",
                "action": "Follow up with Jordan Ellis",
                "reason": "Reminder due.",
                "evidence": {"kind": "reminder", "id": 4},
            }
        ],
    }


def test_read_only_acceptance_captures_revision_dependencies_and_verified_reads():
    api = FakeAPI()

    result = run(api)

    assert result["schema_version"] == 1
    assert result["revision"] == "abc1234"
    assert by_name(result, "Dependencies") == {
        "level": "PASS",
        "name": "Dependencies",
        "detail": "required local dependencies detected",
        "evidence": DEPENDENCIES,
    }
    assert by_name(result, "Application health")["level"] == "PASS"
    assert by_name(result, "Live chat completion")["level"] == "PASS"
    assert by_name(result, "CRM capability") == {
        "level": "PASS",
        "name": "CRM capability",
        "detail": "direct audited CRM read verified",
        "evidence": {"status": "crm_verified"},
    }
    assert by_name(result, "Lead directory")["evidence"] == {
        "api_count": 2,
        "chat_count": 2,
    }
    assert by_name(result, "Briefing truthfulness")["level"] == "PASS"
    assert by_name(result, "Invalid write")["level"] == "SKIP"
    assert by_name(result, "Reviewed write")["level"] == "SKIP"
    assert by_name(result, "Discord")["level"] == "SKIP"
    assert api.pending_posts == []
    assert api.deleted_sessions == ["openhouse-acceptance-test-session"]
    assert acceptance.exit_code(result) == 0


def test_directory_count_must_exactly_match_the_api():
    api = FakeAPI()
    api.directory_count = 3

    result = run(api)

    assert by_name(result, "Lead directory")["level"] == "FAIL"
    assert by_name(result, "Lead directory")["evidence"] == {
        "api_count": 2,
        "chat_count": 3,
    }
    assert acceptance.exit_code(result) == 1


def test_briefing_rejects_fields_outside_the_canonical_crm_shape():
    api = FakeAPI()
    api.briefing_extra = {"neighborhood_outlook": "Prices will definitely rise."}

    result = run(api)

    check = by_name(result, "Briefing truthfulness")
    assert check["level"] == "FAIL"
    assert check["evidence"]["unexpected_fields"] == ["neighborhood_outlook"]


def test_briefing_requires_the_requested_date():
    api = FakeAPI()
    api.briefing_payload = canonical_briefing()
    api.briefing_payload["date"] = "2099-12-30"

    result = run(api)

    check = by_name(result, "Briefing truthfulness")
    assert check["level"] == "FAIL"
    assert check["evidence"]["date_matches"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["schedule"][0].update(
            {"market_forecast": "Prices will rise."}
        ),
        lambda body: body["schedule"][0].pop("appointment_id"),
        lambda body: body["meeting_briefs"][0].update(
            {"news": "Invented neighborhood news."}
        ),
        lambda body: body["meeting_briefs"][0].update({"preferences": "quiet"}),
        lambda body: body["meeting_briefs"][0]["assistant_advice"].update(
            {"forecast": "Guaranteed appreciation."}
        ),
        lambda body: body["suggested_actions"][0]["evidence"].update(
            {"headline": "Fabricated source"}
        ),
    ],
)
def test_briefing_rejects_noncanonical_nested_items(mutate):
    api = FakeAPI()
    api.briefing_payload = canonical_briefing()
    mutate(api.briefing_payload)

    result = run(api)

    check = by_name(result, "Briefing truthfulness")
    assert check["level"] == "FAIL"
    assert check["evidence"]["nested_shape_valid"] is False


def test_briefing_accepts_valid_empty_optional_crm_text_and_advice():
    api = FakeAPI()
    api.briefing_payload = canonical_briefing()
    brief = api.briefing_payload["meeting_briefs"][0]
    brief.update(
        {
            "area": "",
            "budget": 0,
            "timeline": "",
            "intent": "",
            "preferences": [""],
            "persona": "",
            "score": 0,
            "assistant_advice": {"prepare": [""], "recommendation": ""},
        }
    )

    result = run(api)

    assert by_name(result, "Briefing truthfulness")["level"] == "PASS"


def test_briefing_acceptance_preserves_api_permitted_whitespace_facts():
    api = FakeAPI()
    api.briefing_payload = canonical_briefing()
    api.briefing_payload["meeting_briefs"][0]["name"] = "   "
    api.briefing_payload["suggested_actions"][0]["name"] = "   "
    api.briefing_payload["suggested_actions"][0]["reason"] = "   "

    result = run(api)

    assert by_name(result, "Briefing truthfulness")["level"] == "PASS"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["meeting_briefs"][0].update({"budget": 850000.5}),
        lambda body: body["meeting_briefs"][0].update({"score": 72.0}),
        lambda body: body["meeting_briefs"][0].update({"score": -1}),
        lambda body: body["meeting_briefs"][0].update({"score": 101}),
    ],
)
def test_briefing_rejects_invalid_numeric_types_and_ranges(mutate):
    api = FakeAPI()
    api.briefing_payload = canonical_briefing()
    mutate(api.briefing_payload)

    result = run(api)

    check = by_name(result, "Briefing truthfulness")
    assert check["level"] == "FAIL"
    assert check["evidence"]["nested_shape_valid"] is False


def test_crm_capability_requires_direct_crm_verified_status():
    api = FakeAPI()
    api.crm_status = "chat_verified"

    result = run(api)

    assert by_name(result, "CRM capability")["level"] == "FAIL"
    assert by_name(result, "CRM capability")["evidence"] == {
        "status": "chat_verified"
    }
    assert acceptance.exit_code(result) == 1


def test_write_acceptance_requires_explicit_flag():
    api = FakeAPI()

    result = run(api, allow_test_write=False)

    assert api.pending_posts == []
    assert by_name(result, "Reviewed write")["level"] == "SKIP"
    assert by_name(result, "Invalid write")["level"] == "SKIP"


def test_write_checks_do_not_run_until_required_read_checks_pass():
    api = FakeAPI()
    api.directory_count = 99

    result = run(api, allow_test_write=True)

    assert api.pending_posts == []
    assert by_name(result, "Invalid write")["level"] == "SKIP"
    assert by_name(result, "Reviewed write")["level"] == "SKIP"
    assert "read-only" in by_name(result, "Reviewed write")["detail"]


def test_invalid_write_proves_no_proposal_was_created():
    api = FakeAPI()

    result = run(api, allow_test_write=True)

    check = by_name(result, "Invalid write")
    assert check["level"] == "PASS"
    assert check["evidence"] == {"new_pending_count": 0, "lead_applied": False}


def test_invalid_write_fails_and_cleans_up_if_a_proposal_appears():
    api = FakeAPI()
    api.invalid_creates_pending = True

    result = run(api, allow_test_write=True)

    assert by_name(result, "Invalid write")["level"] == "FAIL"
    assert 29 in api.denied
    assert acceptance.exit_code(result) == 1


def test_reviewed_write_is_pending_never_applied_then_denied_and_absent():
    api = FakeAPI()

    result = run(api, allow_test_write=True)

    check = by_name(result, "Reviewed write")
    assert check["level"] == "PASS"
    assert check["evidence"] == {
        "pending_id": 17,
        "absent_before_denial": True,
        "denied": True,
        "absent_after_denial": True,
    }
    assert api.denied == [17]
    assert all("OHI ACCEPTANCE" not in lead["name"] for lead in api.leads)
    assert cleanup_by_name(result, "Deny disposable proposal")["level"] == "PASS"
    assert cleanup_by_name(result, "Delete acceptance chat session")["level"] == "PASS"


def test_fabricated_existing_pending_id_is_never_denied():
    api = FakeAPI()
    api.pending[5] = {
        "id": 5,
        "operation": "create_lead",
        "status": "pending",
        "payload": {"name": "Real customer", "source": "referral"},
        "summary": "Create lead Real customer",
    }
    api.reviewed_reply_pending_id = 5

    result = run(api, allow_test_write=True)

    assert by_name(result, "Reviewed write")["level"] == "FAIL"
    assert api.denied == [17]
    assert 5 in api.pending
    assert api.pending[5]["payload"]["name"] == "Real customer"


def test_concurrent_user_proposal_is_ignored_and_never_denied():
    api = FakeAPI()
    api.concurrent_pending = {
        "id": 18,
        "operation": "update_lead",
        "status": "pending",
        "payload": {"status": "contacted"},
        "summary": "Update lead 1",
    }

    result = run(api, allow_test_write=True)

    assert by_name(result, "Reviewed write")["level"] == "PASS"
    assert api.denied == [17]
    assert 18 in api.pending
    assert api.pending[18]["operation"] == "update_lead"


def test_failed_pending_baseline_sends_no_write_request():
    api = FakeAPI()
    api.pending_get_failures = 1

    result = run(api, allow_test_write=True)

    write_messages = [
        message
        for message in api.chat_messages
        if "unsupported arguments" in message
        or "Create exactly one disposable" in message
    ]
    assert write_messages == []
    assert api.pending_posts == []
    assert by_name(result, "Invalid write")["level"] == "FAIL"
    assert by_name(result, "Reviewed write")["level"] == "SKIP"


def test_invalid_write_snapshot_uncertainty_is_explicit_cleanup_failure():
    api = FakeAPI()
    api.pending_fail_from_call = 2

    result = run(api, allow_test_write=True)

    cleanup = cleanup_by_name(result, "Invalid-write proposal cleanup")
    assert cleanup["level"] == "FAIL"
    assert cleanup["evidence"] == {
        "ownership": "unknown",
        "snapshot_attempts": acceptance.POST_WRITE_SNAPSHOT_ATTEMPTS,
    }
    assert api.pending_get_calls == 1 + acceptance.POST_WRITE_SNAPSHOT_ATTEMPTS
    assert api.denied == []
    assert "no proposal" not in cleanup["detail"].lower()
    assert by_name(result, "Invalid write")["evidence"] == {
        "new_pending_count": "unknown",
        "lead_applied": "unknown",
        "ownership": "unknown",
    }
    assert cleanup_by_name(result, "Delete acceptance chat session")["level"] == "PASS"
    assert acceptance.exit_code(result) == 1


def test_reviewed_write_snapshot_uncertainty_is_explicit_cleanup_failure():
    api = FakeAPI()
    api.pending_fail_from_call = 4

    result = run(api, allow_test_write=True)

    cleanup = cleanup_by_name(result, "Deny disposable proposal")
    assert cleanup["level"] == "FAIL"
    assert cleanup["evidence"] == {
        "ownership": "unknown",
        "snapshot_attempts": acceptance.POST_WRITE_SNAPSHOT_ATTEMPTS,
    }
    assert api.pending_get_calls == 3 + acceptance.POST_WRITE_SNAPSHOT_ATTEMPTS
    assert api.denied == []
    assert 17 in api.pending
    assert "no proposal" not in cleanup["detail"].lower()
    assert cleanup_by_name(result, "Delete acceptance chat session")["level"] == "PASS"
    assert acceptance.exit_code(result) == 1


def test_cleanup_continues_after_an_intermediate_failure_and_is_required():
    api = FakeAPI()
    api.fail_deny = True

    result = run(api, allow_test_write=True)

    assert cleanup_by_name(result, "Deny disposable proposal")["level"] == "FAIL"
    assert cleanup_by_name(result, "Delete acceptance chat session")["level"] == "PASS"
    assert api.deleted_sessions == ["openhouse-acceptance-test-session"]
    assert acceptance.exit_code(result) == 1


def test_chat_reply_must_echo_the_acceptance_session():
    api = FakeAPI()
    api.chat_session_override = "somebody-elses-session"

    result = run(api)

    assert by_name(result, "Lead directory")["level"] == "FAIL"
    assert acceptance.exit_code(result) == 1


@pytest.mark.parametrize(
    ("session_override", "deleted"),
    [
        ("somebody-elses-session", 6),
        (None, 0),
    ],
)
def test_chat_cleanup_requires_exact_session_and_meaningful_deletion(
    session_override, deleted
):
    api = FakeAPI()
    api.delete_session_override = session_override
    api.delete_count = deleted

    result = run(api)

    cleanup = cleanup_by_name(result, "Delete acceptance chat session")
    assert cleanup["level"] == "FAIL"
    assert acceptance.exit_code(result) == 1


def test_report_sanitizes_urls_tokens_exceptions_and_home_paths(monkeypatch):
    api = FakeAPI()
    secret = "super-secret-token"
    monkeypatch.setenv("OHI_API_TOKEN", secret)
    api.health_error = RuntimeError(
        f"failed at http://127.0.0.1:8080/api/private?token={secret} "
        f"from {Path.home()}/openhouse/private.txt"
    )

    result = run(api)
    rendered = acceptance.render_report(result, as_json=True)

    assert acceptance.exit_code(result) == 1
    assert secret not in rendered
    assert str(Path.home()) not in rendered
    assert "http://127.0.0.1:8080" not in rendered
    assert "private.txt" not in rendered
    assert "RuntimeError" in rendered
    parsed = json.loads(rendered)
    assert parsed["schema_version"] == 1


def test_http_boundary_never_forwards_the_api_token_through_a_redirect(monkeypatch):
    class RedirectRejectingOpener:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout):
            self.requests.append(request)
            raise HTTPError(request.full_url, 302, "redirect rejected", {}, None)

    opener = RedirectRejectingOpener()
    handlers = []
    monkeypatch.setattr(
        acceptance.urllib.request,
        "build_opener",
        lambda *items: handlers.extend(items) or opener,
    )
    monkeypatch.setattr(
        acceptance.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the redirect-following global opener was used")
        ),
    )
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")

    api = acceptance.HttpAPI("http://127.0.0.1:8080/api", timeout=2)
    with pytest.raises(acceptance.ApiError) as caught:
        api.request("GET", "/health")

    assert caught.value.status == 302
    assert len(handlers) == 1
    assert (
        handlers[0].redirect_request(None, None, 302, "Found", {}, "https://elsewhere")
        is None
    )
    assert opener.requests[0].get_header("X-api-token") == "must-not-leak"


def test_discord_unbound_is_skip_not_pass():
    result = run(FakeAPI())

    assert by_name(result, "Discord") == {
        "level": "SKIP",
        "name": "Discord",
        "detail": "no Discord account is bound to the CRM agent",
        "evidence": {"bound": False},
    }
