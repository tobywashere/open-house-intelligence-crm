# Mac mini setup

This guide is for a nontechnical operator who wants the CRM and local AI on
one Mac mini.

## Before you start

Minimum supported host:

- Apple-silicon Mac mini (M-series)
- 16 GB unified memory
- macOS with at least 25 GB of free disk space
- Git, Python 3.11+, and Node.js 20+
- OpenClaw with a tool-capable model

Sixteen gigabytes is enough for the CRM plus a modest quantized model. Larger
models may need 32 GB or more. The model and audio provider are configured in
OpenClaw, not in this repository.

## 1. Install the basic tools

Open **Terminal** and check each command:

```bash
git --version
python3 --version
node --version
openclaw --version
```

If one is missing, install it using the vendor's current macOS instructions.
For OpenClaw, complete its setup wizard and verify that it can answer a basic
prompt before continuing. The relevant upstream references are its
[Chat Completions endpoint](https://docs.openclaw.ai/gateway/openai-http-api)
and [audio inference CLI](https://docs.openclaw.ai/cli/infer).

## 2. Download the CRM

```bash
cd ~/Documents
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

The included Mac-safe settings use localhost: the CRM, OpenClaw gateway, and
browser all remain on the same machine.

## 3. Enable OpenClaw chat access

OpenClaw's Chat Completions endpoint is disabled by default in some versions.
Enable it with:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Follow the restart hint printed by OpenClaw. Then verify the endpoint:

```bash
curl -X POST http://localhost:18789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"Reply with READY"}]}'
```

A real JSON completion means the endpoint works. A `404` means it is still
disabled. A `401` means the gateway requires a token; put that token in
`AGENT_GATEWAY_TOKEN` in `.env`.

Keep the gateway on localhost unless you understand the security impact. A
valid gateway credential provides broad operator-level access, not a
restricted end-user session.

## 4. Install the CRM skills

```bash
mkdir -p ~/.openclaw/skills
cp -R skills/crm-db-operations ~/.openclaw/skills/
cp -R skills/business-card-scanner ~/.openclaw/skills/
cp -R skills/daily-command-center ~/.openclaw/skills/
```

Keep those directory names. The scanner depends on the
`crm-db-operations` path.

Make sure the OpenClaw process has:

```text
CRM_API_URL=http://localhost:8080/api
```

How environment variables are attached to OpenClaw depends on how you start
it. If it runs as a macOS service, add the value to that service's
environment rather than typing `export` after every restart.

## 5. Check `.env`

Use these important local values:

```dotenv
AGENT_MODE=openclaw
AGENT_GATEWAY_URL=http://localhost:18789
AGENT_CHAT_PATH=/v1/chat/completions
PORT=8080
HOST=127.0.0.1
CRM_API_URL=http://localhost:8080/api
VOICE_TRANSCRIBE_COMMAND=openclaw
```

If `.env` still says `AGENT_MODE=mock`, change it to `openclaw`.

Do not set `HOST=0.0.0.0` just to make setup easier. Localhost is the safe
default. See [Network access](#network-access-from-another-device) only if
you need another device to reach the CRM.

## 6. Verify voice transcription

The app uses OpenClaw's supported file-transcription command and has no cloud
fallback:

```bash
openclaw infer audio transcribe --file /path/to/a-short-recording.m4a --json
```

You must see JSON containing a non-empty `text` or `transcript` value. If your
audio model is not the OpenClaw default, add its exact `provider/model` name
to `.env`:

```dotenv
VOICE_TRANSCRIBE_MODEL=provider/model
```

Whether transcription is fully local depends on that OpenClaw provider. Check
the provider configuration before using client audio.

## 7. Start the product

```bash
bash scripts/serve.sh
```

The first run installs project dependencies and builds the dashboard. When
you see the startup banner, open
[http://localhost:8080](http://localhost:8080).

Leave that Terminal window open while using the CRM. Press **Control-C** to
stop it.

## 8. Verify the main features

In a second Terminal:

```bash
cd ~/Documents/open-intelligence-crm
python3 scripts/doctor.py
python3 scripts/doctor.py --live-agent
```

Then check these in the browser:

1. The header says the local agent endpoint is enabled or verified.
2. Ask the agent to add a disposable test lead, then confirm it appears in
   **Leads**.
3. Ask it to update the lead and schedule a reminder.
4. Check availability and book a disposable appointment.
5. Open **Add voice note**, record a short note, review its transcript, and
   cancel. Confirm no lead was added.
6. Repeat and explicitly add the lead.
7. Close one disposable lead as **Won** and another as **Lost**. Only the win
   should count in the dashboard close rate.
8. Open **Daily summary**. Appointments and follow-ups must match the CRM.
   If market research has not been published, it must say that instead of
   showing sample news.

This is the target-hardware acceptance checklist. Automated tests verify the
software boundary, but microphone permissions, model choice, and inference
speed must be checked on the actual Mac.

## Daily briefing and market summary

The morning CRM section is always rebuilt from real appointments, lead rows,
and due reminders. The `daily-command-center` skill may add bounded
preparation suggestions for real appointments; it cannot replace names,
times, scores, or other CRM facts.

Market news is separate. It appears only after an agent workflow posts a
daily summary whose articles have valid source URLs. With no published
summary, the dashboard shows an honest empty state.

## Network access from another device

The safest option is a private network such as Tailscale. Set `HOST` to the
Mac's private Tailscale address, not `0.0.0.0`, and set a long random value in
both:

```dotenv
OHI_API_TOKEN=replace-with-a-long-random-secret
VITE_API_TOKEN=replace-with-the-same-secret
```

Also add the exact dashboard origin to `CORS_ORIGINS` when needed. Restart
`scripts/serve.sh` after changing `.env`.

Do not expose the CRM or OpenClaw gateway directly to the public internet.

## Back up and update

Stop the app with **Control-C**, then copy the database:

```bash
cp backend/data/crm.db ~/Desktop/openhouse-crm-backup.db
```

Update only after making a backup:

```bash
git pull
bash scripts/serve.sh
```

## Optional Gmail and Google Calendar

These integrations use Composio and the internet. They are off by default.
When enabled, the necessary client data is sent to the configured services.
See [the general local-AI guide](LOCAL-AI.md#optional-internet-services).

## If something fails

- **Endpoint disabled**: repeat step 3 and follow OpenClaw's restart hint.
- **Unauthorized**: set the matching `AGENT_GATEWAY_TOKEN`.
- **OpenClaw unreachable**: confirm its gateway is running on port 18789.
- **Voice failed**: run the direct transcription command in step 6.
- **No CRM skill actions**: verify the three skill folders and
  `CRM_API_URL`.
- **No market summary**: configure the summary/research workflow; missing
  content is intentionally not replaced with a fabricated sample.
- **Port already in use**: choose another `PORT` in `.env`, restart, and use
  the new localhost URL.
