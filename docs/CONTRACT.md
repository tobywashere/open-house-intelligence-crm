# THE CONTRACT — frozen

This file is the agreement between the three workstreams. **Change it only with all three people present.** Everything else in the repo can drift; this cannot.

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
| source | TEXT | `form` \| `text` \| `note` \| `referral` |
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
| persona | TEXT | nullable, e.g. "Luxury Executive"; agent-set |
| relationship_summary | TEXT | nullable, AI-written profile hero paragraph |
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

## 2. REST API (base: `http://<host>:8000/api`)

| Method & path | Body → Response | Notes |
|---|---|---|
| `POST /leads` | `{raw_text, source}` or structured fields → lead | raw notes get mock/LLM extraction via process |
| `GET /leads?sort=priority&status=` | → `[lead]` | priority = score desc, neglected first |
| `GET /leads/{id}` | → `{...lead, events: [...], appointments: [...]}` | full profile |
| `PATCH /leads/{id}` | partial lead → lead | status transitions validated |
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
| `POST /scan-card` | `{filename, data: base64}` → `{extracted, duplicates, image}` | additive 2026-07-26; extraction ONLY (review-first) — agent reads the saved image via business-card-scanner; mock returns a canned card; **413** image > 8 MB, **400** invalid base64, **502** agent couldn't extract |
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

`GET /metrics` returns:
```json
{
  "active_leads": 0, "high_priority": 0, "followups_due": 0,
  "appointments_booked": 0, "avg_response_minutes": 0,
  "agent_mode": "mock", "cloud_llm_requests": 0
}
```

## 3. Agent tools (OpenClaw skill ⇄ REST mapping)

Every tool call MUST write an `audit_log` row (the endpoints do this automatically).

| Tool | Endpoint |
|---|---|
| `create_lead(raw_text, source)` | `POST /leads` |
| `update_lead(id, fields)` | `PATCH /leads/{id}` |
| `find_duplicate_leads(id)` | `GET /leads/{id}/duplicates` |
| `get_lead_context(id)` | `GET /leads/{id}` |
| `score_lead(id)` | `POST /leads/{id}/process` (score part) |
| `draft_followup(id)` | `POST /leads/{id}/process` (draft part) |
| `check_availability(date)` | `GET /availability?date=` |
| `book_appointment(lead_id, start_ts, end_ts, location)` | `POST /appointments` |
| `schedule_followup(lead_id, due_ts, note)` | `POST /reminders` |
| `find_neglected_leads()` | `POST /demo/advance-time {days:0}` variant / `GET /leads?neglected=1` |
| `generate_dashboard_insights()` | `GET /metrics` + LLM summary |

Curl examples for each live in [`skills/crm-db-operations/SKILL.md`](../skills/crm-db-operations/SKILL.md).

## 4. Division of change

- **Backend behavior** (scoring formula weights, duplicate thresholds, conflict rules): Toby may change freely — they're implementation, not contract.
- **Response shapes, paths, table columns**: frozen. Additive changes (new optional field) need a message in the group chat; breaking changes need all three.
- **LLM-filled fields** (`score_reason`, `intent`, `preferences`, `missing_fields`, drafts): K owns quality; shape stays as above.

## 5. Environment variables

| Var | Default | Used by |
|---|---|---|
| `DB_PATH` | `backend/data/crm.db` | backend |
| `AGENT_MODE` | `mock` | backend (`mock` \| `openclaw`) |
| `AGENT_GATEWAY_URL` | `http://gb10:18789` | backend relay (Tailscale hostname) |
| `AGENT_GATEWAY_TOKEN` | — | backend relay |
| `AGENT_CHAT_PATH` | `/v1/chat/completions` | backend relay (OpenClaw gateway chat endpoint) |
| `VITE_API_URL` | `http://localhost:8000/api` | dashboard |
