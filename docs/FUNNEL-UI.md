# Funnel page — plan to match the concept mock (2026-07-26)

Goal: reproduce the "Sales funnel" concept screenshot almost fully, without breaking
the contract. Everything here is Johaan's except two conventions flagged for group chat.

## What the mock contains vs what data exists

| Mock element | Data status |
|---|---|
| 6-stage funnel (adds **Qualified**, **Offers Submitted**) | statuses are frozen at 4 — solved with derived stages + an event convention (below) |
| Per-stage conversion %, bottleneck, "needs action" flag | computable today from `GET /leads` |
| Time to close (31 days avg) | computable from `status_change` events of closed leads |
| Stage velocity (days per stage, "slow" tags) | computable from `status_change` events |
| KPI strip with ▲/▼ deltas vs last 7 days | needs history → the daily `insights` rows we already write; deltas hidden until ≥2 days exist |
| Lead source conversion (incl. Zillow/Website) | rates computable; new source names are free-text — seed/agent convention, no schema change |
| Top opportunities with deal values | value = `budget` today; offer amount once the offer convention lands |
| Next best actions with impact tags | deterministic templates from existing data |

## Two conventions to post in group chat (additive, no schema change)

1. **Derived "Qualified" stage**: a lead counts as Qualified when `status ∈ {contacted, meeting_booked, closed}` **and** `score ≥ 70`. Pure dashboard math — but the group should agree so K's agent language matches.
2. **Offer events**: an event with `type: "offer"` and content like `"Offer submitted: $1,250,000"` marks a lead as Offers Submitted (amount parsed from content). `events.type` has no CHECK constraint, so this is convention-only. Dashboard adds a "Log offer" quick action on the profile; K's agent can write the same event from chat. → Full 6-stage funnel with zero contract breakage. (If the team later does #10 properly, real statuses replace the derivation with no UI change.)

## Build phases (all dashboard)

**A. Funnel page core**
- Route `/funnel`; nav order becomes Insights · Funnel · Leads · Briefing · Agent activity
- `FunnelChart`: stacked CSS trapezoids (clip-path), single-hue gradient fills + glow,
  stage count centered, connector dots to a conversion rail (`75% · 36/48` chips),
  amber→alert "needs action" tag pinned to the worst transition
- Summary panel: overall conversion (closed/new %), biggest-bottleneck callout,
  **avg time-to-close** from closed leads' created_at→closed status_change delta
- Data layer `funnel.ts`: fetches leads + each lead's events (N+1 is fine at demo
  scale, cached per visit; optional additive ask to Toby later: bulk `GET /events`)

**B. Analytics card row** (4 cards under the funnel)
- Lead source conversion → closed/booked rate per source, bar + `n/N`
- Stage velocity → avg days per stage from `status_change` events, `slow` tag when
  > 1.5× the median stage
- Top opportunities → open leads ranked by score, avatar-initial chips, value =
  offer amount if an offer event exists else budget, est. close from timeline
- Demand by area → existing counts + ▲/▼ deltas when yesterday's insights row exists

**C. Global KPI strip upgrade**
- Six KPIs: Active leads · Qualified buyers · Tours scheduled · Offers submitted ·
  Closed deals · Close rate — replacing the current five-tile strip app-wide
- Deltas computed against `GET /api/insights?date=<yesterday>` (the write-through
  history); rendered only when history exists — no fabricated trends

**D. Next best actions row**
- Deterministic templates: "Follow up with N contacted leads (no response 3+ days)",
  "Book N more tours this week", "Qualify N warm leads", "Follow up on N negotiations"
- impact tag High/Medium by affected pipeline value; CTA buttons deep-link
  (View leads → /leads, View calendar → lead pages / booking)

**E. Demo helpers + chat garnish**
- ⚙ Demo controls: "Seed yesterday snapshot" — POSTs a slightly-perturbed insights
  payload dated yesterday so all deltas light up deterministically on stage
- Page-aware chat placeholder ("Ask anything about your funnel…" on /funnel)
- K (noted, not ours): a "who's closest to closing?" golden prompt that answers as a
  ranked list, like the mock

## Explicitly NOT in scope

- Real `qualified` / `offer_submitted` statuses (breaking — needs an issue/PR discussion, see #10; the "all three" rule this originally referenced is retired)
- Zillow/Website source ingestion (seed/agent content, not dashboard)
- Any backend endpoint changes

## Verification

Seeded DB: /funnel renders 4-real+2-derived stages with correct math; log an offer on
a profile → Offers Submitted stage and Top opportunities value update; Seed-yesterday
→ KPI deltas appear; time-to-close matches the one seeded closed lead; build + browser
walkthrough at 1512×796 with zero horizontal scroll and chat rail intact.
