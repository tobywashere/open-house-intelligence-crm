"""Every public skill tool must be callable and raise only CRMError on failure.
Would have caught delete_lead's NameError (dead since birth)."""
import importlib.util
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

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
    "update_lead": ((1,), {"status": "contacted"}),
    "find_duplicate_leads": ((1,), {}), "get_lead_context": ((1,), {}), "list_leads": ((), {}),
    "score_lead": ((1,), {}), "draft_followup": ((1,), {}), "check_availability": (("2026-08-03",), {}),
    "list_appointments": ((), {}),
    "book_appointment": ((1, "2026-08-03T18:00:00", "2026-08-03T18:45:00", "loc"), {}),
    "schedule_followup": ((1, "2026-08-04T09:00:00", "note"), {}), "find_neglected_leads": ((), {}),
    "generate_dashboard_insights": ((), {}), "merge_leads": ((1, 2), {}), "delete_lead": ((1,), {}),
    "close_lead": ((1, "won", "Contract signed"), {}),
    "post_briefing": (({"date": "2026-08-01", "greeting": "test"},), {}),
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
    """urlopen read-timeouts escape as TimeoutError unless _request catches them."""
    import urllib.request
    with patch.object(urllib.request, "urlopen", side_effect=TimeoutError("read timed out")):
        with pytest.raises(crm.CRMError):
            crm.list_leads()


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def test_x_api_token_header_sent_when_configured():
    """.env.example and docs/LOCAL-AI.md tell operators to set OHI_API_TOKEN once
    the backend binds beyond localhost — every skill call must actually send it,
    or the guarded backend just 401s the whole product."""
    import urllib.request
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    with patch.object(crm, "API_TOKEN", "s3cret"):
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            crm.list_leads()
    assert captured["headers"].get("X-api-token") == "s3cret"


def test_x_api_token_header_absent_when_unset():
    import urllib.request
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    with patch.object(crm, "API_TOKEN", ""):
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            crm.list_leads()
    assert "X-api-token" not in captured["headers"]


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


def test_natural_language_crm_write_and_booking_contract_end_to_end(live_server):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO availability (weekday, start_time, end_time) VALUES (0, '17:00', '18:00')"
        )

    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        lead = crm.create_lead(name="Taylor Brooks", source="note", area="Bellevue")
        updated = crm.update_lead(lead["id"], budget=900_000)
        reminder = crm.schedule_followup(
            lead["id"], "2026-08-03T09:00:00", "Call Taylor"
        )
        slots = crm.check_availability("2026-08-03")
        appointment = crm.book_appointment(
            lead["id"],
            slots[0]["start_ts"],
            slots[0]["end_ts"],
            "Bellevue",
        )

        assert updated["budget"] == 900_000
        assert reminder["lead_id"] == lead["id"]
        assert appointment["lead_id"] == lead["id"]
        assert crm.get_lead_context(lead["id"])["status"] == "meeting_booked"


def test_close_lead_tool_records_explicit_won_outcome(live_server):
    with patch.object(crm, "BASE_URL", f"{live_server}/api"):
        lead = crm.create_lead(name="Won Client", source="note")
        closed = crm.close_lead(lead["id"], "won", "Contract signed")

        assert closed["status"] == "closed"
        assert closed["outcome"] == "won"
        assert crm.get_lead_context(lead["id"])["close_reason"] == "Contract signed"


def test_close_lead_tool_rejects_ambiguous_outcome_before_request():
    with pytest.raises(ValueError, match="won.*lost"):
        crm.close_lead(1, "unknown")
