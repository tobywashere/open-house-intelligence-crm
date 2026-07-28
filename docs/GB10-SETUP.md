> Example deployment on our original demo hardware — the generic,
> hardware-agnostic guide is [`docs/LOCAL-AI.md`](LOCAL-AI.md).

# GB10 setup — dashboard + backend + OpenClaw on one box

Target state: `http://<gb10>:8080` (over Tailscale) serves the dashboard; the same
backend relays chat to OpenClaw on `:18789`; OpenClaw's Qwen 3.6 35B-A3B uses the
[db-operations skill](../skills/crm-db-operations/SKILL.md) to read/write the CRM.

```
browser (any teammate, via Tailscale)
   → GB10 :8080  FastAPI  — serves dashboard build + /api + SQLite
        ↕ chat relay (Bearer token)
     GB10 :18789 OpenClaw gateway — Qwen 3.6 35B-A3B
        ↳ skill: skills/crm-db-operations/tools.py → http://localhost:8080/api
```

## 1. One-time (K owns the OpenClaw half)

1. **Tailscale**: `tailscale up` — note the machine name (below: `gb10`).
2. **OpenClaw + model**: install OpenClaw, point it at the local Qwen 3.6 35B-A3B
   endpoint, note the gateway token. Discord channel: enable in OpenClaw config
   (that's the second chat surface — same agent, zero extra work for us).
3. **Install the skills** into the OpenClaw skills dir. Each skill is a directory
   holding a `SKILL.md` with `name`/`description` frontmatter — copy them whole and
   keep the directory names, since `business-card-scanner` imports `tools.py` from
   `~/.openclaw/skills/crm-db-operations` by path:
   ```bash
   cp -r skills/crm-db-operations \
         skills/business-card-scanner \
         skills/daily-command-center  ~/.openclaw/skills/
   # tools.py needs to reach the backend on the same box:
   export CRM_API_URL=http://localhost:8080/api
   ```
   (Adjust the skills dir if this OpenClaw version loads skills from elsewhere; the
   skills themselves are drop-in. Renaming `crm-db-operations` breaks the scanner's
   import path.)

## 2. Run the product (every boot / demo)

```bash
bash scripts/serve.sh
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
| `CRM_API_URL=http://localhost:8080/api python3 -c "import sys;sys.path.insert(0,'skills/crm-db-operations');import tools;print(tools.list_leads()[0]['name'])"` | a lead name — the skill reaches the backend |
| Dashboard chat: "who needs a follow-up?" | a real Qwen answer grounded in tool calls (watch them land in Agent activity) |
| Discord: same question | same behavior, same audit trail |
| Header badge | green pulse: "Local agent · live" |

If chat 401s → token mismatch. If the agent answers but invents data → the skill
isn't loaded (check the OpenClaw workspace path). If `agent_connected:false` →
gateway URL/port (`AGENT_GATEWAY_URL`, default `http://localhost:18789`).

## Dev machines (unchanged)

Everyone else keeps `bash scripts/dev.sh` (mock mode). To point a local dashboard
at the GB10 instead: `VITE_API_URL=http://gb10:8080/api npm run dev`.

## Google integrations (Gmail + Calendar)

The app itself calls Composio — nothing to install on the GB10 beyond env vars.
In the same env file `scripts/serve.sh` loads, set:

```bash
INTEGRATIONS_MODE=live
COMPOSIO_API_KEY=<Composio project API key (ak_…)>
COMPOSIO_USER_ID=<connected-account user id (usually 'default')>
GCAL_TIMEZONE=America/Los_Angeles
```

To create the Composio API key: log into https://app.composio.dev → project settings → API keys → create a new project API key (starts with `ak_`).

Leave `INTEGRATIONS_MODE` unset (= `off`) for the stage demo: every Google
action is then simulated, audited, and requires no network. The header chip
shows "● Google live" / "○ Google off" so you always know which mode you're in.
The reply poller (every 5 min) and GCal busy-filtering only run in live mode.
K's agent needs no changes: its existing REST tool calls (create_lead,
book_appointment, schedule_followup) fire the Google hooks automatically.

### Group-chat note (paste-ready)

```
Additive changes heads-up (no contract breakage):
1. Two new columns, auto-migrated: appointments.gcal_event_id, reminders.gcal_event_id
2. Two new endpoints: POST /api/email/send {lead_id,subject,body} and GET /api/integrations/status
3. New event-type convention: events.type='email' for sent mail + replies (no CHECK constraint, same trick as the offer events); reply dedupe marker [gmail:<id>] in content
4. Behavior: when INTEGRATIONS_MODE=live on the GB10, create_lead / book_appointment / schedule_followup ALSO create Google Calendar events (+ a Gmail intro draft for leads with email). Off by default; off mode simulates + audits only. K: zero agent changes needed — your existing tool calls trigger it.
5. New source convention: leads.source='email' for leads auto-intaken from the Gmail inbox poller (unknown sender → raw_text pipeline → extracted lead). Replies from known leads also re-run /process so new info (budget etc.) updates fields + score.
```
