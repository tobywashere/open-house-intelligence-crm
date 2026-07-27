# Docs Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three highest-impact documentation defects found in the 2026-07-26 repo audit: a README that describes a product that no longer exists, a stale TODO checkbox that misreports Johaan's remaining work, and one shipped endpoint missing from the frozen contract.

**Architecture:** Three independent, docs-only edits — README.md rewrite, one-line TODO.md fix, one-row CONTRACT.md addition. No code changes anywhere. Each task is one file, one commit.

**Tech Stack:** Markdown only.

## Global Constraints

- **Docs-only.** Do not modify anything under `backend/` or `dashboard/` — a parallel Claude session is actively committing integration code there (`backend/app/integrations/`, `backend/tests/`).
- **Re-read before editing.** The repo has received 6+ commits in the last hour from a parallel session. At the start of every task, run `git log --oneline -3 && git status --short` and re-read the target file. If the target file changed since this plan was written, reconcile before editing.
- **One commit per task**, message prefix `Docs:`.
- **Every factual claim written into a doc must be verified against the repo in the same task** (the verification commands are given per step). Do not carry claims over from memory.
- Findings intentionally excluded as not-most-important: `google_calendar.py` stub docstring, IA staleness inside `docs/BRIEFING-UI.md`/`docs/FUNNEL-UI.md` (historical design docs), on-disk `__pycache__`/`dist` (gitignored, regenerable). `backend/tests/test.db` was already fixed by the parallel session in commit `703cf7b` — do not redo it.

---

### Task 1: Rewrite README.md to match the current product

**Files:**
- Modify: `README.md` (full rewrite, keep the same top-level identity)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks rely on. Independent.

Why: the README still points K at a nonexistent `agent/` directory, documents only the dev-mode launcher, omits the `PORT` and `CRM_API_URL` env vars, and describes none of the shipped product (single merged dashboard at `/`, chat rail, daily summary overlay, GB10 one-port hosting).

- [ ] **Step 1: Verify every claim the new README will make**

Run each command; each must succeed / return the stated value before writing the file:

```bash
cd /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm
ls scripts/dev.sh scripts/gb10.sh docs/GB10-SETUP.md docs/CONTRACT.md docs/INSIGHTS.md docs/BRIEFING-UI.md docs/FUNNEL-UI.md TODO.md skills/tools.py skills/db-operations-skill.md prompts/
grep -n 'PORT="${PORT:-8080}"' scripts/gb10.sh                      # PORT default 8080
grep -n 'http://gb10:18789' backend/app/agent/openclaw.py           # gateway default
grep -n 'http://localhost:8000/api' skills/tools.py                 # CRM_API_URL default
grep -n 'path=' dashboard/src/App.tsx                               # routes: / , /leads , /lead/:id , /activity
grep -n 'postInsights' dashboard/src/pages/Dashboard.tsx            # insights write-through lives in Dashboard now
grep -rn 'auth' docs/GB10-SETUP.md | head -3                        # gateway auth currently "none"
```

If any check fails (routes changed again, defaults moved), adjust the corresponding line of the Step 2 content to match reality — reality wins over this plan.

- [ ] **Step 2: Write the new README.md**

Replace the entire file with:

````markdown
# Open House Intelligence

A local-first real estate sales agent that manages leads from first contact through booked appointment. All inference runs locally on the Dell Pro Max GB10 — client PII never leaves the machine.

**[`docs/CONTRACT.md`](docs/CONTRACT.md) is the frozen schema/API/tool contract — read it before writing code.** [`PLAN.md`](PLAN.md) is the original hackathon plan, kept for history; statuses, pages, and scope have evolved since (the contract is the source of truth).

## Architecture

![Architecture sketch: Client text UI → Virtual AI assistant → OpenClaw harness ↔ Client DB (SQLite), with scheduled reminders flowing back to the UI](docs/images/architecture-sketch.png)

## One-command startup (dev, mock agent)

```bash
bash scripts/dev.sh
```

Seeds the database and starts the backend (http://localhost:8000, mock agent mode) + dashboard (http://localhost:5173). API docs at http://localhost:8000/docs.

## Production on the GB10 (real agent)

```bash
bash scripts/gb10.sh
```

Builds the dashboard and serves the whole product from **one port**: `http://<gb10-tailscale-name>:8080` (`:8000` on the GB10 belongs to the vLLM server). Runs with `AGENT_MODE=openclaw`, relaying chat to the OpenClaw gateway on `:18789`. Full wiring, skill install, and verification checklist: [`docs/GB10-SETUP.md`](docs/GB10-SETUP.md).

## What's in the dashboard

- `/` — the dashboard: live KPI strip in the navbar, sales funnel (with derived Qualified / Offers Submitted stages), and the deterministic insights engine (`dashboard/src/insights.ts` — computed from DB data, never LLM-invented)
- `/leads` — prioritized inbox with the "Needs attention" neglect section
- `/lead/:id` — profile: persona chip + AI relationship summary, activity timeline, follow-up draft with closed-loop "Mark as sent", booking, client-safe export, merge review
- `/activity` — agent audit stream (every tool call), reachable from the dev icon in the navbar
- Global resizable **chat rail** — sessions, markdown rendering, `[Name](lead:12)` links into profiles
- **Daily summary overlay** — auto-opens once per day; "↻ Refresh now" asks the agent (chat session `summary-trigger`) to re-run research and repost

## Manual setup

```bash
# backend (Python 3.11+)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python backend/seed.py
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

# dashboard (Node 20+), separate terminal
cd dashboard && npm install && npm run dev
```

## Who owns what

| Person | Owns | Start here |
|---|---|---|
| **K** | Agent & local inference on the GB10 (OpenClaw, Qwen 3.6 35B-A3B, skills, prompts, cron) | `skills/` — the CRM skill (`db-operations-skill.md` + `tools.py`) and `daily-command-center/`; `prompts/`. The backend relay you answer through is `backend/app/agent/openclaw.py`; mock behavior to replace is `backend/app/agent/mock.py` |
| **Toby** | Backend, SQLite, calendar & business logic | `backend/` — schema in `backend/schema.sql`, scoring weights in `app/scoring.py`, seed data in `seed.py` |
| **Johaan** | Dashboard, integration, demo & pitch | `dashboard/src/` — typed API client in `src/api.ts` |

The contract (`docs/CONTRACT.md`) is frozen — breaking changes need all three of us.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `AGENT_MODE` | `mock` | `mock` (dev, no GB10 needed) or `openclaw` (relay to the GB10) |
| `AGENT_GATEWAY_URL` | `http://gb10:18789` | OpenClaw gateway; `scripts/gb10.sh` overrides to `http://localhost:18789` since backend and gateway share the box |
| `AGENT_GATEWAY_TOKEN` | — | Gateway bearer token — only needed if K enables token auth (currently `gateway.auth.mode: "none"`) |
| `AGENT_CHAT_PATH` | `/v1/chat/completions` | Gateway chat endpoint path |
| `DB_PATH` | `backend/data/crm.db` | SQLite location |
| `PORT` | `8080` | Serve port for `scripts/gb10.sh` (dev mode uses 8000 + 5173) |
| `VITE_API_URL` | `http://localhost:8000/api` | Backend URL for the dashboard |
| `CRM_API_URL` | `http://localhost:8000/api` | Backend URL for the agent's `skills/tools.py` |

Everyone develops locally in mock mode. For integration tests, point `VITE_API_URL` / `AGENT_GATEWAY_URL` at the GB10 over Tailscale.

## Docs index

- [`docs/CONTRACT.md`](docs/CONTRACT.md) — frozen schema / API / agent tools (source of truth)
- [`TODO.md`](TODO.md) — post-MVP work, tracked by owner
- [`docs/GB10-SETUP.md`](docs/GB10-SETUP.md) — hosting the product on the GB10
- [`docs/INSIGHTS.md`](docs/INSIGHTS.md) — deterministic insights engine
- [`docs/BRIEFING-UI.md`](docs/BRIEFING-UI.md), [`docs/FUNNEL-UI.md`](docs/FUNNEL-UI.md) — briefing & funnel design docs (nav has since merged into `/`)
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — approved specs (Google Calendar + Gmail integration)
- [`PLAN.md`](PLAN.md) — original hackathon plan (historical)

## Demo helpers

- `python backend/seed.py` — reset to the 15-lead demo dataset (Sarah Chen's un-merged duplicate is leads #1/#2)
- `POST /api/demo/advance-time {"days":3}` — backdate activity so the neglected-lead check fires on stage
````

- [ ] **Step 3: Verify the rewrite**

```bash
# every relative link in the README resolves to a real file/dir
grep -oE '\]\(([^)#h][^)]*)\)' README.md | tr -d '])' | tr -d '(' | while read -r p; do [ -e "$p" ] || echo "BROKEN: $p"; done
# the phantom directory is gone
grep -c 'agent/`' README.md   # expect 0 — `agent/` as a bare start-here pointer must not appear
```

Expected: no `BROKEN:` lines; the phantom `agent/` pointer is gone (references to `backend/app/agent/...` files are fine).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Docs: README matches the shipped product (GB10 hosting, merged dashboard, full env table, docs index)"
```

---

### Task 2: Check off the completed insights write-through in TODO.md

**Files:**
- Modify: `TODO.md:85` (the Insights section, `docs/INSIGHTS.md` Phase 2 item)

**Interfaces:** Independent; consumes/produces nothing.

Why: the item reads as Johaan's remaining piece, but it shipped — `dashboard/src/pages/Dashboard.tsx` POSTs `computeInsights()` via `api.postInsights()` once per visit.

- [ ] **Step 1: Verify the feature is still shipped**

```bash
grep -n 'postInsights' dashboard/src/pages/Dashboard.tsx dashboard/src/api.ts
```

Expected: a call site in `Dashboard.tsx` (inside the load effect) and the `postInsights` method in `api.ts`. If the call site has moved to another file, use that file's name in Step 2.

- [ ] **Step 2: Edit the line**

In `TODO.md`, replace:

```markdown
- [ ] **Johaan**: Phase 2 write-through — POST today's `computeInsights()` payload after computing (idempotent upsert; endpoint's ready)
```

with:

```markdown
- [x] **Johaan**: Phase 2 write-through — shipped 2026-07-26: `pages/Dashboard.tsx` POSTs today's `computeInsights()` payload once per visit (idempotent upsert via `api.postInsights`)
```

- [ ] **Step 3: Verify no other stale checkbox claims the same work**

```bash
grep -n 'write-through' TODO.md
```

Expected: line 17's "still Johaan's remaining piece" note now contradicts the checked box — append " *(done — see Insights section below)*" to the end of line 17's sentence so the file agrees with itself.

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "Docs: TODO — insights Phase 2 write-through shipped (Dashboard.tsx postInsights)"
```

---

### Task 3: Add the shipped `.ics` endpoint to CONTRACT.md

**Files:**
- Modify: `docs/CONTRACT.md` (§2 REST API table, after the `GET /appointments` row)

**Interfaces:** Independent; consumes/produces nothing.

Why: `GET /api/appointments/{id}/ics` exists in the backend and the dashboard uses it (`icsUrl` in `api.ts`), but it's absent from the frozen contract. Per the contract's own §4 rule, additive changes need a group-chat note — this task documents shipped reality and flags the note.

- [ ] **Step 1: Verify the endpoint exists and is consumed**

```bash
grep -rn 'ics' backend/app/routers/calendar.py | head -5
grep -n 'icsUrl' dashboard/src/api.ts
```

Expected: a `@router.get("/appointments/{appt_id}/ics"...)` route and the `icsUrl` helper.

- [ ] **Step 2: Add the table row**

In `docs/CONTRACT.md` §2, immediately after the row `| \`GET /appointments\` | → \`[appt]\` | |`, insert:

```markdown
| `GET /appointments/{id}/ics` | → `.ics` file | additive (documented 2026-07-26); calendar-file download used by the booking card |
```

- [ ] **Step 3: Verify table renders**

```bash
grep -n 'appointments/{id}/ics' docs/CONTRACT.md
```

Expected: exactly one match, inside the §2 table (pipe-delimited, same column count as neighbors: 3 columns).

- [ ] **Step 4: Commit**

```bash
git add docs/CONTRACT.md
git commit -m "Docs: contract — record shipped GET /appointments/{id}/ics (additive)"
```

- [ ] **Step 5: Flag the group-chat note**

Not automatable: per CONTRACT §4, additive changes get a group-chat message. Report back to Johaan: *"Post in the group chat that the contract now records the already-shipped `GET /appointments/{id}/ics` endpoint."*

---

## Self-Review (completed at plan-writing time)

- **Coverage:** the three most-important audit findings each map to one task; excluded findings are listed in Global Constraints with reasons; `test.db` confirmed already fixed in `703cf7b`.
- **Placeholders:** none — full README content, exact TODO line replacement, exact contract row are inline.
- **Consistency:** file paths and defaults re-verified against the working tree at 16:4x on 2026-07-26 (`PORT` 8080, gateway default `http://gb10:18789`, write-through in `Dashboard.tsx`, routes `/`, `/leads`, `/lead/:id`, `/activity`). Each task re-verifies at execution time because a parallel session is committing.
