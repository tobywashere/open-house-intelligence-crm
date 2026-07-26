# Open House Intelligence

A local-first real estate sales agent that manages leads from first contact through booked appointment. All inference runs locally on the Dell Pro Max GB10 — client PII never leaves the machine.

**Read [`PLAN.md`](PLAN.md) for the full plan and [`docs/CONTRACT.md`](docs/CONTRACT.md) for the frozen schema/API/tool contract before writing code.**

## Architecture

![Architecture sketch: Client text UI → Virtual AI assistant → OpenClaw harness ↔ Client DB (SQLite), with scheduled reminders flowing back to the UI](docs/images/architecture-sketch.png)

## One-command startup

```bash
bash scripts/dev.sh
```

Seeds the database and starts the backend (http://localhost:8000, mock agent mode) + dashboard (http://localhost:5173). API docs at http://localhost:8000/docs.

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
| **K** | Agent & local inference on the GB10 (OpenClaw, Ollama, skills, prompts, cron) | `agent/`, `backend/app/agent/openclaw.py` is your relay target; mock behavior to replace is in `backend/app/agent/mock.py` |
| **Toby** | Backend, SQLite, calendar & business logic | `backend/` — schema in `backend/schema.sql`, scoring weights in `app/scoring.py`, seed data in `seed.py` |
| **Johaan** | Dashboard, integration, demo & pitch | `dashboard/src/` — typed API client in `src/api.ts` |

The contract (`docs/CONTRACT.md`) is frozen — breaking changes need all three of us.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `AGENT_MODE` | `mock` | `mock` (dev, no GB10 needed) or `openclaw` (relay to the GB10) |
| `AGENT_GATEWAY_URL` | `http://gb10:18789` | OpenClaw gateway (GB10's Tailscale hostname) |
| `AGENT_GATEWAY_TOKEN` | — | Gateway bearer token |
| `AGENT_CHAT_PATH` | `/v1/chat/completions` | Gateway chat endpoint path |
| `DB_PATH` | `backend/data/crm.db` | SQLite location |
| `VITE_API_URL` | `http://localhost:8000/api` | Backend URL for the dashboard |

Everyone develops locally in mock mode. For integration tests, point `VITE_API_URL` / `AGENT_GATEWAY_URL` at the GB10 over Tailscale.

## Demo helpers

- `python backend/seed.py` — reset to the 15-lead demo dataset (Sarah Chen's un-merged duplicate is leads #1/#2)
- `POST /api/demo/advance-time {"days":3}` — backdate activity so the neglected-lead check fires on stage
