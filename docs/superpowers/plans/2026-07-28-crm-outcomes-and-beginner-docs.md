# CRM Outcomes and Beginner Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track won/lost outcomes accurately, expose them through natural-language CRM actions and the dashboard, and make operator documentation truthful and approachable.

**Architecture:** Keep the forward-only `closed` lifecycle status for compatibility and add nullable `outcome` plus `close_reason`. A dedicated close endpoint enforces won/lost. Funnel conversion uses won records only. Documentation is operator-first and separates local inference from optional third-party data transmission.

**Tech Stack:** SQLite migrations, FastAPI/Pydantic, pytest, React/TypeScript, OpenClaw Python skill tools, Markdown.

## Global Constraints

- Existing closed rows remain readable with unknown outcome.
- New close actions require `won` or `lost`.
- Closed-lost records never count as successful conversion.
- Documentation uses concrete “natural-language CRM reads, writes, reminders, and booking” wording.
- README targets a nontechnical Mac mini operator first.

---

### Task 1: Additive won/lost data model

**Files:**
- Modify: `backend/schema.sql`
- Modify: `backend/app/db.py`
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/tests/test_migration.py`
- Modify: `backend/tests/test_lead_rules.py`

**Interfaces:**
- Adds lead fields: `outcome: "won" | "lost" | null`, `close_reason: str | null`.
- Adds: `POST /api/leads/{lead_id}/close`.

- [ ] **Step 1: Write failing migration and close tests**

```python
def test_existing_database_gains_outcome_columns(old_db):
    init_db()
    columns = table_columns("leads")
    assert {"outcome", "close_reason"} <= columns


def test_close_requires_explicit_won_or_lost(client):
    lead = make_lead(client)
    assert client.patch(f"/api/leads/{lead['id']}", json={"status": "closed"}).status_code == 400
    closed = client.post(f"/api/leads/{lead['id']}/close", json={
        "outcome": "won",
        "reason": "Offer accepted",
    }).json()
    assert closed["status"] == "closed"
    assert closed["outcome"] == "won"
    assert closed["close_reason"] == "Offer accepted"
```

- [ ] **Step 2: Run and verify missing columns/route failures**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_migration.py backend/tests/test_lead_rules.py -q
```

- [ ] **Step 3: Add schema and migration**

```sql
outcome TEXT CHECK (outcome IN ('won','lost')),
close_reason TEXT,
```

For existing databases:

```python
if "outcome" not in cols:
    conn.execute("ALTER TABLE leads ADD COLUMN outcome TEXT CHECK (outcome IN ('won','lost'))")
if "close_reason" not in cols:
    conn.execute("ALTER TABLE leads ADD COLUMN close_reason TEXT")
```

- [ ] **Step 4: Add dedicated close endpoint**

```python
class CloseLeadIn(BaseModel):
    outcome: Literal["won", "lost"]
    reason: str | None = Field(default=None, max_length=2000)
```

The route validates forward transition, atomically sets
`status='closed'`, `outcome`, `close_reason`, writes a status-change event, and
audits `close_lead`. Direct patch to `status=closed` returns 400 instructing the
caller to use the close endpoint.

- [ ] **Step 5: Run full tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
git add backend/schema.sql backend/app/db.py backend/app/routers/leads.py backend/tests/test_migration.py backend/tests/test_lead_rules.py
git commit -m "feat: track won and lost lead outcomes"
```

### Task 2: Natural-language action contract verification

**Files:**
- Modify: `skills/crm-db-operations/tools.py`
- Modify: `skills/crm-db-operations/SKILL.md`
- Modify: `backend/tests/test_skill_tools.py`
- Modify: `docs/CONTRACT.md`

**Interfaces:**
- Adds: `close_lead(lead_id: int, outcome: str, reason: str | None = None) -> dict`.

- [ ] **Step 1: Write real-boundary skill contract tests**

```python
def test_core_natural_language_action_contract(live_server, monkeypatch):
    monkeypatch.setenv("CRM_API_URL", live_server.base_url)
    lead = tools.create_lead("Taylor Brooks", source="note", area="Bellevue")
    updated = tools.update_lead(lead["id"], budget=900000)
    reminder = tools.schedule_followup(lead["id"], "2026-07-31T09:00:00", "Call Taylor")
    slots = tools.check_availability("2026-08-01")
    appointment = tools.book_appointment(
        lead["id"], slots[0]["start_ts"], slots[0]["end_ts"], "Bellevue"
    )
    assert updated["budget"] == 900000
    assert reminder["lead_id"] == lead["id"]
    assert appointment["lead_id"] == lead["id"]
    assert tools.get_lead(lead["id"])["status"] == "meeting_booked"


def test_close_lead_tool_records_won_outcome(live_server, monkeypatch):
    monkeypatch.setenv("CRM_API_URL", live_server.base_url)
    lead = tools.create_lead("Won Client", source="note")
    closed = tools.close_lead(lead["id"], "won", "Contract signed")
    assert closed["status"] == "closed"
    assert closed["outcome"] == "won"
    profile = tools.get_lead(closed["id"])
    assert profile["close_reason"] == "Contract signed"
```

The existing create/update/reminder/booking portion should pass on the merged
baseline; the close assertion must fail because `close_lead` is missing. This
characterizes the supported action contract before extending it.

- [ ] **Step 2: Run and verify only `close_lead` is missing**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_skill_tools.py -q
```

- [ ] **Step 3: Implement and document the tool**

```python
def close_lead(lead_id: int, outcome: str, reason: str | None = None) -> dict:
    if outcome not in {"won", "lost"}:
        raise ValueError("outcome must be 'won' or 'lost'")
    return _request("POST", f"/leads/{lead_id}/close", {
        "outcome": outcome,
        "reason": reason,
    })
```

Skill instructions require confirmation when the user's statement is ambiguous
and forbid inferring won versus lost.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_skill_tools.py -q
git add skills/crm-db-operations/tools.py skills/crm-db-operations/SKILL.md backend/tests/test_skill_tools.py docs/CONTRACT.md
git commit -m "feat: add natural-language close outcome action"
```

### Task 3: Outcome UI and accurate funnel conversion

**Files:**
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/pages/Lead.tsx`
- Modify: `dashboard/src/funnel.ts`
- Modify: `dashboard/src/pages/Dashboard.tsx`

**Interfaces:**
- Adds: `api.closeLead(id, outcome, reason)`.
- Updates: `Lead.outcome`, `Lead.close_reason`.

- [ ] **Step 1: Change funnel types to expose the missing behavior**

Add outcome fields to `Lead`, then replace the closed/won derivation with
`l.status === 'closed' && l.outcome === 'won'`. Run the build before updating
the UI.

```bash
cd dashboard && npm run build
```

Expected: stale callers or assumptions surface as TypeScript errors.

- [ ] **Step 2: Implement the close dialog**

For open leads, add “Close opportunity.” The dialog requires Won or Lost,
accepts an optional reason, summarizes the irreversible forward-only state, and
calls:

```typescript
closeLead: (id: number, outcome: 'won' | 'lost', reason?: string) =>
  req<Lead>(`/leads/${id}/close`, {
    method: 'POST',
    body: JSON.stringify({ outcome, reason }),
  })
```

Display the final outcome and reason on closed profiles.

- [ ] **Step 3: Update funnel calculations**

Use won-only leads for overall conversion and closed-won stage fallback. Keep
closed-lost leads available for loss counts but outside successful conversion.
Legacy closed/null-outcome rows display “Outcome unknown” and count in neither
won nor lost.

- [ ] **Step 4: Build and browser-smoke both outcomes**

```bash
cd dashboard && npm run build
```

Close one lead won and one lost; verify only the won record increases successful
conversion.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/api.ts dashboard/src/pages/Lead.tsx dashboard/src/funnel.ts dashboard/src/pages/Dashboard.tsx
git commit -m "feat: close opportunities as won or lost"
```

### Task 4: Operator-first README and consistent status

**Files:**
- Rewrite: `README.md`
- Modify: `.env.example`
- Modify: `docs/LOCAL-AI.md`
- Modify: `docs/GB10-SETUP.md`
- Modify: `docs/history/TODO.md`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Documents: demo, Mac mini local-AI, voice, natural-language actions, privacy, backup/update, and troubleshooting.

- [ ] **Step 1: Rewrite README information architecture**

Use this order:

1. plain-language product outcome;
2. honest feature matrix;
3. five-minute demo;
4. Mac mini local-AI path;
5. first-use walkthrough;
6. privacy and optional external services;
7. backup/update;
8. troubleshooting;
9. developer/advanced references.

Replace `git clone <this repo>` with the real repository URL. Do not claim that
PII never leaves the machine when Gmail, Calendar, Composio, or web research is
enabled.

- [ ] **Step 2: Reconcile feature status**

State that natural-language CRM reads/writes/reminders/booking work when the
OpenClaw Chat Completions endpoint and CRM skill are configured. Mark voice as
requiring local transcription. Remove “chat that acts parked.” Mark daily
summary as requiring the daily-command-center skill and a published,
source-valid summary.

- [ ] **Step 3: Update setup and contributor facts**

Make `.env` loading, loopback/Tailscale bind behavior, Mac mini 16 GB minimum,
OpenClaw endpoint enablement, and the current test command/count consistent.
Historical documents remain labeled historical rather than rewritten as live
status.

- [ ] **Step 4: Validate commands and links**

Run every local command in the quickstart that is safe on this host. Run:

```bash
rg -n "<this repo>|chat-that-acts|chat that acts|PII.*never leaves|108 tests" README.md docs CONTRIBUTING.md
bash -n scripts/dev.sh scripts/serve.sh scripts/load-env.sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
cd dashboard && npm run build
```

Expected: the text search has no misleading live-document matches; tests and
build pass.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example docs/LOCAL-AI.md docs/GB10-SETUP.md docs/history/TODO.md CONTRIBUTING.md
git commit -m "docs: make setup truthful and beginner friendly"
```
