# Native First-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible local installation with verified reads and human-approved lead creation on one persistent CRM, plus an independent fresh-WSL acceptance procedure.

**Architecture:** A stdlib Python CLI prepares durable private state and supervises a backend and two isolated native gateways. The existing plugins and request/approval protocol remain unchanged. Runtime-only frontend mode information prevents the new installation from presenting legacy mock chat as its AI workflow.

**Tech Stack:** Python 3.12, FastAPI, SQLite, React/TypeScript/Vite, OpenClaw, local Ollama, pytest and Playwright. Ubuntu 24.04 on WSL2 is the first acceptance target.

**Spec:** [Approved first-run design](../specs/2026-10-05-native-first-run-design.md).

## Global Constraints

- Work in `codex/native-first-run`, based on merged `71558d5`; preserve original worktrees, CRM/config files and previous evidence.
- Two isolated gateway profiles, one persistent initially empty CRM, loopback only.
- Human and agent CRM credentials remain separate; gateways never receive the human key.
- Initial runtime candidates: OpenClaw `2026.8.1-beta.3 (5831b80)`, Ollama `0.32.15`, Node `24.15.0`, Python `3.12`.
- Model: `qwen3.5:9b`, digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`; context 16384, maxTokens 2048, thinking off, localModelLean false, no fallback.
- Verify artifact availability and runtime compatibility before publishing commands. Do not substitute latest or another model silently.
- No new CRM tool actions, inference retries, automatic reset/key rotation, migrations of real installations, service installation, cloud providers or remote exposure.
- `doctor` is read-only and does not infer unless the operator specifies `--live-read`.
- Clean Linux install and live acceptance are external gates; local stubs are identified as simulated.
- No merge, release, external message, or alteration of the other computer is implied by implementing this plan.

## Review Focus

Each risk below is assigned to a regression test rather than deferred to reviewer intuition.

1. A repository `.env` or ambient `VITE_API_URL` sends an unlocked dashboard's credential to a different API. Task 3 must build from a clean tracked-source copy with sanitized environment and prove same-origin API use.
2. A failed setup leaves files that a retry mistakes for a usable installation. Task 2 tests incomplete state, existing unrelated paths, symlinked ancestors and two competing setup processes.
3. Moving a prepared virtual environment or absolute gateway paths breaks the first launch. Task 3 keeps the venv at a stable checkout path and renders final-state paths before publication; Task 4 tests launch from a different cwd.
4. A service at the chosen port belongs to someone else or a child exits just after readiness. Task 4 tests port ownership and supervision; readiness uses authenticated gateway identity rather than a TCP-only check.
5. A released delayed tool call creates a proposal after the human closed the request. Task 6 provides a bounded acceptance-only relay and tests the actual API rejection, with no hidden inference retry.

## Files and interfaces

| File | Responsibility |
|---|---|
| `scripts/native_local.py` | CLI parsing, exit codes, sanitized output |
| `scripts/ohi_native/__init__.py` | Package marker |
| `scripts/ohi_native/runtime.py` | Candidate matrix, bounded local preflight, gateway config and child environments |
| `scripts/ohi_native/state.py` | Private state, validation, locks, atomic manifest publication |
| `scripts/ohi_native/setup.py` | Locked dependencies, clean dashboard build, database initialization, setup orchestration |
| `scripts/ohi_native/processes.py` | Foreground service ownership, readiness and teardown |
| `scripts/ohi_native/doctor.py` | Non-mutating diagnostics and explicit verified read |
| `backend/requirements-native.lock` | Hashed Linux/Python 3.12 dependency resolution |
| `backend/app/runtime_mode.py` | Validated native-only mode, default off |
| `dashboard/src/runtimeMode.tsx` | Nonsecret mode context from auth bootstrap |
| `scripts/native_first_run_relay.py` | Disposable acceptance relay only; never imported by production launcher |
| `docs/NATIVE-FIRST-RUN.md` | Operator installation/start/recovery guide |
| `docs/validation/native-first-run/` | Runbook, blank evidence template, later reviewed results |

Use paths below relative to the worktree. Do not import production functions from
`native_proposal_acceptance.py`; its synthetic fixture contract remains unchanged.
Reuse its configuration shape and process patterns by inspection, with explicit
production tests. Do not extract shared code solely to remove a few duplicated lines.

Core APIs (stdlib types; define these in their owning task):

```python
# runtime.py
@dataclass(frozen=True)
class InstallOptions:
    root: Path
    state: Path
    openclaw: Path
    node: Path
    ports: tuple[int, int, int] = (18080, 18880, 18881)

def preflight(options: InstallOptions) -> dict[str, str]: ...
def gateway_config(options: InstallOptions, kind: str, secret: str) -> dict: ...
def child_environments(options: InstallOptions, secrets: dict[str, str]) -> dict[str, dict[str, str]]: ...

# state.py
class InstallError(RuntimeError):
    code: str

def exclusive_lock(state: Path) -> ContextManager[None]: ...
def load_manifest(state: Path) -> dict: ...
def write_private(path: Path, content: str) -> None: ...
def validate_install(options: InstallOptions) -> dict: ...

# setup.py / processes.py / doctor.py
# Mapping results contain nonsecret metadata only; keys are defined in each task.
def setup_install(options: InstallOptions) -> dict: ...
def run_install(options: InstallOptions) -> int: ...
def diagnose(options: InstallOptions, live_read: bool = False) -> dict: ...
```

The ellipses above are interface declarations, not implementation stubs to ship.
Command errors use stable codes and concise corrective text. Never forward raw
subprocess stdout/stderr, config JSON or exception bodies to public diagnostics.
Private logs may retain detail and must have mode 0600.

### Task 1: Establish the reproducible runtime contract

**Files:** create `scripts/ohi_native/runtime.py`, `scripts/ohi_native/__init__.py`,
`backend/requirements-native.lock`, `backend/tests/test_native_local_runtime.py`,
`docs/validation/native-first-run/runtime-provenance.json`.

**Produces:** `InstallOptions`, `preflight`, `gateway_config`, `child_environments`.

- [ ] Read the approved spec, existing acceptance helper, both plugin manifests,
  `backend/requirements.txt`, and `dashboard/package-lock.json`.
- [ ] Read upstream artifact metadata without installing globally. Record version,
  artifact URL, upstream integrity/checksum, package engine requirement and the
  observation date. Check these exact candidates:

```text
https://registry.npmjs.org/openclaw/2026.8.1-beta.3
https://nodejs.org/dist/v24.15.0/SHASUMS256.txt
https://api.github.com/repos/ollama/ollama/releases/tags/v0.32.15
```

Require a real artifact fetch/checksum check before marking an artifact obtainable;
metadata alone is not a completed fresh installation. Inspect pinned OpenClaw
source/CLI help for a supported authenticated non-inference health/RPC probe.
Record the exact probe in the operator guide. If unavailable, report the specific
blocking candidate rather than continuing with a made-up compatibility matrix.

- [ ] Generate the Python lock with the installed verified uv version recorded in
  provenance. uv is a development lock-generation tool, not a user prerequisite.
  The runtime installer uses pip with required hashes.

```bash
uv pip compile --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes backend/requirements.txt -o backend/requirements-native.lock
```

A Linux CI installation must subsequently prove the lock. Do not claim that
cross-platform resolution constitutes that proof.

- [ ] Write failing tests for exact runtime/model validation, both profile configs,
  loopback endpoints, distinct ports, and environment exclusions. Example:

```python
def test_gateways_share_crm_but_never_receive_human_key(tmp_path, monkeypatch):
    from ohi_native.runtime import InstallOptions, child_environments, gateway_config
    monkeypatch.setenv('OHI_API_TOKEN', 'ambient-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-cloud-secret')
    options = InstallOptions(tmp_path/'repo', tmp_path/'state', tmp_path/'openclaw', tmp_path/'node')
    secrets = {name: name + '-test-secret' for name in ('human', 'agent', 'read', 'proposal')}
    envs = child_environments(options, secrets)
    assert envs['backend']['OHI_API_TOKEN'] == secrets['human']
    for kind in ('read', 'proposal'):
        assert envs[kind]['OHI_AGENT_API_TOKEN'] == secrets['agent']
        assert 'OHI_API_TOKEN' not in envs[kind]
        assert 'OPENAI_API_KEY' not in envs[kind]
    read = gateway_config(options, 'read', secrets['read'])
    proposal = gateway_config(options, 'proposal', secrets['proposal'])
    assert read['tools']['allow'] == ['openhouse_crm']
    assert proposal['tools']['allow'] == ['openhouse_propose_lead']
```

Add parametrized version mismatch, model digest mismatch, malformed/oversized
Ollama JSON, redirect, timeout and inherited proxy tests. Test messages contain no
provided secrets. Use mock transport metadata, never the operator's Ollama.

- [ ] Run `PYTHONPATH=scripts python -m pytest backend/tests/test_native_local_runtime.py -q`;
  confirm failure is missing implementation, then implement configuration and bounded
  preflight. Use fixed loopback Ollama endpoints, reject redirects and ignore proxies.
- [ ] Rerun focused tests; record the verified artifact limits and commit this task.

### Task 2: Durable state and safe repeated setup

**Files:** create `scripts/ohi_native/state.py`, `backend/tests/test_native_local_state.py`.

**Consumes:** `InstallOptions`. **Produces:** `InstallError`, `exclusive_lock`,
`write_private`, `load_manifest`, `validate_install`.

State layout:

```text
state/
  manifest.json                 # nonsecret schema_version=1, complete=true
  crm.db                       # plus SQLite journals, all private
  human.key
  agent.key
  read.key
  proposal.key
  read/home/.openclaw-ohi-native-read/openclaw.json
  read/workspace/AGENTS.md
  proposal/home/.openclaw-ohi-native-proposals/openclaw.json
  proposal/workspace/AGENTS.md
  logs/
```

The manifest includes source root/revision/content digest, build digest, lock
hashes, runtime paths/versions, model digest, ports and preparation time. It never
includes key values. Secret names above are the common mapping used by Task 1.

- [ ] Add failing tests for 0700 directories/0600 files, no-overwrite creation,
  missing files, wrong manifest version, bad permissions, unrelated state paths,
  symlinked targets/ancestors, and mismatching source/config/build fingerprints.
- [ ] Test lock ownership with two real child processes. Put the lock in a private
  validated sibling path and hold `fcntl.flock(LOCK_EX | LOCK_NB)` for the operation.
  Never unlink the lock path while a process can hold its inode.

```python
def test_private_write_refuses_replacement(tmp_path):
    from ohi_native.state import write_private
    path = tmp_path/'human.key'
    write_private(path, 'first')
    with pytest.raises(FileExistsError):
        write_private(path, 'second')
    assert path.read_text() == 'first'
    assert path.stat().st_mode & 0o777 == 0o600
```

- [ ] Run the new tests red, then implement validated private writes, schema checks
  and locks. Stage newly prepared state in a same-filesystem sibling. Completed
  manifests are written atomically; only a completed staged state can be published.
  On failure retain a clearly named incomplete private directory and return its
  location without deleting unrelated content. Do not treat it as completed state.
- [ ] Run tests green and commit. No actual user state is created by tests.

### Task 3: Setup command, dependency installation and empty shared database

**Files:** create `scripts/ohi_native/setup.py`, `scripts/native_local.py`,
`backend/tests/test_native_local_setup.py`; modify `.gitignore` for `.venv-native/`.

**Consumes:** Tasks 1–2. **Produces:** `setup_install(options)` and CLI `setup`.

CLI flags: `--state`, `--openclaw`, `--node`, `--port`, `--read-port`,
`--proposal-port`; defaults are the state/ports in the spec and resolved binaries
from PATH. Persist exact executable paths, do not rediscover a different binary
silently on start. Native Windows and unsupported OS/Python fail clearly.

- [ ] Write failing command-contract tests for invalid/duplicate ports, unsupported
  target, completed matching setup, failed dependency/build/config validation,
  and state unchanged after a second setup. Check all four keys and database hash.
- [ ] Add a build-isolation test that places `.env` with an external
  `VITE_API_URL` in the checkout and supplies a different ambient one. Assert the
  build input excludes both and the served bundle uses relative `/api`.
- [ ] Keep the Python environment at stable `root/.venv-native`; do not rename a
  virtual environment after installation. Acquire a checkout-level build lock as
  well as the state lock in a consistent order. Build from a private copy of the
  tracked dashboard inputs (excluding dotenv, node_modules and dist), using only
  a sanitized environment and a private npm cache. Use these subprocess shapes:

```python
subprocess.run([sys.executable, '-m', 'venv', str(root/'.venv-native')], check=True)
subprocess.run([str(root/'.venv-native/bin/python'), '-m', 'pip', 'install',
                '--require-hashes', '-r', str(root/'backend/requirements-native.lock')], check=True)
subprocess.run([npm_binary, 'ci', '--no-audit', '--no-fund'], cwd=build_copy, check=True)
subprocess.run([npm_binary, 'run', 'build'], cwd=build_copy, check=True)
```

Actual implementation supplies bounded timeouts, private logs, explicit env,
stdin isolation and exit handling to every call. Preserve system CA/proxy needs
for explicit dependency downloads only; never propagate credentials into runtime
children. Install/build failures do not launch services or serve an older build.

- [ ] Generate keys/configs in staged state. Render absolute config paths for the
  final state, checking validation behavior with the pinned CLI. Where validation
  needs existing paths, validate a temporary staging-path rendering first, then
  validate the final rendering immediately after publication before marking the
  installation complete. The manifest must remain incomplete if this fails.
  Initialize only the staged empty DB via the existing `init_db` in a sanitized
  subprocess; do not import it with the parent process's ambient DB settings.
- [ ] Add a real SQLite test for zero initial leads/proposals and both plugins'
  identical backend URL; preserve data and keys on repeat setup. Reject stale or
  mismatching installed state with recovery instructions, not reset/migration.
- [ ] Complete `setup_install` result keys `state`, `source_revision`, `created`
  and `next_command`; print only paths/status. Run focused tests and commit.

### Task 4: Foreground startup and non-mutating diagnostics

**Files:** create `scripts/ohi_native/processes.py`, `scripts/ohi_native/doctor.py`,
`backend/tests/test_native_local_processes.py`, `backend/tests/test_native_local_doctor.py`;
modify `scripts/native_local.py`.

**Consumes:** validated installation/options/environments. **Produces:**
`run_install(options) -> int`, `diagnose(options, live_read=False) -> dict` and CLI
`start`, `doctor [--live-read] [--json]`.

- [ ] Create protocol-faithful fake gateway child scripts in test temporary dirs.
  They support the Task 1 authenticated probe, private config validation and
  controllable early/late exit. They never stand in for real inference evidence.
- [ ] Test occupied ports with a real owned test socket, overlapping starts,
  wrong-service auth, wrong gateway identity, startup timeout, one child failing
  after readiness, SIGTERM, and bounded descendant teardown. Example assertion:

```python
def test_occupied_backend_port_is_not_killed(tmp_path):
    from ohi_native.processes import reserve_listener
    with socket.socket() as owner:
        owner.bind(('127.0.0.1', 0))
        owner.listen()
        with pytest.raises(OSError):
            reserve_listener(owner.getsockname()[1])
        assert owner.fileno() >= 0
```

Define `reserve_listener(port: int) -> socket.socket` in processes.py. Start the
backend with the inherited fd and module invocation through `.venv-native/bin/python`.
Start each gateway in a new process group with its private workspace/HOME. Retain
handles only for children this invocation created; never kill by a discovered PID.
Use a 30-second overall readiness bound, 5-second graceful teardown followed by
bounded kill/wait, and continual child supervision without a session time limit.

- [ ] Test start from another working directory, repository relocation, changed
  source/lock/build, mismatched runtime and modified credential/config files.
  Reject before spawning. No automatic fetching, rebuild or model pull on start.
- [ ] Write doctor tests for running/stopped/invalid states and zero mutation.
  Compare DB/key/config hashes; stub inference and assert zero calls by default.
  With `--live-read`, assert exactly one authenticated `/api/chat/directory` call,
  receipt validation and safe count/timing output; never send a proposal.
- [ ] Implement structured diagnosis: `installed`, `configured`, `reachable`,
  `live_read`, `checked_at`, `source_revision`, `issues`. Return exit 0 only when
  requested checks pass; exit 1 for stopped/unready and 2 for bad configuration or
  CLI usage. Never mark native proposal inference verified from a read result.
- [ ] Run focused process/doctor tests plus existing helper isolation tests.
  Commit only when teardown, lock release and redaction pass.

### Task 5: Native-only first-run presentation

**Files:** create `backend/app/runtime_mode.py`, `backend/tests/test_native_runtime_mode.py`,
`dashboard/src/runtimeMode.tsx`, `dashboard/tests/native-mode.browser.cjs`;
modify `backend/app/main.py`, `backend/app/routers/chat.py`, `dashboard/src/auth.ts`,
`dashboard/src/components/AuthGate.tsx`, `dashboard/src/components/ChatPanel.tsx`,
`dashboard/src/components/LocalBadge.tsx`, `scripts/test_native_read_browser.py`,
`.env.example`, `scripts/ohi_native/runtime.py`.

**Interfaces:** `native_only() -> bool` reads `OHI_NATIVE_ONLY` (unset/0 false, 1 true,
other values configuration error). The existing auth status adds optional
`workflow_mode: 'native' | 'standard'`. Missing mode in older responses means
standard. Invalid present values fail bootstrap. `useRuntimeMode()` reads a context
populated by AuthGate only after its validated bootstrap; credentials remain in memory.

- [ ] Add failing backend tests: flag validation, public nonsecret mode status,
  native mode without capability auth rejected at startup, and legacy POST `/api/chat`
  rejected before model calls or chat-message writes. Use HTTP 409 with stable
  `native_only` code; leave existing modes unchanged when flag is off.

```python
def test_native_mode_rejects_general_chat_without_persistence(client, monkeypatch):
    monkeypatch.setenv('OHI_NATIVE_ONLY', '1')
    monkeypatch.setenv('OHI_API_TOKEN', 'h'*32)
    monkeypatch.setenv('OHI_AGENT_API_TOKEN', 'a'*32)
    response = client.post('/api/chat', headers={'X-API-Token': 'h'*32},
                           json={'message': 'follow up', 'session_id': 'native-test'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'native_only'
    assert client.get('/api/chat/history?session_id=native-test',
                      headers={'X-API-Token': 'h'*32}).json() == []
```

Use the existing test fixtures' actual names/imports when implementing; preserve
real DB and middleware coverage. Add a model-call spy so status alone cannot pass.

- [ ] Extend the disposable browser runner with `--suite native-mode` and generated
  separate human/agent keys. Verify only CRM reads/Propose lead are shown, both
  workflows still work against the real API/simulated completion seam, refresh
  requires unlock, and the badge does not say mock or globally verified.
- [ ] Implement the minimal context/UI/backend boundary. Label native setup
  honestly; verified receipts remain the evidence for individual operations.
  Inspect optional AI controls in this mode: disable those with no configured
  backend or retain an explicit existing fallback label, never advertise native
  coverage for voice, scoring, general chat or integrations.
- [ ] Run the new tests, existing auth/read/proposal browser suites, frontend build
  and backend auth tests. Check standard/demo mode remains available when off.
  Commit this task.

### Task 6: Fresh-install guide, fault acceptance, and CI

**Files:** create `docs/NATIVE-FIRST-RUN.md`,
`docs/validation/native-first-run/ACCEPTANCE.md`,
`docs/validation/native-first-run/evidence-template.json`,
`scripts/native_first_run_relay.py`, `backend/tests/test_native_first_run_relay.py`;
modify `README.md`, `docs/SETUP.md`, `docs/NATIVE-CREATE-LEAD.md`,
`CONTRIBUTING.md`, `.github/workflows/ci.yml`.

**Interfaces:** Acceptance relay is a separate disposable tool. It accepts a fixed
loopback target, a private key-file path, a private control directory and one exact
request ID. It never logs a token/body, follows redirects, targets nonloopback
hosts, exposes a production flag or runs automatically during normal startup.

- [ ] Write tests for the relay before implementing it. Hold one real POST for the
  designated request, signal `captured.json` with ID and timestamp only, wait for a
  private `release` marker, then forward exactly once using a bounded local HTTP
  client. Its overall hold bound is 180 seconds; cancellation releases resources.
  Reject a second/mismatched request, oversized bodies, nonloopback targets and
  redirects. Record only response code/timing. The gateway plugin may time out
  while the relay holds; preserve that failure, then forward the already captured
  request once to test the backend's late-call rejection.
- [ ] Test the real proposal API with the relay: reserve a request, hold the agent
  submission, close with the human key, release the captured call, require 409 and
  no pending/lead insertion. Also test proposal-wins-close, unknown-on-restart and
  no redispatch. This automated probe is separate from the later actual model run.
- [ ] Write the new-user guide using only verified Task 1 artifact commands and
  the implemented CLI. Include fresh WSL/Ubuntu prerequisites, GPU check, private
  pinned OpenClaw install, Ollama/model installation and digest, clone/checkout,
  setup/start/unlock, zero-count read, edited lead approval and same-DB read,
  restart, doctor, shutdown and recovery. Do not use old acceptance fixtures as
  the installation. Show no credentials in command arguments or sample output.
- [ ] Write the independent acceptance runbook with an exact candidate commit
  supplied at handoff, command logging and all eight acceptance criteria from the
  spec. Include fresh private HOME/venv/npm/runtime/model provenance and old-data
  preservation. GPU driver sharing is disclosed. No original service is stopped.
- [ ] Document the relay case in a disposable installation only: back up/hash its
  proposal profile, change only its tool API target to the local relay, capture
  the real tool call before submission, observe uncertainty, restart backend,
  close, release, verify 409/no insertion, and restore the profile exactly. Keep
  application services needed for closure alive. Record the gateway timeout and
  model call count; a captured proposal arriving first is a different result.
- [ ] Add offline acceptance using an isolated network namespace with loopback
  up and GPU device access; start owned backend/gateways/Ollama within it using
  already downloaded artifacts. Record namespace/network configuration. Do not
  change the host firewall or confuse lack of cloud credentials with no egress.
- [ ] Add a Linux/Python 3.12 locked-dependency CI job using
  `pip install --require-hashes -r backend/requirements-native.lock`, the full
  backend suite and simulated orchestration tests. Keep existing plugin/build/
  browser coverage and add native-mode browser coverage. Do not label CI live AI.
- [ ] Run the complete appropriate checks once the final implementation is ready:

```bash
python -m pytest backend/tests -q
node --test openclaw-plugins/openhouse-read/test.mjs openclaw-plugins/openhouse-proposals/test.mjs
npm --prefix dashboard ci
npm --prefix dashboard run build
python scripts/test_native_read_browser.py --suite auth
python scripts/test_native_read_browser.py --suite read
python scripts/test_native_read_browser.py --suite proposals
python scripts/test_native_read_browser.py --suite native-mode
```

Use the actual isolated environment interpreter and installed browser runtime;
retain command exit codes, revision, simulation boundaries and limitations.

- [ ] Review the full branch for secret separation, state preservation, retries,
  cleanup ownership, stale artifacts, and fidelity to the approved spec. Fix
  actionable findings and rerun only the checks justified by changes.
- [ ] Commit docs/tests/CI and prepare a draft PR or offline transfer if publication
  is unavailable. Attach any PR to the task. Provide a copyable message for the
  other computer; do not send it automatically or claim acceptance is complete.

## Evidence template contract

The JSON template starts with `status: 'not_run'` and explicit null values, not
fabricated passes. It contains source/runtime/hardware identities, install steps,
case IDs, timestamps, outcome, inference/tool counts, latency, DB counts, expected
versus observed result, interruption method, network isolation, preservation,
cleanup, and artifact hashes. Secrets, raw prompts, private logs and real CRM data
are excluded. A sanitized failed case remains alongside its subsequent attempt.

## Self-review and handoff

Spec mapping: runtime/reproducibility → Task 1; state/idempotency → Task 2;
installation/shared empty DB → Task 3; start/doctor/ownership → Task 4;
honest UI → Task 5; clean-install/fault/offline evidence and contributor CI → Task 6.
All five Review Focus items have tests in their owning tasks.

Do not mark the overall first-run milestone complete until the other computer's
actual clean-install and interruption evidence has been reviewed. Implementation
can finish locally with an explicit external acceptance handoff still pending.

Execution recommendation: native execution in this session, then one independent
whole-branch review. The tasks share state/config/manifest interfaces tightly;
sequential implementation avoids unnecessary integration overhead.

References for lock generation:
- https://docs.astral.sh/uv/concepts/resolution/
- https://docs.astral.sh/uv/pip/compile/
- https://docs.astral.sh/uv/reference/cli/
