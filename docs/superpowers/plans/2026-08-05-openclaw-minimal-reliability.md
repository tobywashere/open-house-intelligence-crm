# OpenClaw Minimal Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard and Discord CRM chat reliably use a dedicated OpenClaw agent, prove real CRM capability, require review for user-facing writes, and ship beginner-ready Mac mini and Linux setup without rewriting the chat architecture.

**Architecture:** Keep the existing `/v1/chat/completions` relay and Markdown-plus-Python OpenClaw skills. Add explicit `openhouse-crm` agent routing, an idempotent setup helper, a read-only audited capability probe, and narrow extensions to the existing pending-change and health systems. Defer a native OpenClaw plugin and backend-owned tool loop unless supported-hardware verification proves the existing skill path unreliable.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, stdlib setup tooling, httpx, pytest, React 18, TypeScript, Vite, OpenClaw CLI.

## Global Constraints

- The supported OpenClaw agent identifier is `openhouse-crm`; the skill name remains `crm-db-operations`.
- Keep `/v1/chat/completions`; do not add a backend function-calling loop or native OpenClaw plugin.
- A 16 GB Apple-silicon Mac mini is the minimum supported Mac target; Linux and the existing GB10 path remain supported.
- Keep the backend and Gateway on localhost by default. Do not enable cloud providers, Composio, or a mailbox poller automatically.
- Setup must be idempotent, support `--dry-run`, redact secrets, and preserve unrelated OpenClaw agents, channels, providers, models, and credentials.
- Agent execution permission must be isolated to the dedicated CRM agent and disclosed to the operator.
- Host execution must use OpenClaw `allowlist` mode and allow only the shipped `crm-db-operations/cli.py` and `daily-brief/scripts/run_daily_brief.py` entry points for `openhouse-crm`; do not allowlist the `python3` interpreter generally.
- Safe reads may run immediately. Creates, updates, notes, appointments, reminders, closes, merges, and deletes require editable operator approval.
- Voice-note intake remains prepare-only until operator confirmation.
- Real OpenClaw mode must never display sample, placeholder, or unsupported briefing facts.
- Use additive database changes only. Do not replace SQLite or reorganize the dashboard.
- Do not add a runtime dependency solely to parse OpenClaw configuration. Use supported CLI JSON output and stdlib code.

---

## File Map

### New files

- `scripts/setup_openclaw.py`: idempotent macOS/Linux setup for the dedicated CRM agent and per-agent skill workspace.
- `skills/crm-db-operations/cli.py`: executable JSON-argument dispatcher for the existing HTTP client, limited to named CRM operations.
- `backend/tests/test_setup_openclaw.py`: subprocess-free unit tests for setup decisions, dry-run behavior, config preservation, and redaction.
- `backend/tests/test_skill_cli.py`: dispatcher validation, JSON output, and rejection of unknown operations.

### Existing files with focused changes

- `backend/app/agent/openclaw.py`: explicit agent model selection, fresh-session capability prompt, and labeled fallbacks.
- `backend/app/agent/base.py`: capability-check method shared by driver implementations.
- `backend/app/agent/status.py`: separate chat and CRM verification state plus fallback counters.
- `backend/app/routers/misc.py`: health response and audited read-only CRM capability endpoint.
- `scripts/doctor.py`: configuration checks and `--live-crm` verification.
- `.env.example`: `AGENT_ID=openhouse-crm` and clarified readiness/fallback variables.
- `backend/app/routers/leads.py`: queue and replay agent-proposed notes.
- `backend/app/routers/calendar.py`: queue and replay agent-proposed appointments before hooks.
- `backend/app/routers/misc.py`: queue and replay agent-proposed reminders before hooks.
- `backend/app/routers/pending_changes.py`: dispatch the three added operation types.
- `skills/crm-db-operations/tools.py`: expose `add_note` and preserve agent attribution.
- `skills/crm-db-operations/SKILL.md`: document note use and pending responses.
- `dashboard/src/api.ts`: truthful health types and expanded pending operation union.
- `dashboard/src/components/LocalBadge.tsx`: distinguish endpoint, chat, CRM, and degraded states.
- `dashboard/src/components/PendingApprovals.tsx`: editable note, appointment, and reminder previews.
- `backend/app/routers/voice.py`: surface deterministic extraction fallback in review warnings.
- `skills/business-card-scanner/SKILL.md`: remove hardcoded managed-skill path.
- `skills/daily-brief/SKILL.md`: use `{baseDir}` for the runner.
- `skills/daily-command-center/SKILL.md`: make failure and empty-state language explicit.
- `backend/tests/test_openclaw.py`: agent routing, fresh sessions, and fallback tracking.
- `backend/tests/test_doctor.py`: CLI and live CRM check output.
- `backend/tests/test_pending_changes.py`: note, appointment, reminder, denial, and hook timing.
- `backend/tests/test_skill_tools.py`: `add_note` contract.
- `backend/tests/test_voice.py`: fallback warning propagation.
- `backend/tests/test_reports.py`: honest empty and invalid briefing behavior.
- `backend/tests/test_daily_brief_skill.py`: location-independent command contract.
- `README.md`, `docs/LOCAL-AI.md`, `docs/MAC-MINI-SETUP.md`, `docs/GB10-SETUP.md`, `docs/CONTRACT.md`: one synchronized setup and capability contract.

---

### Task 1: Route the Backend to the Dedicated CRM Agent

**Files:**
- Modify: `backend/app/agent/openclaw.py:22-84`
- Modify: `.env.example:48-64`
- Test: `backend/tests/test_openclaw.py`

**Interfaces:**
- Consumes: `AGENT_ID: str`, default `openhouse-crm`.
- Produces: `openclaw_model(agent_id: str | None) -> str`, returning `openclaw/<id>` or compatibility fallback `openclaw`.
- Preserves: existing `_send(message: str, session_id: str) -> str` behavior and Chat Completions payload shape.

- [ ] **Step 1: Write failing routing tests**

Extend the fake client so it captures the submitted JSON, then add:

```python
def test_send_targets_configured_crm_agent(monkeypatch):
    import app.agent.openclaw as module

    fake = FakeClient()
    monkeypatch.setattr(module, "AGENT_ID", "openhouse-crm")
    driver = OpenClawDriver(client_factory=lambda **_: fake)

    assert asyncio.run(driver.chat("List leads", "dash-fresh")) == "READY"
    assert fake.last_post_json["model"] == "openclaw/openhouse-crm"
    assert fake.last_post_json["user"] == "dash-fresh"


def test_blank_agent_id_keeps_openclaw_default_compatibility(monkeypatch):
    import app.agent.openclaw as module

    fake = FakeClient()
    monkeypatch.setattr(module, "AGENT_ID", "")
    driver = OpenClawDriver(client_factory=lambda **_: fake)

    asyncio.run(driver.chat("hello", "compat"))
    assert fake.last_post_json["model"] == "openclaw"
```

Update `FakeClient.post` to assign `self.last_post_json = kwargs["json"]`.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_openclaw.py::test_send_targets_configured_crm_agent backend/tests/test_openclaw.py::test_blank_agent_id_keeps_openclaw_default_compatibility -v
```

Expected: FAIL because the payload still hardcodes `"model": "openclaw"`.

- [ ] **Step 3: Implement minimal explicit routing**

In `openclaw.py`, add:

```python
AGENT_ID = os.environ.get("AGENT_ID", "openhouse-crm").strip()


def openclaw_model(agent_id: str | None = None) -> str:
    selected = AGENT_ID if agent_id is None else agent_id.strip()
    return f"openclaw/{selected}" if selected else "openclaw"
```

Replace the hardcoded payload value with:

```python
"model": openclaw_model(),
```

Add this documented value to `.env.example`:

```dotenv
# Dedicated OpenClaw agent used by dashboard chat. The setup helper creates it.
# Leave blank only to use OpenClaw's default agent as a compatibility fallback.
AGENT_ID=openhouse-crm
```

Also change the module docstring from GB10-specific wording to local OpenClaw Gateway wording and remove references to nonexistent `agent/prompts` and `agent/skills` directories.

- [ ] **Step 4: Run the driver suite**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_openclaw.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the routing change**

```bash
git add backend/app/agent/openclaw.py backend/tests/test_openclaw.py .env.example
git commit -m "fix: target the dedicated OpenClaw CRM agent"
```

---

### Task 2: Add Safe, Idempotent OpenClaw Setup

**Files:**
- Create: `scripts/setup_openclaw.py`
- Create: `skills/crm-db-operations/cli.py`
- Create: `backend/tests/test_setup_openclaw.py`
- Create: `backend/tests/test_skill_cli.py`
- Modify: `skills/business-card-scanner/SKILL.md`
- Modify: `skills/daily-brief/SKILL.md`
- Modify: `skills/daily-command-center/SKILL.md`
- Test: `backend/tests/test_daily_brief_skill.py`

**Interfaces:**
- Produces: `SetupOptions(agent_id, workspace, crm_api_url, bind_discord, dry_run)` dataclass.
- Produces: `OpenClawCLI.run(args: list[str], *, mutate: bool = False) -> CommandResult`.
- Produces: `build_setup_actions(options, agents) -> list[Action]`, where each action has `description`, `argv`, and `mutates`.
- Produces: `sync_skills(repo: Path, workspace: Path, *, dry_run: bool) -> list[Path]`, returning the canonical installed skill paths.
- Produces: `configure_openclaw(options: SetupOptions, cli: OpenClawCLI) -> SetupResult`.
- Produces command: `{baseDir}/cli.py <operation> --args '<JSON object>'`.
- Command: `python3 scripts/setup_openclaw.py [--dry-run] [--agent-id openhouse-crm] [--workspace PATH] [--crm-api-url URL] [--bind-discord ACCOUNT]`.
- Uses official CLI surfaces only: `openclaw --version`, `agents list --json`, `agents add`, `config get/set/validate`, `skills check --agent`, `sandbox explain --agent`, `agents bind`, and `gateway restart`.

- [ ] **Step 1: Write failing setup tests**

Create tests around pure action planning and a fake command runner:

```python
def make_options(tmp_path, *, dry_run=False):
    return SetupOptions(
        agent_id="openhouse-crm",
        workspace=tmp_path / "workspace-openhouse-crm",
        crm_api_url="http://localhost:8080/api",
        bind_discord=None,
        dry_run=dry_run,
    )


class FakeCLI:
    def __init__(self):
        self.calls = []
        self.mutating_calls = []

    def run(self, args, *, mutate=False):
        self.calls.append(args)
        if mutate:
            self.mutating_calls.append(args)
        return CommandResult(0, "{}", "")


@pytest.fixture
def fake_cli():
    return FakeCLI()


def test_missing_agent_plan_creates_only_dedicated_agent(tmp_path):
    options = SetupOptions(
        agent_id="openhouse-crm",
        workspace=tmp_path / "workspace-openhouse-crm",
        crm_api_url="http://localhost:8080/api",
        bind_discord=None,
        dry_run=False,
    )
    actions = build_setup_actions(options, agents=[{"id": "main"}])
    argv = [action.argv for action in actions]
    assert ["openclaw", "agents", "add", "openhouse-crm", "--workspace",
            str(options.workspace), "--non-interactive", "--json"] in argv
    assert all("main" not in " ".join(command) for command in argv)


def test_existing_agent_is_not_recreated(tmp_path):
    options = make_options(tmp_path)
    actions = build_setup_actions(
        options,
        agents=[{"id": "openhouse-crm", "workspace": str(options.workspace)}],
    )
    assert not any(action.argv[1:3] == ["agents", "add"] for action in actions)


def test_conflicting_agent_workspace_requires_explicit_repair(tmp_path):
    options = make_options(tmp_path)
    with pytest.raises(SetupConflict, match="different workspace"):
        build_setup_actions(
            options,
            agents=[{"id": "openhouse-crm", "workspace": "/another/workspace"}],
        )


def test_dry_run_never_executes_mutating_commands(fake_cli, tmp_path):
    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=fake_cli)
    assert result.ok
    assert fake_cli.mutating_calls == []


def test_output_redacts_api_token(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "secret-value")
    result = configure_openclaw(make_options(tmp_path), cli=fake_cli)
    assert "secret-value" not in result.render()


def test_setup_allowlists_only_shipped_skill_entrypoints(tmp_path):
    options = make_options(tmp_path)
    actions = build_setup_actions(options, agents=[{"id": "openhouse-crm"}])
    rendered = [" ".join(action.argv) for action in actions]
    wrapper = str(options.workspace / "skills/crm-db-operations/cli.py")
    daily = str(options.workspace / "skills/daily-brief/scripts/run_daily_brief.py")
    assert any("approvals allowlist add" in command and wrapper in command for command in rendered)
    assert any("approvals allowlist add" in command and daily in command for command in rendered)
    assert not any(command.endswith(" python3") for command in rendered)
```

Create `backend/tests/test_skill_cli.py` with a subprocess-free dispatcher test:

```python
def test_dispatch_calls_only_named_tool(monkeypatch):
    calls = []
    monkeypatch.setitem(skill_cli.OPERATIONS, "list_leads", lambda **kw: calls.append(kw) or [{"id": 1}])
    result = skill_cli.dispatch("list_leads", {"sort": "priority"})
    assert result == [{"id": 1}]
    assert calls == [{"sort": "priority"}]


def test_dispatch_rejects_unknown_operation():
    with pytest.raises(ValueError, match="unknown CRM operation"):
        skill_cli.dispatch("shell", {"command": "whoami"})


def test_cli_requires_json_object_arguments():
    result = subprocess.run(
        [sys.executable, str(CLI), "list_leads", "--args", "[]"],
        text=True, capture_output=True,
    )
    assert result.returncode == 2
    assert "JSON object" in result.stderr
```

Add a skill-text test:

```python
def test_installed_skill_commands_are_location_independent():
    daily = (REPO_ROOT / "skills/daily-brief/SKILL.md").read_text()
    card = (REPO_ROOT / "skills/business-card-scanner/SKILL.md").read_text()
    assert "{baseDir}/scripts/run_daily_brief.py" in daily
    assert "python3 {baseDir}" not in daily
    assert "~/.openclaw/skills/crm-db-operations" not in card
```

- [ ] **Step 2: Run setup and skill tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py backend/tests/test_daily_brief_skill.py -v
```

Expected: FAIL because the setup helper, restricted CLI wrapper, and their interfaces do not exist and the skills use hardcoded paths.

- [ ] **Step 3: Add setup data types, CLI wrapper, and pure action planning**

Use these concrete data types in `scripts/setup_openclaw.py`:

```python
@dataclass(frozen=True)
class SetupOptions:
    agent_id: str
    workspace: Path
    crm_api_url: str
    bind_discord: str | None
    dry_run: bool


@dataclass(frozen=True)
class Action:
    description: str
    argv: list[str]
    mutates: bool = True


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SetupResult:
    ok: bool
    messages: list[str]

    def render(self) -> str:
        return "\n".join(self.messages)


class OpenClawCLI:
    def run(self, args: list[str], *, mutate: bool = False) -> CommandResult:
        command = args if args and args[0] == "openclaw" else ["openclaw", *args]
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class SetupConflict(RuntimeError):
    pass


def build_setup_actions(options: SetupOptions, agents: list[dict]) -> list[Action]:
    actions = []
    existing = next((agent for agent in agents if agent.get("id") == options.agent_id), None)
    if existing and Path(existing.get("workspace", "")).expanduser() != options.workspace.expanduser():
        raise SetupConflict(
            f"agent {options.agent_id} already uses a different workspace"
        )
    if existing is None:
        actions.append(Action(
            "Create dedicated CRM agent",
            ["openclaw", "agents", "add", options.agent_id, "--workspace",
             str(options.workspace), "--non-interactive", "--json"],
        ))
    if options.bind_discord:
        actions.append(Action(
            "Bind Discord account",
            ["openclaw", "agents", "bind", "--agent", options.agent_id,
             "--bind", f"discord:{options.bind_discord}", "--json"],
        ))
    wrapper = options.workspace / "skills/crm-db-operations/cli.py"
    actions.append(Action(
        "Allow only the CRM command wrapper",
        ["openclaw", "approvals", "allowlist", "add", "--agent",
         options.agent_id, str(wrapper)],
    ))
    daily = options.workspace / "skills/daily-brief/scripts/run_daily_brief.py"
    actions.append(Action(
        "Allow only the deterministic daily brief runner",
        ["openclaw", "approvals", "allowlist", "add", "--agent",
         options.agent_id, str(daily)],
    ))
    return actions


def sync_skills(repo: Path, workspace: Path, *, dry_run: bool) -> list[Path]:
    names = ["crm-db-operations", "business-card-scanner",
             "daily-command-center", "daily-brief"]
    targets = [workspace / "skills" / name for name in names]
    if not dry_run:
        for name, target in zip(names, targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(repo / "skills" / name, target, dirs_exist_ok=True)
    return targets
```

- [ ] **Step 4: Implement guarded preflight, mutation, and skill synchronization**

`configure_openclaw` must first run every read-only capability and configuration
query. If any query fails, return a failed `SetupResult` before calling
`sync_skills` or a mutating action. After preflight succeeds, copy skills and
execute each mutating action only when `options.dry_run` is false. After each
mutation, check the return code and stop before the next mutation on failure.
Only after all mutations succeed may it validate configuration and restart the
Gateway. `SetupResult.render()` must replace environment token values with
`<redacted>`.

Create `skills/crm-db-operations/cli.py` as an executable dispatcher, not a
general Python evaluator:

```python
#!/usr/bin/env python3
import argparse
import json
import sys

import tools

OPERATIONS = {
    name: getattr(tools, name)
    for name in (
        "create_lead", "update_lead", "close_lead", "find_duplicate_leads",
        "merge_leads", "get_lead_context", "list_leads", "score_lead",
        "draft_followup", "check_availability", "list_appointments",
        "book_appointment", "schedule_followup", "find_neglected_leads",
        "generate_dashboard_insights", "post_briefing", "get_research_settings",
        "get_insights", "get_summary", "post_summary", "delete_lead",
        "search_knowledge",
    )
}


def dispatch(operation: str, arguments: dict):
    function = OPERATIONS.get(operation)
    if function is None:
        raise ValueError(f"unknown CRM operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("--args must decode to a JSON object")
    return function(**arguments)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    parser.add_argument("--args", default="{}")
    args = parser.parse_args()
    try:
        result = dispatch(args.operation, json.loads(args.args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": result}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Set executable mode on the installed CRM wrapper and daily-brief runner. The setup helper must not execute
shell text from the model or provide an operation that accepts a command string.

Implementation rules:

1. Fail before mutation if `openclaw` is missing or if `agents add`, `skills check`, `config set`, `config validate`, `approvals allowlist`, or `exec-policy show` is absent from the relevant `--help` output.
2. Read agents through `openclaw agents list --json`.
3. Create only `openhouse-crm` with workspace `~/.openclaw/workspace-openhouse-crm` by default.
4. Copy these repository directories into `<workspace>/skills` using `shutil.copytree(..., dirs_exist_ok=True)`:
   `crm-db-operations`, `business-card-scanner`, `daily-command-center`, and `daily-brief`.
5. Find the dedicated agent's index from `openclaw config get agents.list --json`; set only:

```json
{
  "skills": [
    "crm-db-operations",
    "business-card-scanner",
    "daily-command-center",
    "daily-brief"
  ],
  "tools": {
    "allow": ["exec", "web_fetch"],
    "deny": ["write", "edit", "apply_patch", "browser", "canvas", "nodes", "cron"],
    "exec": {"mode": "allowlist", "host": "gateway"}
  },
  "sandbox": {"mode": "off"}
}
```

Use separate `openclaw config set 'agents.list[index].skills' ... --strict-json` and `tools` calls so unrelated agent fields remain untouched.

6. Set `skills.entries["crm-db-operations"].env.CRM_API_URL` and, only when nonempty, `OHI_API_TOKEN`. Never print the token or include it in a dry-run command rendering; display `<redacted>`. Sandbox mode is explicitly off for this dedicated agent because OpenClaw does not inject skill environment variables into sandbox processes; the per-agent executable allowlist is the command boundary.
7. If `--bind-discord ACCOUNT` is supplied, use `openclaw agents bind --agent openhouse-crm --bind discord:ACCOUNT --json`; otherwise print the exact optional binding command without changing Discord routing.
8. Run `openclaw config validate --json`, then `openclaw skills check --agent openhouse-crm --json` and verify the output contains `crm-db-operations`.
9. Run `openclaw sandbox explain --agent openhouse-crm --json` and `openclaw exec-policy show`; require gateway-host `exec`, `allowlist` mode, and exactly the CRM wrapper and daily-brief runner paths for `openhouse-crm`. If the installed version lacks these inspection surfaces, fail with an actionable unsupported-version message instead of guessing.
10. Restart the Gateway only after at least one mutation and successful validation.

- [ ] **Step 5: Replace hardcoded skill paths with installed-location paths**

Change the daily brief command to execute its allowlisted shebang entry point directly:

```bash
{baseDir}/scripts/run_daily_brief.py
```

and its publish command to:

```bash
{baseDir}/scripts/run_daily_brief.py --publish-payload /tmp/<unique-name>.json
```

In `business-card-scanner/SKILL.md`, replace managed-root imports and inline
Python with the sibling CRM wrapper:

```bash
{baseDir}/../crm-db-operations/cli.py create_lead --args '{"name":"Parsed Name","source":"note"}'
```

In `crm-db-operations/SKILL.md`, replace inline `python3 -c` examples with the
allowlisted wrapper form:

```bash
{baseDir}/cli.py list_leads --args '{"sort":"priority"}'
```

Each example must pass one named operation and a JSON object. No example may
invoke a shell, inline Python, or import `tools.py` through `python3 -c`.

Update `daily-command-center/SKILL.md` to use the same wrapper for
`list_appointments`, `get_lead_context`, and `post_briefing`; it must not imply
that the Python functions appear as native OpenClaw tools.

- [ ] **Step 6: Run setup tests and help smoke checks**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py backend/tests/test_skill_cli.py backend/tests/test_daily_brief_skill.py -q
python3 scripts/setup_openclaw.py --help
python3 scripts/setup_openclaw.py --dry-run
```

Expected: tests pass; help exits 0; dry-run makes no configuration changes and either prints the planned actions or exits with a clear `openclaw not found` message on a development host without OpenClaw.

- [ ] **Step 7: Commit setup automation**

```bash
git add scripts/setup_openclaw.py skills/crm-db-operations/cli.py backend/tests/test_setup_openclaw.py backend/tests/test_skill_cli.py backend/tests/test_daily_brief_skill.py skills/crm-db-operations/SKILL.md skills/business-card-scanner/SKILL.md skills/daily-brief/SKILL.md skills/daily-command-center/SKILL.md
git commit -m "feat: automate dedicated OpenClaw CRM setup"
```

---

### Task 3: Separate Chat Health from Verified CRM Capability

**Files:**
- Modify: `backend/app/agent/status.py`
- Modify: `backend/app/agent/base.py`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/app/routers/misc.py`
- Modify: `scripts/doctor.py`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/components/LocalBadge.tsx`
- Test: `backend/tests/test_openclaw.py`
- Test: `backend/tests/test_doctor.py`
- Test: `backend/tests/test_status.py`

**Interfaces:**
- Produces status values: `endpoint_enabled`, `chat_verified`, `crm_verified`, `degraded`, plus existing failure classifications.
- Adds `AgentProbe.crm_verified: bool`, `AgentProbe.agent_id: str | None`, and `AgentProbe.fallbacks: dict[str, int]`.
- Adds `record_crm_capability(ok: bool, detail: str | None = None) -> None`.
- Adds `AgentDriver.request_crm_capability(session_id: str) -> None`.
- Adds `POST /api/health/crm-check`, returning the updated `AgentProbe` JSON.
- Adds doctor flag `--live-crm`.

- [ ] **Step 1: Write failing status and capability tests**

Add status tests:

```python
def test_chat_success_does_not_mean_crm_verified():
    record_chat(True)
    record_crm_capability(False, "no audited CRM call")
    assert resolved_status(gateway_reachable=True, endpoint_enabled=True) == "chat_verified"


def test_crm_success_is_distinct():
    record_chat(True)
    record_crm_capability(True)
    assert resolved_status(gateway_reachable=True, endpoint_enabled=True) == "crm_verified"
```

Add an endpoint test using a fake driver that writes the audited read:

```python
def test_crm_check_requires_new_matching_audit(client, monkeypatch):
    class FakeDriver:
        name = "openclaw"

        async def request_crm_capability(self, session_id):
            with get_conn() as conn:
                audit(conn, "agent", "generate_dashboard_insights", {}, {"active_leads": 0})

        async def probe(self):
            return AgentProbe(
                status=resolved_status(gateway_reachable=True, endpoint_enabled=True),
                gateway_reachable=True,
                endpoint_enabled=True,
                last_chat_ok=True,
                crm_verified=True,
                agent_id="openhouse-crm",
                fallbacks={},
            )

    monkeypatch.setattr(misc, "get_driver", lambda: FakeDriver())
    body = client.post("/api/health/crm-check").json()
    assert body["status"] == "crm_verified"
    assert body["crm_verified"] is True
```

Add the negative variant where the fake returns generic text but writes no audit row; expect `chat_verified` and detail `no audited CRM call`.

Update the doctor help test to require `--live-crm` and add a `run_checks` unit test that mocks `_request_json` responses and expects separate `Live chat completion` and `CRM capability` checks.

- [ ] **Step 2: Run status tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_status.py backend/tests/test_openclaw.py backend/tests/test_doctor.py -v
```

Expected: FAIL because the CRM status fields, capability method, endpoint, and CLI flag do not exist.

- [ ] **Step 3: Implement the status state and driver capability request**

In `status.py`, retain the lock and add CRM state. Centralize status selection in:

```python
def resolved_status(*, gateway_reachable: bool, endpoint_enabled: bool) -> AgentStatus:
    if not gateway_reachable:
        return "unreachable"
    if not endpoint_enabled:
        return "endpoint_disabled"
    if _CRM_OK is True:
        return "crm_verified"
    if _LAST_CHAT_OK is True:
        return "chat_verified"
    if _LAST_CHAT_OK is False:
        return "failed"
    return "endpoint_enabled"
```

Add to `AgentDriver`:

```python
async def request_crm_capability(self, session_id: str) -> None:
    raise RuntimeError("CRM capability is unavailable for this driver")
```

Implement in `OpenClawDriver`:

```python
async def request_crm_capability(self, session_id: str) -> None:
    await self._send(
        "Use the crm-db-operations skill to call generate_dashboard_insights. "
        "Do not modify CRM data. After the call, reply with only CHECKED.",
        session_id,
    )
```

- [ ] **Step 4: Implement the audited CRM capability endpoint**

In `misc.py`, implement `POST /health/crm-check` as follows:

1. Reject mock mode as not a real capability check.
2. Read `MAX(id)` from `audit_log`, close the connection, and never hold a transaction while waiting for OpenClaw.
3. Choose a fresh session ID such as `crm-check-<uuid>`.
4. Await `driver.request_crm_capability(session_id)`.
5. Query for `id > before`, `actor = 'agent'`, and `tool = 'generate_dashboard_insights'`.
6. Call `record_crm_capability(found, None if found else "no audited CRM call")`.
7. Return `asdict(await driver.probe())`.

Modify `metrics(request: Request)` so it records
`audit(conn, "agent", "generate_dashboard_insights", {}, result)` only when
`is_agent_write(request)` is true. Build the deterministic metrics result before
auditing and return that same object. Normal dashboard calls carry no
`X-Actor: agent` header and must not add an audit row.

Do not trust the assistant reply itself. The new agent-only metrics audit row is
the capability evidence. This avoids the optional Composio free/busy call that
an availability probe could trigger in live integration mode.

Update the transport live check to set `chat_verified`, not CRM verified. Update `/health` so `agent_connected` means transport reachability while the nested probe carries exact capability state.

- [ ] **Step 5: Add the CRM capability check to doctor**

In `doctor.py`:

- keep `--live-agent` for ordinary completion;
- add `--live-crm` to call `/health/crm-check`;
- report `PASS CRM capability` only for `crm_verified`;
- report `WARN Chat completion: chat_verified` when chat works but CRM is unverified;
- never print tokens or response bodies that may contain them.

- [ ] **Step 6: Render truthful capability status in the dashboard**

In the dashboard type and badge, render:

```typescript
const label =
  status === 'crm_verified' ? 'CRM agent · verified' :
  status === 'chat_verified' ? 'Chat works · CRM not verified' :
  status === 'degraded' ? 'CRM agent · degraded' :
  status === 'endpoint_enabled' ? 'OpenClaw · endpoint enabled' :
  status === 'endpoint_disabled' ? 'OpenClaw · chat endpoint off' :
  status === 'unauthorized' ? 'OpenClaw · unauthorized' :
  status === 'unreachable' ? 'OpenClaw · unreachable' :
  status === 'failed' ? 'OpenClaw · error' :
  status === 'mock' ? 'Inference · mock mode' :
  'Agent status…'
```

Only `crm_verified` receives the positive accent dot. Mock mode remains explicitly labeled mock.

- [ ] **Step 7: Run backend tests and dashboard build**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_status.py backend/tests/test_openclaw.py backend/tests/test_doctor.py -q
npm --prefix dashboard run build
```

Expected: tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 8: Commit truthful capability status**

```bash
git add backend/app/agent/status.py backend/app/agent/base.py backend/app/agent/openclaw.py backend/app/routers/misc.py scripts/doctor.py dashboard/src/api.ts dashboard/src/components/LocalBadge.tsx backend/tests/test_status.py backend/tests/test_openclaw.py backend/tests/test_doctor.py
git commit -m "fix: verify real CRM capability separately from chat"
```

---

### Task 4: Complete Human Review for Notes, Bookings, and Reminders

**Files:**
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/app/routers/calendar.py`
- Modify: `backend/app/routers/misc.py`
- Modify: `backend/app/routers/pending_changes.py`
- Modify: `skills/crm-db-operations/tools.py`
- Modify: `skills/crm-db-operations/SKILL.md`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/components/PendingApprovals.tsx`
- Modify: `docs/CONTRACT.md`
- Test: `backend/tests/test_pending_changes.py`
- Test: `backend/tests/test_skill_tools.py`
- Test: `backend/tests/test_hooks.py`
- Test: `backend/tests/test_audit_coverage.py`

**Interfaces:**
- Adds pending operations `add_event`, `book_appointment`, and `schedule_followup`.
- Adds `tools.add_note(lead_id: int, content: str) -> dict` using event type `note`.
- Produces apply functions `_apply_add_event`, `_apply_book_appointment`, and `_apply_create_reminder` used by both direct and approved paths. Each accepts `actor: str = "agent"`; direct dashboard routes pass `actor="user"`, while approval replay keeps the default agent attribution and also records the existing user approval audit.
- Preserves direct dashboard behavior: requests without `X-Actor: agent` apply immediately.

- [ ] **Step 1: Write failing approval and hook tests**

Add:

```python
def test_agent_note_queues_and_approval_applies(client):
    lead = make_lead(client)
    queued = client.post(
        f"/api/leads/{lead['id']}/events",
        json={"type": "note", "content": "Requested Saturday tour"},
        headers=AGENT,
    )
    assert queued.status_code == 202
    assert queued.json()["operation"] == "add_event"
    assert client.get(f"/api/leads/{lead['id']}").json()["events"] == []
    approved = client.post(f"/api/pending-changes/{queued.json()['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["content"] == "Requested Saturday tour"


def test_agent_booking_does_not_run_hook_before_approval(client, monkeypatch):
    from app.routers import calendar as calendar_router

    lead = make_lead(client)
    calls = []
    monkeypatch.setattr(calendar_router.hooks, "on_tour_booked", lambda *args: calls.append(args))
    queued = client.post(
        "/api/appointments",
        json={"lead_id": lead["id"], "start_ts": "2026-08-08T10:00:00",
              "end_ts": "2026-08-08T10:45:00"},
        headers=AGENT,
    )
    assert queued.status_code == 202
    assert calls == []
    assert client.get("/api/appointments").json() == []
    client.post(f"/api/pending-changes/{queued.json()['id']}/approve")
    assert len(calls) == 1


def test_agent_reminder_denial_has_no_local_or_external_effect(client, monkeypatch):
    from app.routers import misc

    lead = make_lead(client)
    calls = []
    monkeypatch.setattr(misc.hooks, "on_reminder_created", lambda value: calls.append(value))
    queued = client.post(
        "/api/reminders",
        json={"lead_id": lead["id"], "due_ts": "2026-08-09T09:00:00", "note": "Call"},
        headers=AGENT,
    )
    client.post(f"/api/pending-changes/{queued.json()['id']}/deny")
    assert client.get("/api/reminders").json() == []
    assert calls == []
```

Extend the skill smoke catalog with `add_note` and assert its request uses `POST /leads/{id}/events` plus `X-Actor: agent`.

Extend `test_audit_coverage.py` to assert an untagged dashboard note, booking,
and reminder use actor `user`, while the corresponding approved proposal keeps
the operation audit actor `agent` and adds a separate `approve_pending_change`
row with actor `user`.

- [ ] **Step 2: Run the focused approval tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_pending_changes.py backend/tests/test_skill_tools.py backend/tests/test_hooks.py backend/tests/test_audit_coverage.py -v
```

Expected: new tests fail because the routes apply immediately and `add_note` is missing.

- [ ] **Step 3: Split note, booking, and reminder routes into queue and apply paths**

For each route, use this pattern:

```python
@router.post("/appointments")
def book_appointment(body: AppointmentIn, request: Request = None):
    if is_agent_write(request):
        return queue_pending_change(
            "book_appointment", body.lead_id, body.model_dump(), summarize_appointment(body)
        )
    return _apply_book_appointment(body, actor="user")
```

The concrete signature is:

```python
def _apply_book_appointment(body: AppointmentIn, actor: str = "agent") -> dict:
    # Existing insert/status/event logic, then:
    audit(conn, actor, "book_appointment", body.model_dump(),
          {"appointment_id": appt["id"]}, body.lead_id)
    hooks.on_tour_booked(lead, appt)
    return appt
```

Use equivalent `actor: str = "agent"` signatures for `_apply_add_event` and
`_apply_create_reminder`. Direct untagged endpoints call their apply functions
with `actor="user"`; approved pending changes call them without overriding the
agent default.

Apply the same structure to `add_event` and `create_reminder`. Keep validation before queueing where the operator needs normalized timestamps or an existing lead check. Re-run conflict validation on appointment approval so a slot taken while pending returns 409 without marking the change approved.

- [ ] **Step 4: Extend pending-change replay without import cycles**

In `pending_changes.py`, replace the lead-only mapping with a lazy dispatcher to avoid module-import cycles:

```python
def _operation(operation: str):
    if operation == "add_event":
        return leads_router.EventIn, leads_router._apply_add_event, True
    if operation == "book_appointment":
        from . import calendar as calendar_router
        return calendar_router.AppointmentIn, calendar_router._apply_book_appointment, False
    if operation == "schedule_followup":
        from . import misc as misc_router
        return misc_router.ReminderIn, misc_router._apply_create_reminder, False
    return _OPS[operation]
```

For the `False` cases, the payload already contains `lead_id`, so call the apply function with the parsed body only. Ensure an apply failure leaves the pending row in `pending` state.

- [ ] **Step 5: Expose reviewed note creation through the CRM skill**

Add to `tools.py`:

```python
def add_note(lead_id: int, content: str) -> dict:
    """Propose a note on an existing lead; operator approval is required."""
    if not content.strip():
        raise ValueError("content must not be empty")
    return _request(
        "POST", f"/leads/{int(lead_id)}/events",
        body={"type": "note", "content": content.strip()},
    )
```

Add `"add_note"` to the `OPERATIONS` tuple in
`skills/crm-db-operations/cli.py`, then extend `test_skill_cli.py` to assert the
wrapper dispatches it and still rejects arbitrary operation names.

Update the skill instructions so `pending: true` means proposed, not completed, for all eight reviewed operations.

- [ ] **Step 6: Add editable previews for the three operations**

Expand `PendingChange.operation` in TypeScript. Add editable fields:

- `add_event`: `content` textarea, read-only type.
- `book_appointment`: start, end, and location.
- `schedule_followup`: due time and note.

Update `coerceForSubmit` so timestamp and text values remain strings and only budget is numeric. Continue showing the target lead from `lead_id`.

- [ ] **Step 7: Run approval tests and build the dashboard**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_pending_changes.py backend/tests/test_skill_tools.py backend/tests/test_hooks.py backend/tests/test_audit_coverage.py backend/tests/test_booking_race.py -q
npm --prefix dashboard run build
```

Expected: all tests pass; no hook runs before approval; TypeScript build succeeds.

- [ ] **Step 8: Commit complete human review coverage**

```bash
git add backend/app/routers/leads.py backend/app/routers/calendar.py backend/app/routers/misc.py backend/app/routers/pending_changes.py skills/crm-db-operations/tools.py skills/crm-db-operations/cli.py skills/crm-db-operations/SKILL.md dashboard/src/api.ts dashboard/src/components/PendingApprovals.tsx docs/CONTRACT.md backend/tests/test_pending_changes.py backend/tests/test_skill_tools.py backend/tests/test_skill_cli.py backend/tests/test_hooks.py backend/tests/test_audit_coverage.py
git commit -m "feat: review agent notes bookings and reminders"
```

---

### Task 5: Surface Deterministic Fallbacks Instead of Hiding Them

**Files:**
- Modify: `backend/app/agent/status.py`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/app/routers/voice.py`
- Modify: `dashboard/src/api.ts`
- Test: `backend/tests/test_openclaw.py`
- Test: `backend/tests/test_voice.py`

**Interfaces:**
- Produces `record_fallback(kind: Literal["extract", "draft_followup", "score_explanation"])`.
- `AgentProbe.fallbacks` maps each kind to a process-local count.
- OpenClaw fallback extraction adds internal marker `_fallback_used = "deterministic_parser"`; callers must remove it before persistence.
- Voice preparation exposes the marker as a human-readable entry in existing `warnings: list[str]`.

- [ ] **Step 1: Write failing fallback tests**

Add:

```python
def test_extract_fallback_is_labeled(monkeypatch):
    from app.agent import status
    monkeypatch.setattr(status, "_FALLBACKS", {})
    driver = OpenClawDriver(client_factory=client_factory(post_status=500))
    result = asyncio.run(driver.extract("Met Alex Rivera, budget $900k"))
    assert result.pop("_fallback_used") == "deterministic_parser"
    assert result["name"] == "Alex Rivera"
    assert status.fallback_counts()["extract"] == 1


def test_draft_fallback_is_visibly_labeled():
    driver = OpenClawDriver(client_factory=client_factory(post_status=500))
    result = asyncio.run(driver.draft_followup({"name": "Alex"}))
    assert result.startswith("[deterministic fallback]")
```

Add a voice test with a fake driver returning `_fallback_used`; assert the API warning contains `deterministic parser` and the draft JSON does not contain `_fallback_used`.

- [ ] **Step 2: Run fallback tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_openclaw.py backend/tests/test_voice.py -v
```

Expected: FAIL because fallbacks are currently silent.

- [ ] **Step 3: Implement fallback counters and visibly labeled driver output**

In `status.py`, add locked counters and:

```python
def record_fallback(kind: str) -> None:
    with _LOCK:
        _FALLBACKS[kind] = _FALLBACKS.get(kind, 0) + 1


def fallback_counts() -> dict[str, int]:
    with _LOCK:
        return dict(_FALLBACKS)
```

In each OpenClaw fallback branch, record the correct kind. Add the internal marker only to extraction dictionaries. Prefix draft and score explanation fallback text with `[deterministic fallback] ` so the review surface cannot mistake it for normal model output.

- [ ] **Step 4: Propagate fallback warnings to review surfaces**

In `voice.py`, pop the marker immediately after extraction:

```python
fallback = extracted.pop("_fallback_used", None)
if fallback:
    warnings.append(
        "OpenClaw extraction was unavailable; this draft used the deterministic parser. Review every field."
    )
```

In lead raw-text creation, pop the same marker before constructing fields so it can never become a database column or pending payload. The pending summary remains editable and the health probe exposes cumulative fallback counts.

If any fallback count is nonzero after the last successful CRM verification, render `degraded`; a later successful CRM capability check resets the relevant degradation epoch without erasing historical audit data.

- [ ] **Step 5: Run extraction, voice, and status tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_openclaw.py backend/tests/test_extract.py backend/tests/test_voice.py backend/tests/test_status.py -q
```

Expected: all tests pass and no internal marker reaches API draft fields or database writes.

- [ ] **Step 6: Commit visible fallback behavior**

```bash
git add backend/app/agent/status.py backend/app/agent/openclaw.py backend/app/routers/voice.py backend/app/routers/leads.py dashboard/src/api.ts backend/tests/test_openclaw.py backend/tests/test_voice.py backend/tests/test_status.py
git commit -m "fix: surface deterministic agent fallbacks"
```

---

### Task 6: Lock Briefings to Real Data and Location-independent Skills

**Files:**
- Modify: `skills/daily-command-center/SKILL.md`
- Modify: `skills/daily-brief/SKILL.md`
- Modify: `skills/daily-brief/scripts/run_daily_brief.py`
- Modify: `backend/app/routers/reports.py`
- Modify: `backend/app/report_models.py`
- Test: `backend/tests/test_reports.py`
- Test: `backend/tests/test_daily_brief_skill.py`

**Interfaces:**
- Preserves `POST/GET /api/briefing` and `POST/GET /api/summary` schemas.
- Preserves deterministic default daily-brief mode and explicitly opt-in web mode.
- Enforces that missing source-backed content remains absent or `unavailable`; it is never replaced by sample content.

- [ ] **Step 1: Add failing truthfulness tests**

Add report tests:

```python
def test_empty_briefing_contains_no_sample_people_or_appointments(client):
    body = client.get("/api/briefing?date=2026-08-05").json()
    assert body["schedule"] == []
    assert body["meeting_briefs"] == []
    serialized = json.dumps(body).lower()
    assert "sample" not in serialized
    assert "sarah chen" not in serialized


def test_summary_rejects_market_item_without_supported_source_fields(client):
    payload = {
        "date": "2026-08-05",
        "generated_at": "2026-08-05T07:00:00",
        "greeting": "Daily brief",
        "market_watch": [{
            "title": "Seattle employment",
            "source": "U.S. Bureau of Labor Statistics",
            "url": "https://www.bls.gov/eag/eag.wa_seattle_msa.htm",
            "takeaway": "Review the published figures.",
            "date": "2026-08-05",
            "summary": "Source-backed summary.",
            "geo": "Seattle",
        }],
        "ai_insights": [],
    }
    del payload["market_watch"][0]["url"]
    response = client.post("/api/summary", json=payload)
    assert response.status_code == 422
```

Add skill tests requiring both `{baseDir}` commands and forbidding:

```python
assert "python3 skills/daily-brief" not in skill_text
assert "sample-crm.json" not in command_center_text
assert "do not publish" in command_center_text.lower()
```

Add a script test proving that a failed deterministic source creates no market
claim for that source and records an explicit unavailable-source insight:

```python
def test_deterministic_failure_is_disclosed_without_inventing_item(monkeypatch):
    monkeypatch.setattr(daily_brief, "_fetch_html", lambda url: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(daily_brief, "_job_item_api", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    payload = daily_brief.build_payload()

    assert payload["market_watch"] == []
    failures = [item for item in payload["ai_insights"] if item["title"] == "Sources unavailable"]
    assert len(failures) == 1
    assert "offline" in failures[0]["body"]
```

Keep the existing opt-in AI payload test that requires exactly every configured
source before `--publish-payload` can publish.

- [ ] **Step 2: Run briefing tests and confirm the new assertions fail where coverage is missing**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_reports.py backend/tests/test_daily_brief_skill.py -v
```

Expected: at least the new command/failure contract assertions fail before implementation.

- [ ] **Step 3: Tighten stored report validation and failure preservation**

Keep the existing backend-owned reconstruction of appointment and lead facts. Tighten only missing boundaries:

- `DailySummary` requires a valid configured URL, nonempty source, title, takeaway, date, summary, and geographic scope for every displayed market item.
- Failed stored-payload parsing or publication returns 422/503 and leaves the last valid dated row untouched.
- No exception path calls `MockDriver` or returns a bundled example payload in real mode.
- [ ] **Step 4: Tighten the two daily skill execution contracts**

- `daily-command-center` must post `meeting_briefs: []` when no real appointment exists and must stop without publishing when required CRM reads fail.
- `daily-brief` must use `{baseDir}` in every executable command. Deterministic mode may publish only successfully parsed source items plus an explicit `Sources unavailable` insight; it must not synthesize an item for a failed source. Opt-in AI/WebFetch mode remains all-or-nothing and must not publish unless every configured source validates.
- Remove production instructions that reference `sample-crm.json`; retain the file only as an explicitly labeled development fixture if another test still uses it.

Use validation such as:

```python
@field_validator("source", "title", "takeaway", "summary", "geo")
@classmethod
def nonempty_text(cls, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value
```

Do not add generative filler for missing values.

- [ ] **Step 5: Run briefing and full report suites**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_reports.py backend/tests/test_daily_brief_skill.py backend/tests/test_research_settings.py -q
```

Expected: all tests pass; no test fixture content appears in an empty real briefing.

- [ ] **Step 6: Commit briefing trust fixes**

```bash
git add skills/daily-command-center/SKILL.md skills/daily-brief/SKILL.md skills/daily-brief/scripts/run_daily_brief.py backend/app/routers/reports.py backend/app/report_models.py backend/tests/test_reports.py backend/tests/test_daily_brief_skill.py
git commit -m "fix: keep daily briefings grounded and portable"
```

---

### Task 7: Publish the Beginner Setup and Verify the Release Candidate

**Files:**
- Modify: `README.md`
- Modify: `docs/LOCAL-AI.md`
- Modify: `docs/MAC-MINI-SETUP.md`
- Modify: `docs/GB10-SETUP.md`
- Modify: `docs/CONTRACT.md`
- Modify: `.env.example`
- Modify: `backend/tests/test_launchers.py`
- Modify: `backend/tests/test_doctor.py`

**Interfaces:**
- Beginner command sequence: clone, copy `.env`, run `setup_openclaw.py`, serve, run doctor, open dashboard.
- Clean verification command: `python3 scripts/doctor.py --live-agent --live-crm`.
- Hardware checklist covers 16 GB Mac mini, Linux, and GB10 when available.

- [ ] **Step 1: Write documentation contract tests**

Add tests that read the shipped files:

```python
def test_beginner_docs_use_one_agent_and_skill_name():
    paths = [REPO / "README.md", REPO / "docs/LOCAL-AI.md",
             REPO / "docs/MAC-MINI-SETUP.md", REPO / "docs/GB10-SETUP.md"]
    text = "\n".join(path.read_text() for path in paths)
    assert "AGENT_ID=openhouse-crm" in text
    assert "crm-db-operations" in text
    assert "openhouse-crm skill" not in text.lower()


def test_readme_uses_setup_helper_and_real_capability_check():
    text = (REPO / "README.md").read_text()
    assert "python3 scripts/setup_openclaw.py" in text
    assert "python3 scripts/doctor.py --live-agent --live-crm" in text
    assert "16 GB" in text
```

Also assert `.env.example` contains `AGENT_ID=openhouse-crm` exactly once and the docs do not instruct users to copy skills manually as the primary path.

- [ ] **Step 2: Run documentation contract tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_launchers.py backend/tests/test_doctor.py -v
```

Expected: new documentation assertions fail because the old manual copy flow is still primary.

- [ ] **Step 3: Rewrite the README and Mac mini operator path**

Make the README real-local mode sequence exactly:

```bash
git clone https://github.com/tobywashere/open-house-intelligence-crm.git open-intelligence-crm
cd open-intelligence-crm
cp .env.example .env
python3 scripts/setup_openclaw.py
bash scripts/serve.sh
python3 scripts/doctor.py --live-agent --live-crm
```

Explain in plain language:

- `openhouse-crm` is the agent; `crm-db-operations` is the skill it uses.
- Chat verified means OpenClaw answered; CRM verified means it actually called the local CRM.
- Agent writes wait in Pending approvals.
- A deterministic fallback is labeled and must be reviewed.
- Missing daily information remains unavailable.
- Discord setup is optional and uses `--bind-discord <account>` or the printed binding command.
- 16 GB is the minimum Mac mini memory, but local model size controls speed and quality.
- Gmail, Google Calendar, public web research, and remote model providers are optional internet services and remain off unless configured.

- [ ] **Step 4: Synchronize advanced, GB10, and contract documentation**

Move raw OpenClaw config detail and recovery commands into `docs/LOCAL-AI.md`. Keep `MAC-MINI-SETUP.md` task-oriented with expected visible results after each command. Keep `GB10-SETUP.md` as a hardware-specific variant that calls the same setup helper.

Update `docs/CONTRACT.md` with the expanded pending operations, exact health statuses, and capability-check audit contract. Remove duplicate or stale contract rows while preserving historical endpoint behavior.

Add a target-hardware acceptance checklist with unchecked boxes and a field for OpenClaw version, model/provider, OS, memory, date, and operator. Do not mark hardware verified in repository text until a real run is recorded.

- [ ] **Step 5: Run the complete release verification**

Run:

```bash
.venv/bin/python -m pytest backend/tests -q
npm --prefix dashboard run build
python3 scripts/setup_openclaw.py --help
python3 scripts/doctor.py --help
git diff --check
```

Expected:

- all backend tests pass;
- dashboard TypeScript and production build pass;
- both CLI help commands exit 0;
- no whitespace errors;
- no tests make real Composio, Gmail, Calendar, or model-provider calls.

Then perform the operator-run acceptance checklist on available supported hardware. Record failures as unverified limitations, not successful claims.

- [ ] **Step 6: Commit open source readiness documentation**

```bash
git add README.md docs/LOCAL-AI.md docs/MAC-MINI-SETUP.md docs/GB10-SETUP.md docs/CONTRACT.md .env.example backend/tests/test_launchers.py backend/tests/test_doctor.py
git commit -m "docs: make OpenClaw setup beginner and open source ready"
```

---

## Final Review Gate

Before pushing or opening a pull request:

- [ ] Confirm `git status --short` contains no generated database, `.env`, audio, token, or dashboard build artifacts.
- [ ] Review every new setup command for secret redaction and dry-run safety.
- [ ] Review every agent write route to confirm failure never falls through to direct application.
- [ ] Confirm denied appointments and reminders never call external hooks.
- [ ] Confirm ordinary chat cannot set `crm_verified` without the matching new audit row.
- [ ] Confirm mock mode and deterministic fallbacks are visually distinguishable from verified CRM mode.
- [ ] Confirm an empty database produces no sample leads, appointments, news, or recommendations in real mode.
- [ ] Run the full backend suite and dashboard build again after resolving any merge conflicts.
- [ ] Request code review before integration.

## Upstream References for the Implementer

- OpenClaw agent creation and channel binding: `https://docs.openclaw.ai/cli/agents`
- OpenClaw skill roots and per-agent allowlists: `https://docs.openclaw.ai/skills`
- OpenClaw non-interactive config editing: `https://docs.openclaw.ai/cli/config`
- OpenClaw per-agent tools and sandboxing: `https://docs.openclaw.ai/gateway/config-agents`
- OpenClaw Chat Completions agent selection: `https://docs.openclaw.ai/gateway/openai-http-api`
