# OpenClaw Setup Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenClaw setup helper work on clean installs and on both current keyed and legacy list agent rosters without weakening its fail-closed safety checks.

**Architecture:** Keep compatibility logic at the CLI boundary in `scripts/setup_openclaw.py`. Parse help by section indentation, normalize modern and legacy agent rosters into one `AgentRoster`, and resolve the exact configuration prefix only after OpenClaw exposes the created agent. Preserve the existing mutation order and authoritative post-write checks.

**Tech Stack:** Python 3.12+, pytest, OpenClaw CLI JSON output, Markdown operator documentation.

## Global Constraints

- Do not edit OpenClaw configuration files directly.
- Do not select behavior from an OpenClaw version string.
- Support `agents.entries`, `agents.list`, and a fresh install with no explicit roster.
- Preserve approval review, exec-only tool policy, SecretRef storage, redaction, and post-write verification.
- Accept a missing config path only when the diagnostic exactly names the path requested.
- Fail before mutation on malformed, contradictory, ambiguous, or unauthorized state.
- Do not claim live OpenClaw, provider, Discord, or hardware verification from automated tests.
- Keep production changes in `scripts/setup_openclaw.py`; update only its tests and the relevant local setup documentation.

---

### Task 1: Parse nested OpenClaw help without losing commands

**Files:**
- Modify: `scripts/setup_openclaw.py:802-839`
- Test: `backend/tests/test_setup_openclaw.py:1530-1630`

**Interfaces:**
- Consumes: captured stdout and stderr from an OpenClaw `--help` command.
- Produces: `_command_entries(output: str) -> set[str]`, used by `_require_help` for exact subcommand capability checks.

- [ ] **Step 1: Add the failing nested-examples regression test**

Add a test whose `openclaw config --help` response contains direct commands at two-space indentation and an `Examples:` block indented beneath `patch`, followed by `set` and `validate`:

```python
def test_preflight_keeps_commands_after_nested_examples(tmp_path):
    help_text = """Commands:
  get <path>
  patch [options]
    Examples:
      openclaw config patch --file changes.json
  set <path> <value>
  validate
  file
  unset
"""
    cli = FakeCLI(
        {("openclaw", "config", "--help"): CommandResult(0, help_text, "")}
    )

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=cli)

    assert result.ok, result.render()
    assert cli.mutating_calls == []
```

- [ ] **Step 2: Verify the regression test fails for the reported reason**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py::test_preflight_keeps_commands_after_nested_examples -q
```

Expected: FAIL because setup reports that config help is missing `set` and `validate`.

- [ ] **Step 3: Add top-level termination and false-token tests**

Add focused tests proving that a new top-level `Options:` heading ends command collection, deeper example lines are ignored, and `set-more` or `--json-output` do not satisfy `set` or `--json`.

```python
def test_command_parser_uses_only_direct_children_of_commands_section():
    output = """Commands:
  get
  patch
    Examples:
      validate
  set
Options:
  validate
"""
    assert setup_openclaw._command_entries(output) == {"get", "patch", "set"}
```

- [ ] **Step 4: Implement indentation-aware command parsing**

Add `_command_entries` and call it from `_require_help`. Preserve exact option-token parsing.

```python
def _command_entries(output: str) -> set[str]:
    lines = output.splitlines()
    commands_indent: int | None = None
    direct_indent: int | None = None
    entries: set[str] = set()
    in_commands = False
    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" \t"))
        if not in_commands:
            if re.fullmatch(r"(?:available\s+)?commands?\s*:", stripped, re.I):
                in_commands = True
                commands_indent = indent
            continue
        if stripped and commands_indent is not None and indent <= commands_indent:
            break
        if not stripped:
            continue
        token = stripped.split(maxsplit=1)[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", token):
            continue
        if direct_indent is None:
            direct_indent = indent
        if indent == direct_indent:
            entries.add(token)
    return entries
```

If real fixture formatting exposes tabs, compute indentation consistently from the raw prefix and cover it with a test rather than broadening token matching.

- [ ] **Step 5: Run the parser and preflight tests**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -k 'help or preflight or capability' -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "fix: parse nested OpenClaw help sections"
```

---

### Task 2: Normalize modern, legacy, and empty agent rosters

**Files:**
- Modify: `scripts/setup_openclaw.py:33-60, 563-581, 784-800`
- Test: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Produces: `AgentRoster(schema: str | None, records: list[dict[str, Any]], prefixes: dict[str, str])`.
- Produces: `_configured_agent_roster(payload: Any) -> AgentRoster`.
- Produces: `_cli_agents(payload: Any) -> list[dict[str, Any]]`.
- Produces: `_is_missing_config_path(result: CommandResult, path: str) -> bool`.
- Produces: `_read_agent_roster(cli: OpenClawCLI, *, allow_missing: bool, label: str) -> AgentRoster`.

- [ ] **Step 1: Add failing roster parser tests**

Cover the exact normalized records and prefixes:

```python
def test_configured_roster_normalizes_modern_entries():
    roster = setup_openclaw._configured_agent_roster(
        {
            "defaults": {"workspace": "/default"},
            "entries": {
                "main": {"default": True},
                "openhouse-crm": {"workspace": "/crm"},
            },
        }
    )
    assert roster.schema == "entries"
    assert [agent["id"] for agent in roster.records] == ["main", "openhouse-crm"]
    assert roster.prefixes["openhouse-crm"] == 'agents.entries["openhouse-crm"]'


def test_configured_roster_normalizes_legacy_list():
    roster = setup_openclaw._configured_agent_roster(
        {"defaults": {}, "list": [{"id": "main"}, {"id": "openhouse-crm"}]}
    )
    assert roster.schema == "list"
    assert roster.prefixes["openhouse-crm"] == "agents.list[1]"


def test_configured_roster_accepts_defaults_only_as_empty():
    roster = setup_openclaw._configured_agent_roster({"defaults": {}})
    assert roster.schema is None
    assert roster.records == []
    assert roster.prefixes == {}
```

- [ ] **Step 2: Verify the new parser tests fail**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -k 'configured_roster' -q
```

Expected: FAIL because `AgentRoster` and `_configured_agent_roster` do not exist.

- [ ] **Step 3: Add failing malformed and contradictory roster tests**

Parametrize these rejected payloads and assert `SetupConflict`:

```python
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"list": {}},
        {"entries": []},
        {"list": [{"id": "same"}, {"id": "same"}]},
        {"entries": {"openhouse-crm": {"id": "different"}}},
        {"list": [{"id": "legacy"}], "entries": {"modern": {}}},
    ],
)
def test_configured_roster_rejects_ambiguous_or_malformed_state(payload):
    with pytest.raises(SetupConflict):
        setup_openclaw._configured_agent_roster(payload)
```

Also test that `_cli_agents` accepts `{"agents": [...]}` and keyed
`{"entries": {"openhouse-crm": {"workspace": "/crm"}}}`, while rejecting
non-object entries, missing IDs in list form, duplicate IDs, and mismatched
embedded IDs.

- [ ] **Step 4: Implement strict roster normalization**

Add the dataclass and helpers. Copy entry dictionaries before injecting a key-derived `id` so caller payloads are not mutated.

```python
@dataclass(frozen=True)
class AgentRoster:
    schema: str | None
    records: list[dict[str, Any]]
    prefixes: dict[str, str]
```

For modern entries, use `json.dumps(agent_id)` inside bracket notation so IDs
with a hyphen are passed safely as one argv element. Require nonempty string IDs
and unique IDs for every accepted shape.

- [ ] **Step 5: Add failing exact missing-path tests**

Test the official JSON diagnostic and compatible legacy text:

```python
@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, '{"error":"Config path not found: agents"}', ""),
        CommandResult(1, "", "Config path not found: agents"),
    ],
)
def test_agent_root_exact_missing_path_is_empty_before_creation(tmp_path, result):
    cli = FakeCLI(
        {("openclaw", "config", "get", "agents", "--json"): result}
    )
    roster = setup_openclaw._read_agent_roster(
        cli, allow_missing=True, label="initial agents config"
    )
    assert roster.schema is None
    assert roster.records == []
```

Add negative cases for `agents.list`, `agentsExtra`, permission denied, malformed
error JSON, return code 2, and extra error fields. Each must raise
`SetupConflict`.

- [ ] **Step 6: Implement exact optional-path handling**

`_is_missing_config_path` must compare the requested path exactly. Accept only:

```python
{"error": f"Config path not found: {path}"}
```

or a stream whose complete trimmed content equals
`Config path not found: {path}`. Do not change `_run_required`; optional behavior
belongs only in `_read_agent_roster`. When the command succeeds, parse stdout as
JSON and pass it to `_configured_agent_roster`.

- [ ] **Step 7: Run all Task 2 tests**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -k 'roster or cli_agents or missing_path or agent_root' -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "fix: normalize OpenClaw agent rosters"
```

---

### Task 3: Route setup writes through the discovered roster schema

**Files:**
- Modify: `scripts/setup_openclaw.py:960-1385`
- Test: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Consumes: `AgentRoster` and exact prefixes from Task 2.
- Changes: `_config_actions(options: SetupOptions, prefix: str) -> list[Action]`.
- Produces: `_deferred_agent_config_messages(options: SetupOptions) -> list[str]` for a truthful schema-neutral fresh-install dry run.

- [ ] **Step 1: Add the failing fresh legacy-install setup test**

Create a `FakeCLI` variant whose initial `config get agents --json` response is
`{"defaults": {}}`, whose `agents add` mutation creates a legacy list entry, and
whose later root read returns that list. Assert setup succeeds and writes only
`agents.list[0].skills`, `.tools`, and `.sandbox`.

```python
def test_fresh_install_discovers_legacy_roster_after_agent_creation(tmp_path):
    cli = FreshRosterCLI(schema="list")
    result = configure_openclaw(make_options(tmp_path), cli=cli)
    assert result.ok, result.render()
    rendered = [" ".join(call) for call in cli.mutating_calls]
    assert any("agents.list[0].tools" in call for call in rendered)
    assert not any("agents.entries" in call for call in rendered)
```

- [ ] **Step 2: Add the failing fresh modern-install setup test**

Repeat with `schema="entries"` and assert writes use
`agents.entries["openhouse-crm"].skills`, `.tools`, and `.sandbox`, with no
`agents.list` write.

- [ ] **Step 3: Verify both fresh-install tests fail**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -k 'fresh_install_discovers' -q
```

Expected: FAIL because setup still requires `config get agents.list` and
`_config_actions` still requires an integer index.

- [ ] **Step 4: Replace list-only setup reads with normalized root reads**

In `configure_openclaw`:

1. Parse `openclaw agents list --json` through `_cli_agents`.
2. Read the initial configured roster with `allow_missing=True`.
3. Compare only the dedicated target agent across the CLI and configured views.
4. Preserve all existing workspace, approval, field compatibility, and
   read-before-mutation checks.
5. After agent creation, call `_read_agent_roster(..., allow_missing=False, ...)`.
6. Require the new agent's prefix from `roster.prefixes`.

Do not accept a missing root after creation.

- [ ] **Step 5: Change config and readback helpers to accept a prefix**

Change:

```python
def _config_actions(options: SetupOptions, prefix: str) -> list[Action]:
```

Build fields with `f"{prefix}.skills"`, `f"{prefix}.tools"`, and
`f"{prefix}.sandbox"`. Use the same `prefix` for authoritative tools readback:

```python
["openclaw", "config", "get", f"{prefix}.tools", "--json"]
```

Keep global skill entry paths unchanged.

- [ ] **Step 6: Add and implement schema-neutral dry-run behavior**

Add a failing test where the initial root has only defaults and `dry_run=True`.
Assert:

- no mutation occurs;
- no message contains an invented `agents.list[0]` or `agents.entries` path;
- messages still state that skills, exec-only tools, sandbox mode, CRM URL,
  SecretRef when enabled, and executable allowlist entries will be configured;
- the output states that the exact roster path is selected after agent creation.

When the target already exists, dry-run may render exact commands using its
known prefix.

- [ ] **Step 7: Add existing-agent and conflict regression tests for both schemas**

Parametrize modern and legacy forms to prove:

- a matching existing agent is idempotent;
- a CLI/config mismatch fails before mutation;
- incompatible skills, tools, or sandbox settings fail before mutation;
- both rosters populated fail before mutation;
- authoritative tool-policy readback uses the discovered prefix;
- a successful `agents add` followed by no roster or no target agent fails
  before config writes.

- [ ] **Step 8: Update `FakeCLI` without hiding unsupported shapes**

Make its default root response represent a supported legacy roster so existing
tests remain meaningful. Add a dedicated `FreshRosterCLI` or explicit response
sequence for fresh creation. Do not make `FakeCLI` automatically convert every
missing-path failure into an empty roster, because negative tests need the real
fail-closed behavior.

- [ ] **Step 9: Run the full setup suite**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -q
```

Expected: all setup tests pass.

- [ ] **Step 10: Commit Task 3**

```bash
git add scripts/setup_openclaw.py backend/tests/test_setup_openclaw.py
git commit -m "fix: support fresh and keyed OpenClaw agents"
```

---

### Task 4: Complete the similar-issue audit and update operator guidance

**Files:**
- Modify: `docs/LOCAL-AI.md:55-145`
- Verify: `scripts/setup_openclaw.py`
- Verify: `backend/tests/test_setup_openclaw.py`

**Interfaces:**
- Consumes: final help, roster, missing-path, mutation-order, and readback behavior from Tasks 1 through 3.
- Produces: beginner-readable fresh-install and rerun guidance without schema-specific manual repair steps.

- [ ] **Step 1: Audit every help and config read boundary**

Run:

```bash
rg -n "_require_help|_run_required|_run_sensitive_required|config.*get|returncode" scripts/setup_openclaw.py
```

For each hit, classify it in a short checklist in the commit message notes:

- required capability or post-write verification, which must stay fatal;
- intentionally optional initial roster read, handled only by `_read_agent_roster`;
- sensitive token readback, which must suppress command output on failure.

Confirm the audit maps to concrete regression coverage already required by this
plan:

- Task 1 covers nested help headings and exact command/option tokens.
- Task 2 covers keyed shapes, malformed entries, exact missing diagnostics,
  stdout/stderr variants, wrong paths, permission errors, and invalid JSON.
- Task 3 covers mutation ordering, existing-agent mismatches, and required
  post-creation roster reads.
- Existing token tests cover sensitive readback suppression and SecretRef
  redaction.

Do not add a general ignore-errors helper or broaden missing-path handling.

- [ ] **Step 2: Update the local AI guide**

Add a short beginner-facing note near the setup command:

```markdown
On a new OpenClaw install, it is normal for the config to contain agent
defaults but no explicit agent roster yet. Setup creates the dedicated CRM
agent, then safely detects whether that OpenClaw version uses current keyed
agent entries or the older list format. You do not need to edit either format
by hand.
```

Add troubleshooting text that asks users to share `openclaw --version`, the
failing command, and its exact redacted output rather than locally patching the
script. Keep the existing live verification sequence unchanged.

- [ ] **Step 3: Run focused documentation and setup checks**

Run:

```bash
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py backend/tests/test_launchers.py -q
rg -n "agents\.entries|agents\.list|fresh|new OpenClaw" docs/LOCAL-AI.md README.md
git diff --check
```

Expected: tests and whitespace checks pass; documentation does not instruct a
beginner to edit either roster manually.

- [ ] **Step 4: Commit Task 4**

```bash
git add docs/LOCAL-AI.md
git commit -m "docs: explain fresh OpenClaw agent setup"
```

---

### Task 5: Verify the branch and prepare the pull request

**Files:**
- Verify: all branch changes relative to `origin/main`
- No planned production edits

**Interfaces:**
- Consumes: completed Tasks 1 through 4.
- Produces: a reviewed branch with fresh automated evidence and an explicit
  live-test handoff.

- [ ] **Step 1: Run the complete backend suite**

Run from the worktree root with localhost socket permission:

```bash
../../.venv/bin/python -m pytest backend/tests -q
```

Expected: all tests pass. Existing deprecation warnings may remain, but no test
failures or collection errors are accepted.

- [ ] **Step 2: Run the dashboard production build**

```bash
npm --prefix dashboard run build
```

Expected: TypeScript and Vite complete with exit code 0.

- [ ] **Step 3: Run setup, syntax, and repository integrity checks**

```bash
../../.venv/bin/python -m py_compile scripts/setup_openclaw.py
../../.venv/bin/python scripts/setup_openclaw.py --help
git diff origin/main...HEAD --check
git status --short
```

The help command must exit 0, the diff check must be empty, and status must be
clean after commits. Do not claim a live setup dry run if `openclaw` is not
installed locally.

- [ ] **Step 4: Review the complete diff against the design**

Check every design requirement explicitly:

- nested examples do not hide later commands;
- current entries and legacy list rosters both work;
- empty fresh state is accepted only before creation;
- exact missing-path checks cannot swallow other failures;
- dry-run never mutates or invents a roster path;
- existing safety and authoritative readbacks remain;
- docs remain readable for nontechnical users;
- no live OpenClaw or hardware claim is made.

- [ ] **Step 5: Push and open a pull request**

```bash
git push -u origin codex/openclaw-setup-compat
gh pr create --base main --head codex/openclaw-setup-compat
```

The PR summary must mention Chris's reproduced failures, dual-schema support,
the similar-issue audit, fresh automated results, and the outstanding need for
Chris to rerun setup on a real clean OpenClaw installation.
