# Offline-first open-source release — design

**Date:** 2026-07-27 · **Owner:** Johaan (now sole maintainer — Toby/K workstreams folded in) · **Status:** approved

Turn OpenHouse Intelligence from a hackathon deployment into a usable open-source
project. The product promise: an **offline-first CRM + AI assistant for real estate
agents** — lead intake, scoring, follow-up drafts, booking, morning briefings, and
analytics all work with zero internet, because inference runs on the operator's own
hardware via OpenClaw + a local model. Client PII never leaves the machine, and the
security defaults must actually back that claim. Internet features (Gmail/Google
Calendar via Composio, market-news summaries) are an optional add-on: **off by
default, guarded in code, clearly labeled**.

Decisions locked with Johaan (2026-07-27):
- **OpenClaw stays the required agent harness.** Docs describe "OpenClaw + any local
  model it supports"; the GB10/Qwen setup becomes *an example deployment*, not the
  architecture.
- **Cloud integrations stay in-tree** as a hard-off optional module.
- **One plan, two phases.** Phase 1 = safety/correctness (the 2026-07-27 three-part
  code review's findings). Phase 2 = stranger-ready OSS. Each phase shippable alone.
- **MIT license.** Teammates are on board; Johaan now executes all workstreams.

Input findings: consolidated backend / dashboard / agent-side review delivered
2026-07-27 (5 Critical, ~15 Important; several verified by execution). Fixes below
are the reviewers' own prescriptions.

## Phase 1 — safe and correct for any operator

1. **DB atomicity.** `db.py:get_conn` issues `BEGIN IMMEDIATE` (isolation_level
   None) so every request is one write transaction. Fixes the verified
   double-booking race (8/8 concurrent bookings succeeded) and the whole
   read-modify-write class (merge, status flips, neglect check). Add
   `POST /leads/merge` self-merge → 400 and an allowed-status-transitions map
   (`new→contacted→meeting_booked→closed`, any→closed allowed, backward moves 400).
2. **Event-loop freeze.** Integration hooks are synchronous Composio calls inside
   async endpoints (verified: a slow hook stalls /health). Run them via
   `fastapi.concurrency.run_in_threadpool` or `BackgroundTasks`; parent request
   never waits on Composio.
3. **Network posture.** Default bind `127.0.0.1`; `HOST` env opts into
   LAN/Tailscale. Optional `OHI_API_TOKEN`: when set, all /api routes require
   `X-API-Token` header (middleware); when unset and HOST≠127.0.0.1, log a loud
   startup warning. Dashboard passes the token from `VITE_API_TOKEN`.
4. **Agent-tool integrity** (skills/ + backend/app/integrations/):
   - Fix dead `delete_lead` (`_req` → `_request`, keyword args).
   - Catch read-timeouts (`TimeoutError`/`OSError`) → `CRMError(0, …)`; default
     `CRM_API_TIMEOUT_SECONDS` 120 (process endpoint makes 3 sequential LLM calls).
   - `execute()` slug allowlist (the documented catalog only); `send_email`
     recipient must match an existing `leads.email` (both tools.py copies).
   - `INTEGRATIONS_POLLER` defaults **off**; poller-derived text wrapped in
     `<untrusted-email-content>` delimiters before reaching the model; intake
     failures audit-logged instead of silently swallowed.
   - CLI stdout parsing: parse last JSON-parsing line, not `index("{")`;
     `stdin=subprocess.DEVNULL`; don't put raw stderr in chat-visible errors.
5. **One timezone convention.** Naive local wall-clock at every API boundary.
   `parse_ts` converts aware→local instead of stripping tzinfo; dashboard writes
   local (`NoteBox`, `Lead.tsx` reminder writes — fixes GCal events landing 7–8h
   off); `ReminderIn.due_ts` validated like `AppointmentIn`; schema.sql comment
   corrected from "ISO-8601 UTC" to local naive.
6. **Input-validation cluster.** `score` 0–100, `is_neglected` 0/1, `limit`
   1–500, `advance-time days` ≥0, `source` literal set (+`email`), scan-card
   extension whitelist {jpg,jpeg,png,webp} + magic-byte sniff + no absolute path
   in response, ICS output escaped per RFC 5545 (`\ ; ,` + newlines, 75-octet
   folding) — closes the verified VEVENT injection.
7. **Dashboard correctness.** Insights write-through keyed by date, not a boolean
   (midnight bug); error toasts on process/merge/note-save (mirror `markSent`);
   dedupe the 60s tick (pass fetched leads/appts into `computeInsights`; App reads
   the shared funnel cache); Firefox-safe export (append anchor, deferred revoke);
   409 detection via `ApiError.status`; chat replies dropped if session switched
   mid-flight.
8. **Honest metrics.** `avg_response_minutes` computed from first-contact event
   deltas (or the tile is dropped); `cloud_llm_requests` wired to a real counter
   incremented by the integrations layer, 0 when off.
9. **Tests.** Core API tests: leads CRUD, merge (incl. self-merge), duplicates,
   booking + **concurrency test** for the 409 race, scoring bounds, ICS escaping,
   reminders validation. Skills smoke test importing both `tools.py` modules and
   calling every public function against a stubbed transport (would have caught
   the dead `delete_lead`). Existing 26 integration tests keep passing.

## Phase 2 — stranger-ready open source

1. **LICENSE**: MIT, © 2026 the OpenHouse Intelligence contributors.
2. **Docs for outsiders.** README: offline-first pitch stays on top; new Quickstart
   (clone → `scripts/dev.sh` → mock-mode product in 2 min); "Going local-AI" doc
   (`docs/LOCAL-AI.md`): install OpenClaw, point it at any local model it
   supports, install the CRM skill, one reference config; GB10 file becomes an
   example deployment appendix. Personal specifics scrubbed from skills/docs
   (maintainer Gmail address, personal account references, machine hostnames).
3. **Config surface.** `.env.example` with every env var + comments; CONTRACT.md
   re-frozen in one pass: add `DELETE /leads/{id}`, `POST /email/send`,
   `GET /integrations/status`, `GET /appointments/{id}/ics`, `?neglected=`,
   missing agent tools (`list_leads`, `merge_leads`, `delete_lead`,
   composio-email-calendar family), `source: email`, full env table, base `:8080`;
   audit claim corrected to "every write through the REST layer is audited;
   direct Composio calls are logged by the send path".
4. **Setup UX.** `dev.sh`: trap on EXIT, pip sync on every run; `gb10.sh` →
   generalized `serve.sh`: build to `dist.new` + atomic swap, validate
   `dist/index.html` + `dist/assets` before fallback; seeding split into
   `seed.py --demo` (Sarah Chen dataset) vs default empty schema; empty states
   polished (no "0/0 bottleneck" copy on a fresh install).
5. **Offline briefing made real.** daily-command-center skill reads live CRM via
   `crm-db-operations` tools (step 0 = `list_leads`), outputs the
   `docs/BRIEFING-UI.md` JSON contract, POSTs to `/api/briefing`; documented
   OpenClaw cron entry. Market-news summary labeled internet-optional; the
   overlay shows an explicit "offline — market watch unavailable" state instead
   of mock data that looks real. Mock content only in `AGENT_MODE=mock`, labeled.
6. **Community scaffolding.** CONTRIBUTING.md (setup, test commands, contract-
   change rule), GitHub Actions CI: pytest + `tsc -b` + `vite build` on PR.

## Non-goals

Docker/packaged installers, multi-user accounts, hosted services, mobile apps,
replacing OpenClaw, cloud LLM fallbacks. Not now.

## Testing strategy

Phase 1 lands with its tests in the same tasks (TDD where the fix is behavioral).
The concurrency test is the release gate for §1. CI (Phase 2) keeps the suite
green. Manual gate before tagging v0.1: fresh-machine walkthrough of the
Quickstart on a box with no internet.

## Risks & coordination

- Parallel sessions commit to this repo continuously: every task re-reads state
  before editing; small commits.
- The contract re-freeze is an all-parties change under the contract's own rules —
  Johaan now speaks for all three workstreams (2026-07-27), so a group-chat note
  is a courtesy, not a gate.
- `BEGIN IMMEDIATE` serializes writers; at this product's scale (single operator,
  seconds-apart requests) contention is negligible — accepted.
