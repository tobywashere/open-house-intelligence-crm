# THE CONTRACT — frozen

This file is the agreement between the workstreams. Everything else in the repo can drift; this cannot — changes go through an issue/PR discussion, per [`CONTRIBUTING.md`](../CONTRIBUTING.md).

- K (agent/inference) writes tools that call the REST API below.
- Toby (backend/data) implements the schema and API below.
- Johaan (dashboard) renders the responses below.

## 1. SQLite schema

Canonical DDL lives in [`backend/schema.sql`](../backend/schema.sql). Summary:

### `leads`
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | required |
| phone | TEXT | nullable, E.164-ish |
| email | TEXT | nullable |
| source | TEXT | `form` \| `text` \| `note` \| `referral` \| `email` (additive, recorded 2026-07-27; auto-intake from the Gmail poller) |
| status | TEXT | `new` → `contacted` → `meeting_booked` → `closed` |
| score | INTEGER | 0–100, set by scoring formula |
| score_reason | TEXT | LLM-written explanation |
| budget | INTEGER | dollars |
| area | TEXT | e.g. "Bellevue" |
| timeline | TEXT | e.g. "6 weeks" |
| preferences | TEXT (JSON array) | e.g. `["3br","near schools"]` |
| intent | TEXT | `buy` \| `sell` \| `browse` \| `unknown` |
| missing_fields | TEXT (JSON array) | what the agent still needs to ask |
| is_neglected | INTEGER 0/1 | set by scheduled check |
| persona | TEXT | nullable, e.g. "Luxury Executive"; agent-writable via `update_lead`/`PATCH /leads/{id}` |
| relationship_summary | TEXT | nullable, AI-written profile hero paragraph; agent-writable via `update_lead`/`PATCH /leads/{id}` |
| created_at / last_activity_at | TEXT | ISO-8601, naive local wall-clock (`YYYY-MM-DDTHH:MM:SS`, no `Z`, no offset) |

**Timestamp convention (applies to every `*_ts` / `*_at` field in this
contract — `due_ts`, `start_ts`, `end_ts`, `created_at`, `last_activity_at`,
`ts`, `generated_at`, `computed_at`):** naive local wall-clock, not UTC.
Aware input (e.g. a `Z`-suffixed timestamp) sent to a write endpoint is
CONVERTED to local time server-side, never simply stripped. Clients must
write the same convention — see `toNaiveLocal()` in `dashboard/src/api.ts`.

Merging keeps the primary row, moves the duplicate's events over, and deletes the duplicate.

### `events` — the activity timeline
`id, lead_id FK, type (note|form|text|call|merge|status_change|agent_action), content, created_at`

### `appointments`
`id, lead_id FK, start_ts, end_ts, location, created_at`

### `availability` — hardcoded weekly windows
`id, weekday (0=Mon..6=Sun), start_time "HH:MM", end_time "HH:MM"`

### `reminders` — scheduled follow-ups; dashboard polls for due ones
`id, lead_id FK, due_ts, note, done 0/1, created_at`

### `audit_log` — powers the dashboard's agent-activity stream
`id, ts, actor (agent|user|cron), tool, input JSON, output JSON, lead_id nullable`

### `chat_messages`
`id, session_id, role (user|agent), content, created_at`

### `briefing`, `insights`, `daily_summary` — date-keyed generated content
`date TEXT PRIMARY KEY, payload TEXT (JSON), generated_at`/`computed_at`. One row
per date, upserted by whoever posts. The backend stores/serves the payload as-is —
see §2 for the endpoints and [`docs/BRIEFING-UI.md`](BRIEFING-UI.md) /
[`docs/INSIGHTS.md`](INSIGHTS.md) for the payload shapes.

## 2. REST API (base: `http://<host>:8000/api` dev / `:8080` single-port serve)

| Method & path | Body → Response | Notes |
|---|---|---|
| `POST /leads` | `{raw_text, source}` or structured fields → lead | raw notes get mock/LLM extraction via process |
| `GET /leads?sort=priority&status=&neglected=` | → `[lead]` | priority = score desc, neglected first; `neglected=1` filters to `is_neglected=1` only (additive, recorded 2026-07-27) |
| `GET /leads/{id}` | → `{...lead, events: [...], appointments: [...]}` | full profile |
| `PATCH /leads/{id}` | partial lead → lead | status transitions validated |
| `DELETE /leads/{id}` | → `{deleted}` | additive, recorded 2026-07-27; clears the lead's `audit_log.lead_id` to NULL rather than deleting those rows |
| `POST /leads/{id}/events` | `{type, content}` → event | bumps `last_activity_at` |
| `GET /leads/{id}/duplicates` | → `[{lead, match_on}]` | exact phone/email, fuzzy name |
| `POST /leads/merge` | `{primary_id, duplicate_id}` → merged lead | moves events, deletes duplicate |
| `POST /leads/{id}/process` | `{}` → `{lead, followup_draft}` | extract → score → draft (mock or agent) |
| `GET /availability?date=YYYY-MM-DD` | → `[{start_ts, end_ts}]` | free slots, conflicts removed |
| `POST /appointments` | `{lead_id, start_ts, end_ts, location}` → appt | **409 on conflict**; sets status `meeting_booked` |
| `GET /appointments` | → `[appt]` | |
| `GET /appointments/{id}/ics` | → `.ics` file | additive (documented 2026-07-26); calendar-file download used by the booking card |
| `POST /chat` | `{message, session_id}` → `{reply, session_id}` | relays to agent driver (mock/openclaw) |
| `GET /chat/history?session_id=` | → `[message]` | |
| `GET /chat/sessions` | → `[{session_id, message_count, last_at, preview}]` | additive 2026-07-26 (chat history picker) |
| `DELETE /chat/history?session_id=` | → `{deleted}` | additive 2026-07-26 (clear conversation) |
| `POST /scan-card` | `{filename, data: base64}` → `{extracted, duplicates, filename}` | additive 2026-07-26; extraction ONLY (review-first) — agent reads the saved image via business-card-scanner; mock returns a canned card; **413** image > 8 MB, **400** invalid base64, **422** not a recognized image (unrecognized extension or content doesn't sniff as one), **502** agent couldn't extract |
| `POST /reminders` | `{lead_id, due_ts, note}` → reminder | schedule a follow-up |
| `GET /reminders?due=1` | → `[reminder + lead_name]` | dashboard polls this for the reminder banner |
| `PATCH /reminders/{id}` | → reminder | marks done |
| `GET /audit?limit=50` | → `[audit rows]` | newest first |
| `GET /metrics` | → dashboard tile numbers | see below |
| `GET /health` | → `{ok, agent_mode, agent_connected}` | |
| `POST /demo/advance-time` | `{days}` → `{neglected: [lead]}` | backdates activity, runs neglect check |
| `GET /briefing?date=YYYY-MM-DD` | → briefing JSON | **404** if none generated yet; shape in `docs/BRIEFING-UI.md` |
| `POST /briefing` | briefing JSON (must include `date`) → same JSON | upsert by date |
| `GET /insights?date=YYYY-MM-DD` | → insights JSON | **404** if none yet; shape in `docs/INSIGHTS.md` |
| `POST /insights` | insights JSON (must include `date`) → same JSON | upsert by date |
| `GET /summary?date=YYYY-MM-DD` | → daily summary JSON | **404** if none yet; shape in `docs/BRIEFING-UI.md` |
| `POST /summary` | summary JSON (must include `date`) → same JSON | upsert by date |
| `GET /integrations/status` | → `{mode, gmail, gcal}` | additive, recorded 2026-07-27; `mode` is `INTEGRATIONS_MODE`, `gmail`/`gcal` reflect whether Composio is actually live (mode=live AND a key is configured) |
| `POST /email/send` | `{lead_id, subject, body}` → `{sent: true, simulated}` | additive, recorded 2026-07-27; recipient must be the lead's own `email` (400 otherwise); `simulated: true` when integrations aren't live; logs an `events` row + reminder + audit row; this is the only outbound-email path that is audited (see §3 preamble) |

`GET /metrics` returns:
```json
{
  "active_leads": 0, "high_priority": 0, "followups_due": 0,
  "appointments_booked": 0, "avg_response_minutes": null,
  "agent_mode": "mock", "cloud_llm_requests": 0
}
```
`avg_response_minutes` is `float | null` — the mean minutes from a lead's
`created_at` to its first event's `created_at`, over leads with ≥1 event;
`null` when no lead qualifies (don't assume `0`). `cloud_llm_requests` counts
Composio tool calls (Gmail/Calendar) made on the live path, not local-LLM
inference — it's always `0` in off mode and never counts the openclaw
driver's requests, which stay on-box.

## 3. Agent tools (OpenClaw skill ⇄ REST mapping)

**Audit reality (corrected 2026-07-27, refined 2026-07-28 — the previous
"every tool call MUST write an audit_log row" claim here was false):** every
**write** made through the REST layer is audited, with exactly one
carve-out — **`POST /chat` is not audited.** A chat turn is already
durably recorded in `chat_messages` (the transcript table `GET
/chat/history` and `GET /chat/sessions` serve); writing it a second time to
`audit_log` would double-log the same fact and flood the dashboard's
activity stream with every user message. Every other mutating endpoint
calls `audit()` in the same transaction, including `POST
/leads/{id}/events`, `PATCH /reminders/{id}`, and `DELETE /chat/history`
(none of which audited before 2026-07-27), and `POST /demo/advance-time`,
whose backdate step now audits **unconditionally** as of 2026-07-28 — it
previously only got a trail via the neglect check's own `if neglected:`
guard, so a call that backdated every open lead's `last_activity_at`
without tripping the 2-day threshold left zero record of the mutation (see
`backend/tests/test_audit_coverage.py` for both this and the round-1
fixes). Two reads also audit, as an exception, since they're treated as
agent tool calls for activity-stream purposes: `GET /availability`
(`check_availability`) and `GET /leads/{id}/duplicates`
(`find_duplicate_leads`). All other reads write nothing (`GET /leads`, `GET
/leads/{id}`, `GET /metrics`, `GET /audit` itself, etc.). The optional
`composio-email-calendar` skill's **direct** Composio calls (used when a
human explicitly wants to send/schedule something outside the CRM's closed
loop) bypass the backend entirely and are **not** audited — only the
backend's own `POST /email/send` path (and the calendar-booking hook) logs
an `audit_log` row for outbound comms. Note for readers of the dashboard:
`audit_log` rows carry an `actor` of `agent`, `user`, *or* `cron` (see the
`audit_log` table in §1) — the dashboard's "agent activity" stream is
really an **audit activity** stream covering all three, not agent-only.

| Tool | Endpoint |
|---|---|
| `create_lead(raw_text, source)` | `POST /leads` |
| `update_lead(id, fields)` | `PATCH /leads/{id}` |
| `find_duplicate_leads(id)` | `GET /leads/{id}/duplicates` |
| `merge_leads(primary_id, duplicate_id)` | `POST /leads/merge` (additive, recorded 2026-07-27) |
| `get_lead_context(id)` | `GET /leads/{id}` |
| `list_leads(sort, status, neglected)` | `GET /leads?sort=&status=&neglected=` (additive, recorded 2026-07-27) |
| `delete_lead(lead_id, reason)` | `DELETE /leads/{id}` (additive, recorded 2026-07-27) |
| `score_lead(id)` | `POST /leads/{id}/process` (score part) |
| `draft_followup(id)` | `POST /leads/{id}/process` (draft part) |
| `check_availability(date)` | `GET /availability?date=` |
| `list_appointments()` | `GET /appointments` — all appointments across all leads, ordered by `start_ts`, each row joined with `lead_name` (additive, recorded 2026-07-28); used by `daily-command-center` Step 0.2 to find today's schedule before pulling per-lead context |
| `book_appointment(lead_id, start_ts, end_ts, location)` | `POST /appointments` |
| `schedule_followup(lead_id, due_ts, note)` | `POST /reminders` |
| `find_neglected_leads()` | `POST /demo/advance-time {days:0}` — runs the neglect check now and returns newly-flagged leads; use `list_leads(neglected=1)` to see all currently-neglected leads without re-running it |
| `generate_dashboard_insights()` | `GET /metrics` + LLM summary |
| `post_briefing(payload)` | `POST /briefing` — upserts by `date` (additive, recorded 2026-07-28); used by the `daily-command-center` skill's final step, shape in `docs/BRIEFING-UI.md` |

Curl examples for each live in [`skills/crm-db-operations/SKILL.md`](../skills/crm-db-operations/SKILL.md).

### `composio-email-calendar` (optional, requires internet)

A separate, optional OpenClaw skill that calls Composio (Gmail/Google
Calendar) **directly** — not through this backend's REST API — for flows the
backend doesn't automate. Requires `INTEGRATIONS_MODE=live` plus a working
Composio credential (`COMPOSIO_API_KEY` for the `api` transport, or the
`composio` CLI for the `cli` transport). Its calls are **not** recorded in
`audit_log` (see the audit-reality note above).

| Tool | Notes |
|---|---|
| `send_email(to, subject, body, *, cc=None, bcc=None)` | requires an explicit human confirmation; every recipient must already match a `leads.email` row or it raises — use `create_draft` for anyone else |
| `create_draft(to, subject, body)` | Gmail draft only, never sent |
| `fetch_emails(query="in:inbox", max_results=10)` | read-only Gmail search |
| `create_event(summary, start_datetime, *, duration_minutes=30, ...)` | direct GCal event creation, bypasses `book_appointment`'s conflict check |
| `free_busy(time_min, time_max, ...)` | direct GCal free/busy query |
| `list_events(time_min, time_max, ...)` | direct GCal event listing |

Full guardrails and argument shapes live in
[`skills/composio-email-calendar/SKILL.md`](../skills/composio-email-calendar/SKILL.md).

## 4. Division of change

- **Backend behavior** (scoring formula weights, duplicate thresholds, conflict rules): Toby may change freely — they're implementation, not contract.
- **Response shapes, paths, table columns**: frozen. Additive changes (new optional field) get recorded here same-day; breaking changes go through an issue/PR discussion per `CONTRIBUTING.md`.
- **LLM-filled fields** (`score_reason`, `intent`, `preferences`, `missing_fields`, drafts): K owns quality; shape stays as above.

## 5. Environment variables

Full list with defaults and descriptions lives in [`.env.example`](../.env.example)
(re-derived from the code 2026-07-27 — treat it as current, not this file).
Only the subset below is actually part of the frozen contract (paths/ports
other code and docs may assume); everything else in `.env.example` is
free to evolve without a contract change.

| Var | Default | Used by |
|---|---|---|
| `DB_PATH` | `backend/data/crm.db` | backend |
| `AGENT_MODE` | `mock` | backend (`mock` \| `openclaw`) |
| `HOST` | `127.0.0.1` | backend (bind interface) |
| `PORT` | `8000` dev / `8080` single-port serve | backend launcher scripts |
