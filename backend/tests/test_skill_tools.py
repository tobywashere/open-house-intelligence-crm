"""Every public skill tool must be callable and raise only CRMError on failure.
Would have caught delete_lead's NameError (dead since birth)."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import inspect
import json
import sys
from pathlib import Path
import threading
from unittest.mock import patch

import httpx
import pytest

from .live_server import live_server  # noqa: F401  (end-to-end search_knowledge test)

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SKILLS / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


crm = _load("crm_tools", "crm-db-operations/tools.py")

PUBLIC = [f for n, f in inspect.getmembers(crm, inspect.isfunction) if not n.startswith("_")]
# name -> (positional args, keyword args); extend as the catalog grows.
# update_lead is **fields-only, so its "sample field to patch" must go in kwargs,
# not as a second positional arg (that raises TypeError, not CRMError).
SAMPLE_ARGS = {
    "create_lead": (("note text", "note"), {}),
    "add_note": ((1, "Requested a Saturday tour"), {}),
    "update_lead": ((1,), {"status": "contacted"}),
    "find_duplicate_leads": ((1,), {}), "get_lead_context": ((1,), {}), "list_leads": ((), {}),
    "list_lead_directory": ((), {}),
    "score_lead": ((1,), {}), "draft_followup": ((1,), {}), "check_availability": (("2026-08-03",), {}),
    "list_appointments": ((), {}),
    "book_appointment": ((1, "2026-08-03T18:00:00", "2026-08-03T18:45:00", "loc"), {}),
    "schedule_followup": ((1, "2026-08-04T09:00:00", "note"), {}), "find_neglected_leads": ((), {}),
    "generate_dashboard_insights": ((), {}), "merge_leads": ((1, 2), {}), "delete_lead": ((1,), {}),
    "close_lead": ((1, "won", "Contract signed"), {}),
    "post_briefing": (({"date": "2026-08-01", "greeting": "test"},), {}),
    "get_research_settings": ((), {}), "get_insights": (("2026-08-01",), {}),
    "get_summary": (("2026-08-01",), {}),
    "post_summary": (({"date": "2026-08-01", "generated_at": "2026-08-01T08:00:00Z",
                      "greeting": "test", "market_watch": [], "ai_insights": []},), {}),
    "search_knowledge": (("Amazon RSU vesting",), {}),
}


@pytest.mark.parametrize("fn", PUBLIC, ids=lambda f: f.__name__)
def test_every_tool_raises_only_crmerror_when_backend_down(fn):
    assert fn.__name__ in SAMPLE_ARGS, f"add sample args for new tool {fn.__name__}"
    args, kwargs = SAMPLE_ARGS[fn.__name__]
    with patch.object(crm, "BASE_URL", "http://127.0.0.1:9"):  # nothing listens
        with pytest.raises(crm.CRMError):
            fn(*args, **kwargs)


def test_read_timeout_is_crmerror():
    """Read timeouts escape as TimeoutError unless _request catches them."""
    with patch.object(crm, "_open_request", side_effect=TimeoutError("read timed out")):
        with pytest.raises(crm.CRMError):
            crm.list_leads()


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


@contextmanager
def _local_http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
def test_authenticated_crm_client_hard_fails_redirect_without_remote_hit(
    redirect_status,
):
    origin_tokens = []
    remote_hits = []

    class RemoteHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            remote_hits.append(dict(self.headers.items()))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET

        def log_message(self, *_args):
            pass

    with _local_http_server(RemoteHandler) as remote_url:
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                origin_tokens.append(self.headers.get("X-API-Token"))
                self.send_response(redirect_status)
                self.send_header("Location", remote_url + "/capture")
                self.end_headers()

            def log_message(self, *_args):
                pass

        with _local_http_server(RedirectHandler) as origin_url:
            with patch.object(crm, "BASE_URL", origin_url + "/api"):
                with patch.object(crm, "API_TOKEN", "redirect-secret"):
                    with pytest.raises(crm.CRMError) as exc:
                        crm.post_summary({"date": "2026-08-06"})

    assert exc.value.status == redirect_status
    assert origin_tokens == ["redirect-secret"]
    assert remote_hits == []


def test_authenticated_crm_client_preserves_normal_local_calls():
    received_tokens = []

    class TrustedHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            received_tokens.append(self.headers.get("X-API-Token"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *_args):
            pass

    with _local_http_server(TrustedHandler) as trusted_url:
        with patch.object(crm, "BASE_URL", trusted_url + "/api"):
            with patch.object(crm, "API_TOKEN", "trusted-secret"):
                assert crm.list_leads() == []

    assert received_tokens == ["trusted-secret"]


def test_x_api_token_header_sent_when_configured():
    """.env.example and docs/LOCAL-AI.md tell operators to set OHI_API_TOKEN once
    the backend binds beyond localhost — every skill call must actually send it,
    or the guarded backend just 401s the whole product."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    with patch.object(crm, "API_TOKEN", "s3cret"):
        with patch.object(crm, "_open_request", side_effect=fake_urlopen):
            crm.list_leads()
    assert captured["headers"].get("X-api-token") == "s3cret"


def test_x_api_token_header_absent_when_unset():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    with patch.object(crm, "API_TOKEN", ""):
        with patch.object(crm, "_open_request", side_effect=fake_urlopen):
            crm.list_leads()
    assert "X-api-token" not in captured["headers"]


def test_dashboard_insights_passes_optional_probe_nonce():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse()

    with patch.object(crm, "_open_request", side_effect=fake_urlopen):
        crm.generate_dashboard_insights("c" * 32)

    assert captured["url"].endswith("/metrics?probe_nonce=" + "c" * 32)


def test_dashboard_insights_remains_no_argument_compatible():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse()

    with patch.object(crm, "_open_request", side_effect=fake_urlopen):
        crm.generate_dashboard_insights()

    assert captured["url"].endswith("/metrics")


def test_lead_directory_returns_exact_total_and_compact_page(monkeypatch):
    rows = [
        {
            "id": i,
            "name": f"Lead {i}",
            "status": "new",
            "score": i,
            "area": "Kirkland",
            "timeline": "this month",
            "intent": "buy",
            "is_neglected": 0,
            "last_activity_at": "2026-08-22T09:00:00",
            "relationship_summary": "x" * 5000,
            "preferences": {"beds": 3},
            "missing_fields": ["phone"],
        }
        for i in range(30)
    ]
    captured = {}

    def fake_request(method, path, *, params=None, body=None):
        captured.update(method=method, path=path, params=params, body=body)
        return rows

    monkeypatch.setattr(crm, "_request", fake_request)

    result = crm.list_lead_directory(
        sort="recent", status="new", neglected=0, offset=5, limit=10
    )

    assert result["total"] == 30
    assert result["offset"] == 5
    assert result["limit"] == 10
    assert [row["id"] for row in result["leads"]] == list(range(5, 15))
    assert set(result["leads"][0]) == {
        "id", "name", "status", "score", "area", "timeline", "intent",
        "is_neglected", "last_activity_at",
    }
    assert "relationship_summary" not in result["leads"][0]
    assert "preferences" not in result["leads"][0]
    assert "missing_fields" not in result["leads"][0]
    assert captured == {
        "method": "GET",
        "path": "/leads",
        "params": {"sort": "recent", "status": "new", "neglected": 0},
        "body": None,
    }


@pytest.mark.parametrize(
    ("function_name", "expected"),
    [
        ("score_lead", {"lead_id": 7, "score": 81, "score_reason": "Strong fit"}),
        ("draft_followup", "Hi Jordan, would Tuesday work?"),
    ],
)
def test_narrative_tools_use_non_proposing_process_path(monkeypatch, function_name, expected):
    calls = []

    def fake_request(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {
            "lead": {"score": 81, "score_reason": "Strong fit"},
            "followup_draft": "Hi Jordan, would Tuesday work?",
            "pending_change": None,
        }

    monkeypatch.setattr(crm, "_request", fake_request)

    assert getattr(crm, function_name)(7) == expected
    assert calls == [
        ("POST", "/leads/7/process", {"propose_changes": False}, None)
    ]


def test_add_note_uses_reviewed_event_endpoint():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data)
        return _FakeResponse()

    with patch.object(crm, "_open_request", side_effect=fake_urlopen):
        crm.add_note(7, "  Requested a Saturday tour  ")

    assert captured["url"].endswith("/leads/7/events")
    assert captured["method"] == "POST"
    assert captured["headers"]["X-actor"] == "agent"
    assert captured["body"] == {"type": "note", "content": "Requested a Saturday tour"}


def test_add_note_rejects_blank_content_before_request():
    with pytest.raises(ValueError, match="content must not be empty"):
        crm.add_note(7, "   ")


def test_search_knowledge_end_to_end(live_server):
    """search_knowledge is the agent-invoked path onto the real BM25 index
    over the shipped report (docs/knowledge/) — a domain question should hit,
    ordinary CRM chatter should not, exercised over real HTTP against a real
    running backend (not mocked), same as the model actually calls it."""
    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        hits = crm.search_knowledge("Amazon RSU vesting schedule")
        assert hits, "expected the shipped report to be searchable via the tool"
        assert any("Vesting" in h["heading"] or "Equity" in h["heading"] for h in hits), hits

        no_hits = crm.search_knowledge("remind me to call my mom")
        assert no_hits == []


def _approve(live_server, pending):
    """create_lead/update_lead/close_lead now queue for operator approval
    (tools.py sends X-Actor: agent — see docs/CONTRACT.md's pending-changes
    section) instead of applying directly. Approving stands in for the
    operator's dashboard click so these end-to-end tests can keep exercising
    the rest of the natural-language flow against a real applied lead."""
    assert pending["pending"] is True
    res = httpx.post(f"{live_server}/api/pending-changes/{pending['id']}/approve")
    assert res.status_code == 200, res.text
    return res.json()


def test_natural_language_crm_write_and_booking_contract_end_to_end(live_server):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO availability (weekday, start_time, end_time) VALUES (0, '17:00', '18:00')"
        )

    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        lead = _approve(live_server, crm.create_lead(name="Taylor Brooks", source="note", area="Bellevue"))
        updated = _approve(live_server, crm.update_lead(lead["id"], budget=900_000))
        note = _approve(live_server, crm.add_note(lead["id"], "Asked about Saturday tours"))
        reminder = _approve(live_server, crm.schedule_followup(
            lead["id"], "2026-08-03T09:00:00", "Call Taylor"
        ))
        slots = crm.check_availability("2026-08-03")
        appointment = _approve(live_server, crm.book_appointment(
            lead["id"],
            slots[0]["start_ts"],
            slots[0]["end_ts"],
            "Bellevue",
        ))

        assert updated["budget"] == 900_000
        assert note["content"] == "Asked about Saturday tours"
        assert reminder["lead_id"] == lead["id"]
        assert appointment["lead_id"] == lead["id"]
        assert crm.get_lead_context(lead["id"])["status"] == "meeting_booked"


def test_close_lead_tool_records_explicit_won_outcome(live_server):
    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        lead = _approve(live_server, crm.create_lead(name="Won Client", source="note"))
        closed = _approve(live_server, crm.close_lead(lead["id"], "won", "Contract signed"))

        assert closed["status"] == "closed"
        assert closed["outcome"] == "won"
        assert crm.get_lead_context(lead["id"])["close_reason"] == "Contract signed"


def test_close_lead_tool_rejects_ambiguous_outcome_before_request():
    with pytest.raises(ValueError, match="won.*lost"):
        crm.close_lead(1, "unknown")


def test_score_lead_returns_narrative_without_persisting_or_proposing(live_server):
    created = httpx.post(
        f"{live_server}/api/leads",
        json={
            "name": "Score Candidate",
            "source": "note",
            "email": "score@example.com",
            "budget": 900_000,
            "timeline": "6 weeks",
            "intent": "buy",
        },
    )
    assert created.status_code == 200, created.text
    lead_id = created.json()["id"]

    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        candidate = crm.score_lead(lead_id)
        repeated_candidate = crm.score_lead(lead_id)

    persisted = httpx.get(f"{live_server}/api/leads/{lead_id}").json()
    assert candidate["score"] > 0
    assert candidate["score_reason"]
    assert repeated_candidate == candidate
    assert persisted["score"] is None
    assert persisted["score_reason"] is None
    assert httpx.get(f"{live_server}/api/pending-changes").json() == []


def test_draft_followup_does_not_persist_or_propose_candidate_score(live_server):
    created = httpx.post(
        f"{live_server}/api/leads",
        json={
            "name": "Draft Candidate",
            "source": "note",
            "email": "draft@example.com",
            "budget": 850_000,
            "area": "Kirkland",
            "timeline": "2 months",
            "intent": "buy",
        },
    )
    assert created.status_code == 200, created.text
    lead_id = created.json()["id"]

    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        draft = crm.draft_followup(lead_id)

    persisted = httpx.get(f"{live_server}/api/leads/{lead_id}").json()
    assert "Draft" in draft
    assert persisted["score"] is None
    assert persisted["score_reason"] is None
    assert httpx.get(f"{live_server}/api/pending-changes").json() == []
