#!/usr/bin/env bash
# GB10 production launcher: builds the dashboard, seeds the DB if missing, and
# serves everything on one port with the real OpenClaw agent.
# The whole product is then at http://<gb10-tailscale-name>:$PORT (default 8080;
# 8000 on the GB10 belongs to the vLLM server that backs the agent).
# Binds to 127.0.0.1 by default — nothing outside this machine can reach it.
# For GB10/Tailscale deployments, set HOST=<tailscale-ip> to opt in to
# binding a network-reachable interface (pair with OHI_API_TOKEN).
set -e
cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
# AGENT_MODE=mock bash scripts/serve.sh → full hosted product with the mock agent
# (useful for testing the GB10 deployment before OpenClaw is configured)
export AGENT_MODE="${AGENT_MODE:-openclaw}"
export AGENT_GATEWAY_URL="${AGENT_GATEWAY_URL:-http://localhost:18789}"
# AGENT_GATEWAY_TOKEN is only needed when the gateway runs with
# gateway.auth.mode = "token"/"password". On the GB10 it is "none", so unset is fine.

[ -d .venv ] || python3 -m venv .venv
# always sync deps — a new requirements.txt line must not ImportError-crash
# an existing install (pip is a fast no-op when everything is satisfied)
.venv/bin/pip install -q -r backend/requirements.txt
[ -f backend/data/crm.db ] || .venv/bin/python backend/seed.py

if [ ! -d dashboard/node_modules ]; then
  (cd dashboard && npm install --no-fund --no-audit)
fi
# a broken (or partial/stale) build must not take the API down with it —
# build to a scratch dir and swap atomically, and only fall back to a
# previous dist when it's actually complete (index.html + assets/)
rm -rf dashboard/dist.new
if (cd dashboard && npm run build -- --outDir dist.new); then
  rm -rf dashboard/dist
  mv dashboard/dist.new dashboard/dist
else
  rm -rf dashboard/dist.new
  if [ -f dashboard/dist/index.html ] && [ -d dashboard/dist/assets ]; then
    echo "⚠  dashboard build FAILED — serving the previous dist" >&2
  else
    echo "✖ dashboard build failed and no complete previous dist exists" >&2
    exit 1
  fi
fi

echo "──────────────────────────────────────────────"
echo " Open House Intelligence → http://$HOST:$PORT"
echo " agent: openclaw @ $AGENT_GATEWAY_URL"
echo "──────────────────────────────────────────────"
cd backend && exec ../.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
