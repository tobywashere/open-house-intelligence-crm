import importlib.util
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "setup_openclaw.py"
SPEC = importlib.util.spec_from_file_location("setup_openclaw", SCRIPT)
assert SPEC and SPEC.loader
setup_openclaw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup_openclaw
SPEC.loader.exec_module(setup_openclaw)

Action = setup_openclaw.Action
CommandResult = setup_openclaw.CommandResult
OpenClawCLI = setup_openclaw.OpenClawCLI
SetupConflict = setup_openclaw.SetupConflict
SetupOptions = setup_openclaw.SetupOptions
build_setup_actions = setup_openclaw.build_setup_actions
configure_openclaw = setup_openclaw.configure_openclaw
sync_skills = setup_openclaw.sync_skills


def make_options(tmp_path, *, dry_run=False, bind_discord=None):
    return SetupOptions(
        agent_id="openhouse-crm",
        workspace=tmp_path / "workspace-openhouse-crm",
        crm_api_url="http://localhost:8080/api",
        bind_discord=bind_discord,
        dry_run=dry_run,
    )


class FakeCLI:
    def __init__(self, responses=None):
        self.calls = []
        self.mutating_calls = []
        self.responses = responses or {}
        self.created_agent = None

    def run(self, args, *, mutate=False):
        self.calls.append(args)
        if mutate:
            self.mutating_calls.append(args)
            if args[1:3] == ["agents", "add"]:
                self.created_agent = {
                    "id": args[3],
                    "workspace": args[args.index("--workspace") + 1],
                }
        key = tuple(args)
        if key in self.responses:
            return self.responses[key]
        if args[-1:] == ["--help"]:
            return CommandResult(0, "available", "")
        if args == ["openclaw", "agents", "list", "--json"]:
            return CommandResult(
                0, json.dumps({"agents": [self.created_agent] if self.created_agent else []}), ""
            )
        if args == ["openclaw", "config", "get", "agents.list", "--json"]:
            return CommandResult(0, json.dumps([self.created_agent] if self.created_agent else []), "")
        if args == ["openclaw", "skills", "check", "--agent", "openhouse-crm", "--json"]:
            return CommandResult(0, '{"eligible": ["crm-db-operations"]}', "")
        if args == ["openclaw", "sandbox", "explain", "--agent", "openhouse-crm", "--json"]:
            return CommandResult(
                0,
                '{"mode": "off", "exec": {"host": "gateway", "mode": "allowlist"}}',
                "",
            )
        if args == ["openclaw", "exec-policy", "show", "--json"]:
            return CommandResult(0, '{"requested": {"host": "gateway", "mode": "allowlist"}}', "")
        if args == ["openclaw", "approvals", "get", "--gateway", "--json"]:
            wrapper = next(
                (
                    call[-1]
                    for call in self.mutating_calls
                    if call[1:4] == ["approvals", "allowlist", "add"]
                    and "crm-db-operations/cli.py" in call[-1]
                ),
                "",
            )
            daily = next(
                (
                    call[-1]
                    for call in self.mutating_calls
                    if call[1:4] == ["approvals", "allowlist", "add"]
                    and "daily-brief/scripts/run_daily_brief.py" in call[-1]
                ),
                "",
            )
            payload = {
                "agents": {
                    "openhouse-crm": {
                        "security": "allowlist",
                        "allowlist": [{"pattern": wrapper}, {"pattern": daily}],
                    }
                }
            }
            return CommandResult(0, json.dumps(payload), "")
        return CommandResult(0, "{}", "")


@pytest.fixture
def fake_cli():
    return FakeCLI()


def test_missing_agent_plan_creates_only_dedicated_agent(tmp_path):
    options = make_options(tmp_path)

    actions = build_setup_actions(options, agents=[{"id": "main"}])

    argv = [action.argv for action in actions]
    assert [
        "openclaw",
        "agents",
        "add",
        "openhouse-crm",
        "--workspace",
        str(options.workspace),
        "--non-interactive",
        "--json",
    ] in argv
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


def test_setup_allowlists_only_shipped_skill_entrypoints(tmp_path):
    options = make_options(tmp_path)

    actions = build_setup_actions(options, agents=[{"id": "openhouse-crm"}])

    rendered = [" ".join(action.argv) for action in actions]
    wrapper = str(options.workspace / "skills/crm-db-operations/cli.py")
    daily = str(options.workspace / "skills/daily-brief/scripts/run_daily_brief.py")
    assert any("approvals allowlist add" in command and wrapper in command for command in rendered)
    assert any("approvals allowlist add" in command and daily in command for command in rendered)
    assert all("--gateway" in command for command in rendered if "approvals allowlist add" in command)
    assert not any(command.endswith(" python3") for command in rendered)


def test_discord_binding_is_opt_in(tmp_path):
    without_binding = build_setup_actions(make_options(tmp_path), agents=[])
    with_binding = build_setup_actions(
        make_options(tmp_path, bind_discord="primary"), agents=[]
    )

    assert not any(action.argv[1:3] == ["agents", "bind"] for action in without_binding)
    assert any(
        action.argv
        == [
            "openclaw",
            "agents",
            "bind",
            "--agent",
            "openhouse-crm",
            "--bind",
            "discord:primary",
            "--json",
        ]
        for action in with_binding
    )


def test_sync_skills_copies_canonical_directories_and_sets_entrypoints_executable(tmp_path):
    workspace = tmp_path / "workspace"

    targets = sync_skills(REPO_ROOT, workspace, dry_run=False)

    assert targets == [
        workspace / "skills" / "crm-db-operations",
        workspace / "skills" / "business-card-scanner",
        workspace / "skills" / "daily-command-center",
        workspace / "skills" / "daily-brief",
    ]
    assert (targets[0] / "cli.py").stat().st_mode & 0o111
    assert (targets[3] / "scripts" / "run_daily_brief.py").stat().st_mode & 0o111


def test_dry_run_never_executes_mutating_commands(fake_cli, tmp_path):
    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=fake_cli)

    assert result.ok
    assert fake_cli.mutating_calls == []


def test_output_redacts_api_token(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "secret-value")

    result = configure_openclaw(make_options(tmp_path), cli=fake_cli)

    assert "secret-value" not in result.render()
    assert "<redacted>" in result.render()


def test_failed_preflight_stops_before_sync_or_mutation(tmp_path):
    cli = FakeCLI(
        {
            ("openclaw", "--version"): CommandResult(127, "", "not found"),
        }
    )
    options = make_options(tmp_path)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


def test_inconsistent_agent_indexes_fail_before_sync_or_mutation(tmp_path):
    options = make_options(tmp_path)
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0,
                json.dumps(
                    {
                        "agents": [
                            {"id": "openhouse-crm", "workspace": str(options.workspace)}
                        ]
                    }
                ),
                "",
            ),
            ("openclaw", "config", "get", "agents.list", "--json"): CommandResult(
                0, "[]", ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "inconsistent" in result.render()
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


@pytest.mark.parametrize(
    "agent_policy",
    [
        {"autoAllowSkills": True, "allowlist": []},
        {
            "autoAllowSkills": False,
            "allowlist": [
                {
                    "pattern": "WORKSPACE/skills/crm-db-operations/cli.py",
                    "argPattern": "^list_leads.*$",
                }
            ],
        },
    ],
)
def test_incompatible_gateway_approval_policy_fails_before_mutation(
    tmp_path, agent_policy
):
    options = make_options(tmp_path, dry_run=True)
    serialized = json.dumps(agent_policy).replace("WORKSPACE", str(options.workspace))
    cli = FakeCLI(
        {
            ("openclaw", "approvals", "get", "--gateway", "--json"): CommandResult(
                0,
                '{"agents":{"openhouse-crm":' + serialized + "}}",
                "",
            )
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "approval policy" in result.render()
    assert cli.mutating_calls == []


def test_configuration_updates_only_dedicated_agent_fields(tmp_path, monkeypatch):
    options = make_options(tmp_path)
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    agents = [{"id": "main"}, {"id": "openhouse-crm", "workspace": str(options.workspace)}]
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": agents}), ""
            ),
            ("openclaw", "config", "get", "agents.list", "--json"): CommandResult(
                0, json.dumps(agents), ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert result.ok
    rendered = [" ".join(call) for call in cli.mutating_calls]
    assert any("config set agents.list[1].skills" in call for call in rendered)
    assert any("config set agents.list[1].tools" in call for call in rendered)
    assert any("config set agents.list[1].sandbox" in call for call in rendered)
    assert not any("agents.list[0]" in call for call in rendered)


def test_mutation_failure_stops_before_later_changes(tmp_path):
    options = make_options(tmp_path)
    failing = (
        "openclaw",
        "agents",
        "add",
        "openhouse-crm",
        "--workspace",
        str(options.workspace),
        "--non-interactive",
        "--json",
    )
    cli = FakeCLI({failing: CommandResult(1, "", "could not create")})

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert cli.mutating_calls == [list(failing)]


def test_openclaw_cli_never_invokes_a_shell(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(setup_openclaw.subprocess, "run", fake_run)

    OpenClawCLI().run(["config", "validate", "--json"])

    assert seen["command"] == ["openclaw", "config", "validate", "--json"]
    assert seen["kwargs"]["shell"] is False
