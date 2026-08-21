import importlib.util
import json
import os
from pathlib import Path
import re
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

TOKEN_CONFIG_PATH = 'skills.entries["crm-db-operations"].apiKey'
TOKEN_ENTRY_CONFIG_PATH = 'skills.entries["crm-db-operations"]'
LEGACY_TOKEN_ENV_PATH = 'skills.entries["crm-db-operations"].env'
LEGACY_TOKEN_CONFIG_PATH = f"{LEGACY_TOKEN_ENV_PATH}.OHI_API_TOKEN"


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
        "workspaceRoot": "/redacted/sandboxes",
        "effectiveHostWorkspaceRoot": "/redacted/workspace",
        "runtimeWorkdir": "/redacted/workspace",
        "workspaceMounts": [],
        "workspaceSource": "direct",
        "sessionIsSandboxed": False,
        "tools": {
            "allow": [],
            "deny": [],
            "sources": {
                "allow": {"source": "default", "key": "default"},
                "deny": {"source": "default", "key": "default"},
            },
        },
    },
    "elevated": {
        "enabled": False,
        "allowedByConfig": False,
        "alwaysAllowedByConfig": False,
        "allowFrom": {},
        "failures": [],
    },
    "fixIt": [],
}

OPENCLAW_SANDBOX_EXPLAIN_BETA = {
    **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
    "sandbox": {
        **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
        "scope": "agent",
    },
}


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
    def __init__(
        self,
        responses=None,
        *,
        config_path=None,
        legacy_token=None,
        plugin_path=None,
        plugin_enabled=False,
    ):
        self.calls = []
        self.mutating_calls = []
        self.responses = responses or {}
        self.created_agent = None
        self.config_path = config_path
        self.config_values = {}
        self.legacy_token = legacy_token
        self.plugin_path = plugin_path
        self.plugin_enabled = plugin_enabled
        self.approval_patterns = set()

    def run(self, args, *, mutate=False):
        self.calls.append(args)
        key = tuple(args)
        response = self.responses.get(key)
        if mutate:
            self.mutating_calls.append(args)
        if response is not None and response.returncode != 0:
            return response
        if mutate:
            if args[1:3] == ["agents", "add"]:
                self.created_agent = {
                    "id": args[3],
                    "workspace": args[args.index("--workspace") + 1],
                }
            elif args[1:3] == ["config", "set"]:
                path = args[3]
                if "--ref-provider" in args:
                    self.config_values[path] = {
                        "source": args[args.index("--ref-source") + 1],
                        "provider": args[args.index("--ref-provider") + 1],
                        "id": args[args.index("--ref-id") + 1],
                    }
                elif "--strict-json" in args:
                    value = json.loads(args[-2])
                    if path.endswith(".tools") and isinstance(value, dict):
                        exec_policy = value.get("exec")
                        if (
                            isinstance(exec_policy, dict)
                            and "mode" in exec_policy
                            and ({"security", "ask"} & set(exec_policy))
                        ):
                            return CommandResult(
                                1,
                                "",
                                "Error: Config validation failed: "
                                f"{path}.exec.mode: tools.exec.mode cannot be combined "
                                "with tools.exec.security or tools.exec.ask",
                            )
                    self.config_values[path] = value
                    match = re.fullmatch(
                        r'agents\.(?:list\[\d+\]|entries\["[^\"]+"\])\.(.+)',
                        path,
                    )
                    if match and self.created_agent is not None:
                        self.created_agent[match.group(1)] = self.config_values[path]
            elif args[1:3] == ["config", "unset"]:
                if args[3] == LEGACY_TOKEN_CONFIG_PATH:
                    self.legacy_token = None
                self.config_values.pop(args[3], None)
                match = re.fullmatch(
                    r'agents\.(?:list\[\d+\]|entries\["[^\"]+"\])\.(.+)',
                    args[3],
                )
                if match and self.created_agent is not None:
                    self.created_agent.pop(match.group(1), None)
            elif args[1:3] == ["plugins", "install"]:
                self.plugin_path = args[args.index("--link") + 1]
            elif args[1:3] == ["plugins", "enable"]:
                self.plugin_enabled = True
            elif args[1:3] == ["plugins", "disable"]:
                self.plugin_enabled = False
            elif args[1:3] == ["plugins", "uninstall"]:
                self.plugin_path = None
                self.plugin_enabled = False
            elif args[1:4] == ["approvals", "allowlist", "add"]:
                self.approval_patterns.add(args[-1])
            elif args[1:4] == ["approvals", "allowlist", "remove"]:
                self.approval_patterns.discard(args[-1])
        if response is not None:
            return response
        if args[-1:] == ["--help"]:
            return CommandResult(
                0,
                "Commands:\n"
                "  agents\n  add\n  list\n  bind\n  config\n  get\n  set\n"
                "  unset\n  file\n  validate\n  skills\n  check\n  approvals\n  allowlist\n"
                "  plugins\n  install\n  inspect\n  enable\n  disable\n  uninstall\n  remove\n"
                "  exec-policy\n  show\n  sandbox\n  explain\n  gateway\n"
                "  restart\n"
                "Options:\n"
                "  --workspace PATH\n  --non-interactive\n  --json\n"
                "  --agent ID\n  --bind TARGET\n  --strict-json VALUE\n"
                "  --ref-provider NAME\n  --ref-source SOURCE\n  --ref-id ID\n"
                "  --dry-run\n"
                "  --gateway\n  --link\n  --force\n  --runtime\n  --keep-files",
                "",
            )
        if args == ["openclaw", "--version"]:
            return CommandResult(0, "OpenClaw 2026.8.1\n", "")
        if args == ["openclaw", "config", "file"]:
            if self.config_path is None:
                return CommandResult(1, "", "test config path not set")
            return CommandResult(0, f"{self.config_path}\n", "")
        if args == ["openclaw", "agents", "list", "--json"]:
            return CommandResult(
                0, json.dumps({"agents": [self.created_agent] if self.created_agent else []}), ""
            )
        if args == ["openclaw", "config", "get", "agents", "--json"]:
            return CommandResult(
                0,
                json.dumps(
                    {"defaults": {}, "list": [self.created_agent] if self.created_agent else []}
                ),
                "",
            )
        if args == ["openclaw", "plugins", "list", "--json"]:
            plugins = []
            if self.plugin_path is not None:
                plugins.append(
                    {
                        "id": "openhouse-crm",
                        "enabled": self.plugin_enabled,
                        "format": "openclaw",
                        "source": "linked",
                        "rootDir": self.plugin_path,
                        "dependencyStatus": {"ok": True},
                    }
                )
            return CommandResult(0, json.dumps({"plugins": plugins}), "")
        if args == [
            "openclaw",
            "plugins",
            "inspect",
            "openhouse-crm",
            "--runtime",
            "--json",
        ]:
            return CommandResult(
                0,
                json.dumps(
                    {
                        "plugin": {
                            "id": "openhouse-crm",
                            "enabled": self.plugin_enabled,
                            "rootDir": self.plugin_path,
                        },
                        "runtime": {
                            "tools": [{"name": "openhouse_crm"}],
                            "diagnostics": [],
                        },
                    }
                ),
                "",
            )
        if args == ["openclaw", "config", "get", "plugins.allow", "--json"]:
            if "plugins.allow" not in self.config_values:
                return CommandResult(1, "", "Config path not found: plugins.allow")
            return CommandResult(0, json.dumps(self.config_values["plugins.allow"]), "")
        if args == [
            "openclaw",
            "config",
            "get",
            TOKEN_CONFIG_PATH,
            "--json",
        ]:
            return CommandResult(
                0, json.dumps(self.config_values.get(TOKEN_CONFIG_PATH)), ""
            )
        if args == [
            "openclaw",
            "config",
            "get",
            TOKEN_ENTRY_CONFIG_PATH,
            "--json",
        ]:
            entry = {"apiKey": self.config_values.get(TOKEN_CONFIG_PATH)}
            if self.legacy_token is not None:
                entry["env"] = {"OHI_API_TOKEN": self.legacy_token}
            return CommandResult(0, json.dumps(entry), "")
        if args == [
            "openclaw",
            "config",
            "get",
            LEGACY_TOKEN_ENV_PATH,
            "--json",
        ]:
            env = {}
            if self.legacy_token is not None:
                env["OHI_API_TOKEN"] = self.legacy_token
            return CommandResult(0, json.dumps(env), "")
        if (
            args[1:3] == ["config", "get"]
            and args[3].startswith("agents.")
            and args[3].endswith(".tools")
            and args[-1] == "--json"
        ):
            return CommandResult(0, json.dumps(self.config_values.get(args[3])), "")
        if args == ["openclaw", "skills", "check", "--agent", "openhouse-crm", "--json"]:
            return CommandResult(0, '{"eligible": ["crm-db-operations"]}', "")
        if args == ["openclaw", "sandbox", "explain", "--agent", "openhouse-crm", "--json"]:
            return CommandResult(
                0,
                json.dumps(OPENCLAW_SANDBOX_EXPLAIN_STABLE),
                "",
            )
        if args == ["openclaw", "exec-policy", "show", "--json"]:
            return CommandResult(0, '{"requested": {"host": "gateway", "mode": "allowlist"}}', "")
        if args == ["openclaw", "approvals", "get", "--gateway", "--json"]:
            entries = [
                {"pattern": pattern, "lastUsedAt": 1}
                for pattern in sorted(self.approval_patterns)
            ]
            payload = gateway_approval_payload(entries=entries)
            return CommandResult(0, json.dumps(payload), "")
        if args[1:3] == ["agents", "add"] and self.created_agent is not None:
            return CommandResult(
                0,
                json.dumps(
                    {
                        "agentId": self.created_agent["id"],
                        "workspace": self.created_agent["workspace"],
                    }
                ),
                "",
            )
        return CommandResult(0, "{}", "")


class FreshRosterCLI(FakeCLI):
    def __init__(
        self,
        *,
        schema,
        config_path=None,
        initial_root="defaults",
        ignored_config_paths=(),
    ):
        super().__init__(config_path=config_path)
        self.schema = schema
        self.initial_root = initial_root
        self.ignored_config_paths = set(ignored_config_paths)

    def run(self, args, *, mutate=False):
        if (
            mutate
            and args[1:3] == ["config", "set"]
            and args[3] in self.ignored_config_paths
        ):
            self.calls.append(args)
            self.mutating_calls.append(args)
            return CommandResult(0, "", "")
        if args == ["openclaw", "config", "get", "agents", "--json"]:
            self.calls.append(args)
            if self.created_agent is None:
                if self.initial_root == "missing":
                    return CommandResult(
                        1, '{"error":"Config path not found: agents"}', ""
                    )
                return CommandResult(0, json.dumps({"defaults": {}}), "")
            if self.schema == "list":
                payload = {"defaults": {}, "list": [self.created_agent]}
            else:
                custom = dict(self.created_agent)
                custom.pop("id")
                payload = {
                    "defaults": {},
                    "entries": {
                        "main": {"default": True},
                        self.created_agent["id"]: custom,
                    },
                }
            return CommandResult(0, json.dumps(payload), "")
        return super().run(args, mutate=mutate)


class PartialPolicyCLI(FakeCLI):
    """Expose the real policy transition for an existing partial CRM agent."""

    def __init__(self, agent, responses=None):
        super().__init__(responses)
        self.created_agent = agent

    def run(self, args, *, mutate=False):
        result = super().run(args, mutate=mutate)
        if args != ["openclaw", "approvals", "get", "--gateway", "--json"]:
            return result
        payload = json.loads(result.stdout)
        tools = self.created_agent.get("tools", {})
        if tools.get("exec") != {"mode": "allowlist", "host": "gateway"}:
            scope = payload["effectivePolicy"]["scopes"][0]
            scope["host"] = {"requested": "auto"}
            scope["mode"] = {"effective": "full"}
            scope["security"] = {"effective": "full"}
        return CommandResult(0, json.dumps(payload), "")


class RollbackWorkspaceChangeCLI(PartialPolicyCLI):
    def __init__(self, agent):
        self.validation_failed = False
        super().__init__(
            agent,
            {
                ("openclaw", "config", "validate", "--json"): CommandResult(
                    1, "", "simulated validation failure"
                )
            },
        )

    def run(self, args, *, mutate=False):
        if args == ["openclaw", "config", "validate", "--json"]:
            self.validation_failed = True
        if (
            self.validation_failed
            and args == ["openclaw", "config", "get", "agents", "--json"]
        ):
            self.calls.append(args)
            changed = {**self.created_agent, "workspace": "/reassigned-workspace"}
            return CommandResult(
                0, json.dumps({"defaults": {}, "list": [changed]}), ""
            )
        return super().run(args, mutate=mutate)


class RepairWorkspaceChangeCLI(PartialPolicyCLI):
    def __init__(self, agent, *, change_on_read=3):
        super().__init__(agent)
        self.roster_reads = 0
        self.change_on_read = change_on_read

    def run(self, args, *, mutate=False):
        if args == ["openclaw", "config", "get", "agents", "--json"]:
            self.calls.append(args)
            self.roster_reads += 1
            workspace = (
                "/reassigned-workspace"
                if self.roster_reads >= self.change_on_read
                else self.created_agent["workspace"]
            )
            changed = {**self.created_agent, "workspace": workspace}
            return CommandResult(
                0, json.dumps({"defaults": {}, "list": [changed]}), ""
            )
        return super().run(args, mutate=mutate)


class PostCreateRosterCLI(FreshRosterCLI):
    def __init__(self, *, schema, after_creation):
        super().__init__(schema=schema)
        self.after_creation = after_creation

    def run(self, args, *, mutate=False):
        if (
            args == ["openclaw", "config", "get", "agents", "--json"]
            and self.created_agent is not None
        ):
            self.calls.append(args)
            return self.after_creation
        return super().run(args, mutate=mutate)


class ReorderingLegacyCLI(FakeCLI):
    def __init__(self, *, options, reorder_on_read):
        super().__init__()
        self.options = options
        self.reorder_on_read = reorder_on_read
        self.roster_reads = 0

    def run(self, args, *, mutate=False):
        target = {
            "id": self.options.agent_id,
            "workspace": str(self.options.workspace),
        }
        main = {"id": "main", "workspace": "/main"}
        if args == ["openclaw", "agents", "list", "--json"]:
            self.calls.append(args)
            return CommandResult(0, json.dumps({"agents": [main, target]}), "")
        if args == ["openclaw", "config", "get", "agents", "--json"]:
            self.calls.append(args)
            self.roster_reads += 1
            records = (
                [target, main]
                if self.roster_reads >= self.reorder_on_read
                else [main, target]
            )
            return CommandResult(
                0, json.dumps({"defaults": {}, "list": records}), ""
            )
        return super().run(args, mutate=mutate)


@pytest.fixture
def fake_cli(tmp_path):
    return FakeCLI(config_path=tmp_path / "openclaw.json")


@pytest.fixture(autouse=True)
def isolated_openclaw_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "openclaw-state"))


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


def test_configured_roster_compares_canonical_ids_and_preserves_raw_key_prefix():
    roster = setup_openclaw._configured_agent_roster(
        {
            "defaults": {"workspace": "/default"},
            "entries": {
                "main": {"default": True},
                "OpenHouse-CRM": {"workspace": "/crm"},
            },
        }
    )

    assert [agent["id"] for agent in roster.records] == ["main", "openhouse-crm"]
    assert roster.prefixes["openhouse-crm"] == 'agents.entries["OpenHouse-CRM"]'


def test_configured_roster_does_not_mutate_keyed_input_records():
    payload = {"entries": {"openhouse-crm": {"workspace": "/crm"}}}

    roster = setup_openclaw._configured_agent_roster(payload)

    assert roster.records == [{"workspace": "/crm", "id": "openhouse-crm"}]
    assert payload == {"entries": {"openhouse-crm": {"workspace": "/crm"}}}


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


def test_configured_roster_rejects_bare_object_as_ambiguous():
    with pytest.raises(SetupConflict):
        setup_openclaw._configured_agent_roster({})


@pytest.mark.parametrize(
    "payload",
    [
        {"list": [{"id": "Same"}, {"id": "same"}]},
        {"entries": {"Same": {}, "same": {}}},
    ],
)
def test_configured_roster_rejects_canonical_agent_id_collisions(payload):
    with pytest.raises(SetupConflict, match="duplicate agent ID"):
        setup_openclaw._configured_agent_roster(payload)


@pytest.mark.parametrize("agent_id", ["bad id", ".bad", "a" * 65])
def test_configured_roster_rejects_noncanonical_agent_id_syntax(agent_id):
    with pytest.raises(SetupConflict, match="invalid agent ID"):
        setup_openclaw._configured_agent_roster({"list": [{"id": agent_id}]})


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"list": {}},
        {"entries": []},
        {"list": [{"id": "same"}, {"id": "same"}]},
        {"entries": {"openhouse-crm": {"id": "different"}}},
        {"entries": {"openhouse-crm": {"id": None}}},
        {"list": [{"id": "legacy"}], "entries": {"modern": {}}},
    ],
)
def test_configured_roster_rejects_ambiguous_or_malformed_state(payload):
    with pytest.raises(SetupConflict):
        setup_openclaw._configured_agent_roster(payload)


def test_cli_agents_normalizes_list_and_keyed_entries():
    listed = setup_openclaw._cli_agents(
        {"agents": [{"id": "main"}, {"id": "openhouse-crm"}]}
    )
    entries = setup_openclaw._cli_agents(
        {"entries": {"openhouse-crm": {"workspace": "/crm"}}}
    )

    assert [agent["id"] for agent in listed] == ["main", "openhouse-crm"]
    assert entries == [{"workspace": "/crm", "id": "openhouse-crm"}]


@pytest.mark.parametrize(
    "payload",
    [
        {"agents": ["not-an-object"]},
        {"agents": [{"workspace": "/crm"}]},
        {"agents": [{"id": "same"}, {"id": "same"}]},
        {"entries": {"openhouse-crm": {"id": "different"}}},
    ],
)
def test_cli_agents_rejects_invalid_agent_records(payload):
    with pytest.raises(SetupConflict):
        setup_openclaw._cli_agents(payload)


def test_cli_agents_rejects_canonical_agent_id_collisions():
    with pytest.raises(SetupConflict, match="duplicate agent ID"):
        setup_openclaw._cli_agents(
            {"agents": [{"id": "OpenHouse-CRM"}, {"id": "openhouse-crm"}]}
        )


def test_cli_json_rejects_duplicate_object_keys_before_mutation(tmp_path):
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0,
                '{"agents":[],"agents":[{"id":"openhouse-crm"}]}',
                "",
            )
        }
    )
    options = make_options(tmp_path, dry_run=True)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "invalid JSON" in result.render()
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


def test_keyed_roster_raw_json_rejects_duplicate_keys():
    cli = FakeCLI(
        {
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0,
                '{"defaults":{},"entries":{"same":{},"same":{}}}',
                "",
            )
        }
    )

    with pytest.raises(SetupConflict, match="invalid JSON"):
        setup_openclaw._read_agent_roster(
            cli, allow_missing=True, label="initial agents config"
        )


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, '{"error":"Config path not found: agents"}', ""),
        CommandResult(1, "Config path not found: agents", ""),
        CommandResult(1, "", "Config path not found: agents"),
    ],
)
def test_agent_root_exact_missing_path_is_empty_before_creation(tmp_path, result):
    cli = FakeCLI({("openclaw", "config", "get", "agents", "--json"): result})

    roster = setup_openclaw._read_agent_roster(
        cli, allow_missing=True, label="initial agents config"
    )

    assert roster.schema is None
    assert roster.records == []


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, '{"error":"Config path not found: agents.list"}', ""),
        CommandResult(1, "", "Config path not found: agentsExtra"),
        CommandResult(1, "", "permission denied"),
        CommandResult(1, '{"error":', ""),
        CommandResult(2, '{"error":"Config path not found: agents"}', ""),
        CommandResult(
            1,
            '{"error":"Config path not found: agents", "code":"not-found"}',
            "",
        ),
        CommandResult(
            1,
            '{"error":"Config path not found: agents",'
            '"error":"Config path not found: agents"}',
            "",
        ),
        CommandResult(1, "", '{"error":"Config path not found: agents"}'),
    ],
)
def test_agent_root_rejects_inexact_or_noncanonical_missing_path(tmp_path, result):
    cli = FakeCLI({("openclaw", "config", "get", "agents", "--json"): result})

    with pytest.raises(SetupConflict):
        setup_openclaw._read_agent_roster(
            cli, allow_missing=True, label="initial agents config"
        )


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


@pytest.mark.parametrize("initial_root", ["defaults", "missing"])
def test_fresh_install_discovers_legacy_roster_after_agent_creation(
    tmp_path, initial_root
):
    cli = FreshRosterCLI(schema="list", initial_root=initial_root)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    rendered = [" ".join(call) for call in cli.mutating_calls]
    assert any("agents.list[0].skills" in call for call in rendered)
    assert any("agents.list[0].tools" in call for call in rendered)
    assert any("agents.list[0].sandbox" in call for call in rendered)
    assert not any("agents.entries" in call for call in rendered)


@pytest.mark.parametrize("initial_root", ["defaults", "missing"])
def test_fresh_install_discovers_modern_roster_after_agent_creation(
    tmp_path, initial_root
):
    cli = FreshRosterCLI(
        schema="entries",
        config_path=tmp_path / "openclaw.json",
        initial_root=initial_root,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    rendered = [" ".join(call) for call in cli.mutating_calls]
    assert any('agents.entries["openhouse-crm"].skills' in call for call in rendered)
    assert any('agents.entries["openhouse-crm"].tools' in call for call in rendered)
    assert any('agents.entries["openhouse-crm"].sandbox' in call for call in rendered)
    assert not any("agents.list" in call for call in rendered)


def test_fresh_dry_run_defers_agent_paths_until_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "oh_live_abcdefghijklmnopqrstuvwxyz")
    cli = FreshRosterCLI(schema="entries", config_path=tmp_path / "openclaw.json")

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=cli)

    assert result.ok, result.render()
    assert cli.mutating_calls == []
    rendered = result.render()
    assert "agents.list[0]" not in rendered
    assert "agents.entries" not in rendered
    assert "skills" in rendered
    assert "exec-only tools" in rendered
    assert "sandbox mode" in rendered
    assert "CRM API URL" in rendered
    assert "SecretRef" in rendered
    assert "allowlist" in rendered
    assert "exact roster path" in rendered


def _roster_payload(schema, agents):
    if schema == "list":
        return {"defaults": {}, "list": agents}
    return {"defaults": {}, "entries": {agent["id"]: agent for agent in agents}}


@pytest.mark.parametrize("schema", ["list", "entries"])
def test_existing_agent_is_idempotent_for_each_roster_schema(tmp_path, schema):
    options = make_options(tmp_path)
    agent = {"id": options.agent_id, "workspace": str(options.workspace)}
    prefix = "agents.list[0]" if schema == "list" else 'agents.entries["openhouse-crm"]'
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps(_roster_payload(schema, [agent])), ""
            ),
        }
    )

    first = configure_openclaw(options, cli=cli)
    second = configure_openclaw(options, cli=cli)

    assert first.ok, first.render()
    assert second.ok, second.render()
    assert not any(call[1:3] == ["agents", "add"] for call in cli.mutating_calls)
    assert f"{prefix}.tools" in " ".join(" ".join(call) for call in cli.calls)


@pytest.mark.parametrize("schema", ["list", "entries"])
def test_cli_config_agent_mismatch_fails_before_mutation_for_each_roster_schema(
    tmp_path, schema
):
    options = make_options(tmp_path, dry_run=True)
    agent = {"id": options.agent_id, "workspace": str(options.workspace)}
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps(_roster_payload(schema, [])), ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "inconsistent" in result.render()
    assert cli.mutating_calls == []


@pytest.mark.parametrize("schema", ["list", "entries"])
@pytest.mark.parametrize(
    ("field", "current", "expected"),
    [
        (
            "skills",
            ["unrelated-skill"],
            [
                "crm-db-operations",
                "business-card-scanner",
                "daily-command-center",
                "daily-brief",
            ],
        ),
        (
                "tools",
                {"allow": ["web_fetch"]},
                {
                    "allow": ["openhouse_crm", "exec"],
                "deny": [
                    "web_fetch",
                    "web_search",
                    "browser",
                    "read",
                    "write",
                    "edit",
                    "apply_patch",
                    "canvas",
                    "nodes",
                    "cron",
                ],
                "exec": {"mode": "allowlist", "host": "gateway"},
            },
        ),
        ("sandbox", {"mode": "on"}, {"mode": "off"}),
    ],
)
def test_existing_managed_agent_fields_are_repaired_for_each_schema(
    tmp_path, schema, field, current, expected
):
    options = make_options(tmp_path)
    agent = {"id": options.agent_id, "workspace": str(options.workspace), field: current}
    cli = PartialPolicyCLI(agent)
    if schema == "entries":
        cli = FakeCLI(
            {
                ("openclaw", "agents", "list", "--json"): CommandResult(
                    0, json.dumps({"agents": [agent]}), ""
                ),
                ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                    0, json.dumps(_roster_payload(schema, [agent])), ""
                ),
            }
        )

    result = configure_openclaw(options, cli=cli)

    assert result.ok, result.render()
    prefix = "agents.list[0]" if schema == "list" else 'agents.entries["openhouse-crm"]'
    matching = [
        call
        for call in cli.mutating_calls
        if call[1:3] == ["config", "set"] and call[3] == f"{prefix}.{field}"
    ]
    assert matching
    assert json.loads(matching[-1][-2]) == expected


def test_existing_agent_with_different_workspace_is_not_claimed(tmp_path):
    options = make_options(tmp_path)
    agent = {"id": options.agent_id, "workspace": "/someone-elses-agent"}
    cli = PartialPolicyCLI(agent)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "workspace" in result.render()
    assert cli.mutating_calls == []


def test_repair_stops_if_agent_workspace_changes_before_write(tmp_path):
    options = make_options(tmp_path)
    agent = {
        "id": options.agent_id,
        "workspace": str(options.workspace),
        "tools": {"allow": ["web_fetch"]},
    }
    cli = RepairWorkspaceChangeCLI(agent)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "workspace changed during setup" in result.render()
    assert not any(
        call[1:3] == ["config", "set"] and call[3].startswith("agents.")
        for call in cli.mutating_calls
    )


@pytest.mark.parametrize(
    ("bind_discord", "forbidden_prefix"),
    [
        ("team", ["agents", "bind"]),
        (None, ["approvals", "allowlist", "add"]),
    ],
    ids=["discord-binding", "executable-approval"],
)
def test_agent_addressed_actions_recheck_workspace(
    tmp_path, bind_discord, forbidden_prefix
):
    options = make_options(tmp_path, bind_discord=bind_discord)
    agent = {
        "id": options.agent_id,
        "workspace": str(options.workspace),
        "tools": {"allow": ["web_fetch"]},
    }
    cli = RepairWorkspaceChangeCLI(agent, change_on_read=6)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "workspace changed during setup" in result.render()
    assert not any(
        call[1 : 1 + len(forbidden_prefix)] == forbidden_prefix
        for call in cli.mutating_calls
    )


def test_ambiguous_legacy_and_modern_rosters_fail_before_mutation(tmp_path):
    options = make_options(tmp_path, dry_run=True)
    cli = FakeCLI(
        {
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0,
                json.dumps(
                    {
                        "defaults": {},
                        "list": [],
                        "entries": {"other": {"workspace": "/other"}},
                    }
                ),
                "",
            )
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "ambiguous" in result.render()
    assert cli.mutating_calls == []


def test_authoritative_tool_readback_uses_discovered_modern_prefix(tmp_path):
    cli = FreshRosterCLI(schema="entries")

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert [
        "openclaw",
        "config",
        "get",
        'agents.entries["openhouse-crm"].tools',
        "--json",
    ] in cli.calls


def test_modern_roster_writes_through_raw_key_after_canonical_match(tmp_path):
    options = make_options(tmp_path)
    listed_agent = {
        "id": options.agent_id,
        "workspace": str(options.workspace),
    }
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [listed_agent]}), ""
            ),
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0,
                json.dumps(
                    {
                        "defaults": {},
                        "entries": {
                            "main": {"default": True},
                            "OpenHouse-CRM": {
                                "workspace": str(options.workspace),
                            },
                        },
                    }
                ),
                "",
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert result.ok, result.render()
    assert any(
        call[1:4]
        == ["config", "set", 'agents.entries["OpenHouse-CRM"].tools']
        for call in cli.mutating_calls
    )


def test_ignored_agent_config_write_is_detected_by_path_specific_readback(tmp_path):
    tools_path = 'agents.entries["openhouse-crm"].tools'
    cli = FreshRosterCLI(
        schema="entries",
        ignored_config_paths={tools_path},
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "authoritative" in result.render().lower()
    assert ["openclaw", "config", "get", tools_path, "--json"] in cli.calls
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


@pytest.mark.parametrize(
    "after_creation",
    [
        CommandResult(1, "", "Config path not found: agents"),
        CommandResult(0, json.dumps({"defaults": {}, "list": []}), ""),
    ],
    ids=["missing-roster", "missing-target"],
)
def test_missing_roster_or_target_after_agent_creation_stops_before_config_writes(
    tmp_path, after_creation
):
    cli = PostCreateRosterCLI(schema="list", after_creation=after_creation)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert not any(
        call[1:3] == ["config", "set"] and call[3].startswith("agents.")
        for call in cli.mutating_calls
    )


@pytest.mark.parametrize(
    ("reorder_on_read", "forbidden_operation"),
    [
        (4, ["config", "set", "agents.list[1].tools"]),
        (6, ["config", "get", "agents.list[1].tools"]),
    ],
    ids=["before-second-mutation", "before-authoritative-readback"],
)
def test_legacy_prefix_reordering_fails_closed_before_stale_index_use(
    tmp_path, reorder_on_read, forbidden_operation
):
    options = make_options(tmp_path)
    cli = ReorderingLegacyCLI(
        options=options,
        reorder_on_read=reorder_on_read,
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "legacy agent roster changed" in result.render()
    assert not any(
        call[1 : 1 + len(forbidden_operation)] == forbidden_operation
        for call in cli.calls
    )
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


def test_conflicting_agent_workspace_requires_explicit_repair(tmp_path):
    options = make_options(tmp_path)

    with pytest.raises(SetupConflict, match="different workspace"):
        build_setup_actions(
            options,
            agents=[{"id": "openhouse-crm", "workspace": "/another/workspace"}],
        )


def test_setup_keeps_only_daily_brief_on_the_exec_allowlist(tmp_path):
    options = make_options(tmp_path)

    actions = build_setup_actions(options, agents=[{"id": "openhouse-crm"}])

    rendered = [" ".join(action.argv) for action in actions]
    wrapper = str(options.workspace / "skills/crm-db-operations/cli.py")
    daily = str(options.workspace / "skills/daily-brief/scripts/run_daily_brief.py")
    assert any("approvals allowlist remove" in command and wrapper in command for command in rendered)
    assert any("approvals allowlist add" in command and daily in command for command in rendered)
    assert all("--gateway" in command for command in rendered if "approvals allowlist" in command)
    assert not any(command.endswith(" python3") for command in rendered)


def test_setup_links_enables_and_runtime_verifies_repository_plugin(tmp_path):
    cli = FakeCLI()

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    expected_plugin = str(REPO_ROOT / "openclaw-plugins" / "openhouse-crm")
    assert [
        "openclaw",
        "plugins",
        "install",
        "--link",
        expected_plugin,
        "--force",
    ] in cli.mutating_calls
    assert ["openclaw", "plugins", "enable", "openhouse-crm"] in cli.mutating_calls
    assert [
        "openclaw",
        "plugins",
        "inspect",
        "openhouse-crm",
        "--runtime",
        "--json",
    ] in cli.calls
    assert cli.plugin_path == expected_plugin
    assert cli.plugin_enabled is True


def test_setup_repairs_legacy_crm_exec_approval_and_keeps_daily_runner(tmp_path):
    options = make_options(tmp_path)
    wrapper, daily = setup_openclaw._entrypoints(options)
    cli = FakeCLI()
    cli.approval_patterns.update({str(wrapper), str(daily)})

    result = configure_openclaw(options, cli=cli)

    assert result.ok, result.render()
    assert cli.approval_patterns == {str(daily)}
    assert [
        "openclaw",
        "approvals",
        "allowlist",
        "remove",
        "--agent",
        "openhouse-crm",
        "--gateway",
        str(wrapper),
    ] in cli.mutating_calls


def test_setup_refuses_same_plugin_id_from_an_unrelated_path(tmp_path):
    cli = FakeCLI(plugin_path="/unrelated/openhouse-crm", plugin_enabled=True)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "different source" in result.render().lower()
    assert cli.mutating_calls == []


def test_setup_does_not_restart_when_runtime_plugin_tool_is_missing(tmp_path):
    command = (
        "openclaw",
        "plugins",
        "inspect",
        "openhouse-crm",
        "--runtime",
        "--json",
    )
    cli = FakeCLI(
        {
            command: CommandResult(
                0,
                json.dumps(
                    {
                        "plugin": {"id": "openhouse-crm", "enabled": True},
                        "runtime": {"tools": [], "diagnostics": []},
                    }
                ),
                "",
            )
        }
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "openhouse_crm" in result.render()
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


def test_setup_accepts_real_top_level_runtime_inspection_shape(tmp_path):
    command = (
        "openclaw",
        "plugins",
        "inspect",
        "openhouse-crm",
        "--runtime",
        "--json",
    )
    plugin_root = REPO_ROOT / "openclaw-plugins" / "openhouse-crm"
    cli = FakeCLI(
        {
            command: CommandResult(
                0,
                json.dumps(
                    {
                        "workspaceDir": str(tmp_path / "workspace-openhouse-crm"),
                        "plugin": {
                            "id": "openhouse-crm",
                            "enabled": True,
                            "source": str(plugin_root / "dist" / "index.js"),
                            "status": "loaded",
                        },
                        "tools": [{"name": "openhouse_crm"}],
                        "diagnostics": [],
                    }
                ),
                "",
            )
        }
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()


def test_setup_preserves_unrelated_plugin_allowlist_entries(tmp_path):
    cli = FakeCLI()
    cli.config_values["plugins.allow"] = ["discord", "voice-call"]

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert cli.config_values["plugins.allow"] == [
        "discord",
        "voice-call",
        "openhouse-crm",
    ]


def test_runtime_failure_restores_legacy_approvals_and_new_plugin_state(tmp_path):
    options = make_options(tmp_path)
    wrapper, daily = setup_openclaw._entrypoints(options)
    command = (
        "openclaw",
        "plugins",
        "inspect",
        "openhouse-crm",
        "--runtime",
        "--json",
    )
    cli = FakeCLI(
        {
            command: CommandResult(
                0,
                json.dumps(
                    {
                        "plugin": {"id": "openhouse-crm", "enabled": True},
                        "runtime": {"tools": [], "diagnostics": []},
                    }
                ),
                "",
            )
        }
    )
    cli.approval_patterns.update({str(wrapper), str(daily)})

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert cli.approval_patterns == {str(wrapper), str(daily)}
    assert cli.plugin_path is None
    assert cli.plugin_enabled is False


def test_dedicated_agent_allows_only_native_crm_tool_and_daily_brief_exec(tmp_path):
    actions = setup_openclaw._config_actions(make_options(tmp_path), "agents.list[0]")
    tools_action = next(
        action for action in actions if action.argv[3] == "agents.list[0].tools"
    )
    policy = json.loads(tools_action.argv[-2])

    assert policy["allow"] == ["openhouse_crm", "exec"]
    assert {
        "web_fetch",
        "web_search",
        "browser",
        "read",
        "write",
        "edit",
        "apply_patch",
    } <= set(policy["deny"])
    assert policy["exec"] == {
        "mode": "allowlist",
        "host": "gateway",
    }
    assert "security" not in policy["exec"]
    assert "ask" not in policy["exec"]


@pytest.mark.parametrize(
    "authoritative_tools",
    [
        {
            "allow": ["openhouse_crm", "exec", "web_fetch"],
            "deny": ["write", "edit", "browser"],
            "exec": {"mode": "allowlist", "host": "gateway"},
        },
        {},
    ],
)
def test_setup_rejects_non_authoritative_or_broad_effective_tools(
    tmp_path, authoritative_tools
):
    command = (
        "openclaw",
        "config",
        "get",
        "agents.list[0].tools",
        "--json",
    )
    cli = FakeCLI(
        {command: CommandResult(0, json.dumps(authoritative_tools), "")}
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "authoritative" in result.render().lower()
    assert "Validated the native CRM tool" not in result.render()
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


_EXPECTED_TOOL_DENY = [
    "web_fetch",
    "web_search",
    "browser",
    "read",
    "write",
    "edit",
    "apply_patch",
    "canvas",
    "nodes",
    "cron",
]


@pytest.mark.parametrize(
    "deny",
    [
        [*_EXPECTED_TOOL_DENY, "exec"],
        [*_EXPECTED_TOOL_DENY, "unexpected_tool"],
        _EXPECTED_TOOL_DENY[:-1],
    ],
    ids=["exec-conflict", "unexpected-extra", "missing-required"],
)
def test_setup_rejects_any_authoritative_deny_set_mismatch(tmp_path, deny):
    command = (
        "openclaw",
        "config",
        "get",
        "agents.list[0].tools",
        "--json",
    )
    tools = {
        "allow": ["openhouse_crm", "exec"],
        "deny": deny,
        "exec": {"mode": "allowlist", "host": "gateway"},
    }
    cli = FakeCLI({command: CommandResult(0, json.dumps(tools), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "authoritative" in result.render().lower()
    assert "Validated the native CRM tool" not in result.render()
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


def test_setup_accepts_exact_authoritative_tool_sets_in_any_order(tmp_path):
    command = (
        "openclaw",
        "config",
        "get",
        "agents.list[0].tools",
        "--json",
    )
    tools = {
        "allow": ["exec", "openhouse_crm"],
        "deny": list(reversed(_EXPECTED_TOOL_DENY)),
        "exec": {"mode": "allowlist", "host": "gateway"},
    }
    cli = FakeCLI({command: CommandResult(0, json.dumps(tools), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert "Validated the native CRM tool" in result.render()


@pytest.mark.parametrize(
    "sandbox_payload",
    [OPENCLAW_SANDBOX_EXPLAIN_STABLE, OPENCLAW_SANDBOX_EXPLAIN_BETA],
    ids=["stable-2026.7.1-2", "beta-2026.8.1-beta.2"],
)
def test_setup_accepts_real_sandbox_explain_contract(tmp_path, sandbox_payload):
    command = (
        "openclaw",
        "sandbox",
        "explain",
        "--agent",
        "openhouse-crm",
        "--json",
    )
    cli = FakeCLI({command: CommandResult(0, json.dumps(sandbox_payload), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()


@pytest.mark.parametrize(
    ("sandbox_payload", "message"),
    [
        (
            {**OPENCLAW_SANDBOX_EXPLAIN_STABLE, "agentId": "main"},
            "wrong dedicated agent",
        ),
        (
            {**OPENCLAW_SANDBOX_EXPLAIN_STABLE, "sandbox": []},
            "sandbox explain sandbox",
        ),
        (
            {
                **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
                "sandbox": {
                    **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
                    "mode": "all",
                },
            },
            "sandbox mode",
        ),
        (
            {
                **OPENCLAW_SANDBOX_EXPLAIN_STABLE,
                "sandbox": {
                    **OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"],
                    "sessionIsSandboxed": True,
                },
            },
            "unexpectedly sandboxed",
        ),
    ],
    ids=["wrong-agent", "malformed-sandbox", "sandbox-on", "effective-sandbox"],
)
def test_setup_rejects_unsafe_or_wrong_agent_sandbox_explain(
    tmp_path, sandbox_payload, message
):
    command = (
        "openclaw",
        "sandbox",
        "explain",
        "--agent",
        "openhouse-crm",
        "--json",
    )
    cli = FakeCLI({command: CommandResult(0, json.dumps(sandbox_payload), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert message in result.render().lower()
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


def test_setup_accepts_older_sandbox_explain_without_effective_flag(tmp_path):
    sandbox = dict(OPENCLAW_SANDBOX_EXPLAIN_STABLE["sandbox"])
    sandbox.pop("sessionIsSandboxed")
    payload = {**OPENCLAW_SANDBOX_EXPLAIN_STABLE, "sandbox": sandbox}
    command = (
        "openclaw",
        "sandbox",
        "explain",
        "--agent",
        "openhouse-crm",
        "--json",
    )
    cli = FakeCLI({command: CommandResult(0, json.dumps(payload), "")})

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()


def test_setup_reads_back_normalized_exec_mode_and_is_idempotent(tmp_path):
    cli = FakeCLI()
    options = make_options(tmp_path)

    first = configure_openclaw(options, cli=cli)
    second = configure_openclaw(options, cli=cli)

    assert first.ok, first.render()
    assert second.ok, second.render()
    readbacks = [
        call
        for call in cli.calls
        if call[1:3] == ["config", "get"]
        and call[3].startswith("agents.list[")
        and call[3].endswith("].tools")
    ]
    assert len(readbacks) == 2
    assert "Validated the native CRM tool" in second.render()


@pytest.mark.parametrize(
    "ask_state",
    [
        pytest.param(None, id="missing"),
        pytest.param({"effective": "on-miss"}, id="on-miss"),
        pytest.param({"effective": "always"}, id="always"),
        pytest.param("off", id="malformed-scalar"),
        pytest.param({}, id="malformed-missing-effective"),
        pytest.param(
            {"requested": "off", "effective": "always"},
            id="contradictory-requested-effective",
        ),
        pytest.param(
            {"requested": "always", "effective": "off"},
            id="contradictory-effective-requested",
        ),
    ],
)
def test_setup_never_reports_ready_without_unambiguous_effective_ask_off(
    tmp_path, ask_state
):
    options = make_options(tmp_path)
    agent = {"id": "openhouse-crm", "workspace": str(options.workspace)}
    payload = gateway_approval_payload()
    scope = payload["effectivePolicy"]["scopes"][0]
    if ask_state is None:
        scope.pop("ask")
    else:
        scope["ask"] = ask_state
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps({"defaults": {}, "list": [agent]}), ""
            ),
            ("openclaw", "approvals", "get", "--gateway", "--json"): CommandResult(
                0, json.dumps(payload), ""
            ),
        }
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "ask" in result.render()
    assert "Validated the native CRM tool" not in result.render()


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

    assert allowed == [daily_runner]
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


def test_crm_skill_declares_the_api_token_as_its_primary_environment_variable():
    skill = (REPO_ROOT / "skills" / "crm-db-operations" / "SKILL.md").read_text()
    frontmatter = skill.split("---", 2)[1]
    metadata_lines = [
        line for line in frontmatter.splitlines() if line.startswith("metadata:")
    ]

    assert len(metadata_lines) == 1

    metadata = json.loads(metadata_lines[0].removeprefix("metadata:").strip())

    assert metadata == {"openclaw": {"primaryEnv": "OHI_API_TOKEN"}}


def test_setup_never_places_api_token_in_openclaw_argv_or_output(
    fake_cli, tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "secret-value")

    result = configure_openclaw(make_options(tmp_path), cli=fake_cli)

    assert result.ok, result.render()
    assert all(
        "secret-value" not in argument
        for call in fake_cli.calls
        for argument in call
    )
    assert all(
        json.dumps("secret-value") not in argument
        for call in fake_cli.calls
        for argument in call
    )
    assert "secret-value" not in result.render()
    assert json.dumps("secret-value") not in result.render()


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
        config_path = tmp_path / "state" / "openclaw.json"
        config_path.parent.mkdir()
        cli = FakeCLI(config_path=config_path)
        result = configure_openclaw(options, cli=cli)

        assert options.crm_api_url == "http://localhost:9123/api"
        token_calls = [
            call for call in cli.mutating_calls
            if TOKEN_CONFIG_PATH in call
        ]
        assert len(token_calls) == 1
        token_call = token_calls[0]
        assert "secret-from-dotenv" not in token_call
        assert json.dumps("secret-from-dotenv") not in token_call
        assert token_call[-6:] == [
            "--ref-provider",
            "default",
            "--ref-source",
            "env",
            "--ref-id",
            "OHI_API_TOKEN",
        ]
        assert "--strict-json" not in token_call
        assert "--json" not in token_call
        dry_run_call = [*token_call, "--dry-run"]
        assert dry_run_call in cli.calls
        assert cli.calls.index(dry_run_call) < cli.calls.index(cli.mutating_calls[0])
        assert cli.config_values[TOKEN_CONFIG_PATH] == {
            "source": "env",
            "provider": "default",
            "id": "OHI_API_TOKEN",
        }
        assert "secret-from-dotenv" not in result.render()
        assert json.dumps("secret-from-dotenv") not in result.render()
    finally:
        for key in ("CRM_API_URL", "PORT", "OHI_API_TOKEN", "AGENT_MODE"):
            os.environ.pop(key, None)


def test_setup_defaults_to_the_runtime_agent_id_from_repo_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("AGENT_ID=custom-crm\n")
    monkeypatch.delenv("AGENT_ID", raising=False)

    options = parse_args([], repo=tmp_path)

    assert options.agent_id == "custom-crm"


def test_setup_rejects_agent_id_that_conflicts_with_runtime_env(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".env").write_text("AGENT_ID=runtime-crm\n")
    monkeypatch.delenv("AGENT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--agent-id", "setup-only-crm"], repo=tmp_path)

    assert exc_info.value.code == 2
    assert "set AGENT_ID=setup-only-crm in .env" in capsys.readouterr().err


def test_setup_rejects_unpersisted_agent_id_override(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--agent-id", "custom-crm"], repo=tmp_path)

    assert exc_info.value.code == 2
    assert "set AGENT_ID=custom-crm in .env" in capsys.readouterr().err


def test_setup_rejects_blank_runtime_agent_id(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("AGENT_ID=\n")
    monkeypatch.delenv("AGENT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        parse_args([], repo=tmp_path)

    assert exc_info.value.code == 2
    assert "AGENT_ID must not be blank" in capsys.readouterr().err


def test_setup_rejects_noncanonical_matching_explicit_agent_id(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".env").write_text("AGENT_ID=custom-crm\n")
    monkeypatch.delenv("AGENT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--agent-id", " custom-crm "], repo=tmp_path)

    assert exc_info.value.code == 2
    assert "canonical lowercase" in capsys.readouterr().err


@pytest.mark.parametrize(
    "agent_id",
    [
        "",
        "OpenHouse-CRM",
        " openhouse-crm ",
        "bad.id",
        "a" * 65,
        "main",
        "openclaw",
        "crestodian",
    ],
)
def test_invalid_or_reserved_requested_agent_id_fails_before_any_io(
    tmp_path, agent_id
):
    base = make_options(tmp_path)
    options = SetupOptions(
        agent_id=agent_id,
        workspace=base.workspace,
        crm_api_url=base.crm_api_url,
        bind_discord=base.bind_discord,
        dry_run=base.dry_run,
    )
    cli = FakeCLI()

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert cli.calls == []
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


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
    monkeypatch.delenv("AGENT_ID", raising=False)
    try:
        options = parse_args(
            ["--workspace", str(tmp_path / "workspace")], repo=tmp_path
        )
        dry_run = configure_openclaw(
            SetupOptions(**{**options.__dict__, "dry_run": True}),
            cli=FakeCLI(config_path=tmp_path / "dry-run" / "openclaw.json"),
        )
        (tmp_path / "failed").mkdir()
        failed = configure_openclaw(
            options,
            cli=FakeCLI(
                {("openclaw", "gateway", "restart"): CommandResult(1, "", "dotenv-secret")},
                config_path=tmp_path / "failed" / "openclaw.json",
            ),
        )

        assert "dotenv-secret" not in dry_run.render()
        assert "dotenv-secret" not in failed.render()
        assert "<redacted>" in failed.render()
    finally:
        os.environ.pop("OHI_API_TOKEN", None)


@pytest.mark.parametrize(
    "token",
    ['quote"value', r"slash\\value", "tab\tvalue", "space value", "hash#value"],
)
def test_setup_rejects_non_generated_token_characters_without_leaking(
    fake_cli, tmp_path, monkeypatch, token
):
    monkeypatch.setenv("OHI_API_TOKEN", token)

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=fake_cli)
    rendered = result.render()

    assert not result.ok
    assert fake_cli.mutating_calls == []
    assert token not in rendered
    assert json.dumps(token) not in rendered
    assert json.dumps(token)[1:-1] not in rendered
    for call in fake_cli.calls:
        for argument in call:
            assert token not in argument
            assert json.dumps(token) not in argument
            assert json.dumps(token)[1:-1] not in argument


def test_token_setup_requires_secretref_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")
    cli = FakeCLI(
        {
            ("openclaw", "config", "set", "--help"): CommandResult(
                0,
                "Options:\n"
                "  --strict-json VALUE\n"
                "  --ref-provider NAME\n"
                "  --ref-source SOURCE\n"
                "  --ref-id ID",
                "",
            )
        },
        config_path=tmp_path / "openclaw.json",
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert "environment SecretRef" in result.render()
    assert "must-not-leak" not in result.render()
    assert json.dumps("must-not-leak") not in result.render()


@pytest.mark.parametrize(
    ("commands", "missing"),
    [
        (["get", "set", "unset", "validate"], "file"),
        (["get", "set", "file", "validate"], "unset"),
    ],
)
def test_token_setup_requires_config_file_and_unset_capabilities(
    tmp_path, monkeypatch, commands, missing
):
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")
    help_text = "Commands:\n" + "".join(f"  {command}\n" for command in commands)
    cli = FakeCLI(
        {
            ("openclaw", "config", "--help"): CommandResult(
                0, help_text, ""
            )
        },
        config_path=tmp_path / "openclaw.json",
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert missing in result.render()
    assert "must-not-leak" not in result.render()


def test_token_setup_requires_working_config_unset_help(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")
    cli = FakeCLI(
        {
            ("openclaw", "config", "unset", "--help"): CommandResult(
                1, "", "unsupported"
            )
        },
        config_path=tmp_path / "openclaw.json",
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert "config unset" in result.render()
    assert "must-not-leak" not in result.render()


def test_secretref_builder_validation_fails_before_any_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leak")
    config_path = tmp_path / "state" / "openclaw.json"
    validation = (
        "openclaw",
        "config",
        "set",
        TOKEN_CONFIG_PATH,
        "--ref-provider",
        "default",
        "--ref-source",
        "env",
        "--ref-id",
        "OHI_API_TOKEN",
        "--dry-run",
    )
    cli = FakeCLI(
        {validation: CommandResult(1, "", "unsupported SecretRef")},
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert not make_options(tmp_path).workspace.exists()
    assert not config_path.parent.exists()
    assert not Path(os.environ["OPENCLAW_STATE_DIR"]).exists()
    assert "must-not-leak" not in result.render()


def test_token_setup_writes_gateway_env_in_custom_state_dir_with_mode_0600(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    state_dir = tmp_path / "custom-state" / "nested"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    config_path = tmp_path / "config" / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config_path))
    config_path.parent.mkdir(parents=True)
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    env_path = state_dir / ".env"
    assert result.ok, result.render()
    assert env_path.read_text() == "OHI_API_TOKEN=gateway-secret\n"
    assert env_path.stat().st_mode & 0o777 == 0o600
    assert "gateway-secret" not in result.render()
    assert not (config_path.parent / ".env").exists()


def test_token_setup_defaults_gateway_env_under_openclaw_home(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    openclaw_home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    config_path = tmp_path / "independent-config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    env_path = openclaw_home / ".openclaw" / ".env"
    assert result.ok, result.render()
    assert env_path.read_text() == "OHI_API_TOKEN=gateway-secret\n"
    assert not (config_path.parent / ".env").exists()


@pytest.mark.parametrize("profile", ["default", "Default"])
def test_default_profile_uses_default_state_dir(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    openclaw_home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    monkeypatch.setenv("OPENCLAW_PROFILE", profile)
    config_path = tmp_path / "independent-config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert (openclaw_home / ".openclaw" / ".env").read_text() == (
        "OHI_API_TOKEN=gateway-secret\n"
    )


def test_token_setup_uses_named_profile_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    openclaw_home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    monkeypatch.setenv("OPENCLAW_PROFILE", "team_beta")
    config_path = tmp_path / "independent-config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    env_path = openclaw_home / ".openclaw-team_beta" / ".env"
    assert result.ok, result.render()
    assert env_path.read_text() == "OHI_API_TOKEN=gateway-secret\n"
    assert not (openclaw_home / ".openclaw" / ".env").exists()


def test_explicit_state_dir_overrides_named_profile(tmp_path, monkeypatch):
    state_dir = tmp_path / "explicit-state"
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    openclaw_home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    monkeypatch.setenv("OPENCLAW_PROFILE", "team_beta")
    config_path = tmp_path / "independent-config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert (state_dir / ".env").read_text() == "OHI_API_TOKEN=gateway-secret\n"
    assert not (openclaw_home / ".openclaw-team_beta" / ".env").exists()


@pytest.mark.parametrize("profile", ["../escape", "has space", "slash/name"])
def test_unsafe_profile_fails_before_any_mutation(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    openclaw_home = tmp_path / "openclaw-home"
    monkeypatch.setenv("OPENCLAW_HOME", str(openclaw_home))
    monkeypatch.setenv("OPENCLAW_PROFILE", profile)
    cli = FakeCLI(config_path=tmp_path / "config" / "openclaw.json")

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert not openclaw_home.exists()
    assert "OPENCLAW_PROFILE" in result.render()


def test_gateway_env_is_provisioned_before_first_openclaw_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "gateway-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()

    class ProvisioningOrderCLI(FakeCLI):
        def run(self, args, *, mutate=False):
            if mutate:
                env_path = state_dir / ".env"
                assert env_path.read_text() == "OHI_API_TOKEN=gateway-secret\n"
                assert env_path.stat().st_mode & 0o777 == 0o600
            return super().run(args, mutate=mutate)

    cli = ProvisioningOrderCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()


def test_late_read_only_validation_failure_does_not_create_gateway_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()
    approvals = (
        "openclaw",
        "approvals",
        "get",
        "--gateway",
        "--json",
    )
    cli = FakeCLI(
        {
            approvals: CommandResult(
                0,
                json.dumps(gateway_approval_payload(entries=["/unexpected/tool"])),
                "",
            )
        },
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "approval policy" in result.render()
    assert not (state_dir / ".env").exists()
    assert cli.mutating_calls == []


def test_late_read_only_validation_failure_does_not_change_gateway_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    state_dir.mkdir()
    env_path = state_dir / ".env"
    original = "OTHER=kept\nOHI_API_TOKEN=old-secret\n"
    env_path.write_text(original)
    env_path.chmod(0o640)
    original_inode = env_path.stat().st_ino
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()
    approvals = (
        "openclaw",
        "approvals",
        "get",
        "--gateway",
        "--json",
    )
    cli = FakeCLI(
        {
            approvals: CommandResult(
                0,
                json.dumps(gateway_approval_payload(entries=["/unexpected/tool"])),
                "",
            )
        },
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert env_path.read_text() == original
    assert env_path.stat().st_ino == original_inode
    assert env_path.stat().st_mode & 0o777 == 0o640
    assert cli.mutating_calls == []


def test_token_env_upsert_preserves_other_lines_and_second_run_is_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    state_dir.mkdir()
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    env_path = state_dir / ".env"
    env_path.write_text(
        'OTHER=value\nOHI_API_TOKEN="old-secret"\nTRAILING=kept\n'
    )
    env_path.chmod(0o644)
    cli = FakeCLI(config_path=config_path)
    options = make_options(tmp_path)

    first = configure_openclaw(options, cli=cli)
    first_inode = env_path.stat().st_ino
    second = configure_openclaw(options, cli=cli)

    assert first.ok, first.render()
    assert second.ok, second.render()
    assert env_path.read_text() == (
        "OTHER=value\nOHI_API_TOKEN=new-secret\nTRAILING=kept\n"
    )
    assert env_path.stat().st_ino == first_inode
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_token_env_upsert_normalizes_supported_dotenv_assignment_spacing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    state_dir.mkdir()
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    env_path = state_dir / ".env"
    env_path.write_text(
        "OTHER=value\n"
        "  OHI_API_TOKEN = first-old\n"
        "export OHI_API_TOKEN=second-old\n"
        "\texport\tOHI_API_TOKEN \t= third-old\n"
        "# OHI_API_TOKEN=commented\n"
        "  # export OHI_API_TOKEN = also-commented\n"
        "OHI_API_TOKEN invalid-without-equals\n"
        "TRAILING=kept\n"
    )
    env_path.chmod(0o644)
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert env_path.read_text() == (
        "OTHER=value\n"
        "OHI_API_TOKEN=new-secret\n"
        "# OHI_API_TOKEN=commented\n"
        "  # export OHI_API_TOKEN = also-commented\n"
        "OHI_API_TOKEN invalid-without-equals\n"
        "TRAILING=kept\n"
    )
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_unchanged_gateway_env_uses_descriptor_based_read_and_chmod(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "safe-token")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)
    options = make_options(tmp_path)
    first = configure_openclaw(options, cli=cli)
    assert first.ok, first.render()
    env_path = state_dir / ".env"
    path_operations = []
    original_read_text = Path.read_text
    original_chmod = Path.chmod

    def track_read_text(path, *args, **kwargs):
        if path == env_path:
            path_operations.append("read_text")
        return original_read_text(path, *args, **kwargs)

    def track_chmod(path, *args, **kwargs):
        if path == env_path:
            path_operations.append("chmod")
        return original_chmod(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", track_read_text)
    monkeypatch.setattr(Path, "chmod", track_chmod)

    second = configure_openclaw(options, cli=cli)

    assert second.ok, second.render()
    assert path_operations == []


@pytest.mark.parametrize("token", ["abcXYZ0123", "abc._~+/=-", "a-b_c"])
def test_gateway_env_accepts_generated_token_alphabet(
    tmp_path, monkeypatch, token
):
    monkeypatch.setenv("OHI_API_TOKEN", token)
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert (state_dir / ".env").read_text() == f"OHI_API_TOKEN={token}\n"
    assert token not in result.render()


def test_token_setup_dry_run_does_not_write_gateway_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OHI_API_TOKEN", "dry-run-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path, dry_run=True), cli=cli)

    assert result.ok, result.render()
    assert cli.mutating_calls == []
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    assert not state_dir.exists()
    assert str(state_dir / ".env") in result.render()
    assert "0600" in result.render()


def test_token_setup_refuses_gateway_env_symlink_before_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "safe-secret")
    state_dir = Path(os.environ["OPENCLAW_STATE_DIR"])
    state_dir.mkdir()
    config_path = tmp_path / "config" / "openclaw.json"
    config_path.parent.mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("DO_NOT_TOUCH=yes\n")
    (state_dir / ".env").symlink_to(outside)
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert outside.read_text() == "DO_NOT_TOUCH=yes\n"
    assert "symlink" in result.render().lower()


@pytest.mark.parametrize("token", ["line\nbreak", "carriage\rreturn"])
def test_token_setup_rejects_unsafe_gateway_env_values_before_mutation(
    tmp_path, monkeypatch, token
):
    monkeypatch.setenv("OHI_API_TOKEN", token)
    config_path = tmp_path / "state" / "openclaw.json"
    cli = FakeCLI(config_path=config_path)

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert cli.mutating_calls == []
    assert not Path(os.environ["OPENCLAW_STATE_DIR"]).exists()
    assert token not in result.render()


def test_token_validator_rejects_nul_bytes():
    validate_token = getattr(setup_openclaw, "_validate_api_token", None)

    assert validate_token is not None
    with pytest.raises(SetupConflict, match="dotenv-safe generated-token"):
        validate_token("nul\x00byte")


def test_setup_migrates_legacy_plaintext_token_after_secretref_readback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    cli = FakeCLI(config_path=config_path, legacy_token="old-secret")
    options = make_options(tmp_path)

    first = configure_openclaw(options, cli=cli)
    second = configure_openclaw(options, cli=cli)

    assert first.ok, first.render()
    assert second.ok, second.render()
    unset_calls = [
        call
        for call in cli.mutating_calls
        if call[1:3] == ["config", "unset"]
    ]
    assert unset_calls == [["openclaw", "config", "unset", LEGACY_TOKEN_CONFIG_PATH]]
    assert cli.legacy_token is None
    assert cli.config_values[TOKEN_CONFIG_PATH] == {
        "source": "env",
        "provider": "default",
        "id": "OHI_API_TOKEN",
    }
    for secret in ("old-secret", "new-secret"):
        assert secret not in first.render()
        assert secret not in second.render()
        assert all(secret not in argument for call in cli.calls for argument in call)


def test_clean_setup_inspects_existing_skill_entry_not_missing_env_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    missing_env = (
        "openclaw",
        "config",
        "get",
        LEGACY_TOKEN_ENV_PATH,
        "--json",
    )
    cli = FakeCLI(
        {missing_env: CommandResult(1, '{"error":"Config path not found"}', "")},
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()
    assert list(missing_env) not in cli.calls
    assert [
        "openclaw",
        "config",
        "get",
        TOKEN_ENTRY_CONFIG_PATH,
        "--json",
    ] in cli.calls


def test_legacy_token_inspection_failure_never_renders_the_old_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "new-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    inspection = (
        "openclaw",
        "config",
        "get",
        TOKEN_ENTRY_CONFIG_PATH,
        "--json",
    )
    cli = FakeCLI(
        {inspection: CommandResult(1, "old-plaintext-secret", "")},
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert "old-plaintext-secret" not in result.render()
    assert "new-secret" not in result.render()


def test_setup_rejects_inexact_secretref_readback_before_gateway_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "safe-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    readback = (
        "openclaw",
        "config",
        "get",
        TOKEN_CONFIG_PATH,
        "--json",
    )
    cli = FakeCLI(
        {
            readback: CommandResult(
                0,
                '{"source":"env","provider":"other","id":"OHI_API_TOKEN"}',
                "",
            )
        },
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert not result.ok
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls
    assert "SecretRef" in result.render()


def test_setup_accepts_officially_redacted_secretref_id_readback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OHI_API_TOKEN", "safe-secret")
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir()
    readback = (
        "openclaw",
        "config",
        "get",
        TOKEN_CONFIG_PATH,
        "--json",
    )
    cli = FakeCLI(
        {
            readback: CommandResult(
                0,
                '{"source":"env","provider":"default",'
                '"id":"__OPENCLAW_REDACTED__"}',
                "",
            )
        },
        config_path=config_path,
    )

    result = configure_openclaw(make_options(tmp_path), cli=cli)

    assert result.ok, result.render()


def test_setup_success_requires_runtime_doctor_verification(fake_cli, tmp_path):
    result = configure_openclaw(make_options(tmp_path), cli=fake_cli)

    assert result.ok, result.render()
    assert "configuration" in result.render().lower()
    assert "scripts/doctor.py --live-agent --live-crm" in result.render()


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


def test_capability_failure_reports_openclaw_version_before_mutation(tmp_path):
    cli = FakeCLI(
        {
            ("openclaw", "--version"): CommandResult(
                0, "OpenClaw 2026.8.1 (build abc123)\n", ""
            ),
            ("openclaw", "agents", "--help"): CommandResult(
                0, "Commands:\n  list\n", ""
            ),
        }
    )
    options = make_options(tmp_path)

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "OpenClaw version: OpenClaw 2026.8.1 (build abc123)" in result.render()
    assert "agents help is missing add" in result.render()
    assert cli.mutating_calls == []
    assert not options.workspace.exists()


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


def test_command_parser_chooses_minimum_candidate_indentation_for_section():
    output = """Commands:
    nested-valid-token
      deeper-token
  get
  set
Options:
  validate
"""

    assert setup_openclaw._command_entries(output) == {"get", "set"}


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
            ("openclaw", "config", "--help"),
            "Commands:\n  get\n  set-more\n  validate",
            "set",
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
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps({"defaults": {}, "list": []}), ""
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


def test_partial_agent_policy_is_repaired_before_effective_validation(tmp_path):
    options = make_options(tmp_path)
    original = {
        "id": "openhouse-crm",
        "workspace": str(options.workspace),
        "skills": ["crm-db-operations", "daily-brief"],
        "tools": {
            "allow": ["exec"],
            "deny": list(_EXPECTED_TOOL_DENY),
            "exec": {"host": "auto", "mode": "full"},
        },
        "sandbox": {"mode": "off"},
    }
    cli = PartialPolicyCLI(original)

    result = configure_openclaw(options, cli=cli)

    assert result.ok, result.render()
    assert cli.created_agent["tools"]["exec"] == {
        "host": "gateway",
        "mode": "allowlist",
    }
    assert not any(
        call[1:3] == ["config", "set"] and call[3].startswith("tools.exec")
        for call in cli.mutating_calls
    )


def test_failed_existing_agent_repair_restores_managed_fields(tmp_path):
    options = make_options(tmp_path)
    original = {
        "id": "openhouse-crm",
        "workspace": str(options.workspace),
        "skills": ["legacy-skill"],
        "tools": {"allow": ["web_fetch"]},
        "sandbox": {"mode": "on"},
    }
    validate = ("openclaw", "config", "validate", "--json")
    cli = PartialPolicyCLI(
        dict(original),
        {validate: CommandResult(1, "", "simulated validation failure")},
    )

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert "simulated validation failure" in result.render()
    assert cli.created_agent == original
    assert "Restored the previous dedicated-agent configuration" in result.render()


def test_rollback_stops_if_agent_workspace_changes(tmp_path):
    options = make_options(tmp_path)
    original = {
        "id": "openhouse-crm",
        "workspace": str(options.workspace),
        "tools": {"allow": ["web_fetch"]},
    }
    cli = RollbackWorkspaceChangeCLI(dict(original))

    result = configure_openclaw(options, cli=cli)

    validate_index = cli.calls.index(
        ["openclaw", "config", "validate", "--json"]
    )
    rollback_mutations = [
        call
        for call in cli.calls[validate_index + 1 :]
        if call[1:3] in (["config", "set"], ["config", "unset"])
    ]
    assert not result.ok
    assert rollback_mutations == []
    assert "Could not fully restore" in result.render()


def test_mismatched_effective_scope_identity_is_rejected(tmp_path):
    options = make_options(tmp_path)
    agent = {"id": "openhouse-crm", "workspace": str(options.workspace)}
    payload = gateway_approval_payload()
    scope = payload["effectivePolicy"]["scopes"][0]
    scope["agentId"] = "another-agent"
    cli = FakeCLI(
        {
            ("openclaw", "agents", "list", "--json"): CommandResult(
                0, json.dumps({"agents": [agent]}), ""
            ),
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps({"defaults": {}, "list": [agent]}), ""
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
            ("openclaw", "config", "get", "agents", "--json"): CommandResult(
                0, json.dumps({"defaults": {}, "list": agents}), ""
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
    assert list(failing) in cli.mutating_calls
    assert not any(
        call[1:3] == ["config", "set"] and call[3].startswith("agents.")
        for call in cli.mutating_calls
    )
    assert [
        "openclaw",
        "plugins",
        "uninstall",
        "openhouse-crm",
        "--keep-files",
        "--force",
    ] in cli.mutating_calls


@pytest.mark.parametrize(
    "add_stdout",
    [
        '{"agentId":"different-agent"}',
        '{}',
    ],
    ids=["mismatch", "missing-agent-id"],
)
def test_agent_creation_requires_matching_json_agent_id(
    tmp_path, add_stdout
):
    options = make_options(tmp_path)
    add_command = (
        "openclaw",
        "agents",
        "add",
        options.agent_id,
        "--workspace",
        str(options.workspace),
        "--non-interactive",
        "--json",
    )
    cli = FakeCLI({add_command: CommandResult(0, add_stdout, "")})

    result = configure_openclaw(options, cli=cli)

    assert not result.ok
    assert not any(
        call[1:3] == ["config", "set"] and call[3].startswith("agents.")
        for call in cli.mutating_calls
    )
    assert ["openclaw", "gateway", "restart"] not in cli.mutating_calls


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
