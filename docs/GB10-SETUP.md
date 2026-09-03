# GB10 setup

This optional guide uses the same supported setup on a Dell Pro Max GB10. It
is not required for the CRM. For an Apple-silicon Mac mini, use
[MAC-MINI-SETUP.md](MAC-MINI-SETUP.md); for Linux or another host, use
[LOCAL-AI.md](LOCAL-AI.md).

## Before you start

Install Git, Python 3.11 or newer, Node.js 22.22.3 or newer, and OpenClaw. Configure
a tool-capable model in OpenClaw. The model choice controls memory use, speed,
and quality. Record what you used in the acceptance checklist below instead of
assuming a particular model or hardware combination is verified.

Native Windows is unsupported; use Windows 11 with WSL2.

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

Keep the `-I` in these commands. It prevents personal Python startup
customizations from running before the repository safety checks.

```bash
python3 -I scripts/setup_openclaw.py
bash scripts/serve.sh
```

Keep the private recovery-backup folder that setup reports until dashboard chat
passes the live check. Then remove that exact old folder; never share it.

In a second Terminal, start in the directory where you cloned the project,
then run:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

Open [http://localhost:8080](http://localhost:8080). The helper creates the
dedicated agent selected by `AGENT_ID` with the `crm-db-operations` guidance.
The default is `AGENT_ID=openhouse-crm`; choose another valid lowercase ID in
`.env` before setup if needed. It links the bundled
`openhouse_crm` tool plugin and allows only that tool plus `exec`; only the
daily-brief runner remains executable, while general web, browser, and file
tools are denied. It overrides only this agent's base tool profile, leaving the
global profile unchanged. It disables lean tool compaction for only this agent
so verified CRM functions remain directly visible. It checks required CLI and
policy capabilities, then reads back both the tool policy and live plugin
registration before reporting success.

The expected live result is **CRM capability: crm_verified**. If the result is
only chat verified, rerun `python3 -I scripts/setup_openclaw.py` and use the
recovery notes in [LOCAL-AI.md](LOCAL-AI.md#recovery). Do not trust a generic
assistant answer as proof that it can access CRM tools.

For a hardware acceptance report, create explicit setup evidence first. The
helper runs setup twice and records machine-verifiable evidence tied to the
tested revision, which must remain clean. It also compares canonical structured snapshots of
the installed skills, plugin, agent policy, bindings, approvals, and gateway
references after each run. It verifies tracked HEAD files and executable modes;
modified, missing, or unexpected extra files in material setup sources fail closed.
Strict generated Python caches are isolated and never copied or loaded.
Sanitized run logs are manual diagnostics only and do not make this pass. The
report names the structured prerequisite `Setup twice`.

```bash
python3 -I scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json
```

After the read-only check succeeds, the automated CRM chat acceptance proves an
audited CRM read, exact lead count, invalid-write safety, truthful briefing,
one disposable create-lead proposal, one natural-language booking proposal,
and session cleanup. Neither proposal is approved. Both are denied and cleaned
up. It does not automate voice or Discord delivery. Run it only when you want
to authorize those disposable proposals:

```bash
python3 -I scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json
```

## Optional Discord

Discord is optional and is tested only after dashboard acceptance. Bind it only
when you need it:

```bash
python3 -I scripts/setup_openclaw.py --bind-discord ACCOUNT
```

It uses the same selected dedicated agent. CRM writes wait for review in
dashboard **Pending approvals**. One reply preserves each retained proposal ID
or verified result from that Discord run and reports failures truthfully.

Discord delivery is a manual hardware test. Binding alone is not a pass. A
bound tester must confirm it lists the real CRM lead count and that a disposable
write appears in Pending approvals. Merge waits for this manual evidence when
Discord is in scope.

## What to verify

- A natural-language lead, note, reminder, and booking each appear in
  **Pending approvals** before changing CRM data.
- A rejected booking or reminder does not make an external calendar call.
- Voice intake has a separate optional prerequisite: an optional transcription
  provider configured in OpenClaw. After setup, transcription reaches an
  editable review screen before any lead write.
- If no transcription provider is configured, record voice as
  SKIP (not configured); voice is optional and is not a release blocker.
- A deterministic fallback is visibly labeled for review.
- Daily schedule facts match the CRM and missing market information stays
  unavailable and is never synthesized.
- Market items require a source URL, publication date, summary, and geographic area.
  Incomplete information stays unavailable.

## Target-hardware acceptance record

These are intentionally unchecked. Fill them out after an actual GB10 run.

- [ ] OpenClaw version:
- [ ] Model/provider:
- [ ] Linux distribution/version:
- [ ] Memory:
- [ ] Date and operator:
- [ ] `Setup twice` PASS for the tested revision in
  `openhouse-setup-evidence.json`
- [ ] `--live-agent --live-crm` reports CRM capability verified
- [ ] Automated CRM chat acceptance with
  `python3 -I scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json`
  report inspected and attached
- [ ] Natural-language booking proposal stayed unapplied and was denied
- [ ] Dashboard chat proposes a reviewed CRM write
- [ ] Voice (optional): PASS with a configured provider, or SKIP (not configured)
- [ ] Optional Discord binding, if used, has manual read and reviewed-write evidence

Keep the gateway and CRM on a private interface. Do not commit `.env`, tokens,
client data, recordings, or the SQLite database.
