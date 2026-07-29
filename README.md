# OpenHouse Intelligence

OpenHouse Intelligence is a local-first CRM for real-estate agents. You can
describe a lead in ordinary language, record a voice note, scan a business
card, schedule a follow-up, or book a tour. The CRM turns those actions into
organized records and shows what needs attention.

The easiest supported local-AI host is an Apple-silicon Mac mini with **16 GB
of memory or more**. The original demo ran on a Dell Pro Max GB10. A smaller
local model works on 16 GB; model speed and quality depend on the model you
choose in OpenClaw.

## What works today

| Feature | Demo mode | Real local-AI mode |
|---|---|---|
| Leads, notes, reminders, booking, duplicate review | Yes | Yes |
| Natural-language CRM reads and writes | Sample responses | Yes, after the OpenClaw chat endpoint and CRM skill are enabled |
| Voice-note intake | Requires local transcription | Yes: record/upload → local transcript → review → confirm |
| Business-card intake | Sample extraction | Yes, with the scanner skill |
| Won/lost outcomes and conversion reporting | Yes | Yes |
| CRM schedule and due follow-ups in the morning view | Real database facts only | Real database facts only |
| AI meeting-preparation suggestions | No suggestion until published | Yes, when the briefing skill publishes them |
| Market-news daily summary | Not fabricated | Only after an agent publishes a summary with valid source links |
| Gmail and Google Calendar | Simulated and labeled off | Optional; requires Composio and internet access |

The morning view never fills missing appointments, lead facts, or market
stories with plausible-looking sample content. If a source-backed summary is
missing, the interface says so.

## Five-minute demo

You need Git, Python 3.11 or newer, and Node.js 20 or newer.

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173).

Demo mode needs no model, GPU, API key, or internet connection. On the first
run it creates the local database and, when no database exists yet, loads 15
sample leads. It uses clearly labeled deterministic agent behavior so you can
try the interface safely.

Try this:

1. Open **Leads** and select a person.
2. Add a note or schedule a reminder.
3. Return to the dashboard and choose **Add voice note** to see the
   review-before-save flow. Actual transcription needs the local-AI setup.
4. Choose **Scan card** to try the card review flow.
5. On a profile, choose **Close opportunity**, then select **Won** or **Lost**.
6. Open **Daily summary**. CRM appointments and due follow-ups are real
   database facts; missing market research remains visibly missing.

To deliberately reset to the sample database:

```bash
.venv/bin/python backend/seed.py --demo
```

That command erases the current CRM. Do not use it after entering real data.

## Run it on a Mac mini

Use the beginner-friendly [Mac mini setup guide](docs/MAC-MINI-SETUP.md).
The short version is:

1. Install and configure OpenClaw with a tool-capable local model.
2. Enable OpenClaw's `/v1/chat/completions` endpoint.
3. Copy the CRM, card-scanner, and daily-command-center skills into OpenClaw.
4. Copy `.env.example` to `.env`.
5. Verify local audio transcription.
6. Run `bash scripts/serve.sh`.

The whole product is then at
[http://localhost:8080](http://localhost:8080).

For a different Linux or local-model host, use
[the general local-AI guide](docs/LOCAL-AI.md). The original GB10 deployment
is documented separately in [docs/GB10-SETUP.md](docs/GB10-SETUP.md).

## What you can ask the local agent to do

With the OpenClaw chat endpoint and `crm-db-operations` skill configured, the
agent can perform audited CRM work from plain language:

- “Add Taylor Brooks, looking in Bellevue around $900k.”
- “Update Taylor's timeline to six weeks.”
- “Remind me Monday at 9 to call Taylor.”
- “What times are free Saturday?”
- “Book Taylor for the 10:00 slot.”
- “Close Taylor as won; the contract was signed.”

If “won” versus “lost” is unclear, the agent must ask instead of guessing.
The agent uses the REST tool layer; it does not write SQL directly.

## Voice-note intake

Open **Add voice note**, record or choose an audio file, and select
**Transcribe and review**. The server:

1. checks that the file is recognizable audio and no larger than 20 MB;
2. transcribes it with the local OpenClaw CLI;
3. extracts an editable CRM draft and possible duplicates;
4. deletes the temporary audio file; and
5. writes nothing until you explicitly add a new lead or update an existing
   one.

There is no cloud transcription fallback. See
[Mac mini setup: verify voice](docs/MAC-MINI-SETUP.md#6-verify-voice-transcription).

## Privacy: what stays local and what may leave

SQLite data, local-model inference, local transcription, and the bundled
knowledge index stay on the host.

Data can leave the host only when you enable an external service:

- Composio sends the necessary email/calendar data to Gmail or Google
  Calendar.
- Web or market-research tools send search requests and retrieve public pages.
- A model or transcription provider configured inside OpenClaw may be remote.

Those services are optional. Gmail/Calendar integrations and the inbox poller
are off by default. Check your OpenClaw model/provider configuration before
calling a deployment fully local.

## Back up and update

Stop the app before copying the database, then:

```bash
cp backend/data/crm.db ~/Desktop/openhouse-crm-backup.db
```

To update:

```bash
git pull
bash scripts/serve.sh
```

Startup applies additive database migrations automatically. Keep the backup
until you have checked your leads after the update.

## Troubleshooting

Run the read-only readiness check while the hosted app is running:

```bash
python3 scripts/doctor.py
```

To send one harmless request through the real OpenClaw chat endpoint:

```bash
python3 scripts/doctor.py --live-agent
```

Common results:

- **Endpoint disabled**: enable
  `gateway.http.endpoints.chatCompletions.enabled` in OpenClaw.
- **Unauthorized**: `AGENT_GATEWAY_TOKEN` does not match the gateway.
- **Unreachable**: check that OpenClaw is running on port 18789 and
  `AGENT_GATEWAY_URL` is correct.
- **Voice transcription failed**: test
  `openclaw infer audio transcribe --file <audio-file> --json` directly.
- **No daily market summary**: this is an honest empty state. Configure the
  summary/research workflow; the CRM does not make up articles.

## Developer reference

- [docs/CONTRACT.md](docs/CONTRACT.md) — schema, REST, and tool contract
- [docs/LOCAL-AI.md](docs/LOCAL-AI.md) — general OpenClaw/local-model setup
- [docs/VERTICALS.md](docs/VERTICALS.md) — use another industry pack
- [docs/BRIEFING-UI.md](docs/BRIEFING-UI.md) — source and trust boundaries
- [CONTRIBUTING.md](CONTRIBUTING.md) — tests and contribution workflow
- [docs/OpenHouse-Pitch.pdf](docs/OpenHouse-Pitch.pdf) — original pitch deck

Main folders:

| Path | Purpose |
|---|---|
| `backend/` | FastAPI, SQLite, validation, and agent adapters |
| `dashboard/` | React/TypeScript user interface |
| `skills/` | OpenClaw CRM, card, daily, and optional integration skills |
| `scripts/` | launchers and readiness checks |
| `docs/` | operator and developer documentation |

OpenHouse Intelligence began at the Dell × NVIDIA BuilderBase hackathon in
Seattle and placed among the top-eight finalist teams. Team: Johaan, K, Chris,
and Toby.
