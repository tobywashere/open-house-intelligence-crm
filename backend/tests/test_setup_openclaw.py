import importlib.util
import json
import os
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
parse_args = setup_openclaw._parse_args


def make_options(tmp_path, *, dry_run=False, bind_discord=None):
    return SetupOptions(
        agent_id="openhouse-crm",
        workspace=tmp_path / "workspace-openhouse-crm",
        crm_api_url="http://localhost:8080/api",
        bind_discord=bind_discord,
        dry_run=dry_run,
    )


def gateway_approval_payload(
    *,
    agent_id="openhouse-crm",
    entries=None,
    defaults=None,
    inherited=None,
    agent_policy=None,
    effective=None,
):
    agent = {"autoAllowSkills": False, "allowlist": entries or []}
    agent.update(agent_policy or {})
    agents = {agent_id: agent}
    if inherited is not None:
        agents["*"] = inherited
    scope = {
        "scopeLabel": f"agent:{agent_id}",
        "agentId": agent_id,
        "host": {"requested": "gateway"},
        "mode": {"effective": "allowlist"},
        "security": {"effective": "allowlist"},
        "ask": {"effective": "off"},
        "askFallback": {"effective": "deny"},
    }
    scope.update(effective or {})
    return {
        "path": "~/.openclaw/state/openclaw.sqlite#exec_approvals_config",
        "exists": True,
        "hash": "test-hash",
        "file": {
            "version": 1,
            "defaults": {"autoAllowSkills": False, **(defaults or {})},
            "agents": agents,
        },
        "effectivePolicy": {"scopes": [scope]},
    }


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
            return CommandResult(
                0,
                "Commands:\n"
                "  agents\n  add\n  list\n  bind\n  config\n  get\n  set\n"
                "  validate\n  skills\n  check\n  approvals\n  allowlist\n"
                "  exec-policy\n  show\n  sandbox\n  explain\n  gateway\n"
                "  restart\n"
                "Options:\n"
                "  --workspace PATH\n  --non-interactive\n  --json\n"
                "  --agent ID\n  --bind TARGET\n  --strict-json VALUE\n"
                "  --gateway",
                "",
            )
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
            entries = [
                {"pattern": pattern, "lastUsedAt": 1}
                for pattern in (wrapper, daily)
                if pattern
            ]
            payload = gateway_approval_payload(entries=entries)
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


def test_dashboard_refresh_uses_the_installed_allowlisted_daily_runner(tmp_path):
    options = make_options(tmp_path)
    actions = build_setup_actions(options, agents=[{"id": "openhouse-crm"}])
    allowed = [
        action.argv[-1]
        for action in actions
        if action.argv[1:4] == ["approvals", "allowlist", "add"]
    ]
    daily_runner = str(
        options.workspace / "skills" / "daily-brief" / "scripts" / "run_daily_brief.py"
    )
    overlay = (
        REPO_ROOT / "dashboard" / "src" / "components" / "DailySummaryOverlay.tsx"
    ).read_text()
    skill = (REPO_ROOT / "skills" / "daily-brief" / "SKILL.md").read_text()

    assert allowed == [
        str(options.workspace / "skills" / "crm-db-operations" / "cli.py"),
        daily_runner,
    ]
    assert "daily-brief skill in Mode 1" in overlay
    assert "python3 skills/" not in overlay
    assert "{baseDir}/scripts/run_daily_brief.py" in skill
    assert "Mode 2" not in skill
    assert "--publish-payload" not in skill
    assert "/tmp" not in skill


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


def test_sync_skills_creates_a_missing_custom_workspace_parent(tmp_path):
    workspace = tmp_path / "custom" / "nested" / "workspace"

    targets = sync_skills(REPO_ROOT, workspace, dry_run=False)

    assert all(target.is_dir() for target in targets)
    assert (workspace / "skills" / "crm-db-operations" / "cli.py").is_file()


def test_sync_skills_rejects_destination_symlinks(tmp_path):
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    skills.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (skills / "crm-db-operations").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SetupConflict, match="symlink"):
        sync_skills(REPO_ROOT, workspace, dry_run=False)

    assert list(outside.iterdir()) == []


def test_sync_skills_preflights_every_source_before_mutating(tmp_path):
    repo = tmp_path / "repo"
    for name in setup_openclaw.SKILL_NAMES[:-1]:
        source = repo / "skills" / name
        source.mkdir(parents=True)
        (source / "content.txt").write_text(name)
    workspace = tmp_path / "workspace"

    with pytest.raises(SetupConflict, match="missing"):
        sync_skills(repo, workspace, dry_run=False)

    assert not workspace.exists()


def test_sync_skills_copy_failure_leaves_destination_unmodified(tmp_path, monkeypatch):
    workspace = tmp_path / "custom" / "nested" / "workspace"
    real_copytree = setup_openclaw.shutil.copytree
    calls = 0

    def fail_second_copy(source, target, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated copy failure")
        return real_copytree(source, target, *args, **kwargs)

    monkeypatch.setattr(setup_openclaw.shutil, "copytree", fail_second_copy)

    with pytest.raises(SetupConflict, match="simulated copy failure"):
        sync_skills(REPO_ROOT, workspace, dry_run=False)

    assert not workspace.exists()
    assert not (tmp_path / "custom").exists()


def test_sync_skills_directory_failure_rolls_back_created_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    skills_root = workspace / "skills"
    real_mkdir = Path.mkdir

    def fail_skills_root(path, *args, **kwargs):
        if path == skills_root:
            raise OSError("simulated directory failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_skills_root)

    with pytest.raises(SetupConflict, match="simulated directory failure"):
        sync_skills(REPO_ROOT, workspace, dry_run=False)

    assert not workspace.exists()


def test_sync_skills_swap_failure_restores_every_existing_skill(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    skills_root = workspace / "skills"
    for name in setup_openclaw.SKILL_NAMES:
        target = skills_root / name
        target.mkdir(parents=True)
        (target / "existing.txt").write_text(f"existing {name}")
    real_rename = Path.rename

    def fail_second_staged_install(path, target):
        if path.parent.name == "staged" and path.name == "business-card-scanner":
            raise OSError("simulated swap failure")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second_staged_install)

    with pytest.raises(SetupConflict, match="simulated swap failure"):
        sync_skills(REPO_ROOT, workspace, dry_run=False)

    for name in setup_openclaw.SKILL_NAMES:
        assert (skills_root / name / "existing.txt").read_text() == f"existing {name}"


def test_dry_run_never_executes_mutating_commands(fake_cli, tmp_path):
    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=fake_cli)

    assert result.ok
    assert fake_cli.mutating_calls == []


def test_output_redacts_api_token(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "secret-value")

    result = configure_openclaw(make_options(tmp_path), cli=fake_cli)

    assert "secret-value" not in result.render()
    assert "<redacted>" in result.render()


def test_setup_defaults_load_repo_env_port_and_token_without_leaking(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "PORT=9123\nOHI_API_TOKEN=secret-from-dotenv\nAGENT_MODE=openclaw\n"
    )
    monkeypatch.delenv("CRM_API_URL", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)

    try:
        options = parse_args(
            ["--workspace", str(tmp_path / "workspace")], repo=tmp_path
        )
        cli = FakeCLI()
        result = configure_openclaw(options, cli=cli)

        assert options.crm_api_url == "http://localhost:9123/api"
        token_calls = [
            call for call in cli.mutating_calls
            if 'skills.entries["crm-db-operations"].env.OHI_API_TOKEN' in " ".join(call)
        ]
        assert len(token_calls) == 1
        assert json.loads(token_calls[0][-2]) == "secret-from-dotenv"
        assert "secret-from-dotenv" not in result.render()
        assert "<redacted>" in result.render()
    finally:
        for key in ("CRM_API_URL", "PORT", "OHI_API_TOKEN", "AGENT_MODE"):
            os.environ.pop(key, None)


def test_setup_defaults_prefer_exported_values_and_explicit_cli_args(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "CRM_API_URL=http://dotenv.example/api\nPORT=9123\n"
    )
    monkeypatch.setenv("CRM_API_URL", "http://exported.example/api")

    try:
        exported = parse_args([], repo=tmp_path)
        explicit = parse_args(["--crm-api-url", "http://cli.example/api"], repo=tmp_path)

        assert exported.crm_api_url == "http://exported.example/api"
        assert explicit.crm_api_url == "http://cli.example/api"
    finally:
        os.environ.pop("PORT", None)


def test_setup_defaults_use_dotenv_crm_url_before_port(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "CRM_API_URL=http://crm.example:9555/api\nPORT=9123\n"
    )
    monkeypatch.delenv("CRM_API_URL", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    try:
        options = parse_args([], repo=tmp_path)
        assert options.crm_api_url == "http://crm.example:9555/api"
    finally:
        for key in ("CRM_API_URL", "PORT"):
            os.environ.pop(key, None)


def test_dotenv_token_is_redacted_from_dry_run_and_setup_error(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OHI_API_TOKEN=dotenv-secret\n")
    monkeypatch.delenv("OHI_API_TOKEN", raising=False)
    try:
        options = parse_args(
            ["--workspace", str(tmp_path / "workspace")], repo=tmp_path
        )
        dry_run = configure_openclaw(
            SetupOptions(**{**options.__dict__, "dry_run": True}), cli=FakeCLI()
        )
        failed = configure_openclaw(
            options,
            cli=FakeCLI(
                {("openclaw", "gateway", "restart"): CommandResult(1, "", "dotenv-secret")}
            ),
        )

        assert "dotenv-secret" not in dry_run.render()
        assert "dotenv-secret" not in failed.render()
        assert "<redacted>" in dry_run.render()
        assert "<redacted>" in failed.render()
    finally:
        os.environ.pop("OHI_API_TOKEN", None)


@pytest.mark.parametrize("token", ['quote"value', r"slash\\value", "line\nvalue\tend"])
def test_output_redacts_raw_and_json_escaped_api_tokens(
    fake_cli, tmp_path, monkeypatch, token
):
    monkeypatch.setenv("OHI_API_TOKEN", token)

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=fake_cli)
    rendered = result.render()

    assert result.ok
    assert token not in rendered
    assert json.dumps(token) not in rendered
    assert json.dumps(token)[1:-1] not in rendered
    assert "<redacted>" in rendered


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


@pytest.mark.parametrize(
    ("help_command", "help_text"),
    [
        (("openclaw", "agents", "--help"), "Usage: openclaw agents list"),
        (("openclaw", "config", "set", "--help"), "Usage: openclaw config set"),
    ],
)
def test_preflight_rejects_successful_help_missing_required_surfaces(
    tmp_path, help_command, help_text
):
    cli = FakeCLI({help_command: CommandResult(0, help_text, "")})
    options = make_options(tmp_path, dry_run=True)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "unsupported OpenClaw installation" in result.render()
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


@pytest.mark.parametrize(
    ("help_command", "help_text", "missing"),
    [
        (
            ("openclaw", "config", "get", "--help"),
            "Options:\n  --json-output PATH",
            "--json",
        ),
        (
            ("openclaw", "agents", "--help"),
            "Commands:\n  add-more\n  list-all\n\nDescription: supports add and list workflows",
            "add",
        ),
    ],
)
def test_preflight_rejects_deceptive_help_superstrings(
    tmp_path, help_command, help_text, missing
):
    cli = FakeCLI({help_command: CommandResult(0, help_text, "")})

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=cli)

    assert not result.ok
    assert missing in result.render()
    assert cli.mutating_calls == []


def test_agents_bind_capability_is_required_only_when_requested(tmp_path):
    missing_bind_help = CommandResult(
        0,
        "Commands:\n  add\n  list\nOptions:\n  --workspace\n"
        "  --non-interactive\n  --json\n  --agent\n  --strict-json",
        "",
    )
    without_binding = FakeCLI(
        {("openclaw", "agents", "--help"): missing_bind_help}
    )
    with_binding = FakeCLI(
        {("openclaw", "agents", "--help"): missing_bind_help}
    )

    result_without = configure_openclaw(
        make_options(tmp_path, dry_run=True), cli=without_binding
    )
    result_with = configure_openclaw(
        make_options(tmp_path, dry_run=True, bind_discord="primary"), cli=with_binding
    )

    assert result_without.ok
    assert not result_with.ok
    assert "bind" in result_with.render()


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
    "payload_factory",
    [
        lambda options: {
            **gateway_approval_payload(),
            "file": {**gateway_approval_payload()["file"], "version": 2},
        },
        lambda options: gateway_approval_payload(defaults={"autoAllowSkills": True}),
        lambda options: gateway_approval_payload(
            inherited={"autoAllowSkills": True, "allowlist": []}
        ),
        lambda options: gateway_approval_payload(
            entries=[str(options.workspace / "skills/crm-db-operations/cli.py")]
        ),
        lambda options: gateway_approval_payload(
            entries=[
                {
                    "pattern": str(
                        options.workspace / "skills/crm-db-operations/cli.py"
                    ),
                    "argPattern": "^list_leads.*$",
                }
            ]
        ),
        lambda options: gateway_approval_payload(
            entries=[{"pattern": "allowed", "unexpected": True}]
        ),
        lambda options: gateway_approval_payload(
            agent_policy={"unexpectedPolicyField": True}
        ),
    ],
)
def test_incompatible_gateway_approval_document_fails_before_mutation(
    tmp_path, payload_factory
):
    options = make_options(tmp_path, dry_run=True)
    cli = FakeCLI(
        {
            ("openclaw", "approvals", "get", "--gateway", "--json"): CommandResult(
                0,
                json.dumps(payload_factory(options)),
                "",
            )
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "approval policy" in result.render()
    assert cli.mutating_calls == []


@pytest.mark.parametrize(
    "effective",
    [
        {"security": {"effective": "full"}},
        {"mode": {"effective": "full"}},
        {"askFallback": {"effective": "full"}},
        {"host": {"requested": "auto"}},
    ],
)
def test_unsafe_effective_gateway_policy_fails_before_existing_agent_mutation(
    tmp_path, effective
):
    options = make_options(tmp_path, dry_run=True)
    agent = {"id": "openhouse-crm", "workspace": str(options.workspace)}
    payload = gateway_approval_payload(effective=effective)
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents.list", "--json"): CommandResult(
                0, json.dumps([agent]), ""
            ),
            ("openclaw", "approvals", "get", "--gateway", "--json"): CommandResult(
                0, json.dumps(payload), ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "effective" in result.render()
    assert cli.mutating_calls == []


def test_mismatched_effective_scope_identity_is_rejected(tmp_path):
    options = make_options(tmp_path, dry_run=True)
    agent = {"id": "openhouse-crm", "workspace": str(options.workspace)}
    payload = gateway_approval_payload()
    scope = payload["effectivePolicy"]["scopes"][0]
    scope["agentId"] = "another-agent"
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents.list", "--json"): CommandResult(
                0, json.dumps([agent]), ""
            ),
            ("openclaw", "approvals", "get", "--gateway", "--json"): CommandResult(
                0, json.dumps(payload), ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "effective" in result.render()


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


@pytest.mark.parametrize(
    "skills_payload",
    [
        {"eligible": ["not-crm-db-operations-extra"]},
        {"error": "crm-db-operations unavailable"},
        {"eligible": [{"name": "crm-db-operations"}]},
    ],
)
def test_skill_check_requires_exact_eligible_membership(tmp_path, skills_payload):
    cli = FakeCLI(
        {
            (
                "openclaw",
                "skills",
                "check",
                "--agent",
                "openhouse-crm",
                "--json",
            ): CommandResult(0, json.dumps(skills_payload), "")
        }
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "eligible" in result.render()


def test_filesystem_conflict_is_returned_as_setup_result(tmp_path):
    options = make_options(tmp_path)
    skills_path = options.workspace / "skills"
    skills_path.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    skills_path.symlink_to(outside, target_is_directory=True)

    result = configure_openclaw(options, cli=FakeCLI())

    assert not result.ok
    assert "symlink" in result.render()


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
