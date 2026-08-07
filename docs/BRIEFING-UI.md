# Executive Briefing — source and UI contract

The CRM is the factual source. The agent may add visibly labeled preparation
advice, but it cannot replace schedule or lead facts.

## Endpoints

### New endpoint pair

```
GET  /api/briefing?date=YYYY-MM-DD   → canonical CRM briefing JSON
POST /api/briefing                    → agent writes optional meeting advice
```

### Briefing shape

The GET response is a **verified CRM view**. Schedule blocks and factual lead
fields are rebuilt from current appointment/lead rows on every request.
Agent-posted content may supply only `assistant_advice`; it cannot override
names, times, scores, counts, or create a meeting that does not exist.

```json
{
  "date": "2026-07-26",
  "source": "crm",
  "greeting": "Good morning — 1 appointment scheduled today.",
  "generated_at": "2026-07-26T07:00:12",
  "schedule": [
    {"appointment_id": 12, "start": "10:00", "end": "10:45", "kind": "meeting", "title": "Meeting — Michael Rodriguez", "lead_id": 4}
  ],
  "meeting_briefs": [
    {
      "lead_id": 4, "name": "Michael Rodriguez", "area": "Medina",
      "persona": "Luxury Executive", "score": 98,
      "summary": "Intent: buy. Area: Medina. Budget: $2,000,000.",
      "assistant_advice": {
        "prepare": ["Luxury comps", "Waterfront inventory", "Privacy info"],
        "recommendation": "Lead with evidence, not opinions."
      }
    }
  ],
  "suggested_actions": [
    {"lead_id": 9, "name": "Ryan Miller", "channel": "call",
     "action": "Follow up with Ryan Miller",
     "reason": "Call Ryan about the documents due today.",
     "evidence": {"kind": "reminder", "id": 31}}
  ]
}
```

### Lead profile additions (nullable columns; UI hides when absent)

- `leads.persona` TEXT — e.g. "Growing Family", "Luxury Executive"
- `leads.relationship_summary` TEXT — the AI-written paragraph shown at the top of the profile

### Daily Memory (brain dump) — no new contract needed

The briefing page gets a "Daily memory" input that POSTs to the existing `/api/chat`
(session `memory`). The agent decides which lead each memory belongs to and stores it as an
event. Mock mode just acknowledges.

## Daily summary overlay (added 2026-07-26)

The daily summary is a **full-screen overlay** (auto-opens once per day, ✕/Esc to
close, "☀️ Daily summary" header button reopens any time). The market/insight
portion has no mock fallback:

```
GET/POST /api/summary?date=YYYY-MM-DD
{
  "date": "…", "generated_at": "…", "greeting": "…",
  "market_watch": [{ "title", "source", "takeaway", "url",    // required
                     "date", "summary", "geo",                 // required
                     "content_opportunity?" }],               //   (mirrors prompts/seattle-real-estate-news-reporter.md)
  "ai_insights":  [{ "title", "body" }]                       // model-written narrative
}
```

`ai_insights` here are the model's *written* observations — separate from the
deterministic insights engine (`docs/INSIGHTS.md`), which K's cron can use as input.
Every market item requires a valid HTTP(S) source URL, publication date,
summary, and geographic area. Missing or invalid summaries render an explicit
state rather than sample content.

## Dashboard information architecture

```
/            → Insights (home — the dashboard IS insights-first, links to profiles, chat at hand)
/briefing    → Morning briefing
/leads       → Prioritized inbox
/lead/:id    → Profile (breadcrumb + back)
/activity    → Agent audit stream
```

Chat rail stays global. When a new briefing exists, the agent drops a compact briefing card
into chat with a "View full briefing →" link; agent replies containing `[Name](lead:12)`
render as router links into profiles.

## Build phases (all Johaan, mock-first)

**A. Briefing page** — greeting hero (date, one-line day summary), vertical schedule timeline
with a "now" indicator (meeting blocks emerald, travel dim, buffers dashed), meeting-brief
cards (persona chip, score ring, summary, prepare checklist, recommendation callout,
"Open profile →"), suggested-actions rail with reason text, daily-memory input.
Powered by `GET /api/briefing`. With no appointments it renders an honest empty
schedule. There is no client-side mock or fabricated-meeting fallback.

**B. Profile page upgrade** — breadcrumb ("Briefing → Sarah Chen") + back button
(`navigate(-1)`), persona chip + relationship-summary hero when present, facts grid
(budget/timeline/area/intent — existing), conversation history (existing timeline),
appointments, notes, booking (existing).

**C. Chat integration** — briefing card message type, `lead:` link parsing, unchanged driver.

**D. Visual treatment** — persona → accent-color mapping, SVG score rings,
staggered card entrance animation, now-line that moves in real time, and
honest empty/error states.

## Current publishing responsibility

The backend and UI trust boundary is shipped. An OpenClaw schedule may run
`daily-command-center` to publish optional meeting advice. A separate
source-backed workflow must publish the market summary to `/api/summary`.
