# OpenHouse setup and operations

[Back to the project overview](../README.md)

## Set up real local AI

This is the short, guided setup for a single machine. Before starting, install
OpenClaw and configure a tool-capable model in it. Enable its Chat Completions
endpoint once:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Then run the project commands in order:

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` in a text editor and change `AGENT_MODE=mock` to
`AGENT_MODE=openclaw`. Then continue:

```bash
python3 scripts/setup_openclaw.py
bash scripts/serve.sh
```

Keep that Terminal open. In a second Terminal, start in the directory where
you cloned the project, then run:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

Open [http://localhost:8080](http://localhost:8080). The last command is the
important proof: it checks that chat works **and** that the agent actually made
one safe, read-only CRM tool call.

The setup helper reads `AGENT_ID` from `.env`, creates that dedicated agent,
installs the `crm-db-operations` skill into its workspace, and limits its
command access to the CRM wrapper and daily-brief runner. `exec` is the agent's
only allowed tool; general web, browser, and file tools are denied. The default
is `AGENT_ID=openhouse-crm`. If you use `--agent-id`, set `AGENT_ID` to the same
value in `.env`; setup rejects a conflict so the CRM runtime cannot silently
target a different agent.

The helper prints the installed OpenClaw version for troubleshooting. Support
is capability-based, not tied to a guessed version number: setup checks the
documented commands and prerequisite policy surfaces before it changes
anything. After configuration, it reads the dedicated agent's tool policy back
from OpenClaw and reports success only when `exec` is the sole allowed tool and
its effective execution prompt is off. This lets dashboard and Discord requests
run unattended, but only through the two allowlisted CRM entry points. If a
required surface is missing, setup stops and explains what capability needs
updating.

For a Mac mini, start with [the Mac mini guide](MAC-MINI-SETUP.md). An
Apple-silicon Mac mini with **16 GB** unified memory is the minimum supported
host. It is enough for the CRM and a modest quantized model. A larger model
needs more memory and changes speed and quality. Linux hosts are supported too;
the [general OpenClaw guide](LOCAL-AI.md) explains the choices. The
[GB10 guide](GB10-SETUP.md) is an optional hardware-specific variant,
not a requirement.

## What the status means

- **Endpoint enabled** means the CRM can reach OpenClaw's chat endpoint.
- **Chat verified** means OpenClaw returned a real chat completion.
- **CRM verified** means the selected agent also made an audited, read-only
  CRM call. This is the status to look for before relying on dashboard chat.
- **Degraded** means a previously verified setup later had a chat failure or
  used a labeled deterministic fallback.

Use `python3 scripts/doctor.py` for a read-only local check. Use
`python3 scripts/doctor.py --live-agent --live-crm` while the product is
running to repeat both live checks.

## What happens when you ask for a change

Ask naturally, for example:

- “Add Taylor Brooks, looking in Bellevue around $900k.”
- “Add a note that Taylor wants a yard.”
- “Remind me Monday at 9 to call Taylor.”
- “Book Taylor for the 10:00 slot.”

The agent proposes the lead, note, reminder, booking, update, merge, close,
or deletion. It does not apply that proposal until you approve it. You can
edit a proposal before approval or deny it. For a new lead, this includes the
person's preferences; leave that box blank if you want to clear them. If a
booking slot is no longer free at approval time, it is not booked.

Voice notes follow the same rule: record or upload audio, review the
transcript and extracted details, then choose whether to add or update a lead.
Temporary audio is deleted after transcription. Card scans are also review
first.

When the AI is unavailable for extraction, follow-up drafting, or score
explanations, the interface marks its deterministic fallback. Treat it as a
draft and review it. Chat errors never silently become fabricated CRM facts.

## Daily briefing and market information

Your schedule, lead facts, and follow-ups come from the local CRM database.
The app does not fill missing appointments, client details, market news, or
recommendations with plausible samples.

Market summaries are optional. A real summary only appears after a workflow
stores source-backed items with a publication date, a short summary, and a
geographic area. If that information is missing, the dashboard says it is
unavailable. Gmail, Google Calendar, the fixed-source daily-brief runner, and
remote model providers are optional internet services and remain off unless
you configure them.

## Optional Discord

The dashboard is the primary chat experience. To use the same dedicated agent
in Discord, bind an account during setup:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Replace `ACCOUNT` with the account identifier OpenClaw expects. If setup was
already completed, use the binding command it prints. Discord uses the same
agent configuration and the same review-before-apply CRM rules.

## If something goes wrong

- **Endpoint disabled or 404:** rerun the endpoint-enable command above and
  follow OpenClaw's restart instructions.
- **OpenClaw gateway unauthorized:** put its matching token in
  `AGENT_GATEWAY_TOKEN` in `.env`, then restart `bash scripts/serve.sh`.
- **CRM API unauthorized:** make `OHI_API_TOKEN` and `VITE_API_TOKEN` match in
  `.env`. Rerun `python3 scripts/setup_openclaw.py`, then restart the app. Direct
  API commands must also send that value in `X-API-Token`.
- **Chat verified but CRM not verified:** rerun `python3 scripts/setup_openclaw.py`.
  It checks the dedicated agent, eligible `crm-db-operations` skill, and its
  restricted tool access. See [recovery steps](LOCAL-AI.md#recovery).
- **OpenClaw unreachable:** check that the gateway is running on port 18789
  and that `AGENT_GATEWAY_URL` in `.env` is correct.
- **Voice transcription failed:** run the exact command in
  [the local-AI guide](LOCAL-AI.md#voice-notes).
- **Gmail or Calendar stopped retrying:** failed jobs try five times, then stop
  for review. Use the two beginner recovery commands in
  [the local-AI guide](LOCAL-AI.md#if-gmail-or-calendar-stops-retrying).
- **No daily market summary:** this is an honest empty state, not an error the
  CRM should hide with made-up content.

## Privacy, backup, and developer references

SQLite data, local-model inference, local transcription, and the bundled
knowledge index stay on the host. Data may leave it only when you configure a
remote model provider, Composio Gmail/Calendar, or the fixed-source daily-brief
runner. Keep OpenClaw and the CRM on a private interface. Never commit `.env`,
gateway tokens, client data, recordings, or `backend/data/crm.db`.

To back up, stop the app and copy the database:

```bash
cp backend/data/crm.db ~/Desktop/openhouse-crm-backup.db
```

More detail is available in [docs/LOCAL-AI.md](LOCAL-AI.md),
[docs/CONTRACT.md](CONTRACT.md), and [CONTRIBUTING.md](../CONTRIBUTING.md).
