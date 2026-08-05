#!/usr/bin/env python3
"""Configure a dedicated, restricted OpenClaw agent for this CRM."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


SKILL_NAMES = (
    "crm-db-operations",
    "business-card-scanner",
    "daily-command-center",
    "daily-brief",
)
DESIRED_TOOLS = {
    "allow": ["exec", "web_fetch"],
    "deny": ["write", "edit", "apply_patch", "browser", "canvas", "nodes", "cron"],
    "exec": {"mode": "allowlist", "host": "gateway"},
}
DESIRED_SANDBOX = {"mode": "off"}


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
        rendered = "\n".join(self.messages)
        token = os.environ.get("OHI_API_TOKEN", "")
        return rendered.replace(token, "<redacted>") if token else rendered


class OpenClawCLI:
    def run(self, args: list[str], *, mutate: bool = False) -> CommandResult:
        del mutate
        command = args if args and args[0] == "openclaw" else ["openclaw", *args]
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            return CommandResult(127, "", str(exc))
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class SetupConflict(RuntimeError):
    pass


def _entrypoints(options: SetupOptions) -> tuple[Path, Path]:
    wrapper = options.workspace / "skills" / "crm-db-operations" / "cli.py"
    daily = options.workspace / "skills" / "daily-brief" / "scripts" / "run_daily_brief.py"
    return wrapper, daily


def build_setup_actions(options: SetupOptions, agents: list[dict]) -> list[Action]:
    actions: list[Action] = []
    existing = next(
        (agent for agent in agents if agent.get("id") == options.agent_id), None
    )
    if existing:
        existing_workspace = existing.get("workspace") or existing.get("workspacePath")
        if (
            existing_workspace
            and Path(existing_workspace).expanduser() != options.workspace.expanduser()
        ):
            raise SetupConflict(
                f"agent {options.agent_id} already uses a different workspace"
            )
    else:
        actions.append(
            Action(
                "Create dedicated CRM agent",
                [
                    "openclaw",
                    "agents",
                    "add",
                    options.agent_id,
                    "--workspace",
                    str(options.workspace),
                    "--non-interactive",
                    "--json",
                ],
            )
        )
    if options.bind_discord:
        actions.append(
            Action(
                "Bind Discord account",
                [
                    "openclaw",
                    "agents",
                    "bind",
                    "--agent",
                    options.agent_id,
                    "--bind",
                    f"discord:{options.bind_discord}",
                    "--json",
                ],
            )
        )
    wrapper, daily = _entrypoints(options)
    actions.extend(
        [
            Action(
                "Allow only the CRM command wrapper",
                [
                    "openclaw",
                    "approvals",
                    "allowlist",
                    "add",
                    "--agent",
                    options.agent_id,
                    "--gateway",
                    str(wrapper),
                ],
            ),
            Action(
                "Allow only the deterministic daily brief runner",
                [
                    "openclaw",
                    "approvals",
                    "allowlist",
                    "add",
                    "--agent",
                    options.agent_id,
                    "--gateway",
                    str(daily),
                ],
            ),
        ]
    )
    return actions


def sync_skills(repo: Path, workspace: Path, *, dry_run: bool) -> list[Path]:
    targets = [workspace / "skills" / name for name in SKILL_NAMES]
    if dry_run:
        return targets
    for name, target in zip(SKILL_NAMES, targets):
        source = repo / "skills" / name
        if not source.is_dir():
            raise SetupConflict(f"shipped skill directory is missing: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
    for path in (
        targets[0] / "cli.py",
        targets[3] / "scripts" / "run_daily_brief.py",
    ):
        path.chmod(path.stat().st_mode | 0o111)
    return targets


def _json(result: CommandResult, label: str) -> Any:
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise SetupConflict(f"{label} returned invalid JSON") from exc


def _agents(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("agents", "list", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise SetupConflict("OpenClaw returned an unsupported agents JSON shape")


def _agent_policy(payload: Any, agent_id: str) -> dict:
    if isinstance(payload, dict):
        agents = payload.get("agents")
        if isinstance(agents, dict) and isinstance(agents.get(agent_id), dict):
            return agents[agent_id]
        for value in payload.values():
            found = _agent_policy(value, agent_id)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _agent_policy(value, agent_id)
            if found:
                return found
    return {}


def _allowlist_patterns(payload: Any, agent_id: str) -> set[str]:
    policy = _agent_policy(payload, agent_id)
    entries = policy.get("allowlist", []) if policy else []
    patterns = set()
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, str):
                if entry:
                    patterns.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("pattern"), str):
                if entry["pattern"]:
                    patterns.add(entry["pattern"])
    return patterns


def _validate_approval_policy(payload: Any, agent_id: str) -> None:
    policy = _agent_policy(payload, agent_id)
    if not policy:
        return
    if policy.get("autoAllowSkills") is True:
        raise SetupConflict(
            "dedicated CRM agent has an incompatible gateway approval policy: "
            "autoAllowSkills must be disabled"
        )
    entries = policy.get("allowlist", [])
    if isinstance(entries, list) and any(
        isinstance(entry, dict) and entry.get("argPattern") for entry in entries
    ):
        raise SetupConflict(
            "dedicated CRM agent has an incompatible gateway approval policy: "
            "remove argv-bound entries before setup"
        )


def _contains_pair(payload: Any, key: str, value: str) -> bool:
    if isinstance(payload, dict):
        if payload.get(key) == value:
            return True
        return any(_contains_pair(item, key, value) for item in payload.values())
    if isinstance(payload, list):
        return any(_contains_pair(item, key, value) for item in payload)
    return False


def _run_required(cli: OpenClawCLI, argv: list[str], label: str) -> CommandResult:
    result = cli.run(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise SetupConflict(f"unsupported OpenClaw installation: {label} failed{suffix}")
    return result


def _preflight(cli: OpenClawCLI) -> None:
    checks = (
        (["openclaw", "--version"], "openclaw --version"),
        (["openclaw", "agents", "add", "--help"], "agents add"),
        (["openclaw", "agents", "bind", "--help"], "agents bind"),
        (["openclaw", "skills", "check", "--help"], "skills check"),
        (["openclaw", "config", "set", "--help"], "config set"),
        (["openclaw", "config", "validate", "--help"], "config validate"),
        (["openclaw", "approvals", "allowlist", "--help"], "approvals allowlist"),
        (["openclaw", "approvals", "get", "--help"], "approvals get"),
        (["openclaw", "exec-policy", "show", "--help"], "exec-policy show"),
        (["openclaw", "sandbox", "explain", "--help"], "sandbox explain"),
        (["openclaw", "gateway", "restart", "--help"], "gateway restart"),
    )
    for argv, label in checks:
        _run_required(cli, argv, label)


def _config_actions(options: SetupOptions, index: int) -> list[Action]:
    token = os.environ.get("OHI_API_TOKEN", "")
    prefix = f"agents.list[{index}]"
    actions = [
        Action(
            "Restrict the CRM agent to shipped skills",
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.skills",
                json.dumps(list(SKILL_NAMES)),
                "--strict-json",
            ],
        ),
        Action(
            "Restrict the CRM agent tools and gateway execution",
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.tools",
                json.dumps(DESIRED_TOOLS),
                "--strict-json",
            ],
        ),
        Action(
            "Disable sandboxing for the restricted dedicated agent",
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.sandbox",
                json.dumps(DESIRED_SANDBOX),
                "--strict-json",
            ],
        ),
        Action(
            "Configure the CRM API URL",
            [
                "openclaw",
                "config",
                "set",
                'skills.entries["crm-db-operations"].env.CRM_API_URL',
                json.dumps(options.crm_api_url),
                "--strict-json",
            ],
        ),
    ]
    if token:
        actions.append(
            Action(
                "Configure the CRM API token: <redacted>",
                [
                    "openclaw",
                    "config",
                    "set",
                    'skills.entries["crm-db-operations"].env.OHI_API_TOKEN',
                    json.dumps(token),
                    "--strict-json",
                ],
            )
        )
    return actions


def _render_action(action: Action) -> str:
    token = os.environ.get("OHI_API_TOKEN", "")
    command = " ".join(action.argv)
    if token:
        command = command.replace(token, "<redacted>")
    return f"{action.description}: {command}"


def _run_action(cli: OpenClawCLI, action: Action) -> CommandResult:
    return cli.run(action.argv, mutate=action.mutates)


def configure_openclaw(options: SetupOptions, cli: OpenClawCLI) -> SetupResult:
    messages: list[str] = []
    try:
        _preflight(cli)
        listed = _run_required(
            cli, ["openclaw", "agents", "list", "--json"], "agents list --json"
        )
        agents = _agents(_json(listed, "agents list"))
        configured = _run_required(
            cli,
            ["openclaw", "config", "get", "agents.list", "--json"],
            "config get agents.list --json",
        )
        configured_agents = _agents(_json(configured, "agents.list"))
        listed_agent = next(
            (agent for agent in agents if agent.get("id") == options.agent_id), None
        )
        configured_agent = next(
            (
                agent
                for agent in configured_agents
                if agent.get("id") == options.agent_id
            ),
            None,
        )
        if bool(listed_agent) != bool(configured_agent):
            raise SetupConflict(
                f"OpenClaw has inconsistent records for agent {options.agent_id}; "
                "repair the agent explicitly before running setup"
            )
        if listed_agent and not (
            listed_agent.get("workspace") or listed_agent.get("workspacePath")
        ):
            raise SetupConflict(
                f"agent {options.agent_id} has no readable workspace; repair it explicitly"
            )
        initial_actions = build_setup_actions(options, agents)
        approvals = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "approvals get --gateway --json",
        )
        approvals_payload = _json(approvals, "gateway approvals")
        _validate_approval_policy(approvals_payload, options.agent_id)
        existing_patterns = _allowlist_patterns(approvals_payload, options.agent_id)
        wrapper, daily = _entrypoints(options)
        expected_patterns = {str(wrapper), str(daily)}
        unexpected = existing_patterns - expected_patterns
        if unexpected:
            raise SetupConflict(
                "dedicated CRM agent has unexpected executable allowlist entries: "
                + ", ".join(sorted(unexpected))
            )

        existing = next(
            (agent for agent in configured_agents if agent.get("id") == options.agent_id),
            None,
        )
        if existing:
            for field, desired in (
                ("skills", list(SKILL_NAMES)),
                ("tools", DESIRED_TOOLS),
                ("sandbox", DESIRED_SANDBOX),
            ):
                current = existing.get(field)
                if current not in (None, desired):
                    raise SetupConflict(
                        f"agent {options.agent_id} has incompatible {field} configuration; "
                        "repair it explicitly"
                    )

        if options.dry_run:
            planned = [
                action
                for action in initial_actions
                if not (
                    action.argv[1:4] == ["approvals", "allowlist", "add"]
                    and action.argv[-1] in existing_patterns
                )
            ]
            index = next(
                (
                    i
                    for i, agent in enumerate(configured_agents)
                    if agent.get("id") == options.agent_id
                ),
                len(configured_agents),
            )
            planned.extend(_config_actions(options, index))
            messages.append("Dry run only. No files or OpenClaw configuration were changed.")
            messages.extend(_render_action(action) for action in planned)
            if not options.bind_discord:
                messages.append(
                    "Optional Discord binding: openclaw agents bind --agent "
                    f"{options.agent_id} --bind discord:ACCOUNT --json"
                )
            return SetupResult(True, messages)

        repo = Path(__file__).resolve().parents[1]
        sync_skills(repo, options.workspace, dry_run=False)
        messages.append(f"Installed CRM skills in {options.workspace / 'skills'}")

        create_actions = [
            action for action in initial_actions if action.argv[1:3] == ["agents", "add"]
        ]
        for action in create_actions:
            result = _run_action(cli, action)
            if result.returncode != 0:
                raise SetupConflict(
                    f"{action.description} failed: {(result.stderr or result.stdout).strip()}"
                )
            messages.append(action.description)

        refreshed = _run_required(
            cli,
            ["openclaw", "config", "get", "agents.list", "--json"],
            "config get agents.list after agent creation",
        )
        refreshed_agents = _agents(_json(refreshed, "agents.list"))
        index = next(
            (
                i
                for i, agent in enumerate(refreshed_agents)
                if agent.get("id") == options.agent_id
            ),
            None,
        )
        if index is None:
            raise SetupConflict(
                f"OpenClaw did not expose the {options.agent_id} agent after creation"
            )

        actions = _config_actions(options, index)
        actions.extend(
            action
            for action in initial_actions
            if action.argv[1:3] != ["agents", "add"]
            and not (
                action.argv[1:4] == ["approvals", "allowlist", "add"]
                and action.argv[-1] in existing_patterns
            )
        )
        for action in actions:
            result = _run_action(cli, action)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise SetupConflict(f"{action.description} failed: {detail}")
            messages.append(action.description)

        validate = _run_required(
            cli,
            ["openclaw", "config", "validate", "--json"],
            "config validate --json",
        )
        _json(validate, "config validate")
        skills = _run_required(
            cli,
            ["openclaw", "skills", "check", "--agent", options.agent_id, "--json"],
            "skills check",
        )
        if "crm-db-operations" not in skills.stdout:
            raise SetupConflict(
                "OpenClaw did not report crm-db-operations for the dedicated agent"
            )
        sandbox = _run_required(
            cli,
            ["openclaw", "sandbox", "explain", "--agent", options.agent_id, "--json"],
            "sandbox explain",
        )
        sandbox_payload = _json(sandbox, "sandbox explain")
        if not _contains_pair(sandbox_payload, "mode", "off"):
            raise SetupConflict("dedicated CRM agent sandbox mode is not off")
        if not _contains_pair(sandbox_payload, "host", "gateway"):
            raise SetupConflict("dedicated CRM agent exec host is not gateway")
        if not (
            _contains_pair(sandbox_payload, "mode", "allowlist")
            or _contains_pair(sandbox_payload, "security", "allowlist")
        ):
            raise SetupConflict("dedicated CRM agent exec mode is not allowlist-only")
        policy = _run_required(
            cli,
            ["openclaw", "exec-policy", "show", "--json"],
            "exec-policy show",
        )
        policy_payload = _json(policy, "exec-policy show")
        if not isinstance(policy_payload, dict):
            raise SetupConflict("exec-policy show returned an unsupported JSON shape")
        final_approvals = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "gateway approvals inspection",
        )
        final_approvals_payload = _json(final_approvals, "gateway approvals")
        _validate_approval_policy(final_approvals_payload, options.agent_id)
        final_patterns = _allowlist_patterns(final_approvals_payload, options.agent_id)
        if final_patterns != expected_patterns:
            raise SetupConflict(
                "dedicated CRM agent executable allowlist is not exactly the two shipped entrypoints"
            )

        restart = cli.run(["openclaw", "gateway", "restart"], mutate=True)
        if restart.returncode != 0:
            raise SetupConflict(
                f"Gateway restart failed: {(restart.stderr or restart.stdout).strip()}"
            )
        messages.append("Validated the restricted agent and restarted the OpenClaw Gateway.")
        if not options.bind_discord:
            messages.append(
                "Optional Discord binding: openclaw agents bind --agent "
                f"{options.agent_id} --bind discord:ACCOUNT --json"
            )
        return SetupResult(True, messages)
    except SetupConflict as exc:
        messages.append(str(exc))
        return SetupResult(False, messages)


def _parse_args(argv: list[str] | None = None) -> SetupOptions:
    parser = argparse.ArgumentParser(
        description="Safely configure a dedicated OpenClaw CRM agent"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent-id", default="openhouse-crm")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("~/.openclaw/workspace-openhouse-crm"),
    )
    parser.add_argument("--crm-api-url", default="http://localhost:8080/api")
    parser.add_argument("--bind-discord", metavar="ACCOUNT")
    args = parser.parse_args(argv)
    return SetupOptions(
        agent_id=args.agent_id,
        workspace=args.workspace.expanduser(),
        crm_api_url=args.crm_api_url,
        bind_discord=args.bind_discord,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    options = _parse_args(argv)
    result = configure_openclaw(options, OpenClawCLI())
    stream = sys.stdout if result.ok else sys.stderr
    print(result.render(), file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
