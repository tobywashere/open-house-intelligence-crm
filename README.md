# OpenHouse Intelligence

OpenHouse Intelligence is a local-first CRM for real-estate agents. Use plain
language to add leads, write notes, schedule follow-ups, and book tours. CRM
writes wait for review in **Pending approvals** before anything changes.

## Try the demo

You need Git, Python 3.11 or newer, and Node.js 20 or newer.

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173). Demo mode uses clearly
labeled sample data and does not need a model, GPU, or account.

## Set up real local AI

Real local AI requires OpenClaw and a tool-capable model. Enable OpenClaw's
Chat Completions endpoint once:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Then clone and prepare the CRM:

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` and change `AGENT_MODE=mock` to `AGENT_MODE=openclaw`. The default
`AGENT_ID=openhouse-crm` works for most people. If you choose another lowercase
agent ID, change that one value in `.env`; setup, the app, the plugin safety
hooks, and acceptance checks will all use it. Then run:

Keep the `-I` in these commands. It prevents personal Python startup
customizations from running before the repository safety checks.

```bash
python3 -I scripts/setup_openclaw.py
bash scripts/serve.sh
```

Setup prints the location of a private recovery backup. Keep that exact folder
until the dashboard chat has passed the live check below. After the live check
works, you can remove that old backup; do not share it because it may contain
your previous local CRM setup.

Keep that Terminal open. In a second Terminal, start in the directory where
you cloned the project and run the read-only readiness check:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

Open [http://localhost:8080](http://localhost:8080). Look for
`crm_verified`. This proves the native `openhouse_crm` tool reached the audited
CRM, not merely that a generic assistant answered. Setup installs the
`crm-db-operations` guidance and the restricted CRM tool automatically. Do not
repair OpenClaw agent, tool, exec, profile, or plugin settings by hand. Rerun
the same setup command if it reports a partial installation. Setup also turns
off extended model thinking for this CRM agent only, which helps smaller local
models make reliable CRM tool calls without changing your other OpenClaw agents.

For a hardware acceptance report, first capture two explicit setup runs. This
helper runs setup twice and saves machine-verifiable evidence tied to the
tested revision, which must remain clean. It also compares canonical structured snapshots of
the installed skills, plugin, agent policy, bindings, approvals, and gateway
references after each run. The snapshot covers tracked HEAD files and executable
modes; modified, missing, or unexpected extra files in setup material fail closed.
Strict generated Python caches are isolated and never copied or loaded.
Sanitized run logs are manual diagnostics only. The acceptance report shows the
structured proof as `Setup twice`:

```bash
python3 -I scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json
```

The automated CRM chat acceptance proves an audited CRM read, exact lead
count, invalid-write safety, truthful briefing, one disposable create-lead
proposal, one natural-language booking proposal, and session cleanup. Neither
proposal is approved. Both are denied and cleaned up. It does not automate
voice or Discord delivery. To authorize those disposable proposals, run:

```bash
python3 -I scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json
```

This command never approves the test proposals. Inspect its JSON before sharing
it.

## What the status means

`chat_verified` proves only that OpenClaw answered. `crm_verified` proves the
native CRM tool completed and the backend recorded its matching audit. The
[full local-AI guide](docs/LOCAL-AI.md#live-checks-and-status) explains every
status and the recovery steps.

## Supported computers

| Computer | Support |
|---|---|
| Apple-silicon Mac mini, 16 GB or more | Primary path |
| Linux x86_64 or ARM64, 16 GB or more | Supported |
| Windows 11 with WSL2, 16 GB or more | Supported inside WSL2 |
| Native Windows | Unsupported; use Windows 11 with WSL2 |

Sixteen gigabytes is the minimum for the CRM plus a modest quantized model. A
larger model may need more memory. See the [Mac mini guide](docs/MAC-MINI-SETUP.md),
[Windows/WSL2 guide](docs/WINDOWS-WSL-SETUP.md), [GB10 guide](docs/GB10-SETUP.md),
or [full local-AI guide](docs/LOCAL-AI.md).

Native Windows is unsupported; use Windows 11 with WSL2.

## What happens in the app

- Natural-language reads use verified CRM results.
- New leads, notes, reminders, bookings, updates, merges, closes, and deletes
  wait in **Pending approvals**. You choose whether to approve or deny them.
- Voice intake is optional. Configure a transcription provider first, then
  review the transcript and extracted details before creating or updating a
  lead. If no transcription provider is configured, record voice as
  SKIP (not configured); voice is optional and is not a release blocker.
- A **deterministic fallback** is labeled as a draft for review.
- Briefing schedule facts come from the CRM. Market items require a source URL,
  publication date, summary, and geographic area. Missing information is shown
  as unavailable and is never synthesized.

## Optional Discord

Discord is optional and is tested only after dashboard acceptance. Then, if
wanted, bind it to the same agent:

```bash
python3 -I scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Discord CRM writes wait for review in the dashboard's **Pending approvals**.
If one Discord request attempts several writes, its final reply lists every
verified proposal or applied result it retained, plus any failure. An uncertain
result blocks later writes in that request and tells you to inspect the CRM and
Pending approvals before retrying.

Discord delivery is a manual hardware test. Binding alone is not a pass. A
bound tester must confirm it lists the real CRM lead count and that a disposable
write appears in Pending approvals. Merge waits for this manual evidence when
Discord is in scope.

## Help and privacy

Detailed status meanings, voice setup, recovery, and safe report sharing are in
[docs/LOCAL-AI.md](docs/LOCAL-AI.md). Keep OpenClaw and the CRM on a private
interface. Never commit `.env`, tokens, client data, recordings, or
`backend/data/crm.db`. The REST and database contracts are documented in
[docs/CONTRACT.md](docs/CONTRACT.md).
