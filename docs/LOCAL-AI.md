# Running OpenHouse Intelligence with a real local model

`bash scripts/dev.sh` gets you the full product with a mock agent — no model,
no GPU, canned-but-realistic responses. This doc is for swapping the mock
agent for a real one: any machine running OpenClaw in front of a
tool-capable local model. Nothing here is tied to specific
hardware — the project was originally built and demoed on a Dell GB10 running
Qwen 3.6 35B-A3B (see [`docs/GB10-SETUP.md`](GB10-SETUP.md) for that exact
deployment as a worked example), but any local model that OpenClaw can drive
and that supports tool/function calling will work.

## 1. Install OpenClaw and point it at a local model

This repo doesn't ship or install OpenClaw — follow the OpenClaw project's
own install docs for your platform first. Once it's installed, configure its
gateway to serve an OpenAI-compatible chat endpoint backed by your local
model. The shape of that config (adjust to whatever OpenClaw version/schema
you're on — check `openclaw --help` or its own docs for the authoritative
reference):

```jsonc
// ~/.openclaw/openclaw.json (illustrative — field names may differ by version)
{
  "model": {
    "endpoint": "http://localhost:8001/v1",   // your local inference server (vLLM, llama.cpp, etc.)
    "name": "<your-local-model-name>"          // e.g. "qwen3.6-35b-a3b" — any tool-capable model
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18789,
    "auth": { "mode": "none" }                 // set "token" and add a token for anything network-reachable
  }
}
```

Any tool-capable local model works — the project was built and demoed
against Qwen 3.6 35B-A3B, but there's nothing Qwen-specific in the app; the
agent talks to OpenClaw's OpenAI-compatible `/v1/chat/completions` endpoint
(`AGENT_CHAT_PATH` in `.env.example`) and OpenClaw handles the model
underneath.

## 2. Install the CRM skill

The skills live in this repo under `skills/`. Copy them into OpenClaw's
skills directory so the model can find them (per
[`docs/GB10-SETUP.md`](GB10-SETUP.md) §1, which this mirrors):

```bash
cp -r skills/crm-db-operations \
      skills/business-card-scanner \
      skills/daily-command-center  ~/.openclaw/skills/
```

Copy all three together and keep the directory names — `business-card-scanner`
imports `tools.py` from `crm-db-operations` by path, so renaming or splitting
them up breaks that import. (`composio-email-calendar` is optional — see
§5 below.)

The skill needs to reach this app's backend over HTTP:

```bash
export CRM_API_URL=http://localhost:8080/api
```

Set that in whatever environment OpenClaw's gateway process runs under (a
service env file, not something you export by hand each boot). If the
backend and the model server are on different machines, point it at the
backend's real address instead of `localhost`.

## 3. Run the product

```bash
bash scripts/serve.sh
```

This builds the dashboard and serves the whole product from one port
(`AGENT_MODE=openclaw`, default `:8080`), relaying chat to the OpenClaw
gateway on `:18789`. Binds to `127.0.0.1` unless you set `HOST` — see
`.env.example` before exposing it beyond localhost (`OHI_API_TOKEN` gates the
API once `HOST` is network-reachable).

If this is the first run, `serve.sh` seeds the database only if it's
missing — and a fresh seed is **schema-only, no leads**, since that's the
correct starting point for real use. If you want the 15-lead demo dataset to
explore the product with (what §4 below assumes), seed it explicitly before
or after your first `serve.sh` run:

```bash
.venv/bin/python backend/seed.py --demo
```

## 4. Verify the wiring

Work through these in order — each one isolates a different link in the chain.

| Check | Expect |
|---|---|
| `curl localhost:8080/api/health` | `{"ok":true,"agent_mode":"openclaw","agent_connected":true}` |
| `CRM_API_URL=http://localhost:8080/api python3 -c "import sys;sys.path.insert(0,'skills/crm-db-operations');import tools;print(tools.list_leads()[0]['name'])"` | a lead name — the skill reaches the backend |
| Dashboard chat: "who needs a follow-up?" | a real answer from your model, grounded in tool calls (watch them land in `/activity`) |
| Header badge | green pulse reading "Local agent · live" |

If chat 401s → gateway token mismatch (`AGENT_GATEWAY_TOKEN`). If the agent
answers but invents data → the skill isn't loaded (check the OpenClaw skills
path). If `agent_connected:false` → check `AGENT_GATEWAY_URL` (default
`http://localhost:18789`) and that the gateway process is actually up.

## 5. Optional, needs internet

Everything above is fully offline. Two pieces of the product reach the
internet, and both are off unless you explicitly turn them on:

- **Gmail + Google Calendar (Composio integrations)** — sending real email,
  creating real calendar events, and the inbound-email lead poller.
  Controlled by `INTEGRATIONS_MODE` in `.env.example`, default `off` (fully
  simulated, no network calls). The inbox poller has its own separate flag,
  `INTEGRATIONS_POLLER`, also default `off`, since it reads a real mailbox
  with no human in the loop when enabled. Setup:
  [`skills/composio-email-calendar/SKILL.md`](../skills/composio-email-calendar/SKILL.md)
  and the "Google integrations" section of
  [`docs/GB10-SETUP.md`](GB10-SETUP.md). Once live, set `AGENT_DISPLAY_NAME`
  in `.env.example` if you want AI-drafted intro emails to leads signed with
  your name — left unset, those drafts go out unsigned rather than with a
  placeholder name.
- **Market-news / daily-briefing research** — the "↻ Refresh now" action on
  the daily summary overlay asks the agent to re-run research and repost a
  briefing. This only does real research if your OpenClaw setup gives the
  model an internet-capable tool (e.g. web search); without one, or in mock
  mode, it returns static/canned content instead of live market data. There's
  no separate on/off flag for it — it's simply bounded by what tools your
  agent actually has.
