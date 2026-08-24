# Windows 11 setup with WSL2

This is the supported Windows path for OpenHouse Intelligence. The CRM, Python,
Node.js, OpenClaw, Ollama, and the model all run inside WSL2. You use your normal
Windows browser to open the dashboard.

Native PowerShell setup is not supported yet. Do not mix a Windows OpenClaw
installation with a WSL copy of this repository.

Native Windows is unsupported; use Windows 11 with WSL2.

## What you need

- Windows 11 with WSL2 and a Linux distribution such as Ubuntu
- 16 GB system memory or more
- About 25 GB of free space, plus whatever your chosen model needs
- Git, Python 3.11+, and Node.js 22.22.3+ inside WSL2
- OpenClaw and a tool-capable model that can run inside WSL2

Sixteen gigabytes is enough for the CRM and a modest quantized model. Larger
models need more memory and may be slow even when they technically load. A GPU
is helpful but is not required by the CRM itself.

## 1. Install or verify WSL2

Follow Microsoft's current [WSL installation guide](https://learn.microsoft.com/windows/wsl/install).
On a normal new installation, open PowerShell as Administrator and run:

```powershell
wsl --install
```

Restart Windows if requested. Open the installed Linux distribution and create
its Linux username and password. In PowerShell, confirm the distribution uses
WSL version 2:

```powershell
wsl --list --verbose
```

The remaining commands in this guide run inside the WSL Linux terminal, not
PowerShell.

## 2. Keep the project in the Linux filesystem

Use a folder under your WSL home directory, such as
`~/open-intelligence-crm`. Avoid cloning under `/mnt/c`; Linux development tools
and file permissions are more reliable in the WSL filesystem.

First verify the basics:

```bash
uname -a
python3 --version
git --version
node --version
npm --version
```

Install missing CRM prerequisites through your Linux distribution. For
OpenClaw, follow its current
[installation guide](https://docs.openclaw.ai/install). The official installer
supports Linux and WSL2. Its local-prefix installer is available when you need
OpenClaw and Node under your WSL user account instead of system directories.
The current rootless command is:

```bash
curl -fsSL https://openclaw.ai/install-cli.sh | bash
```

After installation, verify:

```bash
openclaw --version
openclaw doctor
openclaw gateway status
```

Use a stable OpenClaw release unless a maintainer specifically asks you to test
a beta.

The CRM setup command later overrides only the dedicated `openhouse-crm`
agent's base tool profile. It does not change a machine-wide `coding` profile.
The agent is then restricted back to the CRM tool and the daily-brief runner.

## 3. Verify the local model

Ollama is one option, not a CRM requirement. If you use it, install the Linux
version inside WSL by following the current
[Ollama Linux guide](https://docs.ollama.com/linux). Check your GPU against the
[Ollama hardware guide](https://docs.ollama.com/gpu) instead of copying an old
driver command from this repository.

Verify the runtime and your selected model before involving the CRM:

```bash
ollama --version
ollama list
ollama run YOUR_MODEL "Reply with exactly READY"
ollama ps
```

Replace `YOUR_MODEL` with the model you configured for OpenClaw. If you expect
NVIDIA acceleration, `nvidia-smi` should work inside WSL. Ollama's runtime
status should show that the model is using the expected processor. A `READY`
reply proves inference only; the CRM doctor later proves actual tool use.

## 4. Download the CRM

```bash
cd ~
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
```

Open `.env` in a text editor and change:

```dotenv
AGENT_MODE=mock
```

to:

```dotenv
AGENT_MODE=openclaw
```

Keep the normal same-machine values on loopback:

```dotenv
AGENT_GATEWAY_URL=http://localhost:18789
AGENT_ID=openhouse-crm
HOST=127.0.0.1
PORT=8080
CRM_API_URL=http://localhost:8080/api
```

Do not bind the gateway to the LAN or place it behind a public proxy.

## 5. Enable OpenClaw chat access

Some OpenClaw installations leave the Chat Completions endpoint off. Enable it
once and validate the configuration:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw config validate
```

Follow the restart instruction OpenClaw prints. Confirm OpenClaw can still
answer a normal prompt before continuing.

## 6. Run setup twice and save the output

The first run creates or repairs the dedicated CRM agent. The second proves the
same setup is safe to repeat. `pipefail` keeps a failed setup from looking
successful just because `tee` saved its output.

```bash
set -o pipefail
python3 scripts/setup_openclaw.py 2>&1 | tee openhouse-setup.txt
python3 scripts/setup_openclaw.py 2>&1 | tee -a openhouse-setup.txt
```

Setup links the bundled `openhouse_crm` plugin, verifies that OpenClaw really
registered the tool, installs the `crm-db-operations` guidance for
`AGENT_ID=openhouse-crm`, and leaves only the deterministic daily brief on the
exec allowlist. Do not manually edit the agent's plugin, exec host, mode, security,
or global `tools.exec` settings between runs. If setup fails, stop here and keep
`openhouse-setup.txt` for the maintainer.

## 7. Start the product

In the same WSL terminal:

```bash
bash scripts/serve.sh
```

Leave it running. Open [http://localhost:8080](http://localhost:8080) in your
Windows browser.

## 8. Create one shareable compatibility report

Open a second WSL terminal:

```bash
cd ~/open-intelligence-crm
set -o pipefail
python3 scripts/doctor.py --live-agent --live-crm --json \
  | tee openhouse-compatibility.json
```

The report should show `PASS` for **CRM capability** with detail
`crm_verified`. It records the product revision, WSL2 platform, architecture,
memory, dependency versions, and application checks. It is designed not to
include tokens, environment values, CRM rows, chat content, model responses, or
home-directory paths. Inspect the file yourself before sharing it.

After that read-only check reports `crm_verified`, the automated CRM chat
acceptance proves an audited CRM read and exact lead count, an invalid-write
safety attempt, truthful briefing behavior, one disposable create-lead proposal
that is never approved and is denied and cleaned up, and session cleanup. It
does not automate booking, voice, or Discord delivery. Run it only if you want
to authorize that disposable proposal:

```bash
python3 scripts/acceptance_openclaw.py --json --allow-test-write
```

## 9. Check the visible behavior

CRM writes wait for review in **Pending approvals**. Test in this order:

1. Ask dashboard chat to list the CRM leads. Confirm the answer matches the
   local CRM rather than generic session information.
2. Ask it to add a disposable lead. Confirm the change appears in **Pending
   approvals** and does not appear in **Leads** before approval.
3. Approve or deny the disposable proposal, then confirm the CRM matches that
   choice.
4. Voice intake has a separate optional prerequisite: an optional
   transcription provider in OpenClaw. After configuring one, record or upload
   a short voice note. Confirm the transcript and extracted fields appear for
   editing before any lead is created. Cancel the draft. If no transcription
   provider is configured, record voice as SKIP (not configured); voice is
   optional and is not a release blocker.
5. Open **Daily summary**. CRM facts must match stored data. Missing market
   information must stay unavailable and is never synthesized. A displayed
   market item requires a source URL, publication date, summary, and geographic area.
   A **deterministic fallback** is labeled for review.
6. Discord is optional and is tested only after dashboard acceptance. Confirm it
   reads through the same dedicated agent and sends writes to dashboard Pending
   approvals.

For Discord setup, run from the repository:

```bash
python3 scripts/setup_openclaw.py --bind-discord ACCOUNT
```

Replace `ACCOUNT` with the account identifier OpenClaw expects.

## 10. Send one complete test bundle

Return these items together so maintainers do not need to ask for one command
at a time:

- `openhouse-setup.txt`, containing the first and second setup runs
- `openhouse-compatibility.json`
- Pass or fail for dashboard CRM read
- Pass or fail for the reviewed disposable write
- Voice review: PASS with a configured provider, or SKIP (not configured)
- Pass or fail for the truthful daily briefing state
- Optional Discord result

Do not include `.env`, OpenClaw configuration files, tokens, CRM databases,
recordings, or screenshots containing client information.

## WSL recovery notes

- **OpenClaw or Ollama disappears after Windows restarts:** opening a WSL
  terminal starts the Linux environment, but previous Linux processes do not
  survive `wsl --shutdown`. Start the model runtime and gateway, then rerun the
  compatibility report.
- **OpenClaw is installed but the command is missing in a new terminal:** follow
  the OpenClaw installer's PATH instruction. For a local-prefix installation,
  this commonly means adding `~/.openclaw/bin` to your WSL shell PATH and
  opening a new terminal.
- **The OpenClaw user service waits for a login session:** enter the WSL
  distribution first, check `openclaw gateway status`, and follow OpenClaw's
  current Linux/WSL2 service guidance.
- **The model runs on CPU unexpectedly:** verify WSL GPU access and use the
  model runtime's current hardware troubleshooting guide. This is separate from
  CRM setup.
- **Setup reports incompatible OpenClaw configuration:** do not loosen policy
  manually. Send the complete test bundle so the compatibility adapter can be
  fixed against the real output.

For the security model, recovery details, and ordered acceptance checklist, see
[LOCAL-AI.md](LOCAL-AI.md).
