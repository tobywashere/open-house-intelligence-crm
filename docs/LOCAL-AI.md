# Run OpenHouse Intelligence with local AI

This guide is for Linux, a Mac mini, or another host running OpenClaw. It
explains the configuration behind the short setup in the README. Mac mini
owners can follow [MAC-MINI-SETUP.md](MAC-MINI-SETUP.md) instead.

## What is required and what is optional

Required for real local-AI mode:

- Python 3.11+, Node.js 20+, and OpenClaw
- A tool-capable model configured in OpenClaw
- The enabled `/v1/chat/completions` endpoint
- The dedicated `openhouse-crm` agent and the `crm-db-operations` skill

An Apple-silicon Mac mini with **16 GB** is the supported minimum. A modest
quantized model is appropriate at that size. Linux is supported; a GB10 is an
optional host, not a dependency.

Optional services that use the internet are Gmail, Google Calendar, public web
research, and remote model providers. They stay off unless you configure them.

## Basic setup

First, complete OpenClaw's own model/provider setup. Then enable chat access:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Follow OpenClaw's restart instructions. You can confirm the endpoint directly:

```bash
curl -X POST http://localhost:18789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"Reply with READY"}]}'
```

Then clone and set up the CRM. If you already cloned it, start at the `cd`
line instead:

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` and change its safe demo setting from `AGENT_MODE=mock` to
`AGENT_MODE=openclaw`. Then run:

```bash
python3 scripts/setup_openclaw.py
bash scripts/serve.sh
```

Keep the server running. In a second Terminal in the same project folder, run:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

The helper is safe to rerun. It creates or validates `openhouse-crm`, installs
the shipped skills in that agent's workspace, configures
`AGENT_ID=openhouse-crm`, and validates that `crm-db-operations` is eligible.
It restricts command execution to the shipped CRM wrapper and daily-brief
runner, rather than allowing a general shell command.

## Configuration details

The project reads `.env` automatically. These are the normal same-machine
values:

```dotenv
AGENT_MODE=openclaw
AGENT_GATEWAY_URL=http://localhost:18789
AGENT_CHAT_PATH=/v1/chat/completions
AGENT_ID=openhouse-crm
HOST=127.0.0.1
PORT=8080
CRM_API_URL=http://localhost:8080/api
```

Set `AGENT_GATEWAY_TOKEN` only when the OpenClaw gateway requires it. Keep
the token in `.env`, never in source control. The helper passes the CRM API URL
and, when set, the private CRM API token to the skill configuration.

The helper uses the following agent policy on purpose:

- Allowed skills include `crm-db-operations` plus the shipped card and briefing
  skills.
- `exec` runs on the gateway in allowlist mode. The only permitted executable
  entry points are the CRM wrapper and deterministic daily-brief runner.
- The dedicated agent has no broad file-editing, browser, canvas, node, or cron
  tool access.

OpenClaw's skill and configuration interfaces change over time. If the helper
reports an unsupported command or configuration shape, update OpenClaw and
rerun it rather than manually broadening the agent's permissions.

## Live checks and status

While `bash scripts/serve.sh` is running, use:

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --live-agent --live-crm
```

The first command changes nothing. `--live-agent` sends one harmless chat
completion. `--live-crm` asks the selected agent for one audited, read-only
`generate_dashboard_insights` capability call. The CRM check is successful
only after the backend sees that new agent-tagged audit record. It does not
trust the model's text alone.

Status meanings:

| Status | Meaning |
|---|---|
| `endpoint_enabled` | The gateway and chat endpoint respond, but no completion is proved yet. |
| `chat_verified` | A real chat completion succeeded. |
| `crm_verified` | A real chat completion and new audited CRM capability call succeeded. |
| `degraded` | A verified setup later failed a chat call or used a labeled fallback. |
| `unauthorized`, `unreachable`, `endpoint_disabled`, `failed` | Setup needs attention. |

## Review before applying changes

Natural-language CRM operations do not apply directly. New leads, updates,
notes, reminders, bookings, closes, merges, and deletes enter **Pending approvals**
first. The operator can edit, approve, or deny each item. Booking
availability is checked again when approval happens.

This is also true for the optional Discord binding:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Use the `ACCOUNT` identifier OpenClaw documents for your Discord account. The
dashboard remains the place to review proposed CRM changes.

## Voice notes

The app calls this OpenClaw CLI surface without a shell:

```bash
openclaw infer audio transcribe --file /path/to/memo.m4a --json
```

It needs a non-empty `text` or `transcript`. Set
`VOICE_TRANSCRIBE_MODEL=provider/model` in `.env` only if your chosen
OpenClaw provider needs it. There is no cloud fallback. The app checks the
audio signature and 20 MB size limit, removes its temporary file, and shows an
editable review before it creates or updates a lead.

## Grounded briefings and fallbacks

The morning briefing rehydrates schedule blocks, lead facts, and due actions
from current CRM rows. An agent may add labeled preparation advice but cannot
replace those facts. A market summary exists only after a valid, source-backed
daily payload is stored. Every market item needs a source URL, publication date,
summary, and geographic area. Missing information stays visibly
unavailable.

If the AI cannot extract a lead, draft a follow-up, or explain a score, the
application may use a deterministic fallback. It is visibly labeled and must
be reviewed. It is never represented as a verified local-AI response.

## Recovery

- **404 / endpoint disabled:** rerun the endpoint-enable command and restart
  the gateway.
- **401 / 403:** set the matching `AGENT_GATEWAY_TOKEN` in `.env`, then restart
  the CRM.
- **Chat verified, CRM check fails:** rerun `python3 scripts/setup_openclaw.py`.
  It checks the agent workspace, eligible skill, allowlist, and restart.
- **The agent lists generic tools only:** it is not using the dedicated agent.
  Confirm `AGENT_ID=openhouse-crm`, rerun setup, and repeat the live CRM check.
- **OpenClaw unreachable:** start the gateway and verify
  `AGENT_GATEWAY_URL`.
- **Voice failure:** run the direct transcription command above and verify the
  provider/model selected in OpenClaw.

## Linux target acceptance record

These boxes are intentionally unchecked. Complete them after a real Linux
OpenClaw run, rather than treating this documentation as a validation claim.

- [ ] OpenClaw version:
- [ ] Model/provider:
- [ ] Linux distribution/version:
- [ ] Memory:
- [ ] Date and operator:
- [ ] `--live-agent --live-crm` reports CRM capability verified
- [ ] Dashboard chat proposes a reviewed CRM write
- [ ] Voice note reaches the review screen
- [ ] Optional Discord binding, if used, reaches the same agent

For non-local access, bind the CRM to an exact private address, set matching
`OHI_API_TOKEN` and `VITE_API_TOKEN`, and update `CORS_ORIGINS`. Do not expose
the CRM or gateway directly to the public internet.
