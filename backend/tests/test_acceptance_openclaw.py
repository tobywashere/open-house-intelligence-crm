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
            if "unsupported arguments" in message:
                if self.invalid_creates_pending:
                    self._add_pending(29, self._name_from_message(message))
                    return {
                        "reply": (
                            "Queued Pending approval #29: Create lead acceptance. "
                            "Status: pending; the change has not been applied."
                        ),
                        "session_id": payload["session_id"],
                    }
                return {
                    "reply": (
                        "Nothing was queued or changed. [invalid_arguments] "
                        "Unsupported argument: status."
                    ),
                    "session_id": payload["session_id"],
                }
            if "Create exactly one disposable" in message:
                name = self._name_from_message(message)
                if self.create_pending:
                    self._add_pending(17, name)
                    return {
                        "reply": (
                            f"Queued Pending approval #17: Create lead {name}. "
                            "Status: pending; the change has not been applied."
                        ),
                        "session_id": payload["session_id"],
                    }
                return {
                    "reply": "Nothing was queued or changed. The CRM tool failed.",
                    "session_id": payload["session_id"],
                }
            return {
                "reply": (
                    f"{self.directory_count} leads total. Showing 2 (offset 0): "
                    "Jordan Ellis (ID 1, new); Alex Rivera (ID 2, contacted)."
                ),
                "session_id": payload["session_id"],
            }
        if path.startswith("/chat/history?session_id=") and method == "DELETE":
            session_id = path.split("=", 1)[1]
            if self.fail_delete:
                raise acceptance.ApiError(503, "delete failed")
            self.deleted_sessions.append(session_id)
            return {"session_id": session_id, "deleted": 6}
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


def by_name(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["name"] == name)


def cleanup_by_name(result: dict, name: str) -> dict:
    return next(check for check in result["cleanup"] if check["name"] == name)


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


def test_cleanup_continues_after_an_intermediate_failure_and_is_required():
    api = FakeAPI()
    api.fail_deny = True

    result = run(api, allow_test_write=True)

    assert cleanup_by_name(result, "Deny disposable proposal")["level"] == "FAIL"
    assert cleanup_by_name(result, "Delete acceptance chat session")["level"] == "PASS"
    assert api.deleted_sessions == ["openhouse-acceptance-test-session"]
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
