---
name: crm-db-operations
description: Read and write the Open House real-estate CRM lead database through an audited tool layer — create/update/merge/delete leads, pull lead context, list and score leads, draft follow-ups, check availability and book appointments, find neglected leads, and generate dashboard insights. Use for "add a lead", "who should I follow up with", "book a showing", "what do we know about X", "how's the pipeline looking", or any CRM lead/appointment question. Never write SQL directly.
---

# Open House CRM — lead database skill

You are Annie's local real-estate CRM assistant, running on the GB10 with a
local model. This skill is your only way to read or write the client-lead
database. It exists so the model never touches SQL directly and every action
is auditable.

## Rules

1. Use only the tools below for CRM facts and writes — never invent a lead,
   ID, contact field, note, appointment, or score.
2. Every write must resolve a real `lead_id` first. If a name could match more
   than one lead, call `find_duplicate_leads` (or `list_leads`) and ask the
   user to disambiguate rather than guessing.
3. Preserve the user's exact factual content when creating or updating a lead
   — do not embellish or invent details that weren't said.
4. `book_appointment` can 409 on a scheduling conflict — call
   `check_availability` first and surface the conflict instead of retrying blindly.
5. Never fabricate numbers for `generate_dashboard_insights` — it returns the
   real counts from the database; write your narrative on top of those numbers,
   don't override them.
6. `delete_lead` is destructive: use it ONLY when the user explicitly asks to delete a specific lead, and confirm the name back afterwards. Never delete to "clean up" on your own initiative.

## Setup

These tools are a thin HTTP client (`tools.py`, stdlib only, no pip install
needed) over the backend REST API. The backend (FastAPI + SQLite) must be
running and reachable — for the demo it runs on the same GB10 box.

```bash
# CRM_API_URL is already set in the gateway service environment (GB10: http://localhost:8080/api).
# Do NOT export it yourself — :8000 on the GB10 is the vLLM server, not the CRM.
python3 -c "import sys; sys.path.insert(0,'.'); import tools; print(tools.list_leads()[:1])"
```

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.openclaw/skills/crm-db-operations"))
import tools

lead = tools.create_lead(raw_text="Met at open house, Bellevue, $1.1M budget", source="form")
```

Every call raises `tools.CRMError(status, message)` on failure (400/404/409/etc).
Catch it and turn it into a clarifying question or an apology to the user —
never let a raw stack trace reach the chat.

## Tool catalog

| Tool | Signature | Returns | Use it when... |
|---|---|---|---|
| `create_lead` | `(raw_text=None, source="note", *, name=, phone=, email=, budget=, area=, timeline=, intent=)` | created lead | A new person appears — a form fill, text, note, or referral. Pass `raw_text` for anything unstructured; the backend extracts fields. `source` must be one of `form`\|`text`\|`note`\|`referral`\|`email` — any other value gets a hard 422. |
| `update_lead` | `(lead_id, **fields)` | updated lead | Any known field changes — status, phone, budget, etc. Resolve `lead_id` first. |
| `find_duplicate_leads` | `(lead_id)` | `[{lead, match_on}]` | Before merging, or when you suspect this person already has a profile (same phone/email, or a very similar name). |
| `merge_leads` | `(primary_id, duplicate_id)` | merged lead | User confirms two profiles are the same person. Primary's blanks get filled from the duplicate; primary wins conflicts; duplicate is deleted. |
| `get_lead_context` | `(lead_id)` | lead + `events[]` + `appointments[]` | Before answering "what do we know about X", before drafting a message, before deciding next action. |
| `list_leads` | `(sort="priority", status=None, neglected=None)` | `[lead]` | "Who should I follow up with", "show me Bellevue buyers" (filter client-side on the returned fields), inbox-style questions. |
| `score_lead` | `(lead_id)` | `{lead_id, score, score_reason}` | After enough new info lands on a lead to re-score it (new note, new field). Deterministic formula server-side; only the reason is written by you upstream (already filled in by the backend's driver). |
| `draft_followup` | `(lead_id)` | draft message text | User asks you to reach out to someone, or after scoring a hot lead. |
| `check_availability` | `(date: "YYYY-MM-DD")` | `[{start_ts, end_ts}]` free slots | Before booking anything — always check first. |
| `book_appointment` | `(lead_id, start_ts, end_ts, location=None)` | appointment | User agrees to a specific time. Raises 409 on conflict — re-check availability and offer alternatives. Lead status auto-flips to `meeting_booked`. |
| `schedule_followup` | `(lead_id, due_ts, note=None)` | reminder | User wants a reminder ("remind me Friday to..."), or you just flagged someone as neglected and want to close the loop. |
| `find_neglected_leads` | `()` | `[lead]` newly flagged | Scheduled/cron check, or "who haven't I talked to" questions. Evaluates every open lead against the 2-day-idle rule right now. |
| `generate_dashboard_insights` | `()` | `{active_leads, high_priority, followups_due, appointments_booked, avg_response_minutes, agent_mode, cloud_llm_requests}` | Morning summaries, "how's the pipeline looking" questions. These are real counts — narrate them, don't replace them. `avg_response_minutes` can be `null` (no lead has a first-response event yet) — say "not enough data yet" rather than reporting it as `0` or omitting the field silently. |
| `delete_lead` | `(lead_id, reason="")` | `{deleted, lead_id, name}` | **Destructive.** Only call when the user explicitly asked to delete a specific lead — never to "clean up" on your own initiative. Confirm the deleted lead's name back to the user afterwards. |
| `post_briefing` | `(payload: dict)` | the same payload, echoed back | Upsert the day's morning-briefing JSON (must include `date`). Called by the `daily-command-center` skill's final step — see its **Output contract** section for the exact shape (mirrors `docs/BRIEFING-UI.md`). |

Full request/response shapes and the underlying REST endpoints are frozen in
[`docs/CONTRACT.md`](../../docs/CONTRACT.md) — this file is the model-facing
view of that same contract; if they ever disagree, the contract wins.

## Curl equivalents (for debugging without the Python client)

```bash
BASE="${CRM_API_URL:-http://localhost:8080/api}"

curl -s -X POST "$BASE/leads" -H 'content-type: application/json' \
  -d '{"raw_text":"Met at open house, Bellevue, budget $1.1M","source":"form"}'

curl -s "$BASE/leads/1"                                  # get_lead_context
curl -s "$BASE/leads/1/duplicates"                       # find_duplicate_leads
curl -s -X PATCH "$BASE/leads/1" -d '{"status":"contacted"}' -H 'content-type: application/json'
curl -s -X POST "$BASE/leads/1/process"                   # score_lead + draft_followup
curl -s "$BASE/availability?date=2026-07-28"              # check_availability
curl -s -X POST "$BASE/appointments" -H 'content-type: application/json' \
  -d '{"lead_id":1,"start_ts":"2026-07-28T18:00:00","end_ts":"2026-07-28T18:45:00"}'
curl -s -X POST "$BASE/demo/advance-time" -d '{"days":0}' -H 'content-type: application/json'  # find_neglected_leads
curl -s "$BASE/metrics"                                   # generate_dashboard_insights
curl -s -X POST "$BASE/briefing" -H 'content-type: application/json' \
  -d '{"date":"2026-07-28","greeting":"Good morning"}'    # post_briefing (shape in docs/BRIEFING-UI.md)
```

## Maintaining the database

The database is `backend/data/crm.db` (SQLite), schema in `backend/schema.sql`.
You (the agent) never modify these directly — they're listed here so you can
tell the user how to reset the demo if something goes wrong on stage.

- **Full reset to the 15-lead demo dataset:** `python backend/seed.py --demo`
  (wipes and recreates the DB; leads #1/#2 are Sarah Chen's un-merged duplicate,
  built for the merge demo). Bare `python backend/seed.py` resets to an empty
  schema with no demo leads.
- **Force the neglect check to fire on stage:** `POST /demo/advance-time
  {"days": 3}` backdates all activity by 3 days first, then runs the same
  check as `find_neglected_leads`.
- **Inspect what the agent has done:** `GET /audit?limit=50` — every tool call
  in this file writes an `audit_log` row automatically (actor, tool, input,
  output, lead_id); the dashboard's activity stream reads directly from it.
- If `tools.py` can't reach the backend, `CRMError(status=0, ...)` is raised —
  check the backend is running (`GET /health`) and `CRM_API_URL` is correct
  before assuming the DB is broken.

## No funnel endpoint

There is no `GET /api/funnel`. Pipeline/funnel questions are answered from `list_leads` (status field) plus `/api/appointments` — the dashboard derives its funnel client-side the same way.
