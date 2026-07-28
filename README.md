# OpenHouse Intelligence

**Your AI-powered operating system for the modern real estate agent.** Spend less time managing your CRM. Spend more time building relationships.

Real estate agents have data everywhere but intelligence nowhere — leads scattered across text messages, voice notes, emails, business cards, spreadsheets, and legacy CRMs, where information gets lost and nothing helps the agent get better. OpenHouse Intelligence turns those scattered conversations into an intelligent, always-on real estate operation: **just tell your agent what happened** ("I met Sarah Chen at the open house — she wants a 3-bed in Bellevue under $1.2M") and the AI reads and writes the CRM through natural language, drafts the follow-ups, books the tours, and generates real analytics — pipeline health, conversion, lead aging, source performance, next-best actions — with zero manual data entry. The result: less administration, faster follow-up, more closed deals.

And it's **local-first**: all inference runs on the Dell Pro Max GB10, so client PII — budgets, financial situations, relocation reasons — never leaves the machine. Full story: [`docs/OpenHouse-Pitch.pdf`](docs/OpenHouse-Pitch.pdf).

**[`docs/CONTRACT.md`](docs/CONTRACT.md) is the frozen schema/API/tool contract — read it before writing code.** [`PLAN.md`](PLAN.md) is the original hackathon plan, kept for history; statuses, pages, and scope have evolved since (the contract is the source of truth).

## Architecture

![OpenHouse Intelligence architecture: Capture (text, voice notes, Discord) → Virtual AI (unstructured conversation → structured intents) → OpenClaw orchestrator ↔ Client DB (SQLite), enriched by hyper-local market data, with scheduled briefs pushed back to the agent and a live dashboard reading the DB](docs/images/pitch-architecture.png)

<details>
<summary>Original whiteboard sketch this grew from</summary>

![Architecture sketch: Client text UI → Virtual AI assistant → OpenClaw harness ↔ Client DB (SQLite), with scheduled reminders flowing back to the UI](docs/images/architecture-sketch.png)

</details>

## One-command startup (dev, mock agent)

```bash
bash scripts/dev.sh
```

Seeds the database and starts the backend (http://localhost:8000, mock agent mode) + dashboard (http://localhost:5173). API docs at http://localhost:8000/docs.

## Production on the GB10 (real agent)

```bash
bash scripts/serve.sh
```

Builds the dashboard and serves the whole product from **one port**: `http://<gb10-tailscale-name>:8080` (`:8000` on the GB10 belongs to the vLLM server). Runs with `AGENT_MODE=openclaw`, relaying chat to the OpenClaw gateway on `:18789`. Full wiring, skill install, and verification checklist: [`docs/GB10-SETUP.md`](docs/GB10-SETUP.md). (`scripts/gb10.sh` still works as a compat shim.)

## What's in the dashboard

![Real analytics. Period. — the dashboard concept: KPI strip, sales funnel with per-stage conversion, lead source performance, stage velocity, top opportunities, demand by area, next-best actions, and the chat rail](docs/images/pitch-dashboard.png)

- `/` — the dashboard: live KPI strip in the navbar, sales funnel (with derived Qualified / Offers Submitted stages), and the deterministic insights engine (`dashboard/src/insights.ts` — computed from DB data, never LLM-invented)
- `/leads` — prioritized inbox with the "Needs attention" neglect section
- `/scan` — business-card capture with camera relay into lead intake
- `/lead/:id` — profile: persona chip + AI relationship summary, activity timeline, follow-up draft with closed-loop "Mark as sent", booking, client-safe export, merge review
- `/activity` — agent audit stream (every tool call), reachable from the dev icon in the navbar
- Global resizable **chat rail** — sessions, markdown rendering, `[Name](lead:12)` links into profiles
- **Daily summary overlay** — auto-opens once per day; "↻ Refresh now" asks the agent (chat session `summary-trigger`) to re-run research and repost

## Manual setup

```bash
# backend (Python 3.11+)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python backend/seed.py --demo
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

# dashboard (Node 20+), separate terminal
cd dashboard && npm install && npm run dev
```

## Who owns what

| Person | Owns | Start here |
|---|---|---|
| **K** | Agent & local inference on the GB10 (OpenClaw, Qwen 3.6 35B-A3B, skills, prompts, cron) | `skills/` — one directory per skill (`crm-db-operations/` = `SKILL.md` + `tools.py`, plus `business-card-scanner/` and `daily-command-center/`); `prompts/`. The backend relay you answer through is `backend/app/agent/openclaw.py`; mock behavior to replace is `backend/app/agent/mock.py` |
| **Toby** | Backend, SQLite, calendar & business logic | `backend/` — schema in `backend/schema.sql`, scoring weights in `app/scoring.py`, seed data in `seed.py` |
| **Johaan** | Dashboard, integration, demo & pitch | `dashboard/src/` — typed API client in `src/api.ts` |

The contract (`docs/CONTRACT.md`) is frozen — breaking changes go through an issue/PR discussion, per `CONTRIBUTING.md` (the old "needs all three of us" rule is retired now that one person maintains all workstreams).

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `AGENT_MODE` | `mock` | `mock` (dev, no GB10 needed) or `openclaw` (relay to the GB10) |
| `AGENT_GATEWAY_URL` | `http://gb10:18789` | OpenClaw gateway; `scripts/serve.sh` overrides to `http://localhost:18789` since backend and gateway share the box |
| `AGENT_GATEWAY_TOKEN` | — | Gateway bearer token — only needed if K enables token auth (currently `gateway.auth.mode: "none"`) |
| `AGENT_CHAT_PATH` | `/v1/chat/completions` | Gateway chat endpoint path |
| `DB_PATH` | `backend/data/crm.db` | SQLite location |
| `PORT` | `8080` | Serve port for `scripts/serve.sh` (dev mode uses 8000 + 5173) |
| `VITE_API_URL` | `http://localhost:8000/api` | Backend URL for the dashboard |
| `CRM_API_URL` | `http://localhost:8080/api` | Backend URL for the agent's `skills/crm-db-operations/tools.py` (`:8000` on the GB10 is vLLM, not the CRM) |
| `INTEGRATIONS_MODE` | `off` | `off` (demo-safe, simulated) or `live` (real Gmail + Google Calendar via Composio) |
| `COMPOSIO_API_KEY` | — | Composio project API key (`ak_…`) — create one at https://app.composio.dev → project settings → API keys. Not needed with `COMPOSIO_TRANSPORT=cli` |
| `COMPOSIO_TRANSPORT` | `api` | `api` (REST, needs `COMPOSIO_API_KEY`) or `cli` (shell out to the GB10's logged-in `composio` CLI — managed OAuth, no key; the CLI's `uak_` session key does NOT work with the REST API) |
| `COMPOSIO_USER_ID` | `default` | Composio connected-account user id |
| `GCAL_TIMEZONE` | `America/Los_Angeles` | Timezone for created calendar events |

Everyone develops locally in mock mode. For integration tests, point `VITE_API_URL` / `AGENT_GATEWAY_URL` at the GB10 over Tailscale.

## Docs index

- [`docs/OpenHouse-Pitch.pdf`](docs/OpenHouse-Pitch.pdf) — the pitch deck: vision, problem, architecture, demo story
- [`docs/CONTRACT.md`](docs/CONTRACT.md) — frozen schema / API / agent tools (source of truth)
- [`TODO.md`](TODO.md) — post-MVP work, tracked by owner
- [`docs/GB10-SETUP.md`](docs/GB10-SETUP.md) — hosting the product on the GB10
- [`docs/INSIGHTS.md`](docs/INSIGHTS.md) — deterministic insights engine
- [`docs/BRIEFING-UI.md`](docs/BRIEFING-UI.md), [`docs/FUNNEL-UI.md`](docs/FUNNEL-UI.md) — briefing & funnel design docs (nav has since merged into `/`)
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — approved specs (Google Calendar + Gmail integration)
- [`PLAN.md`](PLAN.md) — original hackathon plan (historical)

## Demo helpers

- `python backend/seed.py --demo` — reset to the 15-lead demo dataset (Sarah Chen's un-merged duplicate is leads #1/#2); bare `python backend/seed.py` seeds an empty schema (clean install)
- `POST /api/demo/advance-time {"days":3}` — backdate activity so the neglected-lead check fires on stage
