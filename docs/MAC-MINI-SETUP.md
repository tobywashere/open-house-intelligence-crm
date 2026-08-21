# Mac mini setup

This is the practical path for running the CRM and local AI on one Mac mini.
You should see a clear result after each step.

## What you need

- An Apple-silicon Mac mini with **16 GB** unified memory or more
- macOS, 25 GB free disk space, Git, Python 3.11+, and Node.js 20+
- OpenClaw with a tool-capable model that can answer a basic prompt

Sixteen gigabytes is the minimum for the CRM plus a modest quantized model. It
does not mean every local model will fit. Choose a smaller quantized model if
responses are slow or memory pressure is high. The CRM does not require a
specific model, provider, GPU, or GB10.

## 1. Check the basics

Open **Terminal** and run:

```bash
git --version
python3 --version
node --version
openclaw --version
```

Install anything that is missing using its vendor's current macOS guide. Finish
OpenClaw's own setup and make sure it can answer a simple question first.

## 2. Enable OpenClaw chat access

The CRM talks to OpenClaw through `/v1/chat/completions`. Some OpenClaw
installs leave it off until you enable it:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Follow any restart instruction OpenClaw prints. A missing endpoint gives a
404 even when the gateway itself is running.

## 3. Download and set up the CRM

```bash
cd ~/Documents
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` in a text editor. Change `AGENT_MODE=mock` to
`AGENT_MODE=openclaw`, then continue in Terminal:

```bash
python3 scripts/setup_openclaw.py
bash scripts/serve.sh
```

Keep that Terminal open. Open a second Terminal and run:

```bash
cd ~/Documents/open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

Open [http://localhost:8080](http://localhost:8080). Keep the Terminal running
`bash scripts/serve.sh` open while you use the CRM.

The setup helper creates `openhouse-crm`, copies the shipped skills to its own
workspace, enables the `crm-db-operations` skill for that agent, and sets its
restricted command access. The configuration in `.env` is
`AGENT_ID=openhouse-crm`. The helper prints your OpenClaw version, verifies the
required capabilities before changing anything, and allows only `exec` with
the CRM wrapper and daily-brief runner on its executable allowlist. General
web, browser, and file tools are denied. It then reads that tool policy back
from OpenClaw and will not report success if broader tools remain available.

The doctor command should finish with **CRM capability: crm_verified**. A chat
answer alone is not enough because it could come from a generic agent without
the CRM tool.

If someone else is helping you test, create one sanitized report after the live
check succeeds:

```bash
python3 scripts/doctor.py --live-agent --live-crm --json \
  | tee openhouse-compatibility.json
```

Inspect the file before sharing it. The report is designed to omit tokens, CRM
records, chat content, model responses, and your home-directory path.

## 4. Check the visible behavior

1. Ask chat to add a disposable lead. It should appear in **Pending approvals**,
   not immediately in **Leads**.
2. Approve the lead, then ask to add a note, reminder, and tour booking. Review
   each proposed change before applying it.
3. Open **Add voice note**, record a short note, review its transcript and
   details, then cancel. Confirm no lead was added.
4. Open **Daily summary**. Appointments and follow-ups must match the CRM. A
   missing market summary must stay unavailable, not turn into sample news.

Only **CRM capability: crm_verified** proves that the agent made an audited
CRM tool call. If the app uses a **deterministic fallback**, it labels the
result for review. Market items require a source URL, publication date, short
summary, and geographic area; incomplete information stays unavailable.

## 5. Voice notes

Check local transcription before relying on voice input:

```bash
openclaw infer audio transcribe --file /path/to/a-short-recording.m4a --json
```

The response needs a non-empty `text` or `transcript`. If you selected a
specific audio model in OpenClaw, set its name in `.env` as
`VOICE_TRANSCRIBE_MODEL=provider/model`, then restart the product. The app has
no cloud transcription fallback.

## 6. Optional Discord

Dashboard chat is the primary experience. To use the same dedicated agent in
Discord, run this from the project folder:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Replace `ACCOUNT` with the account identifier required by OpenClaw. The
printed binding command is safe to use later too. CRM writes from Discord are
still proposed for review in the dashboard before they apply.

## 7. Recovery

- **Endpoint disabled / 404:** repeat step 2 and restart OpenClaw.
- **Unauthorized:** add the matching `AGENT_GATEWAY_TOKEN` to `.env`, then
  restart `bash scripts/serve.sh`.
- **Chat verified, CRM capability failed:** rerun
  `python3 scripts/setup_openclaw.py`; it repairs a partial dedicated-agent
  setup, then validates the agent and skills. Do not change global
  `tools.exec` settings.
- **OpenClaw unreachable:** make sure its gateway is listening on port 18789
  and `AGENT_GATEWAY_URL` is correct in `.env`.
- **Slow responses:** choose a smaller model. The 16 GB minimum does not mean
  every model will run quickly.

## Target-hardware acceptance record

Do not check these boxes until someone has completed the run on this machine.

- [ ] OpenClaw version:
- [ ] Model/provider:
- [ ] macOS version:
- [ ] Memory:
- [ ] Date and operator:
- [ ] Product revision from the compatibility report:
- [ ] `--live-agent --live-crm` reports CRM capability verified
- [ ] Dashboard chat proposes a reviewed CRM write
- [ ] Voice note reaches the review screen
- [ ] Optional Discord binding, if used, reaches the same agent
- [ ] Sanitized compatibility report inspected and attached

For private-network access, advanced configuration, or further troubleshooting,
see [LOCAL-AI.md](LOCAL-AI.md).
