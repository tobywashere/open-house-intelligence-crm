# Live Integration Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard-first OpenClaw CRM integrations recoverable and resistant to ordinary duplicate delivery while preserving the existing architecture and approval flow.

**Architecture:** Keep the single durable `hook_outbox` row per approved CRM operation. Pass stable per-step delivery keys into the existing hooks, use stored provider IDs or audited step keys as retry checkpoints, stop after five failures, and expose narrow operator-only inspection and retry endpoints. Independently harden Gmail polling, proposal deduplication, async health reads, agent route boundaries, and OpenClaw token configuration.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, stdlib threading and hashing, httpx/TestClient, pytest, React 18, TypeScript, Vite, OpenClaw CLI.

## Global Constraints

- Keep the existing durable hook outbox and at-least-once delivery model.
- Keep the dedicated `openhouse-crm` agent and `crm-db-operations` skill.
- Preserve human approval for CRM writes and post-approval integrations.
- Stop failed live integration jobs after exactly five claimed attempts.
- Do not claim exactly-once provider delivery.
- Do not add a dependency or replace the chat relay, SQLite database, approval system, or OpenClaw skill architecture.
- Dashboard chat is the primary acceptance path; Discord uses the same dedicated agent and safety policy.
- Never put the plaintext `OHI_API_TOKEN` value in an OpenClaw subprocess argument or rendered setup output.
- Do not claim live Gmail, Calendar, Discord, or Mac mini verification without a real configured run.

---

## File Map

- `backend/app/integrations/hooks.py`: provider calls and stable step checkpoints.
- `backend/app/integrations/hook_outbox.py`: durable claims, retry exhaustion, operator listing, and manual requeue.
- `backend/app/integrations/poller.py`: Gmail message idempotency.
- `backend/app/routers/leads.py`: source-event proposal dedupe keys.
- `backend/app/routers/misc.py`: operator endpoints, route attribution guards, and nonblocking CRM health reads.
- `backend/app/db.py`: additive `exhausted` status migration.
- `scripts/setup_openclaw.py`: SecretRef capability check and configuration.
- `backend/tests/test_hooks.py`: hook ordering and stored-ID replay tests.
- `backend/tests/test_hook_outbox.py`: dispatcher retry, exhaustion, and manual recovery tests.
- `backend/tests/test_migration.py`: legacy outbox schema preservation.
- `backend/tests/test_poller.py`: duplicate Gmail and changed-proposal behavior.
- `backend/tests/test_openclaw.py`: health-check threadpool behavior.
- `backend/tests/test_audit_coverage.py`: agent rejection for user-only routes.
- `backend/tests/test_setup_openclaw.py`: SecretRef and secret-leak prevention.
- `docs/CONTRACT.md`: API and delivery contract.
- `docs/LOCAL-AI.md`: operator setup, recovery, and live acceptance steps.
- `README.md`: short beginner-facing reliability and verification summary.

### Task 1: Make Provider Steps Resume Without Ordinary Duplicates

**Files:**
- Modify: `backend/app/integrations/hooks.py:60-253`
- Modify: `backend/app/integrations/hook_outbox.py:203-230`
- Test: `backend/tests/test_hooks.py`
- Test: `backend/tests/test_hook_outbox.py`
- Test: `backend/tests/test_hook_outbox_worker.py`

**Interfaces:**
- Consumes: `hook_outbox.idempotency_key: str` and existing `HookOutcome` values.
- Produces: hook keyword parameter `delivery_key: str | None = None`; exact step keys `<delivery_key>:calendar` and `<delivery_key>:gmail-draft`.

- [ ] **Step 1: Write failing hook-order and replay tests**

Add focused tests that drive hooks directly with `INTEGRATIONS_MODE=live`:

```python
def test_lead_calendar_failure_does_not_create_gmail_draft(client, monkeypatch):
    from app.integrations import hooks
    from app.integrations.composio_client import IntegrationError

    lead = make_lead(client, email="buyer@example.com")
    calls = []

    def execute(slug, args):
        calls.append(slug)
        raise IntegrationError("calendar unavailable")

    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(hooks.cc, "execute", execute)

    outcome = hooks.on_lead_created(
        lead, delivery_key="pending-change:41"
    )

    assert outcome is hooks.HookOutcome.FAILED
    assert calls == ["GOOGLECALENDAR_CREATE_EVENT"]
```

```python
def test_existing_appointment_event_id_skips_provider(client, monkeypatch):
    from app.integrations import hooks

    lead = make_lead(client)
    appointment = {
        "id": 91,
        "lead_id": lead["id"],
        "start_ts": "2026-08-20T10:00:00",
        "end_ts": "2026-08-20T10:45:00",
        "location": "Kirkland",
        "gcal_event_id": "already-created",
    }
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *_: (_ for _ in ()).throw(AssertionError("provider replayed")),
    )

    assert hooks.on_tour_booked(
        lead, appointment, delivery_key="pending-change:42"
    ) is hooks.HookOutcome.LIVE_DELIVERED
```

Add this reminder counterpart:

```python
def test_existing_reminder_event_id_skips_provider(client, monkeypatch):
    from app.integrations import hooks

    lead = make_lead(client)
    reminder = {
        "id": 92,
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Call",
        "gcal_event_id": "already-created",
    }
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setattr(
        hooks.cc,
        "execute",
        lambda *_: (_ for _ in ()).throw(AssertionError("provider replayed")),
    )

    assert hooks.on_reminder_created(
        reminder, delivery_key="pending-change:43"
    ) is hooks.HookOutcome.LIVE_DELIVERED
```

Extend the existing lead Calendar-success/Gmail-failure retry test with:

```python
assert calls.count("GOOGLECALENDAR_CREATE_EVENT") == 1
assert calls.count("GMAIL_CREATE_EMAIL_DRAFT") == 2
```

- [ ] **Step 2: Run the focused tests and confirm the failures**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_hooks.py backend/tests/test_hook_outbox.py::test_lead_created_retry_resumes_after_calendar_when_gmail_failed -q
```

Expected: FAIL because Gmail runs after Calendar failure, stored provider IDs do
not short-circuit hooks, and dispatcher hooks do not receive a stable delivery
key.

- [ ] **Step 3: Add exact step checkpoints and sequential failure handling**

In `hooks.py`, add a successful-audit lookup that parses JSON safely:

```python
def _step_was_delivered(tool: str, lead_id: int, step_key: str | None) -> bool:
    if not step_key:
        return False
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT input FROM audit_log WHERE lead_id = ? AND tool = ? "
            "ORDER BY id DESC",
            (lead_id, tool),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["input"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("delivery_step") == step_key:
            return True
    return False
```

Extend `_create_event` with `delivery_step: str | None = None`. Keep provider
arguments unchanged, but add `delivery_step` only to the audit input when it is
present. Add `delivery_key` to the three public hooks and their implementation
helpers.

For appointments and reminders, return `LIVE_DELIVERED` before `_create_event`
when live mode is active and the object has a nonblank `gcal_event_id`.

For lead creation:

```python
calendar_step = f"{delivery_key}:calendar" if delivery_key else None
gmail_step = f"{delivery_key}:gmail-draft" if delivery_key else None

if live and _step_was_delivered(
    "gcal_create_event", lead["id"], calendar_step
):
    event_outcome = HookOutcome.LIVE_DELIVERED
else:
    event_outcome, _ = _create_event(
        lead["id"], event_args, live=live, delivery_step=calendar_step
    )
if event_outcome is HookOutcome.FAILED:
    return HookOutcome.FAILED
```

Before Gmail execution, return `LIVE_DELIVERED` when the exact Gmail step already
has a successful `gmail_create_draft` audit. Add the stable step key only to Gmail
audit input, never to Composio arguments.

In `_invoke_hook`, call:

```python
outcome = hook(*args, delivery_key=row["idempotency_key"])
```

Update hook test doubles in `test_hook_outbox.py` and
`test_hook_outbox_worker.py` to accept `**_kwargs` without changing their asserted
business arguments.

- [ ] **Step 4: Run focused hook and dispatcher tests**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_hooks.py backend/tests/test_hook_outbox.py backend/tests/test_hook_outbox_worker.py -q
```

Expected: PASS with one Calendar call across the partial-failure retry and no
Gmail call after a Calendar failure.

- [ ] **Step 5: Commit the provider replay fix**

```bash
git add backend/app/integrations/hooks.py backend/app/integrations/hook_outbox.py backend/tests/test_hooks.py backend/tests/test_hook_outbox.py backend/tests/test_hook_outbox_worker.py
git commit -m "fix: resume external hooks from completed steps"
```

### Task 2: Exhaust Failed Jobs After Five Attempts and Allow Manual Recovery

**Files:**
- Modify: `backend/app/db.py:9-202`
- Modify: `backend/app/integrations/hook_outbox.py:19-460`
- Modify: `backend/app/routers/misc.py`
- Test: `backend/tests/test_migration.py`
- Test: `backend/tests/test_hook_outbox.py`

**Interfaces:**
- Produces: `DEFAULT_MAX_ATTEMPTS = 5`.
- Produces: `list_hook_outbox(status: str) -> list[dict]`.
- Produces: `retry_exhausted(outbox_id: int) -> dict`.
- Produces: `GET /api/integrations/outbox?status=exhausted` and `POST /api/integrations/outbox/{id}/retry`.

- [ ] **Step 1: Write failing migration and five-attempt tests**

Extend the legacy schema fixture in `test_migration.py` so `init_db()` must
preserve a failed row and accept both terminal values:

```python
conn.execute("UPDATE hook_outbox SET status = 'cancelled' WHERE id = 7")
conn.execute("UPDATE hook_outbox SET status = 'exhausted' WHERE id = 7")
assert conn.execute(
    "SELECT status FROM hook_outbox WHERE id = 7"
).fetchone()[0] == "exhausted"
```

Add a dispatcher test:

```python
def test_failed_hook_exhausts_on_fifth_attempt(client, monkeypatch):
    from app.integrations import hook_outbox, hooks

    _configure_live(monkeypatch)
    outbox_id = _seed_live_reminder_rows(client, 1)[0]
    monkeypatch.setattr(
        hooks,
        "on_reminder_created",
        lambda _reminder, **_kwargs: hooks.HookOutcome.FAILED,
    )

    for attempt in range(5):
        hook_outbox.dispatch_hook(outbox_id, retry_base_seconds=0)
        row = _outbox_rows()[0]
        assert row["attempts"] == attempt + 1

    assert row["status"] == "exhausted"
    assert row["next_attempt_at"] is None
    assert hook_outbox.drain_hook_outbox(retry_base_seconds=0) == 0
```

- [ ] **Step 2: Run the focused tests and confirm the failures**

```bash
../../.venv/bin/python -m pytest backend/tests/test_migration.py backend/tests/test_hook_outbox.py::test_failed_hook_exhausts_on_fifth_attempt -q
```

Expected: FAIL because the schema rejects `exhausted` and failures remain
eligible forever.

- [ ] **Step 3: Add the additive status migration and terminal state**

Update `HOOK_OUTBOX_DDL` to allow:

```sql
('pending','processing','failed','delivered','cancelled','exhausted')
```

Rename the status migration helper to `_migrate_hook_outbox_terminal_statuses`
and rebuild when either `cancelled` or `exhausted` is absent from the stored table
SQL. Keep the existing column-intersection copy so old rows and future additive
columns survive.

Add `DEFAULT_MAX_ATTEMPTS = 5`, thread `max_attempts` through `dispatch_hook`,
`drain_hook_outbox`, `_worker_loop`, and `start_worker`, and use a minimum of one.
After a failure:

```python
if row["attempts"] >= max(int(max_attempts), 1):
    _mark_exhausted(row, exc)
else:
    _mark_failed(
        row,
        exc,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
    )
```

`_mark_exhausted` must clear claim and scheduling fields, retain the sanitized
error, and atomically audit `status: exhausted`, `retryable: false`, and the
attempt count.

- [ ] **Step 4: Write failing operator listing and retry tests**

Add `test_operator_can_list_and_retry_exhausted_hook` with this setup and
assertion sequence:

```python
outbox_id = _seed_live_reminder_rows(client, 1)[0]
with get_conn() as conn:
    conn.execute(
        "UPDATE hook_outbox SET status = 'exhausted', attempts = 5, "
        "last_error = 'provider unavailable' WHERE id = ?",
        (outbox_id,),
    )

listed = client.get("/api/integrations/outbox?status=exhausted")
assert listed.status_code == 200
assert [row["id"] for row in listed.json()] == [outbox_id]

retried = client.post(f"/api/integrations/outbox/{outbox_id}/retry")
assert retried.status_code == 200
assert retried.json()["status"] == "pending"
assert retried.json()["attempts"] == 0

agent_retry = client.post(
    f"/api/integrations/outbox/{outbox_id}/retry",
    headers={"X-Actor": "agent"},
)
assert agent_retry.status_code == 403
```

Add this parameterized state test:

```python
@pytest.mark.parametrize(
    "status", ["pending", "failed", "processing", "delivered", "cancelled"]
)
def test_only_exhausted_hook_can_be_retried(client, status):
    outbox_id = _seed_live_reminder_rows(client, 1)[0]
    with get_conn() as conn:
        conn.execute(
            "UPDATE hook_outbox SET status = ? WHERE id = ?", (status, outbox_id)
        )
    assert client.post(
        f"/api/integrations/outbox/{outbox_id}/retry"
    ).status_code == 409
    assert client.post(
        "/api/integrations/outbox/999999/retry"
    ).status_code == 404
```

- [ ] **Step 5: Implement operator-only listing and requeue**

In `hook_outbox.py`, list only these fields:

```python
OUTBOX_OPERATOR_FIELDS = (
    "id", "pending_change_id", "hook_type", "object_id", "lead_id",
    "delivery_mode", "status", "attempts", "last_error",
    "next_attempt_at", "created_at", "updated_at", "delivered_at",
)
```

Implement `list_hook_outbox` with an allowlist of all schema statuses. Implement
`retry_exhausted` in one transaction, require current status `exhausted`, reset
attempts and delivery scheduling, audit `retry_hook_delivery`, then call
`wake_worker()` after commit.

In `misc.py`, use `Literal` to validate the GET status. Reject
`is_agent_write(request)` with HTTP 403 before calling `retry_exhausted`. Map a
missing row to 404 and a non-exhausted row to 409.

- [ ] **Step 6: Run migration, outbox, and API tests**

```bash
../../.venv/bin/python -m pytest backend/tests/test_migration.py backend/tests/test_hook_outbox.py backend/tests/test_hook_outbox_worker.py -q
```

Expected: PASS. Exhausted rows are absent from dispatcher selection until a user
explicitly requeues one.

- [ ] **Step 7: Commit retry exhaustion and recovery**

```bash
git add backend/app/db.py backend/app/integrations/hook_outbox.py backend/app/routers/misc.py backend/tests/test_migration.py backend/tests/test_hook_outbox.py backend/tests/test_hook_outbox_worker.py
git commit -m "fix: cap integration retries and add recovery"
```

### Task 3: Make Gmail Polling and Proposal Deduplication Idempotent

**Files:**
- Modify: `backend/app/integrations/poller.py:105-154`
- Modify: `backend/app/routers/leads.py:637-657`
- Test: `backend/tests/test_poller.py`

**Interfaces:**
- Consumes: `_log_reply(...) -> tuple[int, bool]`.
- Produces: source-event dedupe key `lead-process:<lead_id>:event:<event_id>:candidate:<sha256>`.

- [ ] **Step 1: Change the duplicate-reply test to require one processing call**

Update `test_reply_processing_uses_exact_inserted_or_existing_event_id`:

```python
assert processed == [(lead["id"], first_event_id)]
```

Add `test_changed_payload_for_same_event_can_be_proposed_after_denial`:

```python
def test_changed_payload_for_same_event_can_be_proposed_after_denial(
    client, monkeypatch
):
    lead = make_lead(client, area="Bellevue")
    event = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "email", "content": "My preferred area changed."},
    ).json()
    areas = iter(["Redmond", "Kirkland", "Kirkland"])

    class ChangingDriver(_ReviewableExtractionDriver):
        async def extract(self, raw_text):
            return {"area": next(areas)}

    monkeypatch.setattr(leads_router, "get_driver", lambda: ChangingDriver())

    first = asyncio.run(
        leads_router.process_lead(lead["id"], source_event_id=event["id"])
    )
    client.post(
        f"/api/pending-changes/{first['pending_change']['id']}/deny",
        json={"reason": "incorrect extraction"},
    )
    second = asyncio.run(
        leads_router.process_lead(lead["id"], source_event_id=event["id"])
    )
    third = asyncio.run(
        leads_router.process_lead(lead["id"], source_event_id=event["id"])
    )

    assert second["pending_change"]["id"] == third["pending_change"]["id"]
    assert len(client.get("/api/pending-changes?status=denied").json()) == 1
    pending = client.get("/api/pending-changes").json()
    assert len(pending) == 1
    assert pending[0]["payload"]["area"] == "Kirkland"
```

- [ ] **Step 2: Run the poller tests and confirm the duplicate-processing failure**

```bash
../../.venv/bin/python -m pytest backend/tests/test_poller.py::test_reply_processing_uses_exact_inserted_or_existing_event_id backend/tests/test_poller.py -q
```

Expected: the first focused test FAILS because `_log_reply` invokes
`process_lead` for an existing event.

- [ ] **Step 3: Skip processing for an existing Gmail event**

In `_log_reply`, return immediately after the database transaction when
`inserted` is false:

```python
if not inserted:
    return event_id, False
```

Keep exact source-event processing and fallback auditing unchanged for a newly
inserted event.

- [ ] **Step 4: Include the normalized proposal digest in source-event keys**

Serialize `proposed_fields` once after filtering unchanged values:

```python
serialized_proposal = json.dumps(
    proposed_fields, sort_keys=True, separators=(",", ":"), default=str
).encode()
proposal_digest = hashlib.sha256(serialized_proposal).hexdigest()
```

For source events use:

```python
dedupe_key = (
    f"lead-process:{lead_id}:event:{source_event['id']}:"
    f"candidate:{proposal_digest}"
)
```

Use the same `proposal_digest` for the no-source candidate key. This keeps exact
replays suppressed while allowing a changed reviewed payload to be proposed.

- [ ] **Step 5: Run the complete poller and pending-change tests**

```bash
../../.venv/bin/python -m pytest backend/tests/test_poller.py backend/tests/test_pending_changes.py -q
```

Expected: PASS with one processing call per Gmail message and stable exact-payload
deduplication under retries and concurrent processing.

- [ ] **Step 6: Commit poller and proposal idempotency**

```bash
git add backend/app/integrations/poller.py backend/app/routers/leads.py backend/tests/test_poller.py
git commit -m "fix: deduplicate inbox processing by proposal"
```

### Task 4: Keep CRM Health Nonblocking and Close User-Only Agent Routes

**Files:**
- Modify: `backend/app/routers/misc.py:90-145,226-266`
- Test: `backend/tests/test_openclaw.py`
- Test: `backend/tests/test_audit_coverage.py`

**Interfaces:**
- Produces: `_latest_audit_id() -> int` and `_crm_probe_inputs_after(before: int) -> list[str]`.
- Preserves: `POST /api/health/crm-check` response schema and nonce verification.

- [ ] **Step 1: Write failing threadpool and route-attribution tests**

Add a CRM check test that records the thread running the async driver and the
threads running both database helpers:

```python
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
    assert all(thread_id != loop_threads[0] for thread_id in db_threads)
```

Add:

```python
def test_agent_cannot_complete_reminder_or_advance_demo_time(client):
    lead = _mk(client)
    reminder = client.post("/api/reminders", json={
        "lead_id": lead["id"],
        "due_ts": "2026-08-20T09:00:00",
        "note": "Call",
    }).json()

    assert client.patch(
        f"/api/reminders/{reminder['id']}", headers=AGENT
    ).status_code == 403
    assert client.post(
        "/api/demo/advance-time", json={"days": 0}, headers=AGENT
    ).status_code == 403
```

- [ ] **Step 2: Run focused tests and confirm failures**

```bash
../../.venv/bin/python -m pytest backend/tests/test_openclaw.py::test_crm_check_moves_database_reads_off_event_loop backend/tests/test_audit_coverage.py::test_agent_cannot_complete_reminder_or_advance_demo_time -q
```

Expected: FAIL because the helpers do not exist and user-only routes accept agent
attribution.

- [ ] **Step 3: Move health database reads to FastAPI's threadpool**

Import `run_in_threadpool` from `fastapi.concurrency`. Extract both database reads
into synchronous helpers that open and close their own `get_conn()` blocks. Use:

```python
before = await run_in_threadpool(_latest_audit_id)
```

and after the OpenClaw request:

```python
inputs = await run_in_threadpool(_crm_probe_inputs_after, before)
found = any(_audit_has_nonce(raw_input, probe_nonce) for raw_input in inputs)
```

Do not hold a database connection while awaiting `request_crm_capability` or
`driver.probe`.

- [ ] **Step 4: Reject agent attribution before user-only mutations**

Accept `request: Request` in `complete_reminder` and `advance_time`. At the start
of each handler add:

```python
if is_agent_write(request):
    raise HTTPException(403, "This action requires a dashboard user")
```

Keep existing user audit labels and direct dashboard behavior unchanged.

- [ ] **Step 5: Run health, route, and API-token tests**

```bash
../../.venv/bin/python -m pytest backend/tests/test_openclaw.py backend/tests/test_audit_coverage.py backend/tests/test_validation.py backend/tests/test_api_token.py -q
```

Expected: PASS with unchanged health response data and 403 responses for both
agent-tagged mutations.

- [ ] **Step 6: Commit health and route safety**

```bash
git add backend/app/routers/misc.py backend/tests/test_openclaw.py backend/tests/test_audit_coverage.py
git commit -m "fix: keep health checks nonblocking and user-only"
```

### Task 5: Configure the CRM API Token Through an OpenClaw SecretRef

**Files:**
- Modify: `scripts/setup_openclaw.py:629-826`
- Modify: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Consumes: `OHI_API_TOKEN` from the setup process environment.
- Produces: OpenClaw `config set` arguments containing only provider `default`, source `env`, and secret ID `OHI_API_TOKEN`.

- [ ] **Step 1: Write failing argument-vector and preflight tests**

Replace the plaintext expectation in
`test_setup_defaults_load_repo_env_port_and_token_without_leaking` with:

```python
token_call = token_calls[0]
assert "secret-from-dotenv" not in token_call
assert token_call[-6:] == [
    "--ref-provider", "default",
    "--ref-source", "env",
    "--ref-id", "OHI_API_TOKEN",
]
```

Update the default `FakeCLI` help text to advertise `--ref-provider`,
`--ref-source`, and `--ref-id`. Then add:

```python
def test_token_setup_requires_secretref_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")
    cli = FakeCLI({
        ("openclaw", "config", "set", "--help"): CommandResult(
            0, "Options:\n  --strict-json VALUE", ""
        )
    })

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert "environment SecretRef" in result.render()
    assert "must-not-leak" not in result.render()
```

- [ ] **Step 2: Run setup tests and confirm plaintext failure**

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -q
```

Expected: FAIL because the current action contains the actual token as a JSON
argument and preflight does not require SecretRef support.

- [ ] **Step 3: Make SecretRef support conditional and fail closed**

When a token is present, extend the `config set` preflight requirements with:

```python
("--ref-provider", "--ref-source", "--ref-id")
```

Use a specific conflict message:

```text
This OpenClaw version cannot configure an environment SecretRef for OHI_API_TOKEN. Upgrade OpenClaw or configure that SecretRef manually; setup will not store the token as plaintext.
```

Keep `--strict-json` required for the non-secret configuration actions.

- [ ] **Step 4: Replace the plaintext token action**

Build the token action exactly as:

```python
Action(
    "Configure the CRM API token from environment SecretRef OHI_API_TOKEN",
    [
        "openclaw",
        "config",
        "set",
        'skills.entries["crm-db-operations"].env.OHI_API_TOKEN',
        "--ref-provider",
        "default",
        "--ref-source",
        "env",
        "--ref-id",
        "OHI_API_TOKEN",
    ],
)
```

Keep defensive redaction for OpenClaw stderr and other setup output even though
the token no longer belongs in the command.

- [ ] **Step 5: Run setup, launcher, and skill-token tests**

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py backend/tests/test_launchers.py backend/tests/test_skill_tools.py backend/tests/test_api_token.py -q
```

Expected: PASS. No captured `OpenClawCLI` call or rendered action contains the
token value.

- [ ] **Step 6: Commit SecretRef setup**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "fix: configure OpenClaw token by secret reference"
```

### Task 6: Document Recovery and Honest Live Verification

**Files:**
- Modify: `docs/CONTRACT.md:80-92,152-173`
- Modify: `docs/LOCAL-AI.md`
- Modify: `README.md`

**Interfaces:**
- Documents: exhausted-job listing and retry endpoints.
- Documents: environment SecretRef resolution and target-hardware acceptance.

- [ ] **Step 1: Update the durable delivery contract**

State that the worker retries transient failures with exponential backoff, marks
the fifth failed attempt `exhausted`, and never selects exhausted or cancelled
rows. Document both operator endpoints and their 403, 404, and 409 behavior.

Keep this exact limitation visible:

```text
Delivery is at least once. Step checkpoints prevent ordinary partial-failure replays, but a provider action can still be duplicated if the process stops after the provider accepts it and before the local checkpoint commits.
```

- [ ] **Step 2: Add beginner recovery commands to the local guide**

Add a short section titled `If Gmail or Calendar stops retrying` with commands
that use `CRM_API_URL`, plus `X-API-Token` only when authentication is enabled:

```bash
curl "$CRM_API_URL/integrations/outbox?status=exhausted"
curl -X POST "$CRM_API_URL/integrations/outbox/JOB_ID/retry"
```

Explain in plain language that `JOB_ID` is the `id` from the first command and
that retrying can repeat a provider action if the earlier provider response was
lost during a crash.

- [ ] **Step 3: Document SecretRef setup and acceptance order**

Explain that `OHI_API_TOKEN` must be present in the OpenClaw gateway process
environment because OpenClaw resolves the saved SecretRef at runtime. Tell the
operator to rerun setup after upgrading OpenClaw if the SecretRef options are
missing.

Keep live acceptance in this order:

1. Dashboard CRM capability check reports `CRM verified`.
2. Dashboard chat lists real CRM leads.
3. Dashboard chat proposes a disposable write that appears in Pending approvals.
4. Discord lists the same real CRM leads through the dedicated agent.
5. Discord proposes a disposable write that appears in the same Pending approvals.
6. With live integrations enabled, approve one disposable booking and verify one
   Calendar event.
7. Approve one disposable lead with an email and verify one call block plus one
   Gmail draft.

Leave every target-hardware checkbox unchecked until a person records the OS,
memory, OpenClaw version, model/provider, date, and outcome.

- [ ] **Step 4: Keep the README short and nontechnical**

Add only a brief troubleshooting pointer. Explain that most failed Gmail or
Calendar jobs retry automatically five times, then stop for review, and link to
the local guide for the two recovery commands. Do not duplicate advanced API or
OpenClaw configuration details in the README.

- [ ] **Step 5: Review documentation claims and formatting**

Run:

```bash
rg -n "exactly.once|retry forever|plaintext|exhausted|SecretRef|CRM verified" README.md docs/CONTRACT.md docs/LOCAL-AI.md
git diff --check
```

Expected: no exactly-once claim, no indefinite-retry claim, no plaintext-token
instruction, and clear exhausted, SecretRef, and CRM verification language.

- [ ] **Step 6: Commit operator documentation**

```bash
git add README.md docs/CONTRACT.md docs/LOCAL-AI.md
git commit -m "docs: explain integration recovery and verification"
```

### Task 7: Run Full Verification and Review the Branch

**Files:**
- Verify: all files changed by Tasks 1 through 6.

**Interfaces:**
- Consumes: complete implementation and documentation commits.
- Produces: review evidence suitable for a pull request; no live-hardware claim.

- [ ] **Step 1: Run the complete backend suite**

```bash
../../.venv/bin/python -m pytest backend/tests -q
```

Expected: all tests pass. Record the exact pass count and warnings in the pull
request summary.

- [ ] **Step 2: Build the dashboard production bundle**

```bash
npm --prefix dashboard run build
```

Expected: TypeScript compilation and Vite production build exit successfully.

- [ ] **Step 3: Exercise setup without mutating OpenClaw**

```bash
../../.venv/bin/python scripts/setup_openclaw.py --help
../../.venv/bin/python scripts/setup_openclaw.py --dry-run
```

Expected: help exits successfully. Dry-run either displays only redacted planned
actions or gives the existing clear `openclaw not found` result on a development
host without OpenClaw.

- [ ] **Step 4: Inspect changed code and repository state**

```bash
git diff origin/main...HEAD --check
git status --short
git log --oneline origin/main..HEAD
```

Expected: no whitespace errors, no unexpected generated files, and a focused
commit history containing the design, implementation, tests, and docs.

- [ ] **Step 5: Review the high-risk invariants manually**

Confirm from the diff that:

- Gmail is unreachable after a failed Calendar step;
- a successful step key never enters provider arguments;
- attempt five becomes exhausted and cannot be worker-selected;
- only a user can requeue an exhausted job;
- existing Gmail messages return before agent processing;
- health has no `get_conn()` block on the async event-loop path;
- the API token value cannot enter an OpenClaw argument vector;
- documentation does not claim real hardware or provider verification.

- [ ] **Step 6: Create the final verification commit only if formatting changed**

If verification requires a formatting-only correction, apply it, rerun the
affected command, then commit only that correction:

```bash
git status --short
git add -u
git commit -m "chore: finish reliability verification"
```

If no correction is required, leave the existing task commits unchanged.
