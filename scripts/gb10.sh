#!/usr/bin/env bash
# GB10 production launcher: builds the dashboard, seeds the DB if missing, and
# serves everything on one port with the real OpenClaw agent.
# The whole product is then at http://<gb10-tailscale-name>:$PORT (default 8080;
# 8000 on the GB10 belongs to the vLLM server that backs the agent).
set -e
cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
# AGENT_MODE=mock bash scripts/gb10.sh → full hosted product with the mock agent
# (useful for testing the GB10 deployment before OpenClaw is configured)
export AGENT_MODE="${AGENT_MODE:-openclaw}"
export AGENT_GATEWAY_URL="${AGENT_GATEWAY_URL:-http://localhost:18789}"
# AGENT_GATEWAY_TOKEN is only needed when the gateway runs with
# gateway.auth.mode = "token"/"password". On the GB10 it is "none", so unset is fine.

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
echo " Open House Intelligence → http://0.0.0.0:$PORT"
echo " agent: openclaw @ $AGENT_GATEWAY_URL"
echo "──────────────────────────────────────────────"
cd backend && exec ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
