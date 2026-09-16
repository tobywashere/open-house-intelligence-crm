# Native OpenClaw CRM Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unreliable model-generated CRM shell calls with one native, typed OpenClaw tool shared by dashboard chat and Discord while preserving the existing CRM API, audit log, approval queue, daily brief, and restricted-agent security boundary.

**Architecture:** Ship a dependency-free ESM OpenClaw plugin named `openhouse-crm` that registers `openhouse_crm`. The plugin validates a fixed operation catalog and invokes the existing copied Python wrapper with an argument vector and `shell: false`. `scripts/setup_openclaw.py` links and verifies the repository-owned plugin, exposes only `openhouse_crm` and `exec` to the dedicated agent, and removes the obsolete CRM executable approval while retaining the daily-brief executable approval.

**Tech Stack:** Python 3.11+, pytest, Node.js 22+, Node built-in test runner, ESM JavaScript, OpenClaw plugin and CLI contracts, FastAPI/SQLite, React/Vite verification.

**Spec:** `docs/superpowers/specs/2026-08-21-native-openclaw-crm-tool-design.md`

## Global Constraints

- Keep dashboard and Discord routed through the existing dedicated `openhouse-crm` agent.
- Do not add a dashboard-owned function loop or change the Chat Completions transport.
- Keep CRM HTTP, audit, human-approval, and truthful-briefing behavior authoritative in the existing backend.
- Never let model input select a program, file path, working directory, environment variable, URL, timeout, or shell syntax.
- Run the wrapper with a fixed argument vector, `shell: false`, a fixed timeout, and bounded output.
- Keep `exec` only for the deterministic daily-brief runner. Remove the CRM wrapper from gateway approvals.
- Preserve unrelated agents, plugins, plugin allowlists, and executable approvals.
- Treat an existing `openhouse-crm` plugin from another source path as a conflict, not something to overwrite.
- Setup must remain idempotent, dry-run safe, capability-based, secret-redacted, and recoverable after a failed run.
- Support Apple-silicon macOS and Linux natively, plus Windows 11 through WSL2. Native PowerShell remains unsupported.
- Write each production behavior change only after its focused regression test fails for the expected reason.
- Do not claim live supported-hardware success from mocks or local unit tests.

---

### Task 1: Create One Canonical CRM Operation Catalog

**Files:**
- Create: `skills/crm-db-operations/operations.json`
- Modify: `skills/crm-db-operations/cli.py`
- Create: `backend/tests/test_crm_operation_catalog.py`

**Interfaces:**
- `operations.json`: JSON array of unique operation names.
- `cli.py::_load_operation_names() -> tuple[str, ...]`.
- `cli.py::OPERATIONS: dict[str, Callable]` remains the dispatch map.

- [ ] **Step 1: Add failing catalog tests**

Test that the catalog is a nonempty list of unique identifier-like strings, that its set exactly matches the current public functions exposed by `cli.py::OPERATIONS`, and that every catalog name resolves in `skills/crm-db-operations/tools.py`.

- [ ] **Step 2: Run the focused test and confirm red**

Run: `cd backend && pytest tests/test_crm_operation_catalog.py -q`

Expected: FAIL because `operations.json` and `_load_operation_names` do not exist.

- [ ] **Step 3: Add the catalog and load it in the wrapper**

Move the existing tuple of names into `operations.json`. Load it relative to `cli.py`, reject a malformed catalog at import time, and build `OPERATIONS` only from those names. Keep the CLI input and output contract unchanged.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `cd backend && pytest tests/test_crm_operation_catalog.py -q`

- [ ] **Step 5: Commit**

```bash
git add skills/crm-db-operations/operations.json skills/crm-db-operations/cli.py backend/tests/test_crm_operation_catalog.py
git commit -m "refactor: centralize CRM operation catalog"
```

---

### Task 2: Implement the Shell-Free Plugin Runner

**Files:**
- Create: `openclaw-plugins/openhouse-crm/operations.json`
- Create: `openclaw-plugins/openhouse-crm/dist/runner.js`
- Create: `openclaw-plugins/openhouse-crm/test/runner.test.js`

**Interfaces:**
- `runCrmTool(input, context, runChild?) -> Promise<object>`.
- Input shape: `{operation: string, arguments?: object}`.
- Trusted context: `{workspaceDir: string}` supplied by OpenClaw.
- Child invocation: Python executable plus fixed workspace wrapper, operation, `--args`, and serialized arguments.

- [ ] **Step 1: Add failing Node tests**

Cover a successful read, a pending write response, unknown operation, missing or malformed arguments, oversized arguments, missing workspace, timeout, child failure, oversized output, invalid JSON, and wrapper `{ok:false}` output. Assert the injected child runner receives an argv array and options containing `shell: false`, a fixed timeout, and a bounded buffer.

- [ ] **Step 2: Run the plugin test and confirm red**

Run: `node --test openclaw-plugins/openhouse-crm/test/runner.test.js`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement the minimal runner**

Validate plain-object inputs, operation membership, and serialized argument size. Resolve only `<workspaceDir>/skills/crm-db-operations/cli.py`, invoke it without a shell, parse the existing structured wrapper response, and return bounded sanitized errors without absolute paths, environment values, stack traces, or raw stderr.

- [ ] **Step 4: Prove catalog parity**

Extend `backend/tests/test_crm_operation_catalog.py` to require that the plugin copy and Python canonical catalog match byte-for-byte or as exact parsed arrays. Document that setup copies the plugin from the repository and the parity test prevents drift.

- [ ] **Step 5: Run both focused suites and confirm green**

Run:

```bash
node --test openclaw-plugins/openhouse-crm/test/runner.test.js
cd backend && pytest tests/test_crm_operation_catalog.py -q
```

- [ ] **Step 6: Commit**

```bash
git add openclaw-plugins/openhouse-crm skills/crm-db-operations/operations.json backend/tests/test_crm_operation_catalog.py
git commit -m "feat: add shell-free CRM tool runner"
```

---

### Task 3: Register the Native OpenClaw Tool

**Files:**
- Create: `openclaw-plugins/openhouse-crm/openclaw.plugin.json`
- Create: `openclaw-plugins/openhouse-crm/package.json`
- Create: `openclaw-plugins/openhouse-crm/dist/index.js`
- Create: `openclaw-plugins/openhouse-crm/test/plugin.test.js`

**Interfaces:**
- Plugin ID: `openhouse-crm`.
- Tool ID: `openhouse_crm`.
- Manifest contract: `contracts.tools = ["openhouse_crm"]`.
- Tool schema: operation enum plus optional object `arguments`, with no extra top-level properties.

- [ ] **Step 1: Add failing registration and manifest tests**

Use a fake plugin API and tool context. Assert the plugin registers exactly one tool factory, names it `openhouse_crm`, exposes the exact operation enum, passes `workspaceDir` to the runner, declares its tool contract, has strict empty plugin configuration, and has no runtime dependencies or install script.

- [ ] **Step 2: Run the tests and confirm red**

Run: `node --test openclaw-plugins/openhouse-crm/test/*.test.js`

- [ ] **Step 3: Add the ESM entrypoint and package metadata**

Use `definePluginEntry` from OpenClaw's supported plugin SDK entrypoint. Register the tool with a raw JSON schema so operator setup does not download TypeBox or a compiler. Keep the shipped JavaScript directly runnable.

- [ ] **Step 4: Run plugin tests and confirm green**

Run: `node --test openclaw-plugins/openhouse-crm/test/*.test.js`

- [ ] **Step 5: Commit**

```bash
git add openclaw-plugins/openhouse-crm
git commit -m "feat: register native OpenClaw CRM tool"
```

---

### Task 4: Make Setup Install, Repair, and Verify the Plugin

**Files:**
- Modify: `scripts/setup_openclaw.py`
- Modify: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Plugin source: `<repo>/openclaw-plugins/openhouse-crm`.
- Install/refresh: `openclaw plugins install --link <path> --force`.
- Enable: `openclaw plugins enable openhouse-crm`.
- Inventory: `openclaw plugins list --json` and `openclaw plugins inspect openhouse-crm --runtime --json`.
- Approval removal: `openclaw approvals allowlist remove --agent <id> --gateway <wrapper>`.
- Exact agent tools: `allow = ["openhouse_crm", "exec"]` plus the existing general-tool deny set and gateway allowlist exec policy.

- [ ] **Step 1: Extend the fake CLI and add failing setup tests**

Model plugin help, install, enable, list, inspect, and approval removal. Add tests for fresh install, rerun, upgrade from the existing exec-only setup, partial-install repair, foreign same-ID conflict, dry run, plugin failure rollback, secret redaction, exact model-visible tools, exact daily-only executable approval, and no gateway restart before all validations pass.

- [ ] **Step 2: Run the setup suite and confirm red**

Run: `cd backend && pytest tests/test_setup_openclaw.py -q`

- [ ] **Step 3: Add capability preflight**

Require plugin install/list/inspect/enable commands and flags, plus approval `remove`. Inspect existing plugin inventory before mutation. Accept absent or repository-linked ownership. Reject a same-ID plugin that resolves outside the repository package.

- [ ] **Step 4: Add idempotent install and repair actions**

Link/refresh the source-controlled package, enable it, set exact agent tools, remove the legacy CRM wrapper approval when present, add or retain the daily runner approval, and preserve unrelated plugin configuration and other agents' approvals.

- [ ] **Step 5: Add post-write runtime verification and rollback**

Validate the manifest and active runtime inspection exposes `openhouse_crm`. Verify exact agent tools and daily-only gateway approvals. Restore CRM-owned agent and plugin state if a later setup phase fails. Restart the gateway only after successful validation.

- [ ] **Step 6: Run focused setup tests and confirm green**

Run: `cd backend && pytest tests/test_setup_openclaw.py -q`

- [ ] **Step 7: Commit**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "feat: install and verify OpenClaw CRM plugin"
```

---

### Task 5: Point the Agent and Health Probe at the Real Tool

**Files:**
- Modify: `skills/crm-db-operations/SKILL.md`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/tests/test_openclaw.py`

**Interfaces:**
- Model-facing call: `openhouse_crm({operation, arguments})`.
- Capability operation: `generate_dashboard_insights` with the existing unique `probe_nonce`.
- Capability success remains a matching new backend audit row, not assistant text.

- [ ] **Step 1: Change the probe test first**

Require the exact registered tool name, JSON input shape, read-only operation, and nonce. Assert the prompt says the skill slug is not a tool and forbids `exec` for CRM. Keep the existing negative tests proving plausible text cannot produce `crm_verified` without a matching audit row.

- [ ] **Step 2: Run the focused test and confirm red**

Run: `cd backend && pytest tests/test_openclaw.py -q`

- [ ] **Step 3: Update the prompt and skill guidance**

Replace CLI invocation guidance with `openhouse_crm`. State clearly that `crm-db-operations` is a skill name, never a tool ID, and CRM operations must never use `exec`. Preserve the full operation table, factual-data rules, and pending-approval language.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `cd backend && pytest tests/test_openclaw.py tests/test_setup_openclaw.py -q`

- [ ] **Step 5: Commit**

```bash
git add skills/crm-db-operations/SKILL.md backend/app/agent/openclaw.py backend/tests/test_openclaw.py
git commit -m "fix: route CRM prompts through native tool"
```

---

### Task 6: Make Open-Source Setup and Retesting Clear

**Files:**
- Modify: `README.md`
- Modify: `docs/LOCAL-AI.md`
- Modify: `docs/MAC-MINI-SETUP.md`
- Modify: `docs/WINDOWS-WSL-SETUP.md`
- Modify: `docs/GB10-SETUP.md`
- Modify: `docs/CONTRACT.md`

**Interfaces:**
- Beginner setup remains one documented setup command plus one compatibility report command.
- Troubleshooting distinguishes plugin registration, chat transport, and audited CRM capability.
- Hardware floor remains 16 GB host memory.

- [ ] **Step 1: Add or update documentation assertions where existing tests cover wording**

Require docs to name `openhouse_crm`, explain that the installer links the bundled local plugin without downloading a build toolchain, and show one runtime inspection command. Remove instructions that tell users or agents to run CRM through `exec`.

- [ ] **Step 2: Update beginner-facing documentation**

Explain in plain language what the plugin does, why writes appear under Pending approvals, how dashboard and Discord share the same CRM agent, and what `crm_verified` proves. Keep Mac mini as the primary path, Linux and WSL2 as supported alternatives, and native PowerShell unsupported.

- [ ] **Step 3: Run all automated verification**

Run:

```bash
node --test openclaw-plugins/openhouse-crm/test/*.test.js
cd backend && pytest -q
cd ../dashboard && npm run build
```

Expected: all plugin and backend tests pass; dashboard production build succeeds. If loopback tests are sandbox-blocked, rerun the backend suite with approved local-loopback access and record both results accurately.

- [ ] **Step 4: Review the diff against the approved spec**

Confirm no app redesign, no shell-selected command, no broadened tool access, no direct write bypass, no fabricated verification, no user-specific paths, and no unredacted secrets. Confirm setup handles a fresh machine and the already partially configured test machine.

- [ ] **Step 5: Commit and push PR #7**

```bash
git add README.md docs openclaw-plugins scripts skills backend
git commit -m "docs: explain reliable local CRM chat setup"
git push origin codex/openclaw-setup-compat
```

---

## One-Pass Supported-Hardware Acceptance

After the branch is pushed, ask the Windows/WSL2 tester for one clean evidence bundle, not incremental manual edits:

1. Pull the updated PR branch and run `python3 scripts/setup_openclaw.py` twice.
2. Run the compatibility report with live agent and live CRM checks.
3. Ask dashboard chat to list real leads and record the audited read.
4. Ask dashboard chat to create one disposable lead, verify it appears only in Pending approvals, then deny it.
5. Verify a missing market summary produces no fabricated briefing facts.
6. Ask Discord for the same safe lead list through the dedicated agent.
7. Confirm logs contain neither `Unknown tool id` nor `execution-plan-miss` for CRM operations.

Voice transcription-provider setup and the WSL 15.3 GiB reporting tolerance remain separate follow-ups unless they block this CRM acceptance run.
