# Mac mini setup

This is the practical path for running the CRM and local AI on one Mac mini.
You should see a clear result after each step.

## What you need

- An Apple-silicon Mac mini with **16 GB** unified memory or more
- macOS, 25 GB free disk space, Git, Python 3.11+, and Node.js 22.22.3+
- OpenClaw with a tool-capable model that can answer a basic prompt

Sixteen gigabytes is the minimum for the CRM plus a modest quantized model. It
does not mean every local model will fit. Choose a smaller quantized model if
responses are slow or memory pressure is high. The CRM does not require a
specific model, provider, GPU, or GB10.

Native Windows is unsupported; use Windows 11 with WSL2.

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

The setup helper creates the agent selected by `AGENT_ID`, copies the shipped
skills to its own workspace, enables the `crm-db-operations` guidance for that
agent, links the bundled `openhouse_crm` tool plugin, and sets restricted
access. The
default configuration in `.env` is `AGENT_ID=openhouse-crm`; you may replace it
with another valid lowercase ID before setup. The helper configures the plugin
safety hooks for that exact agent, prints your OpenClaw version, verifies the
required capabilities before changing anything, and allows only
`openhouse_crm` plus `exec`. It overrides only this agent's base tool profile,
so a global `coding` profile cannot hide the CRM tool and is not changed. CRM
work uses the typed plugin without a shell;
only the daily-brief runner remains on the executable allowlist. General web,
browser, and file tools are denied. It reads that policy and the plugin's live
tool registration back from OpenClaw before reporting success.

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

After the read-only check reports `crm_verified`, the automated CRM chat
acceptance proves an audited CRM read and exact lead count, an invalid-write
safety attempt, truthful briefing behavior, one disposable create-lead proposal
that is never approved and is denied and cleaned up, and session cleanup. It
does not automate booking, voice, or Discord delivery. Run it only when you are
comfortable authorizing that disposable proposal:

```bash
python3 scripts/acceptance_openclaw.py --json --allow-test-write
```

## 4. Check the visible behavior

CRM writes wait for review. Test that rule in this order:

1. Ask chat to add a disposable lead. It should appear in **Pending approvals**,
   not immediately in **Leads**.
2. Approve the lead, then ask to add a note, reminder, and tour booking. Review
   each proposed change before applying it.
3. If a transcription provider is configured, open **Add voice note**, record a
   short note, review its transcript and details, then cancel. Confirm no lead
   was added. Otherwise record this check as SKIP (not configured).
4. Open **Daily summary**. Appointments and follow-ups must match the CRM. A
   missing market summary must stay unavailable, not turn into sample news.
   Missing briefing content is never synthesized.

Only **CRM capability: crm_verified** proves that the agent made an audited
CRM tool call. If the app uses a **deterministic fallback**, it labels the
result for review. Market items require a source URL, publication date, short
summary, and geographic area; incomplete information stays unavailable.

## 5. Voice notes

Voice intake has a separate optional prerequisite: an optional transcription
provider configured in OpenClaw. Check it before relying on voice input:

If no transcription provider is configured, record voice as
SKIP (not configured); voice is optional and is not a release blocker.

```bash
openclaw infer audio transcribe --file /path/to/a-short-recording.m4a --json
```

The response needs a non-empty `text` or `transcript`. If you selected a
specific audio model in OpenClaw, set its name in `.env` as
`VOICE_TRANSCRIBE_MODEL=provider/model`, then restart the product. The app has
no cloud transcription fallback.

## 6. Optional Discord

Discord is optional and is tested only after dashboard acceptance. To use the
same dedicated agent, run this from the project folder:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Replace `ACCOUNT` with the account identifier required by OpenClaw. The
printed binding command is safe to use later too. CRM writes from Discord are
still proposed for review in the dashboard before they apply. A multi-write
Discord reply preserves each retained proposal ID or verified result and reports
failures; an uncertain result blocks later writes in that request.

## 7. Recovery

- **Endpoint disabled / 404:** repeat step 2 and restart OpenClaw.
- **Unauthorized:** add the matching `AGENT_GATEWAY_TOKEN` to `.env`, then
  restart `bash scripts/serve.sh`.
- **Chat verified, CRM capability failed:** rerun
  `python3 scripts/setup_openclaw.py`; it repairs a partial dedicated-agent
  setup, relinks the bundled CRM plugin, and verifies the real
  `openhouse_crm` tool. Do not change global `tools.exec` settings.
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
- [ ] Automated CRM chat acceptance with
  `python3 scripts/acceptance_openclaw.py --json --allow-test-write`
  report inspected and attached
- [ ] Dashboard chat proposes a reviewed CRM write
- [ ] Voice (optional): PASS with a configured provider, or SKIP (not configured)
- [ ] Optional Discord binding, if used, reaches the same agent
- [ ] Sanitized compatibility report inspected and attached

For private-network access, advanced configuration, or further troubleshooting,
see [LOCAL-AI.md](LOCAL-AI.md).
