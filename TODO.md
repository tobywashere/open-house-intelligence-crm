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

**chrome-mcp ("streamable-mcp-server") root cause, diagnosed 2026-07-26**: `mcp-chrome-bridge@1.0.31`
binds every HTTP session's transport to ONE module-global MCP `Server` (`dist/mcp/mcp-server.js`
singleton → `dist/server/index.js:194`), and the MCP SDK allows one transport per Server — so the
bridge supports exactly **one client session per process lifetime**. A client that dies without
`DELETE /mcp` wedges it permanently ("Already connected to a transport"; the throw escapes the
handler's try/catch → Fastify 500). On this box the first claim came from Claude Code's own
`chrome-mcp-server` entry in `~/.claude.json` racing the gateway for :12306. **Consequence**:
daily-brief silently loses CNBC entirely + GeekWire article bodies (both under `sources_failed`
in the 2026-07-26 handoff). The bridge is spawned by Brave as a native-messaging host (extension
`hbdgbgagpkpjffpklnamcljpakneikee`), no systemd unit — it lives and dies with the browser.

- [x] Phantom-session unblock: killed the wedged bridge pid; removed the competing `chrome-mcp-server`
  entry from `~/.claude.json` so the gateway is :12306's only client (2026-07-26)
- [ ] **K**: after any Brave/extension restart, confirm the gateway attaches:
  `journalctl --user -u openclaw-gateway -f` → no `[bundle-mcp] failed to start`; then a chat turn
  calling `get_windows_and_tabs` should succeed, and the next daily-brief run should list
  `cnbc-economy` under `sources_ok`
- [x] **Vendored + patched the bridge** (2026-07-26; upstream 1.0.31 is still latest, no fix
  released): live copy is `~/.openclaw/vendor/mcp-chrome-bridge` — patched for one MCP Server per
  session (multiple concurrent clients verified: 3 simultaneous initializes all 200), connect
  errors return clean MCP errors instead of Fastify 500s, `stop()` closes live sessions, and a
  reaper closes sessions idle >30 min (crashed clients can no longer wedge anything). Both
  native-messaging manifests (`~/.config/{google-chrome,BraveSoftware/Brave-Browser}/NativeMessagingHosts/com.chromemcp.nativehost.json`)
  now point at the vendored `run_host.sh`. ⚠ `npm i -g mcp-chrome-bridge` updates do NOT reach
  the live copy — to adopt an upstream fix, re-point the manifests back (or re-vendor) and
  restart Brave.
- [x] **daily-brief pre-flight** (2026-07-26): SKILL.md now starts with a bridge health check +
  headless Brave-restart recovery command, then falls back to the documented degraded path
  (GeekWire headlines-only, CNBC under sources unavailable). Canonical skill source:
  `~/Downloads/agents/daily-brief` (edit there, run `sync.sh`).
- [x] **Memory search now runs on local embeddings** (2026-07-26): installed
  `@openclaw/llama-cpp-provider` and set `agents.defaults.memorySearch = {provider: "local",
  local: {contextSize: 2048}}` in `~/.openclaw/openclaw.json` — GGUF model
  (`embeddinggemma-300m-qat-Q8_0`, ~0.6 GB) runs in-process, no cloud calls, consistent with the
  local-only rule. The old `No API key found for provider "openai"` error is gone; if the local
  provider ever fails it degrades to keyword-only search instead of erroring.

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
