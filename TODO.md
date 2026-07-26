# Post-MVP TODO

Ownership after the job swap: **K = agent/inference (GB10, OpenClaw, Qwen 3.6 35B-A3B) · Toby = backend/SQLite/calendar · Johaan = dashboard/integration/demo + insights.**

The AI has exactly four jobs: DB read/write, scheduled summaries, timed reminders (K), and **insights — now Johaan's**: computed from DB data, presented in the dashboard (never chat), and used as input for the morning summary. Plan: [docs/INSIGHTS.md](docs/INSIGHTS.md). Chat-that-acts is parked with K.

## ✅ Shipped (dashboard side, no dependencies)

- [x] **#11 Client-safe export** — "Export ↓" on the profile downloads a markdown summary (no score/internal notes)
- [x] **#1 Closed-loop follow-ups** — "Mark as sent ✓" on the draft: logs event + status → contacted + auto 3-day reply-check reminder (existing endpoints only)
- [x] **#6 Neglect tiers (UI half)** — "Needs attention" section on the inbox, urgency = days idle × score, computed client-side
- [x] **#4 Merge review (UI half)** — field-by-field diff preview before confirming a merge

## 🔜 Blocked / teammate parts

### #9 Voice-note intake
- [ ] **K**: serve Whisper on the GB10 alongside Qwen
- [ ] **Toby**: additive endpoint — audio upload → transcript → existing `POST /leads` raw-text pipeline (group-chat note, no contract fight)
- [ ] **Johaan**: mic/upload button on the inbox (blocked until the endpoint exists)

### #10 Outcome tracking — ⚠ socialize FIRST, breaking change
- [ ] **All three**: agree on splitting `closed` into `closed_won` / `closed_lost` + `close_reason` column (breaks the status CHECK constraint in Toby's schema → contract change)
- [ ] **Toby**: schema migration + status transition rules
- [ ] **Johaan**: close-lead dialog (won/lost + reason) and conversion analytics tiles
- [ ] **K**: conversion insights via the scheduled-summaries path (AI purpose #4 — NOT chat)

### #6 Neglect tiers (backend half)
- [ ] **Toby** (optional, additive): official `urgency` field / `sort=urgency` — the contract lets backend sort logic change freely; the dashboard swaps its client-side computation for the field when it lands

### #4 Merge review (backend note)
- [ ] **Toby**: group-chat note that merge fills the primary's blank fields from the duplicate (behavior exists; the contract's merge line should mention it)

### #5 Chat-that-acts — parked with K
- [ ] Deliberately deferred; violates none of the four AI purposes when it lands, but nothing on the dashboard blocks on it

### Insights (docs/INSIGHTS.md)
- [ ] **Johaan**: Phase 1 — `insights.ts` engine, `/insights` route, briefing section, mock-summary integration (zero dependencies)
- [ ] **Toby**: Phase 2 — additive `insights` table + `GET/POST /api/insights?date=` (dashboard write-through creates daily history)
- [ ] **K**: Phase 3 — morning-summary cron reads `GET /api/insights` as narrative input (insights stay deterministic)

### Briefing (from docs/BRIEFING-UI.md, still pending)
- [ ] **Toby**: `briefing` table + `GET/POST /api/briefing`, `persona` + `relationship_summary` columns on leads
- [ ] **K**: 7am cron on the GB10 — Qwen composes the briefing JSON and POSTs it; emit `[Name](lead:id)` links in chat replies
