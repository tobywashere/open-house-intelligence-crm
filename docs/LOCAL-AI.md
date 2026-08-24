# Run OpenHouse Intelligence with local AI

This guide is the shared reference for native Linux, a Mac mini, or Windows 11
running the project inside WSL2. It explains the configuration behind the short
setup in the README. Mac mini owners can follow
[MAC-MINI-SETUP.md](MAC-MINI-SETUP.md); Windows owners can follow
[WINDOWS-WSL-SETUP.md](WINDOWS-WSL-SETUP.md).

## What is required and what is optional

Required for real local-AI mode:

- Python 3.11+, Node.js 22.22.3+ for real local AI, and a current OpenClaw installation
- A tool-capable model configured in OpenClaw
- The enabled `/v1/chat/completions` endpoint
- The dedicated agent selected by `AGENT_ID` (default `openhouse-crm`) and the
  `crm-db-operations` skill

An Apple-silicon Mac mini with **16 GB** is the primary supported baseline.
Linux x86_64 or ARM64 and Windows through WSL2 are supported at the same memory
baseline. Native PowerShell setup is not supported. A modest quantized model is
appropriate at 16 GB; larger models can need considerably more memory. A GB10
is an optional host, not a dependency.

Native Windows is unsupported; use Windows 11 with WSL2.

OpenClaw's own Node.js requirements can change independently of this CRM. Use
its current [installation guide](https://docs.openclaw.ai/install) rather than
forcing OpenClaw onto the CRM's minimum Node version.

Optional services that use the internet are Gmail, Google Calendar, the
fixed-source daily-brief runner, and remote model providers. They stay off
unless you configure them.

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

On a new OpenClaw install, it is normal for the config to contain agent
defaults but no explicit agent roster yet. Setup creates the dedicated CRM
agent, then safely detects whether that OpenClaw version uses current keyed
agent entries or the older list format. You do not need to edit either format
by hand.

Run setup while no other process is writing OpenClaw configuration. Setup
rechecks legacy list indexes immediately before each agent-specific write and
readback, but it still expects no concurrent OpenClaw config writer during the
short setup run.

Keep the server running. In a second Terminal, start in the directory where
you cloned the project, then run:

```bash
cd open-intelligence-crm
python3 scripts/doctor.py --live-agent --live-crm
```

The helper is safe to rerun. It creates or validates the agent selected by
`AGENT_ID` in `.env`, installs the shipped skills in that agent's workspace,
links and enables the bundled `openhouse_crm` plugin, and validates that
`crm-db-operations` is eligible. The plugin ships as runnable JavaScript, so
setup does not download a compiler or plugin dependencies. If you pass `--agent-id`,
set `AGENT_ID` to the same nonblank value in `.env`; setup rejects blank or
conflicting values so the helper and CRM runtime cannot select different agents.
Setup also writes that selected value into the plugin's validated `agentId`
configuration and proves the safety hooks protect that exact agent. The default
remains `openhouse-crm`.
CRM reads and proposals use the typed `openhouse_crm` tool without a shell.
Command execution remains available only for the deterministic daily-brief runner.
OpenClaw's local-model lean mode may present that tool through the compact
`tool_search` and `tool_call` controls instead of as a direct tool. The shipped
skills support both presentations.
Setup gives only the dedicated CRM agent an unrestricted base profile before
narrowing its final allowlist to those two tools. This prevents a machine-wide
`coding` profile from hiding the CRM plugin without changing that global profile
or exposing CRM access to unrelated agents.

If an earlier run stopped halfway through, rerun the same command. Setup repairs
the CRM agent's skills, sandbox, and execution policy before checking the final
result. It does not change the global `tools.exec` settings used by other agents.
If a later check fails, setup restores the CRM agent fields that existed before
that run. It will not take over an existing selected CRM agent whose workspace
points somewhere else.

The helper prints `openclaw --version` in both success and failure diagnostics.
Compatibility is capability-based because this repository has no evidence for
a safe numeric version range. Before changing files or configuration, the
helper requires the documented CLI commands, options, and prerequisite JSON
and policy-inspection surfaces. After configuration, it reads the dedicated
agent's authoritative tool policy back and requires the allowed-tool list to be
exactly `openhouse_crm` and `exec`, with general web, browser, and file tools
denied. The dedicated agent's profile must be `full` before that exact allowlist
is applied. It also loads the plugin through OpenClaw's runtime inspection and
requires exactly one registered tool named `openhouse_crm`. It then
requires OpenClaw's effective execution prompt mode to be exactly `off`; a
missing, interactive, or contradictory value is not treated as ready. A
missing or ambiguous surface stops setup instead of guessing or widening
permissions.

The checks come from separate authoritative surfaces. Plugin runtime inspection
proves that `openhouse_crm` is registered. Agent configuration proves that exec
requests the gateway in allowlist mode. Gateway approvals prove the effective
host, security, prompt behavior, and daily-brief executable pattern.
`sandbox explain` proves only that this restricted agent is running directly
with sandbox mode off; it is not expected to repeat exec-host policy.

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
tokens in `.env`, never in source control. The helper passes the CRM API URL to
the skill configuration.

### When CRM API authentication is enabled

`OHI_API_TOKEN` protects the CRM API. The backend reads it from the project
`.env`, and the dashboard uses the matching `VITE_API_TOKEN`. The OpenClaw
agent needs the same API value, but the setup helper does not place that value
in an OpenClaw command or configuration field.

Instead, the helper:

1. Stores `OHI_API_TOKEN` in OpenClaw's gateway `.env` file with permissions
   set to `0600`, so only that local account can read or change it.
2. Configures `skills.entries["crm-db-operations"].apiKey` as an environment
   SecretRef whose ID is `OHI_API_TOKEN`.
3. Uses the skill's `primaryEnv: OHI_API_TOKEN` metadata so OpenClaw supplies
   the resolved value only while that skill runs.
4. Removes the legacy plaintext
   `skills.entries["crm-db-operations"].env.OHI_API_TOKEN` setting if a
   previous setup stored the token directly in OpenClaw config.
5. Restarts the OpenClaw gateway so the saved environment is loaded.

The gateway file is `$OPENCLAW_STATE_DIR/.env` when `OPENCLAW_STATE_DIR` is
set. Otherwise it is `~/.openclaw/.env`, or `~/.openclaw-PROFILE/.env` for a
named `OPENCLAW_PROFILE`. `OPENCLAW_HOME` replaces `~` when configured. The
helper refuses unsafe token characters, unsafe profile names, symbolic links,
and non-file targets before it changes OpenClaw. If the gateway file already
has multiple `OHI_API_TOKEN` lines, the helper normalizes them to one assignment.
It completes the read-only agent, configuration, and approval checks before it
writes that file, so a validation problem leaves the existing token untouched.
If the installed CLI cannot create or read back the required SecretRef, setup
stops and asks you to upgrade OpenClaw. It does not fall back to plaintext
storage.

These details follow OpenClaw's current official
[skills configuration](https://docs.openclaw.ai/tools/skills-config) and
[environment variable](https://docs.openclaw.ai/help/environment) guidance.

The helper uses the following agent policy on purpose:

- Allowed skills include `crm-db-operations` plus the shipped card and briefing
  skills.
- `openhouse_crm` is the only CRM tool. It accepts a fixed operation name and
  argument object, then invokes the audited wrapper without a shell. General
  web fetch/search, browser, and filesystem tools are explicitly denied.
- `exec` runs on the gateway in allowlist mode with its OpenClaw prompt set to
  `off`. The only permitted executable entry point is the deterministic
  daily-brief runner, so dashboard and Discord chat cannot turn CRM requests
  into general shell access.
- The daily-brief runner performs its own fixed-source retrieval and validation;
  the agent cannot replace it with a general web tool or hand-built payload.

OpenClaw's skill and configuration interfaces change over time. If the helper
reports an unsupported command or configuration shape, update OpenClaw and
rerun it rather than manually changing global `tools.exec` settings or broadening
the agent's permissions. If setup still fails, share `openclaw --version`, the
failing command, and its exact redacted output with the project maintainers
rather than patching the script locally.

## Live checks and status

While `bash scripts/serve.sh` is running, use:

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --live-agent --live-crm
python3 scripts/doctor.py --live-agent --live-crm --json
```

The first command changes nothing. `--live-agent` sends one harmless chat
completion. `--live-crm` directly invokes the selected agent's native CRM tool
through OpenClaw for one audited, read-only `generate_dashboard_insights` call.
The CRM check is successful
only after the backend sees that new agent-tagged audit record. It does not
trust the model's text alone.

To inspect the installed tool directly, run:

```bash
openclaw plugins inspect openhouse-crm --runtime --json
```

Its runtime tools must include `openhouse_crm`. This proves registration,
while `crm_verified` additionally proves the tool reached the audited CRM API.

Run `bash scripts/serve.sh` first and leave it running. Setup output and unit
tests are not runtime proof. The required proof is the second command above,
with both `--live-agent` and `--live-crm`.

The JSON form is the easiest report to send to a maintainer. It includes the
product revision, platform, architecture, memory, dependency versions, and the
same application checks. It does not include tokens, environment values, CRM
records, chat content, model responses, or home-directory paths. Inspect any
file yourself before sharing it.

For a supported-hardware report, create setup evidence before running the app.
The helper runs setup twice and records machine-verifiable evidence tied to the
tested revision, which must remain clean. It also compares a canonical structured snapshot of
the installed skills, plugin, agent policy, bindings, approvals, and gateway
references after each run. The JSON report names this prerequisite `Setup twice`.

```bash
python3 scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json
```

After the read-only doctor reports `crm_verified`, the automated CRM chat
acceptance proves an audited CRM read, exact lead count, invalid-write safety,
truthful briefing, one disposable create-lead proposal, one natural-language
booking proposal, and session cleanup. Neither proposal is approved. Both are
denied and cleaned up. It does not automate voice or Discord delivery.

```bash
python3 scripts/acceptance_openclaw.py --json --setup-evidence openhouse-setup-evidence.json
python3 scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json
```

The first form remains read-only. The second explicitly authorizes the two
disposable proposal attempts. Neither command approves a write or edits
OpenClaw configuration between checks. If there is no suitable existing lead,
the booking row fails honestly because the write-enabled result cannot prove
booking.

Status meanings:

| Status | Meaning |
|---|---|
| `endpoint_enabled` | The gateway and chat endpoint respond, but no completion is proved yet. |
| `chat_verified` | OpenClaw answered, but CRM access has not been proven. |
| `crm_verified` | The native CRM tool completed and the backend recorded the matching audit. |
| `degraded` | CRM was previously verified, but the latest chat completion failed. |
| `failed` | A required live check did not complete. |
| `unauthorized`, `unreachable`, `endpoint_disabled` | The connection or endpoint needs attention. |

## Review before applying changes

CRM writes wait for review and do not apply directly. New leads, updates,
notes, reminders, bookings, closes, merges, and deletes enter **Pending approvals**
first. The operator can edit, approve, or deny each item. Booking
availability is checked again when approval happens. New-lead preferences are
shown as one item per line; editing them changes what is saved, and clearing
the box saves an empty preference list.

The native CRM tool does not use OpenClaw command execution. The remaining
daily-brief command allowlist does not bypass this CRM review screen.

Discord is optional and is tested only after dashboard acceptance. The same
review rule applies to its writes:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Use the `ACCOUNT` identifier OpenClaw documents for your Discord account. The
dashboard remains the place to review proposed CRM changes.
One Discord run keeps an ordered, bounded safety summary of its CRM writes.
The reply lists each retained Pending proposal ID, each verified direct write,
and any definite or uncertain failure. An uncertain result blocks later writes
in the same run, while reads remain available, and the reply tells you when
additional outcomes were omitted by the safety bound.

Discord delivery is a manual hardware test. Binding alone is not a pass. A
bound tester must confirm it lists the real CRM lead count and that a disposable
write appears in Pending approvals. Merge waits for this manual evidence when
Discord is in scope. The automated report records only whether a binding was
found and never changes that into a delivery PASS.

## Voice notes

Voice intake has a separate optional prerequisite: an optional transcription
provider configured in OpenClaw. The CRM does not silently substitute a cloud
provider. Test the provider itself before relying on voice intake. If no
transcription provider is configured, record voice as SKIP (not configured);
voice is optional and is not a release blocker.

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
unavailable. Missing briefing content is never synthesized.

If the AI cannot extract a lead, draft a follow-up, or explain a score, the
application may use a deterministic fallback. It is visibly labeled and must
be reviewed. It is never represented as a verified local-AI response.

## If Gmail or Calendar stops retrying

Most temporary integration failures retry automatically. A job stops in an
`exhausted` state after five claimed attempts have not completed delivery, so a
broken provider cannot loop indefinitely. A recovered stale fifth claim may
stop without another provider call because that claim already counted as an
attempt. First list the stopped jobs:

```bash
export CRM_API_URL=http://localhost:8080/api
curl "$CRM_API_URL/integrations/outbox?status=exhausted"
```

Find the job's `id` in that list. After correcting the problem shown in its
sanitized `last_error`, replace `JOB_ID` below and requeue it:

```bash
curl -X POST "$CRM_API_URL/integrations/outbox/JOB_ID/retry"
```

If `OHI_API_TOKEN` is enabled, run these commands from the project folder to
load your own `.env`, then include its token header:

```bash
bash -c '
source scripts/load-env.sh
load_repo_env .env
CRM_API_URL="${CRM_API_URL:-http://localhost:8080/api}"
curl -H "X-API-Token: $OHI_API_TOKEN" \
  "$CRM_API_URL/integrations/outbox?status=exhausted"
curl -X POST -H "X-API-Token: $OHI_API_TOKEN" \
  "$CRM_API_URL/integrations/outbox/JOB_ID/retry"
'
```

Keep `bash scripts/serve.sh` running so the worker can pick up the requeued job.
A `403` means an agent tried to use this user-only retry, `404` means that job
ID does not exist, and `409` means the job is not exhausted. When API
authentication is enabled, a missing or incorrect `X-API-Token` returns `401`.

Retries remember completed Calendar and Gmail steps, so an ordinary partial
failure resumes at the unfinished step. Delivery is still at least once. A
provider action can be repeated if the provider accepted it just before the
CRM process stopped, but the CRM had not yet saved the local success
checkpoint. Check the provider before manually retrying if that may have
happened.

Incoming Gmail replies use the exact Gmail message and CRM event as their
checkpoint. Failed or deferred processing is tried again. Processing is skipped
only after that exact event has a successful processing audit. Rewording only a
score explanation does not create another proposal, while changed structured
CRM fields can create a new proposal for review.

## Recovery

- **404 / endpoint disabled:** rerun the endpoint-enable command and restart
  the gateway.
- **OpenClaw gateway 401 / 403:** set its matching token as
  `AGENT_GATEWAY_TOKEN` in `.env`, then restart the CRM.
- **CRM API 401:** make `OHI_API_TOKEN` and `VITE_API_TOKEN` match in `.env`.
  Include `X-API-Token` in direct API commands, rerun
  `python3 scripts/setup_openclaw.py`, then restart `bash scripts/serve.sh`.
- **Chat verified, CRM check fails:** rerun `python3 scripts/setup_openclaw.py`.
  It repairs partial dedicated-agent setup, relinks the bundled plugin, verifies
  the `openhouse_crm` runtime tool, and removes the obsolete CRM wrapper
  approval. Do not change global `tools.exec` settings.
- **The agent lists generic tools only:** it is not using the dedicated agent.
  Confirm the intended `AGENT_ID` in `.env` (normally `openhouse-crm`), rerun
  setup, and repeat the live CRM check. Do not configure a different plugin
  agent by hand; setup keeps the app, plugin hooks, and acceptance check aligned.
- **OpenClaw unreachable:** start the gateway and verify
  `AGENT_GATEWAY_URL`.
- **Voice failure:** run the direct transcription command above and verify the
  provider/model selected in OpenClaw.
- **WSL service does not restart after Windows reboots:** enter the WSL
  distribution, start OpenClaw and the model runtime, then rerun the doctor.
  Follow the current OpenClaw and WSL service guidance rather than changing the
  dedicated CRM agent's policy.

## Target hardware and live acceptance record

These boxes are intentionally unchecked. Automated tests do not verify a Mac
mini, Windows/WSL2 system, model, OpenClaw gateway, provider account, or Discord
account. Fill them in only after a person records a real run.

- [ ] Product revision from the JSON report:
- [ ] Operating system and version:
- [ ] Hardware and architecture:
- [ ] Memory: confirm at least 16 GB for local-AI mode.
- [ ] OpenClaw version:
- [ ] Model/provider:
- [ ] Date and operator:
- [ ] Sanitized JSON report inspected and attached:

Verify required dashboard behavior and truthful briefing first. Then record
optional providers in this order. Steps 6 and 8 through 11 are conditional and
may be recorded as SKIP when those optional providers or accounts are not
configured:

- [ ] 1. `Setup twice` is PASS from `openhouse-setup-evidence.json` at this
  tested revision.
- [ ] 2. `--live-agent --live-crm` reports `CRM verified`.
- [ ] 3. Automated CRM chat acceptance with
  `python3 scripts/acceptance_openclaw.py --json --allow-test-write --setup-evidence openhouse-setup-evidence.json` has its
  report inspected and attached.
- [ ] 4. Dashboard chat lists real CRM leads.
- [ ] 5. Dashboard chat proposes a reviewed CRM write that appears in Pending
   approvals.
- [ ] 6. Voice (optional): when a transcription provider is configured, a note
  reaches the review screen without creating a lead; otherwise record
  SKIP (not configured).
- [ ] 7. Daily briefing uses stored CRM facts and leaves missing market
   information unavailable.
- [ ] 8. Optional Discord binding: if bound, manually verify it lists the same
  real CRM leads
  through the dedicated agent; otherwise record SKIP.
- [ ] 9. If Discord is bound, manually verify it proposes a disposable write that appears in the
  same Pending approvals; otherwise record SKIP.
- [ ] 10. With live integrations enabled, approve one disposable booking and
  verify one Google Calendar event; otherwise record SKIP.
- [ ] 11. With live integrations enabled, approve one disposable lead with an
  email and verify one Calendar call block plus one Gmail draft; otherwise
  record SKIP.

Optional feature checks after the ordered acceptance run:

- [ ] Gmail account and result recorded:
- [ ] Google Calendar account and result recorded:
- [ ] Discord account and result recorded:

For non-local access, bind the CRM to an exact private address, set matching
`OHI_API_TOKEN` and `VITE_API_TOKEN`, and update `CORS_ORIGINS`. Do not expose
the CRM or gateway directly to the public internet.
