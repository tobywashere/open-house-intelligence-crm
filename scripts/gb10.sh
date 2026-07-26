#!/usr/bin/env bash
# GB10 production launcher: builds the dashboard, seeds the DB if missing, and
# serves everything on one port with the real OpenClaw agent.
# The whole product is then at http://<gb10-tailscale-name>:8000
set -e
cd "$(dirname "$0")/.."

# AGENT_MODE=mock bash scripts/gb10.sh → full hosted product with the mock agent
# (useful for testing the GB10 deployment before OpenClaw is configured)
export AGENT_MODE="${AGENT_MODE:-openclaw}"
export AGENT_GATEWAY_URL="${AGENT_GATEWAY_URL:-http://localhost:18789}"
if [ "$AGENT_MODE" = "openclaw" ] && [ -z "$AGENT_GATEWAY_TOKEN" ]; then
  echo "⚠  AGENT_GATEWAY_TOKEN is not set — chat relay will 401. export it first." >&2
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
fi
[ -f backend/data/crm.db ] || .venv/bin/python backend/seed.py

if [ ! -d dashboard/node_modules ]; then
  (cd dashboard && npm install --no-fund --no-audit)
fi
(cd dashboard && npm run build)

echo "──────────────────────────────────────────────"
echo " Open House Intelligence → http://0.0.0.0:8000"
echo " agent: openclaw @ $AGENT_GATEWAY_URL"
echo "──────────────────────────────────────────────"
cd backend && exec ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
