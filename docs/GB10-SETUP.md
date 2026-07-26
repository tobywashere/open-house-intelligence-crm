# GB10 setup — dashboard + backend + OpenClaw on one box

Target state: `http://<gb10>:8080` (over Tailscale) serves the dashboard; the same
backend relays chat to OpenClaw on `:18789`; OpenClaw's Qwen 3.6 35B-A3B uses the
[db-operations skill](../skills/db-operations-skill.md) to read/write the CRM.

```
browser (any teammate, via Tailscale)
   → GB10 :8080  FastAPI  — serves dashboard build + /api + SQLite
        ↕ chat relay (Bearer token)
     GB10 :18789 OpenClaw gateway — Qwen 3.6 35B-A3B
        ↳ skill: skills/tools.py → http://localhost:8080/api
```

## 1. One-time (K owns the OpenClaw half)

1. **Tailscale**: `tailscale up` — note the machine name (below: `gb10`).
2. **OpenClaw + model**: install OpenClaw, point it at the local Qwen 3.6 35B-A3B
   endpoint, note the gateway token. Discord channel: enable in OpenClaw config
   (that's the second chat surface — same agent, zero extra work for us).
3. **Install the CRM skill** into the OpenClaw workspace:
   ```bash
   mkdir -p ~/.openclaw/skills/openhouse-crm
   cp skills/db-operations-skill.md ~/.openclaw/skills/openhouse-crm/SKILL.md
   cp skills/tools.py               ~/.openclaw/skills/openhouse-crm/tools.py
   # tools.py needs to reach the backend on the same box:
   export CRM_API_URL=http://localhost:8080/api
   ```
   (Adjust the skills dir to wherever this OpenClaw version loads skills from;
   the skill file itself is written to be drop-in.)

## 2. Run the product (every boot / demo)

```bash
bash scripts/gb10.sh
```

The GB10's gateway currently runs `gateway.auth.mode: "none"`, so no token is needed. If K
ever enables token auth, export it first — it lives in `~/.openclaw/openclaw.json` under
`gateway.auth.token`:
`export AGENT_GATEWAY_TOKEN=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')))['gateway']['auth']['token'])")`

That script: venv + deps → seed if no DB → `npm run build` → uvicorn on
`0.0.0.0:8080` (`PORT` env to change; :8000 belongs to vLLM) with `AGENT_MODE=openclaw`. The backend serves the built
dashboard itself — **one port, one URL: `http://gb10:8080`**.

## 3. Verify the wiring (in order — each step isolates one link)

| Check | Expect |
|---|---|
| `curl localhost:8080/api/health` | `{"ok":true,"agent_mode":"openclaw","agent_connected":true}` |
| `CRM_API_URL=http://localhost:8080/api python3 -c "import sys;sys.path.insert(0,'skills');import tools;print(tools.list_leads()[0]['name'])"` | a lead name — the skill reaches the backend |
| Dashboard chat: "who needs a follow-up?" | a real Qwen answer grounded in tool calls (watch them land in Agent activity) |
| Discord: same question | same behavior, same audit trail |
| Header badge | green pulse: "Qwen 3.6 35B-A3B · Local on Dell GB10 / Cloud LLM requests: 0" |

If chat 401s → token mismatch. If the agent answers but invents data → the skill
isn't loaded (check the OpenClaw workspace path). If `agent_connected:false` →
gateway URL/port (`AGENT_GATEWAY_URL`, default `http://localhost:18789`).

## Dev machines (unchanged)

Everyone else keeps `bash scripts/dev.sh` (mock mode). To point a local dashboard
at the GB10 instead: `VITE_API_URL=http://gb10:8080/api npm run dev`.
