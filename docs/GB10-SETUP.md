# GB10 setup

This optional guide uses the same supported setup on a Dell Pro Max GB10. It
is not required for the CRM. For an Apple-silicon Mac mini, use
[MAC-MINI-SETUP.md](MAC-MINI-SETUP.md); for Linux or another host, use
[LOCAL-AI.md](LOCAL-AI.md).

## Before you start

Install Git, Python 3.11 or newer, Node.js 20 or newer, and OpenClaw. Configure
a tool-capable model in OpenClaw. The model choice controls memory use, speed,
and quality. Record what you used in the acceptance checklist below instead of
assuming a particular model or hardware combination is verified.

Enable the chat endpoint once:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

## Install and run

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` and change `AGENT_MODE=mock` to `AGENT_MODE=openclaw`. Then run:

```bash
python3 scripts/setup_openclaw.py
bash scripts/serve.sh
```

In a second Terminal, start in the directory where you cloned the project,
then run:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

Open [http://localhost:8080](http://localhost:8080). The helper creates the
dedicated `openhouse-crm` agent with the `crm-db-operations` skill and uses
`AGENT_ID=openhouse-crm` in the CRM configuration. It gives that dedicated
agent only `exec`; the executable allowlist contains the CRM wrapper and daily
brief runner, while general web, browser, and file tools are denied. It records
the installed OpenClaw version and checks required CLI and policy capabilities
before changing configuration.

The expected live result is **CRM capability: crm_verified**. If the result is
only chat verified, rerun `python3 scripts/setup_openclaw.py` and use the
recovery notes in [LOCAL-AI.md](LOCAL-AI.md#recovery). Do not trust a generic
assistant answer as proof that it can access CRM tools.

## Optional Discord

Dashboard chat is the primary experience. Bind Discord only when you need it:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

It uses the same dedicated agent. Agent-created leads, notes, reminders,
bookings, and other CRM writes still wait for dashboard approval.

## What to verify

- A natural-language lead, note, reminder, and booking each appear in
  **Pending approvals** before changing CRM data.
- A rejected booking or reminder does not make an external calendar call.
- Voice transcription reaches an editable review screen before any lead write.
- A deterministic fallback is visibly labeled for review.
- Daily schedule facts match the CRM and missing market information stays
  unavailable instead of being fabricated.
- Market items require a source URL, publication date, summary, and geographic area.
  Incomplete information stays unavailable.

## Target-hardware acceptance record

These are intentionally unchecked. Fill them out after an actual GB10 run.

- [ ] OpenClaw version:
- [ ] Model/provider:
- [ ] Linux distribution/version:
- [ ] Memory:
- [ ] Date and operator:
- [ ] `--live-agent --live-crm` reports CRM capability verified
- [ ] Dashboard chat proposes a reviewed CRM write
- [ ] Voice note reaches the review screen
- [ ] Optional Discord binding, if used, reaches the same agent

Keep the gateway and CRM on a private interface. Do not commit `.env`, tokens,
client data, recordings, or the SQLite database.
