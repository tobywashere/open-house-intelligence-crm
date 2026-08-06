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
| source | TEXT | `form` \| `text` \| `note` \| `referral` \| `email` (additive, recorded 2026-07-27; approved Gmail-poller intake proposals use `email`) |
| status | TEXT | `new` → `contacted` → `meeting_booked` → `closed`; new closes use the dedicated close endpoint |
| outcome | TEXT | nullable `won` \| `lost`; legacy closed rows remain `null` |
| close_reason | TEXT | nullable operator-provided reason |
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

### `pending_changes` — agent-initiated CRM writes awaiting operator approval
`id, operation (create_lead|update_lead|add_event|book_appointment|schedule_followup|close_lead|delete_lead|merge_leads), lead_id nullable, payload JSON, summary TEXT, status (pending|applying|approved|denied), result JSON nullable, deny_reason nullable, created_at, decided_at nullable`.

An agent proposal for a lead create or update, note, booking, reminder, close,
merge, or deletion is a proposal, not a write. `applying` is an internal
atomic-claim state that prevents two approval workers from replaying the same
proposal. If validation or a conflict check fails before mutation, the row
returns to `pending` for correction, retry, or denial.

Rows are created only when one of the reviewed CRM write endpoints is called
with header `X-Actor: agent`, sent by `skills/crm-db-operations/tools.py`.
Dashboard actions without that header continue to apply immediately. See §2,
§3, and `backend/app/approvals.py`.

### `chat_messages`
`id, session_id, role (user|agent), content, created_at`

### `briefing`, `insights`, `daily_summary` — date-keyed generated content
`date TEXT PRIMARY KEY, payload TEXT (JSON), generated_at`/`computed_at`. One row
per date. Briefing storage contains bounded meeting advice; every briefing GET
rehydrates displayed facts from canonical CRM rows. Daily summaries are
schema-validated and market items require valid source URLs. Insights remain
the deterministic dashboard payload. See [`docs/BRIEFING-UI.md`](BRIEFING-UI.md)
and [`docs/INSIGHTS.md`](INSIGHTS.md).

## 2. REST API (base: `http://<host>:8000/api` dev / `:8080` single-port serve)

**Pending-approval deviation (additive, recorded 2026-07-28):** on the 8
CRM write endpoints marked **⏸** below, a request carrying header
`X-Actor: agent` does NOT apply — it queues a `pending_changes` row and
returns `202 {pending: true, id, operation, summary, status: "pending"}`
instead of the documented lead/result shape. Requests without that header
(the dashboard) are unaffected and get the documented response exactly as
before. See the `pending_changes` table (§1) and §3's tool-catalog note.

| Method & path | Body → Response | Notes |
|---|---|---|
| `POST /leads` ⏸ | `{raw_text, source}` or structured fields → lead | raw notes get mock/LLM extraction via process |
| `GET /leads?sort=priority&status=&neglected=` | → `[lead]` | priority = score desc, neglected first; `neglected=1` filters to `is_neglected=1` only (additive, recorded 2026-07-27) |
| `GET /leads/{id}` | → `{...lead, events: [...], appointments: [...]}` | full profile |
| `PATCH /leads/{id}` ⏸ | partial lead → lead | status transitions validated; cannot set `closed` directly |
| `POST /leads/{id}/close` ⏸ | `{outcome: "won"|"lost", reason?}` → lead | forward-only close with an explicit business outcome |
| `DELETE /leads/{id}` ⏸ | → `{deleted}` | additive, recorded 2026-07-27; clears the lead's `audit_log.lead_id` to NULL rather than deleting those rows |
| `POST /leads/{id}/events` ⏸ | `{type, content}` → event | bumps `last_activity_at`; agent-tagged calls queue for review |
| `GET /leads/{id}/duplicates` | → `[{lead, match_on}]` | exact phone/email, fuzzy name |
| `POST /leads/merge` ⏸ | `{primary_id, duplicate_id}` → merged lead | moves events, deletes duplicate |
| `GET /pending-changes?status=pending` | → `[pending_changes row]` | additive, recorded 2026-07-28; newest first; `status` defaults to `pending` (also accepts `approved`/`denied`); `payload`/`result` are returned as parsed JSON objects, not strings (additive, recorded 2026-07-29) — `payload` for `create_lead` holds already-resolved fields (`name`, `raw_text`, `source`, `phone`, `email`, `budget`, `area`, `timeline`, `intent`, `preferences[]`, `missing_fields[]`), extracted at queue time so the dialog has real values to show/edit rather than a raw note; the other 7 operations' `payload` is their normal request body |
| `POST /pending-changes/{id}/approve` | `{fields?}` → the applied lead/result | additive, recorded 2026-07-28; atomically claims and replays the queued write through the same logic the direct path uses; **400** if not currently `pending`; validation/conflict failure before mutation restores `pending` for correction, retry, or denial. Once local mutation commits, the row becomes `approved` before any external calendar/email hook runs, so a hook failure cannot make the proposal replayable. Optional `fields` (additive, recorded 2026-07-29) is merged over (overriding) the stored `payload` before applying — lets the operator edit values in the approval dialog before they land; omit or send `{}` to approve the queued payload verbatim |
| `POST /pending-changes/{id}/deny` | `{reason?}` → the `pending_changes` row, `status: "denied"` | additive, recorded 2026-07-28; no mutation to the underlying lead |
| `POST /leads/{id}/process` | `{}` → `{lead, followup_draft}` | extract → score → draft (mock or agent) |
| `GET /availability?date=YYYY-MM-DD` | → `[{start_ts, end_ts}]` | free slots, conflicts removed |
| `POST /appointments` ⏸ | `{lead_id, start_ts, end_ts, location}` → appt | **409 on conflict**; agent-tagged calls queue, then re-check the slot on approval before setting status `meeting_booked` |
| `GET /appointments` | → `[appt]` | |
| `GET /appointments/{id}/ics` | → `.ics` file | additive (documented 2026-07-26); calendar-file download used by the booking card |
| `POST /chat` | `{message, session_id}` → `{reply, session_id}` | relays to agent driver (mock/openclaw) |
| `GET /chat/history?session_id=` | → `[message]` | |
| `GET /chat/sessions` | → `[{session_id, message_count, last_at, preview}]` | additive 2026-07-26 (chat history picker) |
| `DELETE /chat/history?session_id=` | → `{deleted}` | additive 2026-07-26 (clear conversation) |
| `POST /scan-card` | `{filename, data: base64}` → `{extracted, duplicates, filename}` | additive 2026-07-26; extraction ONLY (review-first) — agent reads the saved image via business-card-scanner; mock returns a canned card; **413** image > 8 MB, **400** invalid base64, **422** not a recognized image (unrecognized extension or content doesn't sniff as one), **502** agent couldn't extract |
| `POST /voice-note/prepare` | `{filename, content_type, data: base64}` → `{transcript, draft, duplicates, warnings}` | local transcription and extraction only; validates audio signature/20 MB limit, deletes temporary audio, and never writes a lead |
| `POST /reminders` ⏸ | `{lead_id, due_ts, note}` → reminder | agent-tagged calls queue for review |
| `GET /reminders?due=1` | → `[reminder + lead_name]` | dashboard polls this for the reminder banner |
| `PATCH /reminders/{id}` | → reminder | marks done |
| `GET /audit?limit=50` | → `[audit rows]` | newest first |
| `GET /metrics` | → dashboard tile numbers | ordinary reads do not audit; an `X-Actor: agent` request audits `generate_dashboard_insights`, including optional `probe_nonce` |
| `GET /health` | → `{ok, agent_mode, agent_connected, agent_status}` | status is one of `mock`, `endpoint_enabled`, `chat_verified`, `crm_verified`, `degraded`, `endpoint_disabled`, `unauthorized`, `unreachable`, or `failed` |
| `POST /health/agent-check` | → `agent_status` | sends one harmless completion; success proves chat, not CRM tool access |
| `POST /health/crm-check` | → `agent_status` | asks the selected OpenClaw agent for read-only `generate_dashboard_insights` with a fresh nonce; only a new agent-tagged `/metrics` audit row with that nonce yields `crm_verified`; mock mode returns 409 |
| `POST /demo/advance-time` | `{days}` → `{neglected: [lead]}` | backdates activity, runs neglect check |
| `GET /briefing?date=YYYY-MM-DD` | → canonical CRM briefing JSON | always derives schedule, lead facts, and due actions from current rows; stored agent advice is optional |
| `POST /briefing` | `{date, generated_at?, meeting_briefs:[{lead_id,prepare,recommendation}]}` → validated advice | factual replacement fields are ignored; unknown lead IDs are rejected |
| `GET /insights?date=YYYY-MM-DD` | → insights JSON | **404** if none yet; shape in `docs/INSIGHTS.md` |
| `POST /insights` | insights JSON (must include `date`) → same JSON | upsert by date |
| `GET /summary?date=YYYY-MM-DD` | → daily summary JSON | **404** if none yet; shape in `docs/BRIEFING-UI.md` |
| `POST /summary` | summary JSON (must include `date`) → same JSON | upsert by date |
| `GET /integrations/status` | → `{mode, configured, last_operation, detail}` | distinguishes off/configured/verified/failed without exposing secrets |
| `POST /email/send` | `{lead_id, subject, body}` → `{sent: true, simulated}` | additive, recorded 2026-07-27; recipient must be the lead's own `email` (400 otherwise); `simulated: true` when integrations aren't live; logs an `events` row + reminder + audit row; this is the only outbound-email path that is audited (see §3 preamble) |
| `GET /knowledge/search?q=&k=` | → `[{doc, heading, breadcrumb, score, text}]` | additive, recorded 2026-07-28; local BM25 lexical search over `docs/knowledge/*.md` (`backend/app/knowledge/`), ranked highest-score-first, empty list if nothing clears `KNOWLEDGE_MIN_SCORE`; `q` required non-empty (422 otherwise), `k` bounded 1-10; debug/dashboard endpoint — the same retrieval also runs inside `POST /chat` (see §3) to ground agent replies in the operator's own market-intelligence doc; read-only, does **not** audit (see §3 preamble — it is not one of the two audited reads) |
| `GET /knowledge/docs` | → `[{name, chunks, bytes}]` | additive, recorded 2026-07-28; the `*.md` files in `KNOWLEDGE_DIR`, sorted by name. `chunks` is counted from the **live** corpus, so a file present but unindexed honestly reports `0` rather than a count it doesn't have; `README.md` is excluded from indexing by convention (see `knowledge/index.py`) and so reports `0`. Read-only, does **not** audit (see §3 preamble) |
| `POST /knowledge/docs` | `{filename, data: base64}` → `{name, chunks, bytes}` | additive, recorded 2026-07-28; writes a markdown doc into `KNOWLEDGE_DIR` and returns the slug it was stored under — the client filename is reduced to its basename, lowercased, filtered to `[a-z0-9._-]`, and must end `.md`. Re-uploading a name replaces it. **413** decoded body > 2 MB, **400** invalid base64, **422** not `.md` / not UTF-8 / contains NUL / no usable name after slugging (e.g. a dot-leading or punctuation-only name). The index self-invalidates on mtime — no restart or cache-bust needed. Audited as `upload_knowledge_doc` (a write — see §3 preamble) |
| `DELETE /knowledge/docs/{name}` | → `{name, deleted}` | additive, recorded 2026-07-28; removes the doc and (via the same mtime invalidation) de-indexes it. `name` is slugged and re-resolved against `KNOWLEDGE_DIR` before the unlink, so it cannot escape the corpus directory. **404** no such doc, **422** name unusable or resolving outside the directory. Audited as `delete_knowledge_doc` (a write — see §3 preamble) |
| `GET /vertical` | → resolved vertical pack JSON (`{name, display_name, stages, labels, intent_values, personas, copy, research}`) | additive, recorded 2026-07-28; serves `load_pack()` (`backend/app/vertical.py`) — the active `VERTICAL`/`VERTICALS_DIR` pack merged over real-estate defaults; dashboard's `vertical.ts` fetches this once at startup and falls back to its own built-in copy on any failure; read-only, does **not** audit (see §3 preamble — it is not one of the two audited reads) |
| `GET /research-settings` | → `{role, audience, lookback_days, regions[], topics[], exclusions[], national_scope_note, rendered_prompt}` | additive, recorded 2026-07-28; the daily market-search scope — the stored `settings` row if the operator has saved one, else the active pack's `research` block. `rendered_prompt` is `prompts/market-news-reporter.md.template` filled from those fields, so the UI can show exactly what the agent will be asked. Read-only, does **not** audit (see §3 preamble — it is not one of the two audited reads) |
| `PUT /research-settings` | same shape (minus `rendered_prompt`) → same JSON + `rendered_prompt` | additive, recorded 2026-07-28; upserts the `settings` row keyed `research`, fully replacing it. `lookback_days` 1–90, `regions` non-empty, `role`/`audience` non-empty (422 otherwise). Audited as `update_research_settings` (a write — see §3 preamble) |

`GET /metrics` returns:
```json
{
  "active_leads": 0, "high_priority": 0, "followups_due": 0,
  "appointments_booked": 0, "avg_response_minutes": null,
  "agent_mode": "mock", "cloud_llm_requests": 0
}
```
When the request has `X-Actor: agent`, `/metrics` creates one
`audit_log` row with `actor: "agent"`, tool `generate_dashboard_insights`, and
the request's `probe_nonce` when supplied. This narrowly scoped audited read is
the evidence used by `POST /health/crm-check`; ordinary dashboard metrics reads
remain read-only and unaudited.
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
/leads/{id}`, `GET /metrics`, `GET /audit` itself, `GET /knowledge/search`
(additive, recorded 2026-07-28), etc.). The optional
`composio-email-calendar` skill's **direct** Composio calls (used when a
human explicitly wants to send/schedule something outside the CRM's closed
loop) bypass the backend entirely and are **not** audited — only the
backend's own `POST /email/send` path (and the calendar-booking hook) logs
an `audit_log` row for outbound comms. Note for readers of the dashboard:
`audit_log` rows carry an `actor` of `agent`, `user`, *or* `cron` (see the
`audit_log` table in §1) — the dashboard's "agent activity" stream is
really an **audit activity** stream covering all three, not agent-only.

**Pending-approval gate:** `create_lead`, `update_lead`, `add_note`,
`book_appointment`, `schedule_followup`, `close_lead`, `delete_lead`, and
`merge_leads` — the eight tools below marked ⏸ — no longer write directly.
Because `tools.py` sends
`X-Actor: agent` on every call (see §2), calling one of these queues a
`pending_changes` row (not itself audited — nothing was applied yet) and
returns `{pending: true, ...}` instead of applying; a human then approves or
denies it from the dashboard. Denying writes a single `deny_pending_change`
audit row (actor `user`) and nothing else. Approving runs the original write,
so it audits twice: the original tool's own row (actor `agent`, e.g.
`create_lead`) plus the operator's `approve_pending_change` (actor `user`).
The in-process Gmail poller has no HTTP request from which to derive an actor,
but its extraction is still automated agent work. Unknown-sender mail therefore
uses the same editable `create_lead` approval boundary instead of writing a lead
directly. The real inbound text stays in the proposal for review, duplicate poll
passes do not queue it again, and the poller audits
`email_intake_review_required` (actor `cron`). If extraction used the backup
parser, the proposal summary says so; the internal fallback marker is never part
of the editable fields. Only approval creates the lead and runs its external
hook.

| Tool | Endpoint |
|---|---|
| `create_lead(raw_text, source)` ⏸ | `POST /leads` |
| `update_lead(id, fields)` ⏸ | `PATCH /leads/{id}` |
| `add_note(lead_id, content)` ⏸ | `POST /leads/{id}/events` with `type: note` |
| `close_lead(id, outcome, reason)` ⏸ | `POST /leads/{id}/close` |
| `find_duplicate_leads(id)` | `GET /leads/{id}/duplicates` |
| `merge_leads(primary_id, duplicate_id)` ⏸ | `POST /leads/merge` (additive, recorded 2026-07-27) |
| `get_lead_context(id)` | `GET /leads/{id}` |
| `list_leads(sort, status, neglected)` | `GET /leads?sort=&status=&neglected=` (additive, recorded 2026-07-27) |
| `delete_lead(lead_id, reason)` ⏸ | `DELETE /leads/{id}` (additive, recorded 2026-07-27) |
| `score_lead(id)` | `POST /leads/{id}/process` (score part) |
| `draft_followup(id)` | `POST /leads/{id}/process` (draft part) |
| `check_availability(date)` | `GET /availability?date=` |
| `list_appointments()` | `GET /appointments` — all appointments across all leads, ordered by `start_ts`, each row joined with `lead_name` (additive, recorded 2026-07-28); used by `daily-command-center` Step 0.2 to find today's schedule before pulling per-lead context |
| `book_appointment(lead_id, start_ts, end_ts, location)` ⏸ | `POST /appointments` |
| `schedule_followup(lead_id, due_ts, note)` ⏸ | `POST /reminders` |
| `find_neglected_leads()` | `POST /demo/advance-time {days:0}` — runs the neglect check now and returns newly-flagged leads; use `list_leads(neglected=1)` to see all currently-neglected leads without re-running it |
| `generate_dashboard_insights()` | `GET /metrics` + LLM summary |
| `post_briefing(payload)` | `POST /briefing` — publishes bounded advice for real appointments; backend supplies all visible CRM facts |
| `get_research_settings()` | `GET /research-settings` — returns the configured market-research URLs and keywords |
| `get_insights(date)` | `GET /insights?date=` — returns the stored CRM insight inputs for the requested day |
| `get_summary(date)` | `GET /summary?date=` — returns the persisted daily market summary |
| `search_knowledge(query, k=3)` | `GET /knowledge/search?q=&k=` — precise agent-invoked access to the same local BM25 retrieval that `POST /chat` may use as best-effort grounding |

Curl examples for each live in [`skills/crm-db-operations/SKILL.md`](../skills/crm-db-operations/SKILL.md).
`POST /summary` remains a trusted application endpoint, but it is not exposed
through the model-callable CRM wrapper. The validating `daily-brief` runner is
the only supported agent publication path.

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
| `KNOWLEDGE_DIR` | `docs/knowledge` | backend (`backend/app/knowledge/`) — local BM25 retrieval corpus, chunked/indexed for `POST /chat` and `GET /knowledge/search` |
