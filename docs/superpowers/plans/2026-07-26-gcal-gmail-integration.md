# Google Calendar + Gmail Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the CRM to Johaan's real Google account (via Composio REST) so tours/leads/reminders land on Google Calendar and AI-drafted follow-ups send via Gmail with one click — all behind `INTEGRATIONS_MODE=off|live`, off by default.

**Architecture:** New `backend/app/integrations/` package (Composio client, outbound hooks, inbound poller, router). Existing routers call fire-and-forget hooks after their DB commit; hooks open their own connection, never raise, and audit everything (simulated in off mode). Dashboard gets a send button, compose box, status chip.

**Tech Stack:** FastAPI, SQLite, httpx (already deps), pytest (new), Composio v3 REST API, React/TS dashboard.

**Spec:** `docs/superpowers/specs/2026-07-26-gcal-gmail-integration-design.md`

## Global Constraints

- `INTEGRATIONS_MODE` defaults to `off`; off mode must NEVER touch the network.
- Hooks must never fail the parent request — catch everything, audit, move on. Exception: `POST /email/send` surfaces failure as HTTP 502.
- All schema changes additive only (`appointments.gcal_event_id`, `reminders.gcal_event_id` via `_migrate`); the frozen contract endpoints are untouched.
- Event-type convention: `events.type = 'email'`; reply dedupe marker `[gmail:<message_id>]` inside event content.
- Every integration action writes an `audit_log` row; simulated actions get a ` (simulated)` tool suffix, failures ` (failed)`.
- Env vars: `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID` (default `default`), `GCAL_TIMEZONE` (default `America/Los_Angeles`), `COMPOSIO_BASE_URL` (default `https://backend.composio.dev`).
- Backend venv is repo-root `.venv`; run tests as `cd backend && ../.venv/bin/python -m pytest tests -v`.
- Frontend has no test framework: verification is `cd dashboard && npx tsc --noEmit` plus `npm run build`.
- Commit after every task; message prefix `Integrations:`.

---

### Task 1: Test harness + additive DB migration

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/db.py:31-37` (`_migrate`)
- Create: `backend/tests/__init__.py` (empty), `backend/tests/conftest.py`
- Test: `backend/tests/test_migration.py`

**Interfaces:**
- Consumes: existing `db.init_db()` / `_migrate` pattern.
- Produces: pytest `client` fixture (fresh temp DB per test, `TestClient` with startup run) used by every later test; columns `appointments.gcal_event_id TEXT`, `reminders.gcal_event_id TEXT`.

- [ ] **Step 1: Add pytest to requirements and install**

Append to `backend/requirements.txt`:

```
pytest>=8
```

Run: `.venv/bin/pip install -r backend/requirements.txt`

- [ ] **Step 2: Write conftest with temp-DB client fixture**

`backend/tests/conftest.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ importable

TEST_DB = Path(__file__).resolve().parent / "test.db"
os.environ["DB_PATH"] = str(TEST_DB)          # must be set BEFORE importing app
os.environ["AGENT_MODE"] = "mock"
os.environ["INTEGRATIONS_MODE"] = "off"   # hard-set: ambient live env must not leak into tests

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB) + suffix)
        if p.exists():
            p.unlink()
    with TestClient(app) as c:  # startup event runs init_db()
        yield c


def make_lead(client, **overrides) -> dict:
    body = {"name": "Test Lead", "email": "lead@example.com",
            "phone": "+14255550100", "area": "Bellevue", "budget": 900000,
            "timeline": "6 weeks", "source": "note"}
    body.update(overrides)
    res = client.post("/api/leads", json=body)
    assert res.status_code == 200, res.text
    return res.json()
```

Also create empty `backend/tests/__init__.py`.

- [ ] **Step 3: Write the failing migration test**

`backend/tests/test_migration.py`:

```python
import sqlite3

from conftest import TEST_DB


def test_gcal_columns_migrated(client):
    conn = sqlite3.connect(TEST_DB)
    appt_cols = {r[1] for r in conn.execute("PRAGMA table_info(appointments)")}
    rem_cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
    conn.close()
    assert "gcal_event_id" in appt_cols
    assert "gcal_event_id" in rem_cols
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_migration.py -v`
Expected: FAIL — `assert 'gcal_event_id' in appt_cols`

- [ ] **Step 5: Extend `_migrate` in `backend/app/db.py`**

Replace the `_migrate` function body:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column backfill for DBs created before a column existed —
    lets an existing GB10/demo DB pick up new fields without a reseed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    for col in ("persona", "relationship_summary"):
        if col not in cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    for table in ("appointments", "reminders"):
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "gcal_event_id" not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN gcal_event_id TEXT")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/app/db.py backend/tests/
git commit -m "Integrations: test harness + additive gcal_event_id columns"
```

---

### Task 2: Composio client + mode plumbing

**Files:**
- Create: `backend/app/integrations/__init__.py` (empty), `backend/app/integrations/composio_client.py`
- Test: `backend/tests/test_composio_client.py`

**Interfaces:**
- Produces (used by every later backend task):
  - `composio_client.mode() -> str` — reads `INTEGRATIONS_MODE` at call time (so tests/env flips work).
  - `composio_client.is_live() -> bool` — `mode() == "live"` AND `COMPOSIO_API_KEY` set.
  - `composio_client.execute(slug: str, arguments: dict) -> dict` — runs a Composio tool, one retry, returns the tool's `data` dict; raises `IntegrationError` on failure.
  - `composio_client.IntegrationError(Exception)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_composio_client.py`:

```python
import pytest

from app.integrations import composio_client as cc


def test_mode_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    assert cc.mode() == "off"
    assert not cc.is_live()
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    assert not cc.is_live()          # live without a key is not live
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    assert cc.is_live()


def test_execute_success(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    calls = []

    class FakeResp:
        status_code = 200
        def json(self):
            return {"successful": True, "data": {"response_data": {"id": "evt1"}}}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return FakeResp()

    monkeypatch.setattr(cc.httpx, "post", fake_post)
    data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", {"summary": "x"})
    assert data == {"response_data": {"id": "evt1"}}
    url, headers, body = calls[0]
    assert url.endswith("/api/v3/tools/execute/GOOGLECALENDAR_CREATE_EVENT")
    assert headers["x-api-key"] == "k"
    assert body["arguments"] == {"summary": "x"}


def test_execute_retries_once_then_raises(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    attempts = []

    class FakeResp:
        status_code = 200
        def json(self):
            return {"successful": False, "error": "boom"}

    monkeypatch.setattr(cc.httpx, "post",
                        lambda *a, **kw: attempts.append(1) or FakeResp())
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})
    assert len(attempts) == 2


def test_execute_without_key_raises(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_composio_client.py -v`
Expected: FAIL — `ModuleNotFoundError: app.integrations`

- [ ] **Step 3: Implement the client**

Create empty `backend/app/integrations/__init__.py`, then `backend/app/integrations/composio_client.py`:

```python
"""Thin Composio v3 REST client (only touched when INTEGRATIONS_MODE=live).

Docs: POST {base}/api/v3/tools/execute/{slug} with x-api-key header and
{"user_id": ..., "arguments": {...}} body → {"successful": bool, "data": {...},
"error": str|null}. Tool schemas: `composio execute <SLUG> --get-schema`.
"""
import os

import httpx


class IntegrationError(Exception):
    pass


def mode() -> str:
    return os.environ.get("INTEGRATIONS_MODE", "off")


def is_live() -> bool:
    return mode() == "live" and bool(os.environ.get("COMPOSIO_API_KEY"))


def execute(slug: str, arguments: dict) -> dict:
    key = os.environ.get("COMPOSIO_API_KEY")
    if not key:
        raise IntegrationError("COMPOSIO_API_KEY not set")
    base = os.environ.get("COMPOSIO_BASE_URL", "https://backend.composio.dev")
    body = {"user_id": os.environ.get("COMPOSIO_USER_ID", "default"),
            "arguments": arguments}
    last_err = None
    for _ in range(2):  # one retry
        try:
            r = httpx.post(f"{base}/api/v3/tools/execute/{slug}",
                           headers={"x-api-key": key}, json=body, timeout=15)
            payload = r.json() if r.status_code < 500 else {}
            if r.status_code == 200 and payload.get("successful"):
                return payload.get("data") or {}
            last_err = payload.get("error") or f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last_err = str(e)
    raise IntegrationError(f"{slug}: {last_err}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS (all)

- [ ] **Step 5: Verify the real API wrapper shape (one manual call, Johaan's machine)**

The unit tests mock HTTP; confirm the live wrapper once so live mode isn't a surprise later. Key lives in `~/.composio/user_data.json` (`api_key` field).

```bash
KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.composio/user_data.json')))['api_key'])")
curl -s -X POST "https://backend.composio.dev/api/v3/tools/execute/GMAIL_FETCH_EMAILS" \
  -H "x-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"user_id":"default","arguments":{"max_results":1}}' | head -c 400
```

Expected: JSON containing `"successful":true` and a `"data"` object. If the wrapper differs (e.g. no `successful` key), fix `execute()` to match the observed shape before continuing — the later tasks all go through this one function.

- [ ] **Step 6: Commit**

```bash
git add backend/app/integrations/ backend/tests/test_composio_client.py
git commit -m "Integrations: Composio v3 REST client behind INTEGRATIONS_MODE"
```

---

### Task 3: `POST /email/send` + `GET /integrations/status`

**Files:**
- Create: `backend/app/integrations/router.py`
- Modify: `backend/app/main.py:9,20-24` (import + include router)
- Test: `backend/tests/test_email_send.py`

**Interfaces:**
- Consumes: `composio_client.is_live/mode/execute/IntegrationError`, `db.audit/get_conn`, `routers.leads.fetch_lead/NOW`, conftest `make_lead`.
- Produces:
  - `POST /api/email/send {lead_id, subject, body}` → `{sent: true, simulated: bool}`; 400 if lead has no email; 502 if live Gmail send fails. Side effects: `email` event, status `new`→`contacted` (+`status_change` event), 3-day reply-check reminder (note starts `Check for a reply from `), audit row `gmail_send`.
  - `GET /api/integrations/status` → `{mode: "off"|"live", gmail: bool, gcal: bool}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_email_send.py`:

```python
from conftest import make_lead
from app.integrations import composio_client as cc


def test_status_off_by_default(client):
    res = client.get("/api/integrations/status").json()
    assert res == {"mode": "off", "gmail": False, "gcal": False}


def test_send_simulated_runs_closed_loop(client):
    lead = make_lead(client)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "Homes in Bellevue", "body": "Hi!"})
    assert res.status_code == 200
    assert res.json() == {"sent": True, "simulated": True}

    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert profile["status"] == "contacted"
    types = [e["type"] for e in profile["events"]]
    assert "email" in types and "status_change" in types

    reminders = client.get("/api/reminders").json()
    assert any(r["lead_id"] == lead["id"] and
               r["note"].startswith("Check for a reply") for r in reminders)

    audit = client.get("/api/audit?limit=10").json()
    assert any(a["tool"] == "gmail_send (simulated)" for a in audit)


def test_send_no_email_400(client):
    lead = make_lead(client, email=None, name="No Email")
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.status_code == 400


def test_send_live_calls_gmail(client, monkeypatch):
    lead = make_lead(client)          # create BEFORE going live: the lead hook must not hit the network
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    sent = {}

    def fake_execute(slug, arguments):
        sent["slug"], sent["args"] = slug, arguments
        return {"response_data": {"id": "msg123"}}

    monkeypatch.setattr("app.integrations.router.cc.execute", fake_execute)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.json() == {"sent": True, "simulated": False}
    assert sent["slug"] == "GMAIL_SEND_EMAIL"
    assert sent["args"]["recipient_email"] == "lead@example.com"

    profile = client.get(f"/api/leads/{lead['id']}").json()
    email_ev = next(e for e in profile["events"] if e["type"] == "email")
    assert "[gmail:msg123]" in email_ev["content"]


def test_send_live_failure_502(client, monkeypatch):
    lead = make_lead(client)          # create BEFORE going live (see above)
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")

    def boom(slug, arguments):
        raise cc.IntegrationError("scope missing")

    monkeypatch.setattr("app.integrations.router.cc.execute", boom)
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.status_code == 502
    profile = client.get(f"/api/leads/{lead['id']}").json()
    assert profile["status"] == "new"          # closed loop did NOT run
    assert not any(e["type"] == "email" for e in profile["events"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_email_send.py -v`
Expected: FAIL — 404s (routes don't exist)

- [ ] **Step 3: Implement the router**

`backend/app/integrations/router.py`:

```python
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import audit, get_conn
from ..routers.leads import NOW, fetch_lead
from . import composio_client as cc

router = APIRouter(tags=["integrations"])


class EmailIn(BaseModel):
    lead_id: int
    subject: str
    body: str


@router.get("/integrations/status")
def status():
    live = cc.is_live()
    return {"mode": cc.mode(), "gmail": live, "gcal": live}


@router.post("/email/send")
def send_email(body: EmailIn):
    with get_conn() as conn:
        lead = fetch_lead(conn, body.lead_id)
    if not lead.get("email"):
        raise HTTPException(400, "lead has no email address")

    simulated = not cc.is_live()
    marker = ""
    if not simulated:
        try:
            data = cc.execute("GMAIL_SEND_EMAIL", {
                "recipient_email": lead["email"],
                "subject": body.subject,
                "body": body.body,
            })
            msg_id = (data.get("response_data") or data).get("id")
            if msg_id:
                marker = f"\n[gmail:{msg_id}]"
        except cc.IntegrationError as e:
            raise HTTPException(502, f"Gmail send failed: {e}")

    # closed loop only after a confirmed (or simulated) send
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (lead["id"], "email",
             f"Email sent: {body.subject}\n\n{body.body}{marker}"))
        if lead["status"] == "new":
            conn.execute(
                f"UPDATE leads SET status = 'contacted', last_activity_at = ({NOW}) "
                "WHERE id = ?", (lead["id"],))
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead["id"], "status_change", "new → contacted"))
        due = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
            (lead["id"], due, f"Check for a reply from {lead['name']}"))
        conn.execute(
            f"UPDATE leads SET last_activity_at = ({NOW}) WHERE id = ?", (lead["id"],))
        audit(conn, "user", "gmail_send" + (" (simulated)" if simulated else ""),
              {"lead_id": lead["id"], "subject": body.subject},
              {"simulated": simulated}, lead["id"])
    return {"sent": True, "simulated": simulated}
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

Change the routers import line and add one include:

```python
from .integrations import router as integrations
from .routers import calendar, chat, leads, misc, reports
```

```python
app.include_router(integrations.router, prefix="/api")
```

(place the include next to the existing five `include_router` lines).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/integrations/router.py backend/app/main.py backend/tests/test_email_send.py
git commit -m "Integrations: POST /email/send with closed loop + /integrations/status"
```

---

### Task 4: Outbound hooks — tour→GCal, new lead→call block + Gmail draft, reminder→GCal

**Files:**
- Create: `backend/app/integrations/hooks.py`
- Modify: `backend/app/routers/calendar.py:34-55` (`book_appointment`), `backend/app/routers/leads.py:62-92` (`create_lead`), `backend/app/routers/misc.py:25-34` (`create_reminder`)
- Test: `backend/tests/test_hooks.py`

**Interfaces:**
- Consumes: `composio_client` (Task 2), `db.audit/get_conn`.
- Produces (called by routers AFTER their `with get_conn()` block commits):
  - `hooks.on_tour_booked(lead: dict, appt: dict) -> None`
  - `hooks.on_lead_created(lead: dict) -> None`
  - `hooks.on_reminder_created(reminder: dict) -> None`
  - All open their own connection, never raise, audit as actor `user` with tools `gcal_create_event` / `gmail_create_draft` (+ ` (simulated)` / ` (failed)` suffixes), and store `gcal_event_id` on live success.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_hooks.py`:

```python
from conftest import make_lead


def _audit_tools(client):
    return [a["tool"] for a in client.get("/api/audit?limit=30").json()]


def test_booking_hook_simulated(client):
    lead = make_lead(client)
    res = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-07-28T10:00:00",
        "end_ts": "2026-07-28T10:45:00", "location": "123 Main St"})
    assert res.status_code == 200
    assert "gcal_create_event (simulated)" in _audit_tools(client)


def test_new_lead_hook_simulated_event_and_draft(client):
    make_lead(client)
    tools = _audit_tools(client)
    assert "gcal_create_event (simulated)" in tools     # call block
    assert "gmail_create_draft (simulated)" in tools    # intro draft


def test_new_lead_without_email_no_draft(client):
    make_lead(client, email=None, name="Phone Only")
    assert "gmail_create_draft (simulated)" not in _audit_tools(client)


def test_reminder_hook_simulated(client):
    lead = make_lead(client)
    client.post("/api/reminders", json={
        "lead_id": lead["id"], "due_ts": "2026-07-29T09:00:00", "note": "call back"})
    assert "gcal_create_event (simulated)" in _audit_tools(client)


def test_booking_hook_live_stores_event_id(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    monkeypatch.setattr("app.integrations.hooks.cc.execute",
                        lambda slug, args: {"response_data": {"id": "gcal-evt-9"}})
    lead = make_lead(client)
    appt = client.post("/api/appointments", json={
        "lead_id": lead["id"], "start_ts": "2026-07-28T11:00:00",
        "end_ts": "2026-07-28T11:45:00", "location": None}).json()
    profile = client.get(f"/api/leads/{lead['id']}").json()
    stored = next(a for a in profile["appointments"] if a["id"] == appt["id"])
    assert stored["gcal_event_id"] == "gcal-evt-9"


def test_hook_failure_never_breaks_request(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")

    def boom(slug, args):
        from app.integrations.composio_client import IntegrationError
        raise IntegrationError("network down")

    monkeypatch.setattr("app.integrations.hooks.cc.execute", boom)
    lead = make_lead(client)                      # hook fails silently
    assert lead["id"] > 0
    assert any(t.endswith("(failed)") for t in _audit_tools(client))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_hooks.py -v`
Expected: FAIL — no `(simulated)` audit rows / `ModuleNotFoundError: app.integrations.hooks`

- [ ] **Step 3: Implement the hooks**

`backend/app/integrations/hooks.py`:

```python
"""Outbound Google hooks (spec: docs/superpowers/specs/2026-07-26-gcal-gmail-
integration-design.md). Fire-and-forget: a hook must never raise — failures
land in audit_log and the triggering request succeeds regardless. Hooks open
their own connection because they run after the caller's transaction commits."""
import os
from datetime import datetime, timedelta

from ..db import audit, get_conn
from . import composio_client as cc


def _tz() -> str:
    return os.environ.get("GCAL_TIMEZONE", "America/Los_Angeles")


def _lead_details(lead: dict) -> str:
    budget = f"${lead['budget']:,}" if lead.get("budget") else "—"
    return (f"Phone: {lead.get('phone') or '—'}\n"
            f"Email: {lead.get('email') or '—'}\n"
            f"Budget: {budget}\n"
            f"Area: {lead.get('area') or '—'}\n"
            f"Timeline: {lead.get('timeline') or '—'}\n\n"
            "— Open House Intelligence")


def _create_event(lead_id: int | None, args: dict) -> str | None:
    """Create a GCal event (or simulate). Returns the Google event id when live."""
    with get_conn() as conn:
        if not cc.is_live():
            audit(conn, "user", "gcal_create_event (simulated)", args,
                  {"simulated": True}, lead_id)
            return None
        try:
            data = cc.execute("GOOGLECALENDAR_CREATE_EVENT", args)
            event_id = (data.get("response_data") or data).get("id")
            audit(conn, "user", "gcal_create_event", args,
                  {"event_id": event_id}, lead_id)
            return event_id
        except cc.IntegrationError as e:
            audit(conn, "user", "gcal_create_event (failed)", args,
                  {"error": str(e)}, lead_id)
            return None


def on_tour_booked(lead: dict, appt: dict) -> None:
    start = datetime.fromisoformat(appt["start_ts"])
    end = datetime.fromisoformat(appt["end_ts"])
    minutes = max(int((end - start).total_seconds() // 60), 15)
    event_id = _create_event(lead["id"], {
        "calendar_id": "primary",
        "summary": f"Home tour with {lead['name']}",
        "description": _lead_details(lead),
        "location": appt.get("location") or "",
        "start_datetime": appt["start_ts"],
        "event_duration_minutes": minutes,
        "timezone": _tz(),
    })
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE appointments SET gcal_event_id = ? WHERE id = ?",
                         (event_id, appt["id"]))


def on_lead_created(lead: dict) -> None:
    start = (datetime.now() + timedelta(minutes=30)).replace(second=0, microsecond=0)
    _create_event(lead["id"], {
        "calendar_id": "primary",
        "summary": f"📞 Call new lead: {lead['name']}",
        "description": _lead_details(lead),
        "start_datetime": start.isoformat(),
        "event_duration_minutes": 30,
        "timezone": _tz(),
    })
    if not lead.get("email"):
        return
    first = lead["name"].split()[0]
    subject = (f"Your home search in {lead['area']}" if lead.get("area")
               else "Great to connect!")
    body = (f"Hi {first},\n\nThanks for reaching out — I'd love to help with "
            "your home search. When would be a good time for a quick call?\n\n"
            "Best,\nJohaan")
    args = {"recipient_email": lead["email"], "subject": subject, "body": body}
    with get_conn() as conn:
        if not cc.is_live():
            audit(conn, "user", "gmail_create_draft (simulated)", args,
                  {"simulated": True}, lead["id"])
            return
        try:
            data = cc.execute("GMAIL_CREATE_EMAIL_DRAFT", args)
            audit(conn, "user", "gmail_create_draft", args,
                  {"id": (data.get("response_data") or data).get("id")}, lead["id"])
        except cc.IntegrationError as e:
            audit(conn, "user", "gmail_create_draft (failed)", args,
                  {"error": str(e)}, lead["id"])


def on_reminder_created(reminder: dict) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM leads WHERE id = ?",
                           (reminder["lead_id"],)).fetchone()
    name = row["name"] if row else f"lead #{reminder['lead_id']}"
    event_id = _create_event(reminder["lead_id"], {
        "calendar_id": "primary",
        "summary": f"Follow up: {name}" + (f" — {reminder['note']}" if reminder.get("note") else ""),
        "description": "Scheduled by Open House Intelligence.",
        "start_datetime": reminder["due_ts"],
        "event_duration_minutes": 15,
        "timezone": _tz(),
    })
    if event_id:
        with get_conn() as conn:
            conn.execute("UPDATE reminders SET gcal_event_id = ? WHERE id = ?",
                         (event_id, reminder["id"]))
```

- [ ] **Step 4: Wire the three routers (hook call AFTER the `with` block)**

`backend/app/routers/calendar.py` — add import `from ..integrations import hooks`; in `book_appointment`, change the tail so the hook runs after commit:

```python
        appt = dict(conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (cur.lastrowid,)).fetchone())
        audit(conn, "agent", "book_appointment", body.model_dump(),
              {"appointment_id": appt["id"]}, body.lead_id)
    hooks.on_tour_booked(lead, appt)
    return appt
```

`backend/app/routers/leads.py` — add import `from ..integrations import hooks`; in `create_lead`, change the tail:

```python
        lead = fetch_lead(conn, lead_id)
        audit(conn, "agent", "create_lead", {"source": body.source}, {"lead_id": lead_id}, lead_id)
    hooks.on_lead_created(lead)
    return lead
```

`backend/app/routers/misc.py` — add import `from ..integrations import hooks`; in `create_reminder`:

```python
        cur = conn.execute(
            "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
            (body.lead_id, body.due_ts, body.note),
        )
        audit(conn, "agent", "schedule_followup", body.model_dump(), {}, body.lead_id)
        reminder = dict(conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)).fetchone())
    hooks.on_reminder_created(reminder)
    return reminder
```

Note: `create_lead` is `async def`; the hook makes a short blocking HTTP call in live mode (≤15 s worst case). Acceptable for this single-user app — do not add threading.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS (all — including earlier tasks' tests, which now see extra simulated audit rows; they assert membership, not exact lists)

- [ ] **Step 6: Commit**

```bash
git add backend/app/integrations/hooks.py backend/app/routers/ backend/tests/test_hooks.py
git commit -m "Integrations: outbound hooks — tour/lead/reminder to GCal, intro Gmail draft"
```

---

### Task 5: Dashboard — send via Gmail, compose box, status chip, booking confirmation

**Files:**
- Modify: `dashboard/src/api.ts:155` (api object) and after the `ChatSession` interface
- Create: `dashboard/src/components/EmailCompose.tsx`
- Modify: `dashboard/src/pages/Lead.tsx` (draft card, compose, timeline icon)
- Modify: `dashboard/src/App.tsx:89-99` (header chip)
- Modify: `dashboard/src/components/BookingCard.tsx` (booked confirmation line)

**Interfaces:**
- Consumes: `POST /api/email/send`, `GET /api/integrations/status` (Task 3 shapes).
- Produces: `api.sendEmail(lead_id, subject, body)`, `api.integrationsStatus()`, `IntegrationsStatus` interface, `<EmailCompose leadId email onSent />`.

- [ ] **Step 1: Extend `api.ts`**

After the `ChatSession` interface add:

```ts
export interface IntegrationsStatus {
  mode: 'off' | 'live'
  gmail: boolean
  gcal: boolean
}
```

In the `api` object (before the closing brace) add:

```ts
  sendEmail: (lead_id: number, subject: string, body: string) =>
    req<{ sent: boolean; simulated: boolean }>('/email/send', {
      method: 'POST',
      body: JSON.stringify({ lead_id, subject, body }),
    }),
  integrationsStatus: () => req<IntegrationsStatus>('/integrations/status'),
```

- [ ] **Step 2: Create `EmailCompose.tsx`**

`dashboard/src/components/EmailCompose.tsx`:

```tsx
import { useState } from 'react'
import { api } from '../api'
import { toast } from './Toast'

// Free-form email to the lead via POST /email/send (Gmail when live, simulated
// when integrations are off). The backend logs the event + closed loop.
export function EmailCompose({ leadId, email, onSent }:
  { leadId: number; email: string; onSent: () => void }) {
  const [open, setOpen] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async () => {
    if (!subject.trim() || !body.trim()) return
    setBusy(true)
    try {
      const res = await api.sendEmail(leadId, subject, body)
      toast(res.simulated ? '✓ Simulated send — integrations off' : `✓ Emailed ${email}`)
      setSubject('')
      setBody('')
      setOpen(false)
      onSent()
    } catch {
      toast('✗ Send failed — try again')
    } finally {
      setBusy(false)
    }
  }

  if (!open)
    return (
      <button onClick={() => setOpen(true)} className="text-sm text-accent hover:underline">
        ✉ Compose email
      </button>
    )
  return (
    <div className="rounded-lg border border-tile bg-surface p-3 space-y-2">
      <div className="text-xs text-sub/80">Email {email}</div>
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="Subject"
        className="w-full rounded-md bg-tile border border-line px-2 py-1.5 text-sm"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Message…"
        rows={4}
        className="w-full rounded-md bg-tile border border-line px-2 py-1.5 text-sm"
      />
      <div className="flex gap-2">
        <button
          onClick={send}
          disabled={busy || !subject.trim() || !body.trim()}
          className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-xs font-medium"
        >
          {busy ? 'Sending…' : 'Send via Gmail'}
        </button>
        <button onClick={() => setOpen(false)} className="text-xs text-sub hover:text-ink px-2">
          Cancel
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wire the Lead page**

In `dashboard/src/pages/Lead.tsx`:

a) Add imports: `import { EmailCompose } from '../components/EmailCompose'` and add `useState` for subject + sending near the other state hooks:

```tsx
  const [subject, setSubject] = useState('')
  const [sending, setSending] = useState(false)
```

b) In `process()`, after `setDraft(res.followup_draft)` prefill the subject:

```tsx
      setSubject(lead?.area ? `Your home search in ${lead.area}` : 'Following up on your home search')
```

c) Add the send handler next to `markSent`:

```tsx
  const sendViaGmail = async () => {
    if (!draft || !lead) return
    setSending(true)
    try {
      const res = await api.sendEmail(leadId, subject || 'Following up', draft)
      toast(res.simulated ? '✓ Simulated send — integrations off' : `✓ Emailed ${lead.email}`)
      setDraft(null)
      load()
    } catch {
      toast('✗ Send failed — try again')
    } finally {
      setSending(false)
    }
  }
```

d) In the draft card (the `{draft && (...)}` block), above the buttons row add a subject input, and add the Gmail button before "Mark as sent ✓":

```tsx
          {lead.email && (
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject"
              className="w-full rounded-md bg-tile border border-line px-2 py-1.5 text-xs"
            />
          )}
          <div className="flex gap-2 pt-1">
            {lead.email && (
              <button
                onClick={sendViaGmail}
                disabled={sending}
                className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-xs font-medium"
              >
                {sending ? 'Sending…' : 'Send via Gmail ✉'}
              </button>
            )}
            <button
              onClick={markSent}
              className="rounded-md border border-line hover:border-accent/60 px-3 py-1.5 text-xs"
            >
              Mark as sent ✓
            </button>
            <span className="text-xs text-sub/80 self-center">
              logs + schedules a 3-day reply check
            </span>
          </div>
```

(“Mark as sent” drops from filled to outline style — the real send is now the primary action.)

e) Below `<NoteBox …/>` (inside the `lead.status !== 'closed'` fragment) add:

```tsx
          {lead.email && <EmailCompose leadId={leadId} email={lead.email} onSent={load} />}
```

f) In the activity timeline map, give email events an icon — replace the type span with:

```tsx
              <span className="text-sub/80 shrink-0 w-24 uppercase text-xs pt-0.5">
                {e.type === 'email' ? '✉ email' : e.type}
              </span>
```

- [ ] **Step 4: Header chip in `App.tsx`**

Add `IntegrationsStatus` to the `./api` import. Add next to the `Tile` component at the bottom of the file:

```tsx
function IntegrationsChip() {
  const [st, setSt] = useState<IntegrationsStatus | null>(null)
  useEffect(() => {
    api.integrationsStatus().then(setSt).catch(() => {})
  }, [])
  if (!st) return null
  const live = st.mode === 'live'
  return (
    <span
      title={live ? 'Gmail + Google Calendar connected (Composio)' : 'Google integrations off — demo-safe mode'}
      className={`rounded-full border px-2.5 py-1 text-[10px] ${
        live ? 'border-accent/40 text-accent' : 'border-line text-sub/70'
      }`}
    >
      {live ? '● Google live' : '○ Google off'}
    </span>
  )
}
```

Render it in the header before `<LocalBadge metrics={metrics} />`:

```tsx
          <IntegrationsChip />
          <LocalBadge metrics={metrics} />
```

- [ ] **Step 5: BookingCard confirmation line**

In `dashboard/src/components/BookingCard.tsx`: import `IntegrationsStatus` and add state fetched once:

```tsx
  const [intg, setIntg] = useState<IntegrationsStatus | null>(null)
  useEffect(() => {
    api.integrationsStatus().then(setIntg).catch(() => {})
  }, [])
```

(add `IntegrationsStatus` to the existing `../api` import). In the booked-confirmation JSX (the block rendered when `booked` is set, next to the existing `.ics` link) add:

```tsx
          {intg?.mode === 'live' && (
            <div className="text-xs text-accent">✓ Added to Google Calendar</div>
          )}
```

- [ ] **Step 6: Type-check and build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: both succeed with no errors

- [ ] **Step 7: Off-mode browser walkthrough**

Run `bash scripts/dev.sh`; on a lead profile: Analyze & draft → subject prefilled → "Send via Gmail ✉" → toast "Simulated send"; timeline shows ✉ email event; status chip shows "○ Google off"; book a tour → no GCal line (off mode), `.ics` still present; compose box sends simulated. Agent activity page shows the `(simulated)` rows.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/
git commit -m "Integrations: send-via-Gmail UI, compose box, status chip, GCal booking confirmation"
```

---

### Task 6: Phase 2 — Gmail reply poller

**Files:**
- Create: `backend/app/integrations/poller.py`
- Modify: `backend/app/main.py:27-29` (startup)
- Test: `backend/tests/test_poller.py`

**Interfaces:**
- Consumes: `composio_client.execute/is_live`, `db.audit/get_conn`.
- Produces:
  - `poller.check_replies() -> int` — one synchronous pass; new replies logged as `email` events with `[gmail:<id>]` marker; reply-check reminders marked done; idempotent.
  - `poller.poll_loop()` — async loop, every 300 s, live mode only, started from FastAPI startup.
  - `poller.busy_blocks(date_str) -> list[tuple[str, str]]` — used by Task 7 (defined here to share the module-level cache).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_poller.py`:

```python
from conftest import make_lead
from app.integrations import poller


def _fake_fetch(messages):
    def fake_execute(slug, arguments):
        assert slug == "GMAIL_FETCH_EMAILS"
        return {"response_data": {"messages": messages}}
    return fake_execute


def test_reply_logged_and_reminder_done(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})  # creates reply-check reminder

    monkeypatch.setattr("app.integrations.poller.cc.execute", _fake_fetch([{
        "messageId": "reply-1",
        "sender": "Test Lead <lead@example.com>",
        "preview": {"body": "Sounds great, let's talk Tuesday"},
    }]))
    assert poller.check_replies() == 1

    profile = client.get(f"/api/leads/{lead['id']}").json()
    reply_evs = [e for e in profile["events"] if "[gmail:reply-1]" in e["content"]]
    assert len(reply_evs) == 1 and reply_evs[0]["type"] == "email"
    reminders = client.get("/api/reminders").json()   # only done=0 returned
    assert not any(r["note"].startswith("Check for a reply") for r in reminders)


def test_reply_dedupe_second_pass_noop(client, monkeypatch):
    lead = make_lead(client)
    client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    fake = _fake_fetch([{"messageId": "reply-2",
                         "sender": "lead@example.com",
                         "preview": {"body": "hi"}}])
    monkeypatch.setattr("app.integrations.poller.cc.execute", fake)
    assert poller.check_replies() == 1
    assert poller.check_replies() == 0   # marker dedupe


def test_no_active_leads_no_call(client, monkeypatch):
    def explode(slug, arguments):
        raise AssertionError("should not be called")
    monkeypatch.setattr("app.integrations.poller.cc.execute", explode)
    assert poller.check_replies() == 0   # fresh DB: no contacted leads with email
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_poller.py -v`
Expected: FAIL — `ImportError` (no poller module)

- [ ] **Step 3: Implement the poller**

`backend/app/integrations/poller.py`:

```python
"""Phase 2 inbound: Gmail reply detection + GCal busy cache (live mode only).

Reply idempotence: every logged reply embeds "[gmail:<message_id>]" in the
event content; a message id seen once is never logged again."""
import asyncio
import time
from datetime import datetime

from ..db import audit, get_conn
from . import composio_client as cc

POLL_SECONDS = 300


def check_replies() -> int:
    with get_conn() as conn:
        leads = [dict(r) for r in conn.execute(
            "SELECT id, name, email FROM leads WHERE email IS NOT NULL "
            "AND email != '' AND status IN ('contacted','meeting_booked')")]
    if not leads:
        return 0
    by_email = {l["email"].lower(): l for l in leads}
    query = "from:(" + " OR ".join(by_email) + ") newer_than:7d"
    data = cc.execute("GMAIL_FETCH_EMAILS", {"query": query, "max_results": 20})
    inner = data.get("response_data") or data
    messages = inner.get("messages") or []
    new = 0
    with get_conn() as conn:
        for m in messages:
            msg_id = m.get("messageId") or m.get("id")
            sender = (m.get("sender") or m.get("from") or "").lower()
            lead = next((l for e, l in by_email.items() if e in sender), None)
            if not msg_id or not lead:
                continue
            if conn.execute("SELECT 1 FROM events WHERE lead_id = ? AND content LIKE ?",
                            (lead["id"], f"%[gmail:{msg_id}]%")).fetchone():
                continue
            preview = m.get("preview") or {}
            snippet = (preview.get("body") if isinstance(preview, dict) else None) \
                or m.get("snippet") or "(no preview)"
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead["id"], "email",
                 f"Reply received: {snippet[:300]} [gmail:{msg_id}]"))
            conn.execute(
                "UPDATE reminders SET done = 1 WHERE lead_id = ? AND done = 0 "
                "AND note LIKE 'Check for a reply%'", (lead["id"],))
            audit(conn, "cron", "gmail_reply_detected", {"lead_id": lead["id"]},
                  {"message_id": msg_id}, lead["id"])
            new += 1
    return new


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(check_replies)
        except Exception:
            pass  # transient failure — next tick retries
        await asyncio.sleep(POLL_SECONDS)


# ---- GCal busy cache (used by the availability endpoint, Task 7) ----
_busy_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def _local_naive(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat()


def busy_blocks(date_str: str) -> list[tuple[str, str]]:
    """Busy (start,end) local-naive ISO pairs for 'primary' on date_str.
    5-min cache; [] on any failure so availability degrades to local-only."""
    cached = _busy_cache.get(date_str)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        data = cc.execute("GOOGLECALENDAR_FREE_BUSY_QUERY", {
            "time_min": f"{date_str}T00:00:00",
            "time_max": f"{date_str}T23:59:59",
            "timezone": "America/Los_Angeles",
            "items": [{"id": "primary"}],
        })
        inner = data.get("response_data") or data
        cal = (inner.get("calendars") or {}).get("primary", {})
        blocks = [(_local_naive(b["start"]), _local_naive(b["end"]))
                  for b in cal.get("busy", [])]
    except (cc.IntegrationError, KeyError, ValueError):
        blocks = []
    _busy_cache[date_str] = (time.time(), blocks)
    return blocks
```

- [ ] **Step 4: Start the loop on startup (live mode only)**

In `backend/app/main.py`, extend the startup handler:

```python
@app.on_event("startup")
def startup():
    init_db()
    from .integrations import composio_client as cc
    if cc.is_live():
        import asyncio
        from .integrations.poller import poll_loop
        asyncio.get_event_loop().create_task(poll_loop())
```

(Off mode — and therefore every test — never starts the loop.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/integrations/poller.py backend/app/main.py backend/tests/test_poller.py
git commit -m "Integrations: Gmail reply poller with marker dedupe + GCal busy cache"
```

---

### Task 7: Phase 2 — GCal busy filter on availability + inbox replied badge

**Files:**
- Modify: `backend/app/routers/calendar.py:18-23` (`availability`)
- Modify: `dashboard/src/pages/Lead.tsx` (replied badge in the profile header)
- Test: `backend/tests/test_busy_filter.py`

**Interfaces:**
- Consumes: `poller.busy_blocks(date)` (Task 6), `composio_client.is_live`.
- Produces: `GET /api/availability` excludes slots overlapping GCal busy when live; inbox rows show `✉ replied` when the lead's latest `email` event starts with `Reply received:`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_busy_filter.py`:

```python
def test_busy_blocks_filter_slots(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    # seeded availability comes from schema+seed; use a Tuesday
    date = "2026-07-28"
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    baseline = client.get(f"/api/availability?date={date}").json()
    if not baseline:                      # no availability windows seeded on this day
        import pytest
        pytest.skip("no availability windows for test date")
    first = baseline[0]

    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setattr("app.routers.calendar.integrations_busy",
                        lambda d: [(first["start_ts"], first["end_ts"])])
    filtered = client.get(f"/api/availability?date={date}").json()
    assert first not in filtered
    assert len(filtered) == len(baseline) - 1
```

Note: the test relies on `availability` importing the helper as `integrations_busy` (see Step 3) so it can be monkeypatched without touching the cache.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_busy_filter.py -v`
Expected: FAIL — `AttributeError: app.routers.calendar has no attribute 'integrations_busy'`

- [ ] **Step 3: Implement the filter**

In `backend/app/routers/calendar.py` add imports and the filter:

```python
from ..integrations import composio_client as cc
from ..integrations.poller import busy_blocks as integrations_busy
```

```python
@router.get("/availability")
def availability(date: str):
    with get_conn() as conn:
        slots = calendar.free_slots(conn, date)
        if cc.is_live():
            busy = integrations_busy(date)
            slots = [s for s in slots if not any(
                s["start_ts"] < b_end and s["end_ts"] > b_start
                for b_start, b_end in busy)]
        audit(conn, "agent", "check_availability", {"date": date}, {"free": len(slots)})
    return slots
```

(ISO local-naive strings compare correctly lexicographically; `busy_blocks` already normalizes Google's timestamps to local-naive.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests -v`
Expected: PASS (all)

- [ ] **Step 5: Replied badge on the lead profile**

The leads list (`GET /leads`) doesn't include events, so an inbox-row badge would need an N+1 fetch — the badge therefore lives on the profile, where the events are already loaded (deliberate deviation from the spec's "inbox badge"; same information, zero extra requests). In `dashboard/src/pages/Lead.tsx`, next to the persona chip in the header, add:

```tsx
            {lead.events.some((e) => e.type === 'email' && e.content.startsWith('Reply received:')) && (
              <span className="rounded-full border border-accent/40 text-accent px-2 py-0.5 text-xs">
                ✉ replied
              </span>
            )}
```

(Do not modify Inbox.tsx after all — remove it from this task's file list when executing; the profile badge covers the spec's "replied" flag without an N+1.)

- [ ] **Step 6: Type-check and build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/calendar.py backend/tests/test_busy_filter.py dashboard/src/pages/Lead.tsx
git commit -m "Integrations: GCal busy filters availability; replied badge on profile"
```

---

### Task 8: Docs, env plumbing, GB10 deploy notes, group-chat message

**Files:**
- Modify: `README.md` (env table), `docs/GB10-SETUP.md`, `TODO.md`
- No code changes.

**Interfaces:** none — documentation of everything above.

- [ ] **Step 1: README env table**

Add rows to the Environment variables table:

```markdown
| `INTEGRATIONS_MODE` | `off` | `off` (demo-safe, simulated) or `live` (real Gmail + Google Calendar via Composio) |
| `COMPOSIO_API_KEY` | — | Composio key (Johaan's; from `~/.composio/user_data.json` `api_key`) |
| `COMPOSIO_USER_ID` | `default` | Composio connected-account user id |
| `GCAL_TIMEZONE` | `America/Los_Angeles` | Timezone for created calendar events |
```

- [ ] **Step 2: GB10 setup doc**

Append to `docs/GB10-SETUP.md`:

```markdown
## Google integrations (Gmail + Calendar)

The app itself calls Composio — nothing to install on the GB10 beyond env vars.
In the same env file `scripts/gb10.sh` loads, set:

    INTEGRATIONS_MODE=live
    COMPOSIO_API_KEY=<from ~/.composio/user_data.json on Johaan's Mac>

Leave `INTEGRATIONS_MODE` unset (= `off`) for the stage demo: every Google
action is then simulated, audited, and requires no network. The header chip
shows "● Google live" / "○ Google off" so you always know which mode you're in.
The reply poller (every 5 min) and GCal busy-filtering only run in live mode.
K's agent needs no changes: its existing REST tool calls (create_lead,
book_appointment, schedule_followup) fire the Google hooks automatically.
```

- [ ] **Step 3: TODO.md**

Add under "✅ Shipped (dashboard side...)" a new section:

```markdown
## ✅ Shipped — Google integrations (2026-07-26, Johaan)

- [x] `INTEGRATIONS_MODE=off|live` adapter (Composio): tours/new-leads/reminders → Google Calendar, one-click "Send via Gmail" + free compose with the closed-loop, intro-draft on new lead, reply poller (marks reply-check reminders done, ✉ replied badge), GCal busy-time filtering of availability. Off mode simulates everything (demo-safe, zero network). Spec: docs/superpowers/specs/2026-07-26-gcal-gmail-integration-design.md
```

- [ ] **Step 4: Post the group-chat note (Johaan sends this — paste ready)**

```
Additive changes heads-up (no contract breakage):
1. Two new columns, auto-migrated: appointments.gcal_event_id, reminders.gcal_event_id
2. Two new endpoints: POST /api/email/send {lead_id,subject,body} and GET /api/integrations/status
3. New event-type convention: events.type='email' for sent mail + replies (no CHECK constraint, same trick as the offer events); reply dedupe marker [gmail:<id>] in content
4. Behavior: when INTEGRATIONS_MODE=live on the GB10, create_lead / book_appointment / schedule_followup ALSO create Google Calendar events (+ a Gmail intro draft for leads with email). Off by default; off mode simulates + audits only. K: zero agent changes needed — your existing tool calls trigger it.
```

- [ ] **Step 5: Full verification sweep**

```bash
cd backend && ../.venv/bin/python -m pytest tests -v        # all green
cd ../dashboard && npx tsc --noEmit && npm run build         # clean
cd .. && bash scripts/dev.sh                                 # off-mode walkthrough per Task 5 Step 7
```

Live smoke test (Johaan's machine, when ready): `INTEGRATIONS_MODE=live COMPOSIO_API_KEY=... bash scripts/dev.sh` → create a lead with your own email → calendar call-block + Gmail draft appear in Google; book a tour → GCal event; Send via Gmail → real email arrives; reply to it → ✉ replied badge + reminder cleared within 5 min; add a busy block in GCal → overlapping slot disappears from the booking card.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/GB10-SETUP.md TODO.md
git commit -m "Integrations: docs, GB10 env setup, group-chat note"
```
