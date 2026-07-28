# Google Calendar + Gmail integration — design

**Date:** 2026-07-26 · **Owner:** Johaan (dashboard/integration) · **Status:** approved

Connect the CRM to Johaan's real Google account (via Composio, already authed as
<your-google-account>) so that appointments, reminders, and new leads land on
Google Calendar, and AI-drafted follow-ups are sendable via Gmail with one click.
Everything sits behind a mode flag, **off by default**, honoring the on-record rule
that the demo must never depend on venue internet or live OAuth
(`backend/app/calendar_adapter/google_calendar.py` stub).

## Architecture

- New module `backend/app/integrations/` wrapping the **Composio Python SDK**
  (`COMPOSIO_API_KEY` in `.env`). Tools used: `GMAIL_SEND_EMAIL`,
  `GMAIL_CREATE_EMAIL_DRAFT`, `GMAIL_FETCH_EMAILS` (search),
  `GOOGLECALENDAR_CREATE_EVENT`, free/busy lookup.
- `INTEGRATIONS_MODE=off|live` env var (default `off`), mirroring `AGENT_MODE`.
  - **off**: every integration action is *simulated* — the code path runs, writes
    its `audit_log` row (tool name suffixed ` (simulated)`), returns a
    `{simulated: true}` marker; UI toasts "Simulated — integrations off".
  - **live**: same calls go through Composio against the real account.
- Existing routers get small hooks: `on_lead_created`, `on_tour_booked`,
  `on_reminder_created`. Hooks fire for **any** API caller — dashboard buttons and
  K's agent tools alike — so agent-booked tours reach the calendar too.
- Hooks are fire-and-forget: any Composio/network error is caught, one retry,
  then an `audit_log` failure row; the parent request (booking, lead create,
  reminder) always succeeds. Exception: `POST /email/send` surfaces failure to the
  UI — silently dropping a send is worse than a red toast.
- `seed.py` writes directly to SQLite (no HTTP), so demo resets never fire hooks —
  no calendar spam.

**Rejected alternatives:** sidecar sync process (second process to babysit, no
synchronous button feedback); agent-side OpenClaw skills (K's ownership zone,
agent critical path).

## Contract impact (all additive — group-chat note required before merge)

1. Columns `appointments.gcal_event_id TEXT` and `reminders.gcal_event_id TEXT`,
   auto-migrated on startup like `leads.persona` was.
2. New endpoints:
   - `POST /api/email/send {lead_id, subject, body}` → sends (or simulates), logs
     timeline event, status → `contacted` if `new`, creates 3-day reply-check
     reminder. Returns `{sent, simulated, event_id}`.
   - `GET /api/integrations/status` → `{mode, gmail: bool, gcal: bool}`.
3. Event-type convention: `events.type = 'email'` for sent mail and replies.
   `events.type` has no CHECK constraint — convention-only, same mechanism as the
   funnel offer events. Reply events embed a `gmail:<message_id>` marker in
   content for idempotent polling (no schema change).
4. Files touched in Toby's tree: `routers/leads.py`, `routers/calendar.py`
   (hooks, a few lines each), `local_calendar.py` (Phase 2 busy filter).

## Phase 1 — outbound

| Trigger | Action |
|---|---|
| `POST /appointments` success | GCal event "Home tour with {name}" at slot time; location; description = phone, email, budget, area, timeline. `gcal_event_id` stored. |
| `POST /leads` (new lead) | Same-day 30-min GCal block "📞 Call new lead: {name}"; if lead has email, pre-create a Gmail **draft** (intro template) in Johaan's account. |
| `POST /reminders` | 15-min GCal event at `due_ts`: "Follow up: {name} — {note}". |
| "Send via Gmail" click | `POST /email/send` → Gmail send to `lead.email` → `email` timeline event + closed-loop (status, reply-check reminder), exactly like today's "Mark as sent" but real. |

Dashboard (Phase 1):
- Lead profile draft card: editable subject (prefilled) + **Send via Gmail**
  button; existing "Mark as sent ✓" stays for leads without email / off-mode
  preference.
- Collapsible free-compose box on the profile (subject + body → same endpoint).
- BookingCard: "Added to Google Calendar ✓" when live; existing `.ics` download
  remains the off-mode path.
- Header: integrations status chip next to the local badge (`live` / `off`).
- Timeline renders `email` events with a ✉ icon.

## Phase 2 — inbound

- **Reply detection**: FastAPI background task (live mode only, every 5 min)
  polls the Gmail inbox; for each message from a known lead not already logged
  (dedupe on `gmail:<message_id>` marker), log "Reply received" `email` event
  and mark that lead's reply-check reminders done. Inbox shows a "replied"
  badge (computed from events).
- **Smart email intelligence** (added 2026-07-26, same poll pass):
  - *Reply re-extraction*: after logging a reply, re-run `/leads/{id}/process`
    so new info in the email (budget, timeline, …) backfills missing fields and
    re-scores the lead. `process` re-extraction now reads `email` events too.
  - *Lead intake from unknown senders*: inbox mail from an address matching no
    lead (and not matching a noise filter — no-reply/newsletter/notification/…)
    is fed through the existing `POST /leads {raw_text}` extraction pipeline as
    a new lead with `source = 'email'` (convention — `leads.source` has no
    CHECK constraint). The raw event carries the `[gmail:<id>]` marker for
    idempotence. This also triggers the standard new-lead hooks (call block,
    intro draft).
- **Busy sync**: when live, `free_slots()` subtracts real GCal busy blocks
  (free/busy query, ~5-min TTL cache) so neither UI nor agent offers a slot
  Johaan is busy in. Local availability windows and the 409 conflict rule are
  unchanged — Google busy only filters *offered* slots.

## Error handling summary

- Composio call fails → 1 retry → audit_log failure row, parent request unaffected.
- Email send fails → error surfaced to UI ("Send failed — try again"); no
  timeline event, no status change, no reminder (the closed-loop only runs on
  confirmed send).
- Poller failures are logged and skipped; next tick retries naturally.
- Off mode never touches the network.

## Verification

1. **Off-mode walkthrough** (demo-safe): create lead, book tour, add reminder,
   send draft — all four simulate, audit rows appear, UI toasts "simulated",
   nothing hits Google.
2. **Live smoke test**: flip `INTEGRATIONS_MODE=live` → new lead produces a
   calendar block + Gmail draft; booked tour produces a calendar event; "Send via
   Gmail" delivers a real email; replying to it flips the badge within 5 min;
   a busy block on GCal removes the overlapping availability slot.
3. `tsc --noEmit` clean; backend starts in both modes with and without
   `COMPOSIO_API_KEY` set (missing key + live mode → clear startup warning,
   behaves as off).

## Pitch framing

All *inference* stays local on the GB10 — unchanged. Johaan's own Google account
is the only external service, opt-in via one env var, and `cloud_llm_requests`
stays 0. The status chip makes the boundary visible on stage.
