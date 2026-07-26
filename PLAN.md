# Open House Intelligence — Hackathon Plan

**A local-first real estate sales agent that manages leads from first contact through booked appointment. All inference runs locally on the Dell Pro Max GB10 — client PII never leaves the machine.**

## Core demo flow

1. New lead enters from a form, text, or note.
2. Duplicate information is merged into one SQLite profile.
3. Agent extracts budget, timeline, preferences, intent, and missing details.
4. Agent scores and prioritizes the lead.
5. Agent drafts the best follow-up.
6. Agent checks the realtor's availability.
7. Agent books a meeting and updates the CRM.
8. A scheduled job later finds neglected leads and recommends action.
9. Dashboard shows leads, appointments, activity, and conversion insights.

## Architecture

```text
React dashboard
      ↓
FastAPI / Node backend
      ↓
SQLite
      ↓
OpenClaw tools
      ↓
Local model on GB10
      ↓
Cron/scheduled agent checks
```

**Rule of thumb:** calculations and database operations stay deterministic. The model handles language — extraction, intent classification, summarization, explaining priority, drafting messages, choosing tools.

### OpenClaw tools

```text
create_lead
update_lead
find_duplicate_leads
get_lead_context
score_lead
draft_followup
check_availability
book_appointment
find_neglected_leads
generate_dashboard_insights
```

---

## The Contract (freeze in the first 30 minutes)

The SQLite schema and the tool function signatures are the contract between all three workstreams. Agree on them together, write them down, freeze them. After that, nobody blocks anybody.

Tables: `leads`, `notes` / `events` (activity timeline), `appointments`, `availability`, `audit_log`.

Lead statuses: `new → contacted → meeting_booked` (+ `neglected` flag from the scheduled check).

---

## Ownership

Ownership ≠ only that person works on it. It means one person is responsible for getting it finished.

### K — Agent & Local Inference (the "brain")

Owns everything between the model and the tools.

- Get the model serving on the GB10 (Ollama/vLLM, Qwen-class instruct model) — **hour one, before anything else**: validate JSON extraction on the Sarah Chen notes
- Extraction pipeline: raw note → structured JSON (budget, timeline, preferences, intent, missing fields) with schema validation + one retry
- Lead scoring (deterministic formula; LLM only writes the *explanation*)
- Follow-up drafting
- Duplicate-merge confirmation (rule-based matching lives in Toby's layer; the model only confirms borderline cases)
- Agent loop / OpenClaw tool wiring — with a fixed-pipeline fallback (extract → score → draft as three explicit calls) ready if free-form tool-calling is flaky
- Neglected-lead check logic + the scheduled job

**Mid-hackathon deliverable:** `process_lead(raw_text) → structured lead + score + draft`, callable by anyone, working offline.

### Toby — Backend, Data & Calendar (the "spine")

Owns the contract itself and everything deterministic.

- SQLite schema — **written and frozen in the first 30 minutes**
- Backend (FastAPI or Node): lead CRUD, endpoints the dashboard reads, endpoints the agent tools call
- Duplicate detection: exact phone/email match, fuzzy name as tiebreaker; borderline cases handed to K's model
- Calendar adapter (`calendar/local_calendar.py` first, `google_calendar.py` only if time permits):
  - Availability + appointments in SQLite
  - Conflict detection
  - `.ics` export
- `book_appointment` + CRM status transitions
- **Seed script: ~15 realistic leads** with varied ages/statuses (dashboard must never demo empty)
- **"Simulate 3 days passing" demo endpoint** — backdates timestamps so the neglected-lead check fires live on stage

**Mid-hackathon deliverable:** running API with seeded data that K and Johaan can hit.

### Johaan — Dashboard, Integration & Demo (the "face")

Owns whether the whole thing demos.

- React dashboard against Toby's API. Build order by demo value:
  1. **Prioritized lead inbox** (score badges, status)
  2. **Lead profile** with merged sources + activity timeline
  3. **Agent audit log** — live stream of tool calls ("called `score_lead` → 87, called `check_availability` → Tue 6pm free"). This is the wow factor; do not leave it for last
  4. Follow-up draft panel + "book it" flow
  5. Metric tiles + the **"Inference: Local on Dell Pro Max GB10 / Cloud LLM requests: 0"** badge
  6. Calendar view — simplest thing that shows the Tuesday 6pm slot
- Visible "agent thinking" state so local-inference latency reads as *working*, not *broken*
- **Integration owner:** first to feel it when K's output shape and Toby's API drift — call it out and force fixes early
- Demo script, Sarah Chen walkthrough, **backup video the night before (non-negotiable)**, pitch framing (local-first = client PII never leaves the office)

**Mid-hackathon deliverable:** inbox + profile rendering seeded leads from the real API. Mock nothing — real data from Toby's seed script.

---

## Dashboard layout

**Top metrics:** total active leads · high-priority leads · follow-ups due · appointments booked · avg response time · local AI status

**Main sections:** prioritized lead inbox · consolidated lead profile · activity timeline · recommended next action · follow-up draft · calendar availability · agent audit log

---

## Priorities

### Must finish

- SQLite lead storage; create/update/view leads
- AI extraction from unstructured notes
- Duplicate lead consolidation (rule-based; cut to a seeded demo if flaky by mid-hackathon)
- Lead scoring and priority ranking
- Follow-up drafting
- Availability lookup (hardcoded weekly table + conflict detection — keep it trivial)
- Appointment creation
- Scheduled neglected-lead check (with the simulate-time button)
- Clear dashboard
- Complete local demo on the GB10

### Stretch (only after core flows work end to end)

- Web scraping for local housing topics → daily blog-post ideas → auto-generated drafts (separate **Marketing Ideas** tab; use 2–3 saved article pages so it works offline)
- Advanced analytics and forecasting
- Real email or SMS sending
- Google Calendar connection

---

## Milestones

**Hour 1, in parallel:**
- K: model serving on GB10, JSON extraction validated on sample notes
- Toby: schema frozen + seed data loaded
- Johaan: app shell hitting a stub endpoint

**Flow 1 (first):**
```text
New lead note → stored in SQLite → analyzed locally → appears on dashboard → prioritized → follow-up drafted
```

**Flow 2 (second):**
```text
Lead requests a meeting → agent checks availability → appointment booked → CRM status updated
```

Do not build further features until both flows work end to end on the GB10.

**Feature freeze at ~75% of time elapsed.** The last quarter is integration, demo rehearsal, and the backup video. Three people polishing one flow beats three people finishing three features.

---

## Demo story — Sarah Chen

One lead, end to end:

1. Open-house form: Bellevue, $1.1M budget.
2. Agent note: relocating, needs a home within six weeks.
3. Text: her husband wants to tour.
4. Agent merges everything into one profile.
5. Marks her high priority (and explains why).
6. Drafts a personalized follow-up.
7. Checks availability.
8. Books Tuesday at 6:00 p.m.
9. "Simulate 3 days passing" → scheduled check surfaces a *different* neglected lead, unprompted.

Shows CRM, intelligence, autonomy, persistence, scheduling, and business value in one uncluttered story.

## Demo insurance

- Database pre-seeded with ~15 realistic leads
- Backup video recorded the night before
- Simulate-time button so the scheduled job fires on command
- Fixed-pipeline fallback if free-form tool-calling misbehaves
- Local calendar adapter — demo survives with no internet

## Pitch note

"Cloud LLM requests: 0" is the badge; the thesis is *why it matters*: realtors handle PII — budgets, financial situations, relocation reasons. A local-first agent means none of that leaves the office. That turns the GB10 from a hardware constraint into the reason the product makes sense.
