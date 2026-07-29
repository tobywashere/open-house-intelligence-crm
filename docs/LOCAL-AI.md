# Run OpenHouse Intelligence with a real local model

This is the general setup guide for OpenClaw hosts. Mac mini owners should
start with [MAC-MINI-SETUP.md](MAC-MINI-SETUP.md).

The CRM does not install OpenClaw or a model. Configure those first, then use
OpenClaw as the gateway between the CRM and your chosen model.
Current upstream references:
[Chat Completions endpoint](https://docs.openclaw.ai/gateway/openai-http-api),
[inference/audio CLI](https://docs.openclaw.ai/cli/infer), and
[configuration CLI](https://docs.openclaw.ai/cli/config).

## Requirements

- Python 3.11+
- Node.js 20+
- OpenClaw
- a tool-capable model configured in OpenClaw
- enough memory for that model (Apple silicon with 16 GB is the supported
  minimum; use a modest quantized model at that size)

The original deployment used a Dell Pro Max GB10 and Qwen 3.6 35B-A3B, but
the CRM has no GB10- or Qwen-specific dependency.

## 1. Verify OpenClaw itself

Complete OpenClaw's current setup for your model/provider. These commands
should succeed:

```bash
openclaw --version
openclaw config validate
```

The provider configured inside OpenClaw determines whether inference is
local or remote. This repository cannot make a remote provider local.

## 2. Enable the Chat Completions endpoint

The CRM calls OpenClaw at `/v1/chat/completions`. OpenClaw may keep that
endpoint disabled by default even while the gateway process is healthy.

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Follow the restart hint OpenClaw prints, then test it:

```bash
curl -X POST http://localhost:18789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"Reply with READY"}]}'
```

Expected: a JSON response containing a real assistant completion.

- `404`: the endpoint is disabled or the path is different.
- `401`/`403`: configure the matching `AGENT_GATEWAY_TOKEN`.
- connection refused: start the gateway or correct `AGENT_GATEWAY_URL`.

Treat access to this endpoint as broad operator access to the OpenClaw
instance. Keep it on a private interface and require authentication when it
is network-reachable.

## 3. Install the CRM skills

```bash
mkdir -p ~/.openclaw/skills
cp -R skills/crm-db-operations ~/.openclaw/skills/
cp -R skills/business-card-scanner ~/.openclaw/skills/
cp -R skills/daily-command-center ~/.openclaw/skills/
cp -R skills/daily-brief ~/.openclaw/skills/
```

Keep those names. The card scanner imports from
`~/.openclaw/skills/crm-db-operations`.

Give the OpenClaw process this environment value:

```text
CRM_API_URL=http://localhost:8080/api
```

The CRM skill supports natural-language reads, creates, updates, reminders,
availability checks, booking, and explicit won/lost closes. It never writes
SQL directly. Ambiguous closes must be clarified rather than guessed.

## 4. Configure the CRM

```bash
cp .env.example .env
```

For a same-machine deployment, set:

```dotenv
AGENT_MODE=openclaw
AGENT_GATEWAY_URL=http://localhost:18789
AGENT_CHAT_PATH=/v1/chat/completions
AGENT_GATEWAY_TOKEN=
HOST=127.0.0.1
PORT=8080
CRM_API_URL=http://localhost:8080/api
```

Both launchers load `.env` automatically. A value explicitly exported in the
shell wins over the file.

## 5. Configure and verify voice

Voice intake invokes this exact OpenClaw CLI surface:

```bash
openclaw infer audio transcribe --file /path/to/memo.m4a --json
```

The command must return a non-empty `text` or `transcript` field. If a
specific provider/model is required:

```dotenv
VOICE_TRANSCRIBE_COMMAND=openclaw
VOICE_TRANSCRIBE_MODEL=provider/model
VOICE_TRANSCRIBE_TIMEOUT_SECONDS=120
```

There is no cloud fallback. Whether transcription stays local depends on the
provider you configured in OpenClaw.

The application accepts WebM, Ogg, WAV, MP4/M4A, and MP3 up to 20 MB. It
checks the audio signature, writes a uniquely named temporary file, deletes
that file after transcription even on errors, and prepares an editable draft.
It does not create or update a lead until the operator confirms.

## 6. Run the product

```bash
bash scripts/serve.sh
```

This creates/synchronizes the Python environment, installs dashboard
dependencies when missing, builds the UI, initializes an empty database when
needed, and serves the application on
[http://localhost:8080](http://localhost:8080).

It does not overwrite an existing database.

## 7. Verify each boundary

Run:

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --live-agent
```

The first command is read-only. The second sends one harmless completion to
the configured chat endpoint.

Useful direct checks:

```bash
curl http://localhost:8080/api/health

CRM_API_URL=http://localhost:8080/api python3 -c \
  "import sys; sys.path.insert(0,'skills/crm-db-operations'); import tools; print(tools.list_leads())"
```

The header distinguishes mock, endpoint enabled, verified, disabled,
unauthorized, unreachable, and failed states. A reachable gateway is not
reported as a verified completion until an actual completion succeeds.

Use the visible symptom to isolate common wiring problems:

- If chat always returns the canned “The agent didn't answer in time”
  fallback despite `agent_connected:true`, test `/v1/chat/completions`
  directly. That health field proves only that the gateway process is
  reachable, not that the chat endpoint is enabled.
- If chat returns `401`/`403`, check `AGENT_GATEWAY_TOKEN`.
- If the agent answers but invents CRM data, verify that the
  `crm-db-operations` skill is loaded from the expected OpenClaw skills path.
- If `agent_connected:false`, check `AGENT_GATEWAY_URL` (default
  `http://localhost:18789`) and confirm that the gateway is running.

Then test the real workflow:

1. Ask for a new disposable lead.
2. Ask for an update and a reminder.
3. Check availability, then book a specific free slot.
4. Record a disposable voice note and cancel from the review screen; no lead
   should be written.
5. Confirm a voice note and verify the resulting lead.
6. Close one lead won and one lost; only the win counts as conversion.

## Morning CRM briefing: factual boundary

`GET /api/briefing?date=YYYY-MM-DD` is built from current SQLite records on
every request:

- schedule blocks come only from real appointments;
- names, areas, budgets, timelines, preferences, personas, and scores come
  only from the referenced lead rows;
- due actions come only from reminders or leads already marked neglected.

The `daily-command-center` skill may post preparation suggestions and a
recommendation for a real appointment. Those fields are visibly labeled as
AI suggestions. The backend ignores agent-supplied names, times, scores,
schedule blocks, and other replacement facts.

If no advice was published, the real CRM schedule still appears and the UI
says that no AI suggestions were generated.

## Daily market summary: source boundary

Market watch is a separate, optional workflow. The dashboard displays it only
after `POST /api/summary` receives a valid daily payload. Every market item
must include a valid source URL.

Missing, malformed, or unavailable summaries have different visible states.
There is no sample-news fallback. The **Refresh now** button asks the agent to
publish a newer summary; it reports success only after a newer stored
`generated_at` appears.

If you give OpenClaw a web-search tool, search queries and retrieved pages
use the internet. Without a configured publishing workflow, leave the
summary missing—the CRM will not invent one.

The `daily-brief` skill provides that publishing workflow. Its default mode
fetches the configured URLs deterministically and persists the validated
report. When explicitly asked for AI WebFetch mode, the agent fetches each
configured URL with its web tool, summarizes the results, and sends the
payload through the same validated publish-and-read-back path.

## Local knowledge

Markdown files in `docs/knowledge/` are indexed locally using the bundled
BM25 implementation. No vector database or embedding service is required.
The agent can use the `search_knowledge` CRM tool for market, tax, financing,
pricing, neighborhood, and school-district questions.

The active directory can be changed with `KNOWLEDGE_DIR`. The dashboard also
supports adding and removing Markdown knowledge files.

## Optional internet services

Gmail and Google Calendar use Composio. They are off by default:

```dotenv
INTEGRATIONS_MODE=off
INTEGRATIONS_POLLER=off
```

When enabled, necessary email/calendar data is sent to Composio and the
connected Google services. The inbound mailbox poller is a separate opt-in
because it reads a real mailbox automatically.

Market research also uses the internet when an internet-capable tool is
configured.

Do not claim that all client data stays on the machine when these services
or a remote OpenClaw provider are enabled.

## Private network access

The default `HOST=127.0.0.1` is localhost-only. To use a private Tailscale/LAN
address:

1. set `HOST` to that exact private address;
2. set a long identical value in `OHI_API_TOKEN` and `VITE_API_TOKEN`;
3. add the exact browser origin to `CORS_ORIGINS` if it differs; and
4. rebuild/restart with `bash scripts/serve.sh`.

Avoid `0.0.0.0` unless you have deliberately secured every reachable
interface. Do not expose the CRM or gateway directly to the public internet.

## Another industry

The funnel stages, field labels, copy, personas, knowledge, and research
scope come from a vertical pack. See [VERTICALS.md](VERTICALS.md).
