# GB10 setup

This is the worked example for the original Dell Pro Max GB10 deployment.
For Apple silicon, use [MAC-MINI-SETUP.md](MAC-MINI-SETUP.md). For other
hosts, use [LOCAL-AI.md](LOCAL-AI.md).

Target layout:

```text
private browser
  → GB10 :8080  CRM dashboard + API + SQLite
      → localhost:18789  OpenClaw gateway
          → configured local model
      ← crm-db-operations tools call localhost:8080/api
```

The original model server used port 8000, so the CRM uses port 8080.

## One-time setup

1. Install OpenClaw and configure the local tool-capable model.
2. Enable the Chat Completions endpoint:

   ```bash
   openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
   openclaw config validate
   ```

3. Install the skills:

   ```bash
   mkdir -p ~/.openclaw/skills
   cp -R skills/crm-db-operations ~/.openclaw/skills/
   cp -R skills/business-card-scanner ~/.openclaw/skills/
   cp -R skills/daily-command-center ~/.openclaw/skills/
   cp -R skills/daily-brief ~/.openclaw/skills/
   ```

4. Give the OpenClaw process:

   ```text
   CRM_API_URL=http://localhost:8080/api
   ```

5. Copy the application settings:

   ```bash
   cp .env.example .env
   ```

   Set:

   ```dotenv
   AGENT_MODE=openclaw
   AGENT_GATEWAY_URL=http://localhost:18789
   AGENT_CHAT_PATH=/v1/chat/completions
   PORT=8080
   ```

6. Test voice on the actual GB10:

   ```bash
   openclaw infer audio transcribe --file /path/to/test.m4a --json
   ```

   If needed, set `VOICE_TRANSCRIBE_MODEL=provider/model` in `.env`.

## Start

```bash
bash scripts/serve.sh
```

Open `http://localhost:8080` on the machine.

For Tailscale access, bind `HOST` to the GB10's exact Tailscale address and
set the same long secret in `OHI_API_TOKEN` and `VITE_API_TOKEN`. Do not bind
the CRM or OpenClaw gateway publicly.

## Verify

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --live-agent
```

Then verify:

1. Natural-language create, update, reminder, and booking.
2. Card scan review before create.
3. Voice transcript and field review before any write.
4. Explicit won/lost close behavior.
5. Morning schedule matches actual CRM appointments.
6. Missing market research stays missing rather than displaying sample news.

If the gateway is reachable but chat does not work, test
`POST /v1/chat/completions` directly. A `404` means the endpoint is disabled;
`401`/`403` means the gateway token does not match.

If chat always returns the canned “The agent didn't answer in time” fallback
despite `agent_connected:true`, check the endpoint first: that health field
only proves the gateway process is reachable. If the agent answers but invents
CRM data, verify that the `crm-db-operations` skill is loaded from the expected
OpenClaw workspace. If `agent_connected:false`, check
`AGENT_GATEWAY_URL` (default `http://localhost:18789`) and confirm that the
gateway process is running.

## Optional Google integrations

Gmail and Google Calendar use Composio and the internet:

```dotenv
INTEGRATIONS_MODE=live
COMPOSIO_TRANSPORT=api
COMPOSIO_API_KEY=replace-with-project-key
COMPOSIO_USER_ID=default
GCAL_TIMEZONE=America/Los_Angeles
```

Leave `INTEGRATIONS_MODE=off` for a local-only or stage-safe deployment.
Enable `INTEGRATIONS_POLLER=on` separately only if automatic reading of the
connected mailbox is intended.

The header reports integrations as off, configured, verified, or failed.
Non-idempotent sends and calendar creates are not automatically replayed
after an ambiguous timeout.
