"""OpenClaw gateway readiness and response-shape handling."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.agent.mock import MockDriver
from app.agent.openclaw import OpenClawDriver
from app.agent import status as agent_status
from app.db import audit, get_conn
from app.routers import misc


def test_default_gateway_url_is_localhost():
    import app.agent.openclaw as module

    resolver = getattr(module, "resolve_gateway_url", None)
    assert callable(resolver)
    assert resolver({}) == "http://localhost:18789"
    assert resolver({"AGENT_GATEWAY_URL": "http://agent-box:18789"}) == (
        "http://agent-box:18789"
    )


class FakeClient:
    def __init__(self, option_status=405, post_status=200, post_json=None):
        self.option_status = option_status
        self.post_status = post_status
        self.post_json = post_json or {
            "choices": [{"message": {"content": "READY"}}]
        }
        self.last_post_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def options(self, url, **kwargs):
        return httpx.Response(
            self.option_status,
            request=httpx.Request("OPTIONS", url),
        )

    async def post(self, url, **kwargs):
        self.last_post_json = kwargs["json"]
        return httpx.Response(
            self.post_status,
            request=httpx.Request("POST", url),
            json=self.post_json,
        )


def client_factory(*, option_status=405, post_status=200, post_json=None):
    def make_client(**kwargs):
        return FakeClient(option_status, post_status, post_json)

    return make_client


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (404, "endpoint_disabled"),
        (405, "endpoint_enabled"),
    ],
)
def test_probe_classifies_configured_chat_endpoint(code, expected):
    driver = OpenClawDriver(client_factory=client_factory(option_status=code))

    probe = asyncio.run(driver.probe())

    assert probe.status == expected
    assert probe.endpoint_enabled is (code == 405)


def test_connected_is_false_for_auth_and_missing_endpoint():
    for code in (401, 403, 404):
        driver = OpenClawDriver(client_factory=client_factory(option_status=code))
        assert asyncio.run(driver.connected()) is False


def test_malformed_successful_response_marks_chat_failed():
    driver = OpenClawDriver(
        client_factory=client_factory(post_json={"choices": []})
    )

    reply = asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert "agent" in reply.lower() and ("unavailable" in reply.lower() or "didn't answer" in reply.lower())
    assert probe.status == "failed"
    assert probe.last_chat_ok is False


def test_latest_chat_failure_is_not_hidden_by_stale_crm_detail(monkeypatch):
    monkeypatch.setattr(agent_status, "_CRM_OK", False)
    monkeypatch.setattr(agent_status, "_CRM_DETAIL", "no audited CRM call")
    driver = OpenClawDriver(
        client_factory=client_factory(post_json={"choices": []})
    )

    asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert probe.status == "failed"
    assert probe.detail == "invalid completion response"


def test_chat_failure_after_crm_verification_marks_probe_degraded(monkeypatch):
    monkeypatch.setattr(agent_status, "_EVENT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_CHAT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_SEQUENCE", 0, raising=False)
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    driver = OpenClawDriver(
        client_factory=client_factory(post_json={"choices": []})
    )

    asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert probe.status == "degraded"
    assert probe.crm_verified is True
    assert probe.detail == "invalid completion response"


def test_valid_completion_marks_chat_verified(monkeypatch):
    import app.agent.status as status

    monkeypatch.setattr(status, "_CRM_OK", None)
    driver = OpenClawDriver(client_factory=client_factory())

    reply = asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert reply == "READY"
    assert probe.status == "chat_verified"
    assert probe.last_chat_ok is True
    assert probe.crm_verified is False


def test_extract_fallback_is_labeled(monkeypatch):
    monkeypatch.setattr(agent_status, "_FALLBACKS", {}, raising=False)
    driver = OpenClawDriver(client_factory=client_factory(post_status=500))

    result = asyncio.run(driver.extract("Met Alex Rivera, budget $900k"))

    assert result.pop("_fallback_used") == "deterministic_parser"
    assert result["name"] == "Alex Rivera"
    assert agent_status.fallback_counts()["extract"] == 1


def test_draft_fallback_is_visibly_labeled():
    driver = OpenClawDriver(client_factory=client_factory(post_status=500))

    result = asyncio.run(driver.draft_followup({"name": "Alex"}))

    assert result.startswith("[deterministic fallback]")


def test_score_fallback_is_labeled_and_counted(monkeypatch):
    monkeypatch.setattr(agent_status, "_FALLBACKS", {}, raising=False)
    driver = OpenClawDriver(client_factory=client_factory(post_status=500))

    result = asyncio.run(driver.explain_score({"name": "Alex"}, 55))

    assert result.startswith("[deterministic fallback]")
    assert agent_status.fallback_counts() == {"score_explanation": 1}


def test_failed_deterministic_draft_does_not_record_a_fallback(monkeypatch):
    monkeypatch.setattr(agent_status, "_FALLBACKS", {}, raising=False)
    monkeypatch.setattr(agent_status, "_EVENT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_FALLBACK_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_SEQUENCE", 0, raising=False)
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    driver = OpenClawDriver()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("gateway unavailable")

    async def broken_draft(*args, **kwargs):
        raise RuntimeError("deterministic draft failed")

    monkeypatch.setattr(driver, "_send", unavailable)
    monkeypatch.setattr(MockDriver, "draft_followup", broken_draft)

    with pytest.raises(RuntimeError, match="deterministic draft failed"):
        asyncio.run(driver.draft_followup({"name": "Alex"}))

    assert agent_status.fallback_counts() == {}
    assert agent_status.resolved_status(
        gateway_reachable=True, endpoint_enabled=True
    ) == "crm_verified"


def test_failed_deterministic_score_explanation_does_not_record_a_fallback(monkeypatch):
    monkeypatch.setattr(agent_status, "_FALLBACKS", {}, raising=False)
    monkeypatch.setattr(agent_status, "_EVENT_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_LAST_FALLBACK_SEQUENCE", 0, raising=False)
    monkeypatch.setattr(agent_status, "_CRM_SEQUENCE", 0, raising=False)
    agent_status.record_chat(True)
    agent_status.record_crm_capability(True)
    driver = OpenClawDriver()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("gateway unavailable")

    async def broken_explanation(*args, **kwargs):
        raise RuntimeError("deterministic explanation failed")

    monkeypatch.setattr(driver, "_send", unavailable)
    monkeypatch.setattr(MockDriver, "explain_score", broken_explanation)

    with pytest.raises(RuntimeError, match="deterministic explanation failed"):
        asyncio.run(driver.explain_score({"name": "Alex"}, 55))

    assert agent_status.fallback_counts() == {}
    assert agent_status.resolved_status(
        gateway_reachable=True, endpoint_enabled=True
    ) == "crm_verified"


def test_crm_capability_request_is_read_only_and_targets_skill(monkeypatch):
    import app.agent.openclaw as module

    fake = FakeClient()
    monkeypatch.setattr(module, "AGENT_ID", "openhouse-crm")
    driver = OpenClawDriver(client_factory=lambda **_: fake)

    asyncio.run(driver.request_crm_capability("crm-check-123", "a" * 32))

    payload = fake.last_post_json
    assert payload["user"] == "crm-check-123"
    assert "crm-db-operations" in payload["messages"][0]["content"]
    assert "generate_dashboard_insights" in payload["messages"][0]["content"]
    assert "--args" in payload["messages"][0]["content"]
    assert json.dumps({"probe_nonce": "a" * 32}) in payload["messages"][0]["content"]
    assert "Do not modify CRM data" in payload["messages"][0]["content"]


def _capability_probe() -> agent_status.AgentProbe:
    chat_ok, chat_detail = agent_status.last_chat()
    crm_ok, crm_detail = agent_status.last_crm_capability()
    return agent_status.AgentProbe(
        status=agent_status.resolved_status(gateway_reachable=True, endpoint_enabled=True),
        gateway_reachable=True,
        endpoint_enabled=True,
        last_chat_ok=chat_ok,
        crm_verified=crm_ok is True,
        agent_id="openhouse-crm",
        fallbacks={},
        detail=chat_detail if chat_ok is False else crm_detail,
    )


def test_crm_check_requires_new_matching_audit(client, monkeypatch):
    class FakeDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            with get_conn() as conn:
                audit(
                    conn,
                    "agent",
                    "generate_dashboard_insights",
                    {"probe_nonce": probe_nonce},
                    {"active_leads": 0},
                )

        async def probe(self):
            return _capability_probe()

    agent_status.record_chat(True)
    monkeypatch.setattr(misc, "get_driver", lambda: FakeDriver())

    response = client.post("/api/health/crm-check")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "crm_verified"
    assert body["crm_verified"] is True
    assert body["agent_id"] == "openhouse-crm"


def test_crm_check_moves_database_reads_off_event_loop(client, monkeypatch):
    loop_threads = []
    db_threads = []
    real_latest = misc._latest_audit_id
    real_inputs = misc._crm_probe_inputs_after

    monkeypatch.setattr(
        misc,
        "_latest_audit_id",
        lambda: db_threads.append(threading.get_ident()) or real_latest(),
    )
    monkeypatch.setattr(
        misc,
        "_crm_probe_inputs_after",
        lambda before: db_threads.append(threading.get_ident())
        or real_inputs(before),
    )

    class Driver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            loop_threads.append(threading.get_ident())

        async def probe(self):
            return _capability_probe()

    monkeypatch.setattr(misc, "get_driver", lambda: Driver())

    assert client.post("/api/health/crm-check").status_code == 200
    assert db_threads
    assert loop_threads
    assert all(thread_id != loop_threads[0] for thread_id in db_threads)


def test_crm_check_keeps_newer_chat_failure_degraded(client, monkeypatch):
    class AuditedThenFailedDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            with get_conn() as conn:
                audit(
                    conn,
                    "agent",
                    "generate_dashboard_insights",
                    {"probe_nonce": probe_nonce},
                    {"active_leads": 0},
                )

        async def probe(self):
            agent_status.record_chat(False, "timeout")
            return _capability_probe()

    agent_status.record_chat(True)
    monkeypatch.setattr(misc, "get_driver", lambda: AuditedThenFailedDriver())

    body = client.post("/api/health/crm-check").json()

    assert body["status"] == "degraded"
    assert body["crm_verified"] is True
    assert body["detail"] == "timeout"


def test_crm_check_does_not_trust_generic_reply_or_old_audit(client, monkeypatch):
    with get_conn() as conn:
        audit(
            conn,
            "agent",
            "generate_dashboard_insights",
            {},
            {"active_leads": 99},
        )

    class GenericDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            return None

        async def probe(self):
            return _capability_probe()

    agent_status.record_chat(True)
    monkeypatch.setattr(misc, "get_driver", lambda: GenericDriver())

    response = client.post("/api/health/crm-check")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "chat_verified"
    assert body["crm_verified"] is False
    assert body["detail"] == "no audited CRM call"


def test_crm_check_rejects_unrelated_agent_activity(client, monkeypatch):
    class UnrelatedDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            with get_conn() as conn:
                audit(
                    conn,
                    "agent",
                    "generate_dashboard_insights",
                    {"probe_nonce": "unrelated-activity"},
                    {"active_leads": 4},
                )

        async def probe(self):
            return _capability_probe()

    agent_status.record_chat(True)
    monkeypatch.setattr(misc, "get_driver", lambda: UnrelatedDriver())

    body = client.post("/api/health/crm-check").json()

    assert body["status"] == "chat_verified"
    assert body["crm_verified"] is False
    assert body["detail"] == "no audited CRM call"


def test_overlapping_crm_checks_do_not_share_audit_evidence(client, monkeypatch):
    calls_lock = threading.Lock()
    evidence_written = threading.Event()
    call_count = 0

    class OverlappingDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id, probe_nonce):
            nonlocal call_count
            with calls_lock:
                call_index = call_count
                call_count += 1
            if call_index == 0:
                with get_conn() as conn:
                    audit(
                        conn,
                        "agent",
                        "generate_dashboard_insights",
                        {"probe_nonce": probe_nonce},
                        {"active_leads": 0},
                    )
                evidence_written.set()
            else:
                await asyncio.to_thread(evidence_written.wait, 2)

        async def probe(self):
            return _capability_probe()

    agent_status.record_chat(True)
    monkeypatch.setattr(misc, "get_driver", lambda: OverlappingDriver())

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda _: client.post("/api/health/crm-check").json(),
            range(2),
        ))

    assert sum(body["crm_verified"] for body in responses) == 1
    assert sorted(body["status"] for body in responses) == [
        "chat_verified",
        "crm_verified",
    ]


def test_metrics_audits_only_agent_tagged_reads(client):
    dashboard_result = client.get("/api/metrics")
    assert dashboard_result.status_code == 200
    assert client.get("/api/audit").json() == []

    nonce = "b" * 32
    agent_result = client.get(
        "/api/metrics",
        params={"probe_nonce": nonce},
        headers={"X-Actor": "agent"},
    )

    assert agent_result.status_code == 200
    rows = client.get("/api/audit").json()
    assert len(rows) == 1
    assert rows[0]["actor"] == "agent"
    assert rows[0]["tool"] == "generate_dashboard_insights"
    assert json.loads(rows[0]["input"]) == {"probe_nonce": nonce}
    assert json.loads(rows[0]["output"]) == agent_result.json()


def test_send_targets_configured_crm_agent(monkeypatch):
    import app.agent.openclaw as module

    fake = FakeClient()
    monkeypatch.setattr(module, "AGENT_ID", "openhouse-crm")
    driver = OpenClawDriver(client_factory=lambda **_: fake)

    assert asyncio.run(driver.chat("List leads", "dash-fresh")) == "READY"
    assert fake.last_post_json["model"] == "openclaw/openhouse-crm"
    assert fake.last_post_json["user"] == "dash-fresh"


def test_blank_agent_id_keeps_openclaw_default_compatibility(monkeypatch):
    import app.agent.openclaw as module

    fake = FakeClient()
    monkeypatch.setattr(module, "AGENT_ID", "")
    driver = OpenClawDriver(client_factory=lambda **_: fake)

    asyncio.run(driver.chat("hello", "compat"))
    assert fake.last_post_json["model"] == "openclaw"


def test_mock_follow_up_prompt_accepts_hyphenated_wording():
    reply = asyncio.run(
        MockDriver().chat(
            "Which active buyers need a follow-up?",
            "dashboard",
        )
    )

    assert "2 leads haven't been touched" in reply
