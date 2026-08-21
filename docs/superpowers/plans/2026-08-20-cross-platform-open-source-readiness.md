# Cross-Platform Open-Source Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing OpenClaw setup contract work against real stable and beta CLI output, document native macOS/Linux and Windows-through-WSL2 support, and produce one sanitized report for real-machine testing.

**Architecture:** Keep the CRM and OpenClaw integration architecture unchanged. Correct the verification boundary in `scripts/setup_openclaw.py`, extend the existing read-only doctor rather than adding another diagnostic system, and make support claims match tested platform boundaries. Security remains fail-closed through authoritative agent-tool readback and effective gateway approval checks.

**Tech Stack:** Python 3.11+, pytest, OpenClaw CLI JSON contracts, Bash launch scripts, Markdown documentation, React/Vite dashboard verification.

**Spec:** `docs/superpowers/specs/2026-08-20-cross-platform-open-source-readiness-design.md`

## Global Constraints

- Support Apple-silicon macOS and Linux natively; support Windows through WSL2, not native PowerShell.
- Keep 16 GB memory as the minimum supported local-AI baseline while treating model fit and speed as model-runtime concerns.
- Do not redesign dashboard chat, the OpenClaw transport, CRM tools, or approval flow.
- Do not pin users to a beta OpenClaw release or select behavior from a version string.
- Do not broaden the dedicated agent's tool or executable allowlists.
- Keep the gateway and CRM bound to loopback by default.
- Do not place usernames, home paths, environment values, tokens, CRM rows, chat text, or model responses in the shareable report.
- Preserve safe reruns, partial-install repair, rollback, SecretRef handling, and exact gateway approval validation.
- Write each production behavior change only after its regression test fails for the expected reason.
- Do not claim live hardware success from local mocks or CI.

---

### Task 1: Validate Real OpenClaw Sandbox Output on the Correct Surface

**Files:**
- Modify: `backend/tests/test_setup_openclaw.py:20-240, 1080-1240`
- Modify: `scripts/setup_openclaw.py:990-1001, 1190-1227, 1690-1705`

**Interfaces:**
- Consumes: JSON from `openclaw sandbox explain --agent <agent-id> --json`.
- Produces: `_validate_sandbox_explain(payload: Any, agent_id: str) -> None`.
- Preserves: `_validate_authoritative_tools(payload: Any) -> None` as the exact configured exec-host check.
- Preserves: `_validate_gateway_approval_payload(payload, agent_id, require_effective=True)` as the effective exec and approval-policy check.

- [ ] **Step 1: Add representative stable and beta sandbox fixtures**

Add two fixture constants near the fake CLI. Both must use the official nested shape and intentionally omit exec-host and exec-mode fields:

```python
OPENCLAW_SANDBOX_EXPLAIN_STABLE = {
    "docsUrl": "https://docs.openclaw.ai/sandbox",
    "agentId": "openhouse-crm",
    "sessionKey": "agent:openhouse-crm:main",
    "mainSessionKey": "agent:openhouse-crm:main",
    "sandbox": {
        "mode": "off",
        "scope": "session",
        "backend": "docker",
        "workspaceAccess": "none",
        "effectiveHostWorkspaceRoot": "/redacted/workspace",
        "runtimeWorkdir": "/redacted/workspace",
        "workspaceMounts": [],
        "workspaceSource": "direct",
        "sessionIsSandboxed": False,
        "tools": {"allow": [], "deny": [], "sources": {}},
    },
    "elevated": {"enabled": False, "allowedByConfig": False},
    "fixIt": [],
}

OPENCLAW_SANDBOX_EXPLAIN_BETA = {
    **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
    "sandbox": {
        **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
        "scope": "agent",
    },
}
```

The fixture paths remain redacted and contain no user-specific value.

- [ ] **Step 2: Add the failing real-contract regression test**

```python
@pytest.mark.parametrize(
    "sandbox_payload",
    [OPENCLAW_SANDBOX_EXPLAIN_STABLE, OPENCLAW_SANDBOX_EXPLAIN_BETA],
    ids=["stable-2026.7.1-2", "beta-2026.8.1-beta.2"],
)
def test_setup_accepts_real_sandbox_explain_contract(tmp_path, sandbox_payload):
    command = (
        "openclaw", "sandbox", "explain", "--agent", "openhouse-crm", "--json"
    )
    cli = FakeCLI({command: CommandResult(0, json.dumps(sandbox_payload), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
```

- [ ] **Step 3: Run the regression test and verify the expected failure**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py::test_setup_accepts_real_sandbox_explain_contract -q
```

Expected: both cases fail with `dedicated CRM agent exec host is not gateway` because the current validator asks `sandbox explain` for a field that command does not report.

- [ ] **Step 4: Add failing field-addressed rejection tests**

Parametrize mutations of the stable fixture and assert setup fails without a gateway restart:

```python
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({**OPENCLAW_SANDBOX_EXPLAIN_STABLE, "agentId": "main"}, "wrong agent"),
        ({**OPENCLAW_SANDBOX_EXPLAIN_STABLE, "sandbox": []}, "sandbox"),
        (
            {
                **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
                "sandbox": {
                    **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
                    "mode": "all",
                },
            },
            "mode",
        ),
        (
            {
                **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
                "sandbox": {
                    **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
                    "sessionIsSandboxed": True,
                },
            },
            "sandboxed",
        ),
    ],
)
def test_setup_rejects_unsafe_or_wrong_agent_sandbox_explain(tmp_path, payload, message):
    command = (
        "openclaw", "sandbox", "explain", "--agent", "openhouse-crm", "--json"
    )
    cli = FakeCLI({command: CommandResult(0, json.dumps(payload), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert message in result.render().lower()
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls
```

Add one compatibility test that removes `sessionIsSandboxed` but keeps explicit
`sandbox.mode: off`; this payload must pass.

- [ ] **Step 5: Implement explicit sandbox validation**

Replace `_contains_pair` with a field-addressed validator:

```python
def _validate_sandbox_explain(payload: Any, agent_id: str) -> None:
    root = _require_mapping(payload, "sandbox explain")
    if root.get("agentId") != agent_id:
        raise SetupConflict("sandbox explain returned the wrong dedicated agent")
    sandbox = _require_mapping(root.get("sandbox"), "sandbox explain sandbox")
    if sandbox.get("mode") != "off":
        raise SetupConflict("dedicated CRM agent sandbox mode is not off")
    if (
        "sessionIsSandboxed" in sandbox
        and sandbox.get("sessionIsSandboxed") is not False
    ):
        raise SetupConflict("dedicated CRM agent session is unexpectedly sandboxed")
```

Call `_validate_sandbox_explain(sandbox_payload, options.agent_id)` at the
existing post-write boundary. Do not add an exec-host fallback. Authoritative
tools and gateway approvals already prove that guarantee.

- [ ] **Step 6: Make the fake CLI default realistic**

Change the fake CLI's default `sandbox explain` response to the stable fixture.
Remove `_contains_pair` if no call sites remain. Keep all authoritative-tools
and effective gateway-policy tests unchanged.

- [ ] **Step 7: Run the focused setup suite**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -q
```

Expected: every setup test passes, including fresh creation, second run, partial
repair, rollback, keyed roster, legacy roster, SecretRef, and approval-policy
coverage.

- [ ] **Step 8: Commit Task 1**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "fix: validate real OpenClaw sandbox output"
```

---

### Task 2: Add a Sanitized Cross-Platform Compatibility Report

**Files:**
- Modify: `scripts/doctor.py:1-180`
- Modify: `backend/tests/test_doctor.py:1-110`

**Interfaces:**
- Produces: `system_checks() -> list[Check]` for OS, architecture, WSL, memory, commit, and dependency versions.
- Produces: `render_report(checks: list[Check], *, as_json: bool) -> str`.
- Extends: `python3 scripts/doctor.py --json --live-agent --live-crm`.
- Preserves: `run_checks(base_url, live_agent, live_crm, live_timeout) -> list[Check]`.

- [ ] **Step 1: Add failing platform classification tests**

Extract platform classification into a pure function so it can be tested without
pretending CI is another operating system:

```python
@pytest.mark.parametrize(
    ("system", "machine", "release", "expected"),
    [
        ("Darwin", "arm64", "25.0.0", "macOS arm64"),
        ("Linux", "x86_64", "6.8.0-generic", "Linux x86_64"),
        ("Linux", "x86_64", "6.6.87.2-microsoft-standard-WSL2", "Windows WSL2 x86_64"),
        ("Windows", "AMD64", "11", "native Windows AMD64 (unsupported; use WSL2)"),
    ],
)
def test_platform_label(system, machine, release, expected):
    assert doctor._platform_label(system, machine, release) == expected
```

- [ ] **Step 2: Add failing memory parser and baseline tests**

```python
def test_linux_memory_parser_returns_bytes():
    assert doctor._linux_memory_bytes("MemTotal:       16777216 kB\n") == 16 * 1024**3


@pytest.mark.parametrize(
    ("total", "level"),
    [(16 * 1024**3, "PASS"), (15 * 1024**3, "WARN"), (None, "WARN")],
)
def test_memory_check_uses_16_gib_baseline(total, level):
    assert doctor._memory_check(total).level == level
```

- [ ] **Step 3: Add failing shareable JSON tests**

Test a fixed check list rather than the live host:

```python
def test_json_report_is_structured_and_omits_local_paths():
    checks = [
        doctor.Check("PASS", "Platform", "Windows WSL2 x86_64"),
        doctor.Check("PASS", "OpenClaw", "2026.8.1-beta.2"),
        doctor.Check("PASS", "CRM capability", "crm_verified"),
    ]
    rendered = doctor.render_report(checks, as_json=True)
    payload = json.loads(rendered)
    assert payload == {
        "schema_version": 1,
        "checks": [
            {"level": "PASS", "name": "Platform", "detail": "Windows WSL2 x86_64"},
            {"level": "PASS", "name": "OpenClaw", "detail": "2026.8.1-beta.2"},
            {"level": "PASS", "name": "CRM capability", "detail": "crm_verified"},
        ],
    }
    assert str(Path.home()) not in rendered
```

Extend the CLI help test to require `--json`.

- [ ] **Step 4: Verify the new doctor tests fail**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_doctor.py -q
```

Expected: failures report missing `_platform_label`, `_linux_memory_bytes`,
`_memory_check`, `render_report`, and `--json`.

- [ ] **Step 5: Implement platform and memory checks with the standard library**

Use `platform.system()`, `platform.machine()`, and `platform.release()`. Detect
WSL case-insensitively from `microsoft` in the Linux release. Read Linux/WSL
memory from `/proc/meminfo`; on macOS call `sysctl -n hw.memsize` without a
shell. Native Windows remains explicitly unsupported and does not need a new
memory API in this change.

The memory check must render whole or one-decimal GiB and warn, rather than
crash, when memory cannot be determined.

- [ ] **Step 6: Implement sanitized version and commit checks**

Run version commands with `subprocess.run([...], shell=False, timeout=10)` and
keep only the first nonempty line, capped at 160 characters. Report:

```text
Product revision
Platform
Memory
Python
Node.js
npm
OpenClaw CLI
Ollama (optional)
```

Use only `git rev-parse --short HEAD` for the product revision. Change current
Node, dashboard-source, and database details so shareable output says the
version or availability state instead of printing local absolute paths.
OpenClaw remains a warning when absent because demo mode works without it;
Ollama remains optional because OpenClaw may use another provider.

- [ ] **Step 7: Implement plain and JSON renderers**

```python
def render_report(checks: list[Check], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "schema_version": 1,
                "checks": [dataclasses.asdict(check) for check in checks],
            },
            indent=2,
            sort_keys=True,
        )
    return "\n".join(
        f"{check.level:4}  {check.name}: {check.detail}" for check in checks
    )
```

Add `--json` to the CLI and print one renderer result. Preserve exit code 1
only when a check is `FAIL`.

- [ ] **Step 8: Run the doctor suite and a local shareable report**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_doctor.py -q
../../.venv/bin/python scripts/doctor.py --json
```

Expected: tests pass. The local command may exit 1 when the application server
is not running, but its output must be valid JSON and must not contain the local
home path or environment values.

- [ ] **Step 9: Commit Task 2**

```bash
git add scripts/doctor.py backend/tests/test_doctor.py
git commit -m "feat: add shareable compatibility report"
```

---

### Task 3: Document Mac, Linux, and Windows/WSL2 Setup Truthfully

**Files:**
- Modify: `README.md:15-105, 145-190`
- Modify: `docs/LOCAL-AI.md:1-75, 300-380`
- Modify: `docs/MAC-MINI-SETUP.md:1-35, 120-160`
- Create: `docs/WINDOWS-WSL-SETUP.md`

**Interfaces:**
- Produces: one beginner entry point for each supported platform route.
- Produces: one two-file Windows/WSL2 evidence bundle consisting of setup output and sanitized doctor JSON.

- [ ] **Step 1: Add the support matrix to the README**

State these exact boundaries:

```markdown
| Computer | Support |
|---|---|
| Apple-silicon Mac mini, 16 GB or more | Primary setup path |
| Linux x86_64 or ARM64, 16 GB or more | Supported |
| Windows 11 with WSL2, 16 GB or more | Supported through Linux in WSL2 |
| Native Windows PowerShell | Not currently supported |
```

Explain in plain language that 16 GB is enough for the CRM plus a modest
quantized model, while larger models need more memory. Link the Mac guide,
Windows/WSL2 guide, and general local-AI reference immediately below the table.

- [ ] **Step 2: Add the Windows/WSL2 beginner guide**

Write `docs/WINDOWS-WSL-SETUP.md` with short numbered sections:

1. What is supported.
2. Verify WSL2 and GPU/model runtime using current Microsoft, OpenClaw, and
   Ollama documentation links.
3. Clone into the WSL Linux filesystem, not `/mnt/c`.
4. Verify `python3`, `node`, `npm`, `openclaw`, `ollama`, and a simple model
   response.
5. Enable Chat Completions and configure `.env`.
6. Run setup and keep its redacted output:

```bash
python3 scripts/setup_openclaw.py 2>&1 | tee openhouse-setup.txt
```

7. Start the CRM in one WSL terminal:

```bash
bash scripts/serve.sh
```

8. Generate the single shareable report in a second WSL terminal:

```bash
python3 scripts/doctor.py --live-agent --live-crm --json | tee openhouse-compatibility.json
```

9. Run the ordered dashboard, reviewed-write, voice, briefing, and optional
   Discord checks.
10. Send `openhouse-setup.txt`, `openhouse-compatibility.json`, and the manual
    checklist result. State that neither file should contain tokens or CRM data,
    but the tester should still inspect attachments before sharing.

Include recovery notes for WSL user-service startup, Ollama restart after WSL
shutdown, and persisting the OpenClaw path in the WSL shell. Do not prescribe a
single vendor-specific GPU installation command that may become stale.

- [ ] **Step 3: Align the Mac and shared local-AI guides**

Clarify that the Mac mini is the primary target but not a hardware dependency.
Add the shareable report command to both guides. Explain that setup verifies
exec host and mode from the agent configuration and effective gateway approval
surface, while sandbox-explain verifies only direct sandbox state.

Keep dashboard acceptance before Discord. Keep the daily-brief requirement that
missing or unsourced market information remains unavailable.

- [ ] **Step 4: Run documentation consistency checks**

Run:

```bash
rg -n "native Windows|WSL2|16 GB|sandbox explain|shareable|openhouse-compatibility" README.md docs/LOCAL-AI.md docs/MAC-MINI-SETUP.md docs/WINDOWS-WSL-SETUP.md
rg -n "change global tools.exec|disable.*safety|expose.*18789|fabricat" README.md docs/LOCAL-AI.md docs/MAC-MINI-SETUP.md docs/WINDOWS-WSL-SETUP.md
git diff --check
```

Expected: support boundaries and report commands are consistent; no guide asks
users to broaden global execution policy or expose the unauthenticated gateway.

- [ ] **Step 5: Commit Task 3**

```bash
git add README.md docs/LOCAL-AI.md docs/MAC-MINI-SETUP.md docs/WINDOWS-WSL-SETUP.md
git commit -m "docs: add Windows WSL2 setup path"
```

---

### Task 4: Verify the Complete Branch and Prepare the Hardware Handoff

**Files:**
- Review: every file changed by Tasks 1 through 3
- Update when verification changes commands: `docs/WINDOWS-WSL-SETUP.md`

**Interfaces:**
- Consumes: the final implementation and documentation.
- Produces: a tested branch plus one ordered real-machine handoff.

- [ ] **Step 1: Run focused safety suites**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py backend/tests/test_doctor.py -q
```

Expected: all focused tests pass with zero failures.

- [ ] **Step 2: Run the complete backend suite**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests -q
```

Expected: all backend tests pass. Existing deprecation warnings may remain but
must be reported rather than described as clean output.

- [ ] **Step 3: Run the dashboard production build**

Run:

```bash
npm run build
```

Working directory: `dashboard/`

Expected: TypeScript and Vite complete with exit code 0.

- [ ] **Step 4: Inspect the shareable report for privacy**

Run the JSON doctor command with the application stopped if necessary, capture
its output, validate it with Python's JSON parser, and search it for the current
home path and common secret field names. A failed API health check is acceptable
in this local privacy probe; invalid JSON or leaked paths are not.

- [ ] **Step 5: Review the final diff against security invariants**

Confirm from the diff that:

- `DESIRED_TOOLS` still allows only `exec` and uses gateway allowlist mode;
- the executable allowlist still contains only the shipped CRM and daily-brief
  entrypoints;
- authoritative agent-tool readback remains exact;
- effective gateway approval validation remains exact;
- rollback and workspace-identity checks remain present;
- no global OpenClaw `tools.exec` write was added;
- no native Windows or all-hardware guarantee was added;
- no pre-existing unrelated file was changed.

- [ ] **Step 6: Run final repository checks**

Run:

```bash
git diff --check origin/main...HEAD
git status --short
```

Expected: no whitespace errors and only intentional branch changes.

- [ ] **Step 7: Deliver the Windows/WSL2 test bundle**

Give the tester these ordered stages, all in one message:

1. Pull the branch and record the commit.
2. Run setup once and save `openhouse-setup.txt`.
3. Run setup a second time to prove idempotence.
4. Start the product.
5. Generate `openhouse-compatibility.json` with both live flags.
6. Test dashboard CRM read and one reviewed disposable write.
7. Test voice review and truthful briefing empty state.
8. Optionally bind Discord, read CRM data, and propose one reviewed write.
9. Return both files and the manual results together.

Do not ask the tester to change OpenClaw policy manually between stages.

- [ ] **Step 8: Commit any verification-only documentation correction**

If and only if a verification command required a documentation correction:

```bash
git add docs/WINDOWS-WSL-SETUP.md
git commit -m "docs: correct compatibility test handoff"
```

Otherwise leave the existing task commits unchanged.
