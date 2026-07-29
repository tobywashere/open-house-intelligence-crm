# Contributing to OpenHouse Intelligence

## Dev setup

```bash
bash scripts/dev.sh
```

This creates a `.venv`, installs backend dependencies, seeds a local SQLite
database, installs dashboard dependencies, and starts both the backend
(`:8000`) and the dashboard (`:5173`). Ctrl-C stops both.

## Running tests

Backend:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
```

Dashboard (typecheck + build):

```bash
cd dashboard && npm run build
```

Both must pass before opening a PR. CI runs the same checks (with plain
`python -m pytest`, since CI has no `.venv`).

## The contract rule

[`docs/CONTRACT.md`](docs/CONTRACT.md) is the frozen agreement on the SQLite
schema and REST API shape that the backend, agent tooling, and dashboard all
depend on. Changes to it need an issue or PR discussion before merging — it's
no longer gated on getting three specific people in a room together; anyone
proposing a change should open an issue or PR so it can be reviewed in the
open.

## Code style

Match the surrounding code — no repo-wide style is enforced by CI yet.
Skills under `skills/` stay stdlib-only: no third-party dependencies, since
they need to run in constrained agent environments.

## Where things live

- `backend/` — FastAPI app, SQLite schema (`schema.sql`), and tests.
- `dashboard/` — React + TypeScript + Vite frontend.
- `skills/` — stdlib-only tools used by the agent (e.g. business card
  scanning, CRM DB operations, composio email/calendar integration).
- `docs/` — design docs, the API/schema contract, and setup guides.
