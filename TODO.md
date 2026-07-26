# Post-MVP TODO

Ownership after the job swap: **K = agent/inference (GB10, OpenClaw, Qwen 3.6 35B-A3B) · Toby = backend/SQLite/calendar · Johaan = dashboard/integration/demo + insights.**

The AI has exactly four jobs: DB read/write, scheduled summaries, timed reminders (K), and **insights — now Johaan's**: computed from DB data, presented in the dashboard (never chat), and used as input for the morning summary. Plan: [docs/INSIGHTS.md](docs/INSIGHTS.md). Chat-that-acts is parked with K.

## ✅ Shipped (dashboard side, no dependencies)

- [x] **#11 Client-safe export** — "Export ↓" on the profile downloads a markdown summary (no score/internal notes)
- [x] **#1 Closed-loop follow-ups** — "Mark as sent ✓" on the draft: logs event + status → contacted + auto 3-day reply-check reminder (existing endpoints only)
- [x] **#6 Neglect tiers (UI half)** — "Needs attention" section on the inbox, urgency = days idle × score, computed client-side
- [x] **#4 Merge review (UI half)** — field-by-field diff preview before confirming a merge

## ✅ Shipped (backend, 2026-07-26)

- [x] **Briefing persistence** — `briefing` table + `GET/POST /api/briefing` (upsert by date, 404 if none yet); `leads.persona` + `leads.relationship_summary` columns, settable via `PATCH /leads/{id}`. Existing DBs auto-migrate (no reseed needed).
- [x] **Insights persistence (Phase 2 endpoint)** — `insights` table + `GET/POST /api/insights?date=`. Dashboard write-through (POSTing after `computeInsights()`) is still Johaan's remaining piece — the endpoint is ready and tested.
- [x] **Daily summary persistence** — `daily_summary` table + `GET/POST /api/summary?date=`, matching `prompts/seattle-real-estate-news-reporter.md`'s output shape.
- Round-tripped all three (POST → GET, plus upsert-not-duplicate) against a live server; verified against the exact TS interfaces in `dashboard/src/{briefing,summary,insights}.ts` so the existing mock-fallback UI now renders real data with zero UI changes once K's crons post to them.

## 🔜 Blocked / teammate parts

### #12 GB10 hosting issues found in gateway logs (2026-07-26) — K

- [ ] **chrome-mcp won't attach**: `openclaw-gateway` logs
  `[bundle-mcp] failed to start server "streamable-mcp-server" (http://127.0.0.1:12306/mcp):
  … "Already connected to a transport. Call close() before connecting to a new transport"`
  every few minutes. The `mcp-chrome-bridge` on :12306 holds one stale transport and rejects the
  gateway's reconnects. **Consequence**: the daily-brief skill silently degrades — GeekWire article
  bodies (Cloudflare-blocked without a browser) and the entire CNBC feed drop out; today's handoff
  logs both under `sources_failed`. **Likely fix**: restart the chrome bridge (and Chrome) so the
  stale connection clears, or upgrade `mcp-chrome-bridge` to a version that allocates one Protocol
  instance per connection. Verify with: `journalctl --user -u openclaw-gateway -f` → the
  `[bundle-mcp]` error should stop, and the next daily-brief run should list `cnbc-economy` under
  `sources_ok`.
- [ ] **Memory search wants an OpenAI key**: `[memory] sync failed (search-bootstrap): No API key
  found for provider "openai"`. OpenClaw's memory-search bootstrap builds a semantic index and
  defaults its *embedding* provider to OpenAI. Nothing is sent anywhere (it fails before any call)
  and chat/tools are unaffected — memory falls back to keyword-only search. Since the product rule
  is local-only inference, don't add an OpenAI key; either set
  `agents.defaults.memorySearch.provider` to a local option (Ollama / local GGUF / an
  OpenAI-compatible local endpoint serving an embedding model) or leave it — the error is cosmetic.

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
- [x] **Johaan**: Phase 1 — `insights.ts` engine, `/insights` route, briefing section, mock-summary integration (zero dependencies)
- [x] **Toby**: Phase 2 endpoint — additive `insights` table + `GET/POST /api/insights?date=`
- [ ] **Johaan**: Phase 2 write-through — POST today's `computeInsights()` payload after computing (idempotent upsert; endpoint's ready)
- [ ] **K**: Phase 3 — morning-summary cron reads `GET /api/insights` as narrative input (insights stay deterministic)

### Briefing (from docs/BRIEFING-UI.md)
- [x] **Toby**: `briefing` table + `GET/POST /api/briefing`, `persona` + `relationship_summary` columns on leads
- [ ] **K**: 7am cron on the GB10 — Qwen composes the briefing JSON and POSTs it; emit `[Name](lead:id)` links in chat replies

### Daily summary overlay (docs/BRIEFING-UI.md)
- [x] **Toby**: `daily_summary` table + `GET/POST /api/summary?date=`
- [ ] **K**: cron that runs `prompts/seattle-real-estate-news-reporter.md` + writes the AI-insights narrative, POSTs to `/api/summary`
