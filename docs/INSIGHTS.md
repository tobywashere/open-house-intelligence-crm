# Insights — Plan (Johaan's workstream)

AI purpose #4 (insights) is now Johaan's. Definition of the lane:

- Computed **from DB information** (the CRM is the source of truth)
- **Presented in the dashboard** — never delivered via chat
- **Used as input for the morning summary** (K's 7am cron consumes them)

Design principle, same as the rest of the project: **the numbers are deterministic; the
model only ever narrates them.** An insight must be right even if no LLM ever touches it.

## Architecture

```
existing endpoints (leads, appointments, audit)
        │
dashboard/src/insights.ts        ← pure functions, fully testable, no new deps
  computeInsights(data) → Insights JSON
        │
        ├── /insights route            (full panel: cards + mini charts)
        ├── Briefing page section      (top 3 insights inline)
        └── POST /api/insights (Phase 2, write-through)
                    │
              Toby stores per-date  →  K's morning-summary cron reads it
```

The same `computeInsights` module powers the live dashboard AND produces the JSON the
morning summary consumes — one implementation, two consumers. Until Toby's endpoint
exists, the briefing mock already imports the module directly, so the "insights feed
the summary" story is demoable with zero dependencies.

## Insights JSON (v1 contract shape)

```json
{
  "date": "2026-07-26",
  "computed_at": "2026-07-26T19:30:00Z",
  "insights": [
    {
      "id": "funnel",
      "title": "Pipeline funnel",
      "severity": "info",            // info | good | warn
      "headline": "38% of contacted leads book a meeting",
      "detail": "8 new → 5 contacted → 2 booked. The new→contacted step is the bottleneck.",
      "data": { "new": 8, "contacted": 5, "meeting_booked": 2, "closed": 1 }
    }
  ]
}
```

`data` is per-insight chart fodder; `headline`/`detail` are deterministic template
sentences (LLM narration is an optional later garnish via K's scheduled path — never chat).

## Insight set v1 — computable TODAY from existing endpoints

| id | What it says | Source |
|---|---|---|
| `funnel` | Status counts + stage-to-stage conversion %, names the bottleneck stage | `GET /leads` |
| `source_effectiveness` | Booking rate per source (form/text/note/referral) — "referrals book 3× more than forms" | `GET /leads` |
| `pipeline_value` | Total budget by stage; **"warm value at risk"** = Σ budget of neglected high-score leads | `GET /leads` |
| `demand_map` | Lead count + avg budget per area — where demand actually is | `GET /leads` |
| `aging` | Avg days idle per stage; count sliding toward neglected | `GET /leads` |
| `booking_pattern` | Which weekdays/times tours get booked — "evenings win" | `GET /appointments` |
| `agent_activity` | Tool calls in the last 24h by type — what the AI actually did | `GET /audit` |

v2 (unlocked by Phase 2 history): trends ("booking rate up 12% vs yesterday"), and
conversion analytics once #10 outcome tracking lands (see TODO.md).

## Phases

**Phase 1 — engine + UI (zero dependencies, build now)**
1. `dashboard/src/insights.ts`: types + `computeInsights(leads, appointments, audit)`
2. `/insights` route: one card per insight — severity accent, headline, detail sentence,
   small inline-SVG chart (funnel bars, source rates, demand bars; no chart library)
3. Briefing page: "Pipeline insights" section renders the top 3 by severity
4. Mock briefing generator folds insight headlines into the greeting/summary —
   proving the "insights feed the morning summary" loop end to end in mock mode

**Phase 2 — persistence (one additive ask to Toby)**
- `insights` table (`date` PK, `payload` JSON) + `GET/POST /api/insights?date=`
- Dashboard write-through: after computing, POST today's payload (idempotent upsert)
- Each daily POST becomes a history row → trend insights come free in v2

**Phase 3 — morning summary integration (K, when the cron lands)**
- K's 7am cron does `GET /api/insights?date=yesterday` and feeds the JSON to Qwen as
  context for the briefing narrative — insights stay deterministic; the model narrates
- Optional: one-sentence LLM phrasing per insight via the same scheduled path

## Division of labor

- **Johaan**: everything in Phase 1, the write-through in Phase 2, all UI
- **Toby**: the additive `insights` table + two endpoints (Phase 2) — group-chat note, not a contract fight
- **K**: nothing until Phase 3 (cron reads one GET)
