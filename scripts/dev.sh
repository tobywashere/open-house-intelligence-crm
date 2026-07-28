#!/usr/bin/env bash
# One-command startup: seed + backend (:8000) + dashboard (:5173). Ctrl-C stops both.
set -e
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv .venv
# always sync deps — a new requirements.txt line must not ImportError-crash
# an existing dev venv (pip is a fast no-op when everything is satisfied)
.venv/bin/pip install -q -r backend/requirements.txt
[ -f backend/data/crm.db ] || .venv/bin/python backend/seed.py --demo

BACKEND_PID=
DASH_PID=
trap 'kill $BACKEND_PID $DASH_PID 2>/dev/null' EXIT INT TERM

(cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!

if [ ! -d dashboard/node_modules ]; then
  (cd dashboard && npm install --no-fund --no-audit)
fi
(cd dashboard && npm run dev) &
DASH_PID=$!

echo "Backend: http://localhost:8000  |  Dashboard: http://localhost:5173"
wait
