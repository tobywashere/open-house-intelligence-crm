# Trustworthy Briefings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fabricated client-side briefings and summaries with typed, CRM-derived facts plus clearly labeled, referentially valid AI advice.

**Architecture:** FastAPI builds the factual briefing envelope from SQLite on every read. Stored agent payloads may add advice only after Pydantic and database-reference validation. The dashboard renders explicit loading, empty, and error states and never generates plausible fallback content.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite, pytest, React 18, TypeScript, Vite.

## Global Constraints

- Appointments, lead identity, scores, and counts come only from SQLite.
- AI content is labeled as advice/draft and cannot override CRM facts.
- Missing or failed report requests never fall back to mock content.
- Preserve compatibility with Bobo's concurrently updated `daily-command-center` skill.
- Existing GB10 and Mac mini installations migrate additively.

---

### Task 1: Typed report contracts

**Files:**
- Create: `backend/app/report_models.py`
- Modify: `backend/app/routers/reports.py`
- Create: `backend/tests/test_reports.py`

**Interfaces:**
- Produces: `BriefingPost`, `DailySummaryPost`, `validate_http_source(url: str)`.
- Preserves: `GET/POST /api/briefing`, `GET/POST /api/summary`, and `GET/POST /api/insights`.

- [ ] **Step 1: Write failing route-validation tests**

```python
def test_briefing_post_rejects_unknown_lead_reference(client):
    payload = {
        "date": "2026-07-28",
        "meeting_briefs": [{
            "lead_id": 999,
            "prepare": ["Review history"],
            "recommendation": "Ask about timing",
        }],
    }
    response = client.post("/api/briefing", json=payload)
    assert response.status_code == 422


def test_summary_post_requires_a_real_source_url(client):
    response = client.post("/api/summary", json={
        "date": "2026-07-28",
        "greeting": "Good morning",
        "market_watch": [{
            "title": "Rates changed",
            "source": "Unknown",
            "url": "not-a-url",
            "takeaway": "Check the source",
        }],
        "ai_insights": [],
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests and verify the current arbitrary-dict routes accept both payloads**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_reports.py -q
```

Expected: both tests fail because the current routes return 200.

- [ ] **Step 3: Add strict Pydantic contracts**

```python
class MeetingAdvice(BaseModel):
    lead_id: int = Field(gt=0)
    prepare: list[str] = Field(default_factory=list, max_length=10)
    recommendation: str | None = Field(default=None, max_length=2000)


class BriefingPost(BaseModel):
    date: date
    generated_at: datetime | None = None
    meeting_briefs: list[MeetingAdvice] = Field(default_factory=list)


class MarketWatchItem(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl
    takeaway: str = Field(min_length=1, max_length=3000)


class DailySummaryPost(BaseModel):
    date: date
    generated_at: datetime | None = None
    greeting: str = Field(default="", max_length=1000)
    market_watch: list[MarketWatchItem] = Field(default_factory=list, max_length=20)
    ai_insights: list[InsightItem] = Field(default_factory=list, max_length=20)
```

Use `model_dump(mode="json")` before storing JSON. In `post_briefing`, verify every
`lead_id` exists before `_upsert`; return HTTP 422 listing invalid IDs.

- [ ] **Step 4: Run the new report tests and the existing backend suite**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_reports.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/report_models.py backend/app/routers/reports.py backend/tests/test_reports.py
git commit -m "fix: validate generated report payloads"
```

### Task 2: CRM-derived factual briefing

**Files:**
- Create: `backend/app/briefing_service.py`
- Modify: `backend/app/routers/reports.py`
- Modify: `backend/tests/test_reports.py`
- Modify: `docs/BRIEFING-UI.md`

**Interfaces:**
- Consumes: `BriefingPost` from Task 1.
- Produces: `build_briefing(conn, date_key: str, advice: BriefingPost | None) -> dict`.

- [ ] **Step 1: Write failing tests for factual-only schedule construction**

```python
def test_briefing_never_promotes_a_lead_to_a_fake_meeting(client):
    lead = make_lead(client, name="No Appointment")
    response = client.get("/api/briefing?date=2026-07-28")
    assert response.status_code == 200
    body = response.json()
    assert body["schedule"] == []
    assert body["meeting_briefs"] == []
    assert str(lead["id"]) not in json.dumps(body)


def test_briefing_rehydrates_facts_from_real_appointment(client):
    lead = make_lead(client, name="Canonical Name", area="Bellevue", budget=900000)
    appointment = client.post("/api/appointments", json={
        "lead_id": lead["id"],
        "start_ts": "2026-07-28T17:00:00",
        "end_ts": "2026-07-28T17:45:00",
        "location": "Main Street",
    }).json()
    body = client.get("/api/briefing?date=2026-07-28").json()
    assert body["schedule"] == [{
        "appointment_id": appointment["id"],
        "start": "17:00",
        "end": "17:45",
        "kind": "meeting",
        "title": "Meeting — Canonical Name",
        "lead_id": lead["id"],
    }]
    assert body["meeting_briefs"][0]["name"] == "Canonical Name"
    assert body["meeting_briefs"][0]["area"] == "Bellevue"
    assert body["meeting_briefs"][0]["budget"] == 900000
```

- [ ] **Step 2: Run the tests and confirm GET currently returns 404 without stored content**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_reports.py -q
```

Expected: failures show 404 or agent-authored facts instead of canonical rows.

- [ ] **Step 3: Implement `build_briefing`**

The service queries appointments joined to leads for `date_key`, creates schedule
blocks from `start_ts`/`end_ts`, and creates deterministic meeting summaries from
stored `intent`, `area`, `budget`, `timeline`, and `preferences`. It queries due
reminders and neglected open leads for deterministic suggested actions.

```python
def build_briefing(conn, date_key: str, advice: BriefingPost | None) -> dict:
    appointments = conn.execute(
        "SELECT a.*, l.name, l.area, l.budget, l.timeline, l.intent, "
        "l.preferences, l.persona, l.score "
        "FROM appointments a JOIN leads l ON l.id = a.lead_id "
        "WHERE substr(a.start_ts, 1, 10) = ? ORDER BY a.start_ts",
        (date_key,),
    ).fetchall()
    advice_by_lead = {item.lead_id: item for item in advice.meeting_briefs} if advice else {}
    return {
        "date": date_key,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "crm",
        "greeting": factual_greeting(len(appointments)),
        "schedule": [schedule_block(row) for row in appointments],
        "meeting_briefs": [meeting_brief(row, advice_by_lead.get(row["lead_id"])) for row in appointments],
        "suggested_actions": deterministic_actions(conn),
    }
```

The GET route reads a stored advice payload if present, validates it, discards
invalid legacy advice, and always calls `build_briefing`.

- [ ] **Step 4: Verify canonical facts win over posted prose**

Add a test that POSTs a meeting brief with a false name/time/score, then asserts
GET returns the SQLite values while retaining only `prepare` and
`recommendation` under `assistant_advice`.

- [ ] **Step 5: Run focused and full backend tests**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_reports.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
```

- [ ] **Step 6: Update the contract and commit**

```bash
git add backend/app/briefing_service.py backend/app/routers/reports.py backend/tests/test_reports.py docs/BRIEFING-UI.md
git commit -m "fix: derive briefing facts from CRM records"
```

### Task 3: Remove dashboard briefing fabrication

**Files:**
- Modify: `dashboard/src/briefing.ts`
- Modify: `dashboard/src/components/BriefingSection.tsx`
- Modify: `dashboard/src/components/DailySummaryOverlay.tsx`

**Interfaces:**
- Consumes: canonical `GET /api/briefing` response from Task 2.
- Produces: `fetchBriefing(): Promise<Briefing>` with no fallback generator.

- [ ] **Step 1: Delete the fallback contract in a failing TypeScript build**

First change `Briefing` to require `source: 'crm'`, `appointment_id` on meeting
schedule blocks, and `assistant_advice` on meeting briefs. Change
`fetchBriefing` to return only `api.briefing`. Leave old callers temporarily
unchanged.

Run:

```bash
cd dashboard && npm run build
```

Expected: TypeScript failures identify every stale `mock`, `prepare`, or
`recommendation` access.

- [ ] **Step 2: Remove `mockBriefing`, `briefOf`, and fabrication-only imports**

The final data function is:

```typescript
export async function fetchBriefing(): Promise<Briefing> {
  return api.briefing<Briefing>(localDateKey())
}
```

`BriefingSection` keeps separate `loading`, `error`, and `briefing` state.
On error it renders “Briefing unavailable” and a Retry button. Empty schedule
copy says “No appointments are scheduled today.” Advice sections render only
when `assistant_advice` exists and are titled “AI suggestion.”

- [ ] **Step 3: Remove the summary overlay's claim that a fallback briefing works**

Replace any copy that promises an offline/mock briefing with factual wording:
“No daily summary has been published. Your CRM schedule and priorities are shown
above.”

- [ ] **Step 4: Build and manually inspect the no-appointment path**

```bash
cd dashboard && npm run build
```

Run the app against a temporary empty database and confirm the overlay contains
no invented person, appointment, source, or market headline.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/briefing.ts dashboard/src/components/BriefingSection.tsx dashboard/src/components/DailySummaryOverlay.tsx
git commit -m "fix: remove fabricated briefing fallbacks"
```

### Task 4: Honest daily summary loading and refresh

**Files:**
- Modify: `dashboard/src/summary.ts`
- Modify: `dashboard/src/components/DailySummaryOverlay.tsx`
- Modify: `backend/tests/test_reports.py`

**Interfaces:**
- Consumes: typed summary contract from Task 1.
- Produces: `fetchDailySummary(): Promise<DailySummary>` without mock fallback.

- [ ] **Step 1: Add backend tests for malformed legacy summary rows**

Insert invalid JSON and a payload with a non-HTTP source URL directly into
`daily_summary`; assert GET returns 422 with “stored daily summary is invalid”
rather than raw content or a server traceback.

- [ ] **Step 2: Run focused tests and observe the current raw/500 behavior**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_reports.py -q
```

- [ ] **Step 3: Validate stored summaries on read**

In `get_summary`, parse through `DailySummaryPost.model_validate`; translate
JSON decode or validation errors into HTTP 422. Return
`model_dump(mode="json")`.

- [ ] **Step 4: Remove `mockSummarySample` and add explicit UI states**

`fetchDailySummary` becomes:

```typescript
export async function fetchDailySummary(): Promise<DailySummary> {
  return api.summary<DailySummary>(localDateKey())
}
```

The overlay distinguishes:

- 404: “No daily summary has been published.”
- 422: “The published summary was rejected because its sources were invalid.”
- network/500: “Daily summary unavailable.”

The refresh button reports success only when `generated_at` changes. Timeout
copy remains a failure/pending message. Replace the unconditional “nothing
leaves this machine” footer with: “CRM inference is local. Optional market
research and Google integrations may send the necessary data to their configured
providers.”

- [ ] **Step 5: Run all verification and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
cd dashboard && npm run build
git add backend/app/routers/reports.py backend/tests/test_reports.py dashboard/src/summary.ts dashboard/src/components/DailySummaryOverlay.tsx
git commit -m "fix: make daily summary states truthful"
```
