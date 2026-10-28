#!/usr/bin/env bash
# One-command startup: seed + backend (:8000) + dashboard (:5173). Ctrl-C stops both.
set -e
cd "$(dirname "$0")/.."
source scripts/load-env.sh
load_repo_env .env

case "${1:-}" in
  --demo)
    export OHI_DEMO_MODE=1 AGENT_MODE=mock INTEGRATIONS_MODE=off INTEGRATIONS_POLLER=off HOST=127.0.0.1
    unset OHI_API_TOKEN OHI_AGENT_API_TOKEN
    export DB_PATH="$PWD/backend/data/demo.db"
    ;;
  '') ;;
  *) echo 'Usage: bash scripts/dev.sh [--demo]' >&2; exit 2 ;;
esac
export DB_PATH="${DB_PATH:-$PWD/backend/data/crm.db}"

# Reject unsafe authentication before dependency installs, seeding, or services.
python3 scripts/check_auth.py

[ -d .venv ] || python3 -m venv .venv
# always sync deps — a new requirements.txt line must not ImportError-crash
# an existing dev venv (pip is a fast no-op when everything is satisfied)
.venv/bin/pip install -q -r backend/requirements.txt
if [ "${OHI_DEMO_MODE:-}" = 1 ]; then
  [ -f "$DB_PATH" ] || .venv/bin/python backend/seed.py --demo
else
  [ -f "${DB_PATH:-backend/data/crm.db}" ] || .venv/bin/python backend/seed.py
fi

BACKEND_PID=
DASH_PID=
trap 'kill $BACKEND_PID $DASH_PID 2>/dev/null' EXIT INT TERM

(cd backend && ../.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --no-proxy-headers --port 8000) &
BACKEND_PID=$!

if [ ! -d dashboard/node_modules ]; then
  (cd dashboard && npm install --no-fund --no-audit)
fi
(cd dashboard && npm run dev) &
DASH_PID=$!

echo "Backend: http://localhost:8000  |  Dashboard: http://localhost:5173"
wait
