# Executive Briefing — Dashboard Plan

> Product vision: the CRM is the memory, the AI is the trusted assistant. Every morning the
> realtor gets an AI-generated executive briefing: today's schedule, a brief for every meeting,
> and the highest-impact suggested actions. Johaan owns everything visual here; the agent
> (K) generates the content on a morning cron; the backend (Toby) stores and serves it.

## Contract addition (proposed — Toby & K sign off)

### New endpoint pair

```
GET  /api/briefing?date=YYYY-MM-DD   → briefing JSON below, or 404 if none generated yet
POST /api/briefing                    → agent cron writes the day's briefing (upsert by date)
```

### Briefing shape

```json
{
  "date": "2026-07-26",
  "greeting": "Good morning, Annie 👋 — 2 showings, 1 listing appointment, 3 follow-ups due.",
  "generated_at": "2026-07-26T07:00:12Z",
  "schedule": [
    {"start": "10:00", "end": "10:45", "kind": "meeting", "title": "Showing — Michael Rodriguez", "lead_id": 4},
    {"start": "10:45", "end": "11:05", "kind": "travel",  "title": "Travel to Bellevue"},
    {"start": "11:05", "end": "11:45", "kind": "buffer",  "title": "Buffer / follow-ups"}
  ],
  "meeting_briefs": [
    {
      "lead_id": 4, "name": "Michael Rodriguez", "area": "Medina",
      "persona": "Luxury Executive", "score": 98,
      "summary": "Cash buyer referred by Tom Wilson. Waterfront luxury. Analytical — wants data.",
      "prepare": ["Luxury comps", "Waterfront inventory", "Privacy info"],
      "recommendation": "Lead with evidence, not opinions."
    }
  ],
  "suggested_actions": [
    {"lead_id": 9, "name": "Ryan Miller", "channel": "text",
     "action": "Text Ryan Miller",
     "reason": "No contact for 6 days; mortgage-rate concerns; responds better to text."}
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
close, "☀️ Daily summary" header button reopens any time) — it is NOT delivered in
chat. Two portions, both agent-generated (K), UI already built and mock-backed:

```
GET/POST /api/summary?date=YYYY-MM-DD
{
  "date": "…", "generated_at": "…", "greeting": "…",
  "market_watch": [{ "title", "source", "takeaway",           // required
                     "url?", "date?", "summary?", "geo?",     // optional — all rendered
                     "content_opportunity?" }],               //   (mirrors prompts/seattle-real-estate-news-reporter.md)
  "ai_insights":  [{ "title", "body" }]                       // model-written narrative
}
```

`ai_insights` here are the model's *written* observations — separate from the
deterministic insights engine (`docs/INSIGHTS.md`), which K's cron can use as input.

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
Powered by `GET /api/briefing`; on 404, a **client-side mock generator** derives a plausible
briefing from `/leads` + `/appointments` so the page looks finished today and flips to real
agent content with zero UI changes.

**B. Profile page upgrade** — breadcrumb ("Briefing → Sarah Chen") + back button
(`navigate(-1)`), persona chip + relationship-summary hero when present, facts grid
(budget/timeline/area/intent — existing), conversation history (existing timeline),
appointments, notes, booking (existing).

**C. Chat integration** — briefing card message type, `lead:` link parsing, unchanged driver.

**D. Cool factor** — persona → accent-color mapping, SVG score rings, staggered card entrance
animation, now-line that moves in real time, empty state ("Your briefing generates at 7:00 —
here's yesterday's" / mock).

## What Johaan needs from teammates (non-blocking, mock covers until then)

- **Toby**: `briefing` table + GET/POST endpoints, `persona` + `relationship_summary` columns.
- **K**: 7am cron on the GB10 — Qwen 3.6 35B-A3B composes the briefing JSON from leads,
  events, and appointments, POSTs it to `/api/briefing`. (This is the "scheduled workflows"
  bullet from PLAN.md — same shape as the neglected-lead cron.)
