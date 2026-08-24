#!/usr/bin/env python3
"""Configure a dedicated, restricted OpenClaw agent for this CRM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SKILL_NAMES = (
    "crm-db-operations",
    "business-card-scanner",
    "daily-command-center",
    "daily-brief",
)
DESIRED_TOOL_DENY = (
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
)
DESIRED_TOOLS = {
    "profile": "full",
    "allow": ["openhouse_crm", "exec"],
    "deny": list(DESIRED_TOOL_DENY),
    "exec": {"mode": "allowlist", "host": "gateway"},
}
PLUGIN_ID = "openhouse-crm"
PLUGIN_TOOL = "openhouse_crm"
REQUIRED_PLUGIN_HOOKS = (
    "after_tool_call",
    "before_tool_call",
    "gateway_stop",
    "reply_payload_sending",
)
DASHBOARD_CHANNEL = "openhouse-dashboard"
SETUP_PROBE_TOOL = "openhouse_setup_capability_probe"
SETUP_BLOCK_PROBE_OPERATION = "__openhouse_setup_probe__"
GATEWAY_PROBE_TIMEOUT_SECONDS = 30
GATEWAY_PROBE_MAX_BYTES = 256 * 1024
CONTRACT_RELATIVE_PATH = Path("skills") / "crm-db-operations" / "contract.json"
DESIRED_SANDBOX = {"mode": "off"}
TOKEN_CONFIG_PATH = 'skills.entries["crm-db-operations"].apiKey'
TOKEN_ENTRY_CONFIG_PATH = 'skills.entries["crm-db-operations"]'
LEGACY_TOKEN_ENV_PATH = 'skills.entries["crm-db-operations"].env'
LEGACY_TOKEN_CONFIG_PATH = f"{LEGACY_TOKEN_ENV_PATH}.OHI_API_TOKEN"
TOKEN_SECRETREF = {
    "source": "env",
    "provider": "default",
    "id": "OHI_API_TOKEN",
}
TOKEN_SECRETREF_REDACTED = {
    **TOKEN_SECRETREF,
    "id": "__OPENCLAW_REDACTED__",
}
VALID_AGENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}", re.IGNORECASE)
RESERVED_AGENT_IDS = frozenset({"main", "openclaw", "crestodian"})
LEGACY_AGENT_PREFIX_RE = re.compile(r"agents\.list\[\d+\]")


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
class AgentRoster:
    schema: str | None
    records: list[dict[str, Any]]
    prefixes: dict[str, str]


@dataclass
class AgentRollback:
    snapshot: dict[str, Any]
    changed_fields: list[str]


@dataclass
class SkillRollback:
    workspace: Path
    backup_root: Path
    existing_names: set[str]
    workspace_existed: bool
    skills_root_existed: bool
    missing_parent_dirs: list[Path]


@dataclass(frozen=True)
class SetupResult:
    ok: bool
    messages: list[str]

    def render(self) -> str:
        return _redact_api_token("\n".join(self.messages))


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

    def _post_gateway_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        channel: str,
    ) -> CommandResult:
        gateway_url = os.environ.get("AGENT_GATEWAY_URL", "http://localhost:18789")
        endpoint = gateway_url.rstrip("/") + "/" + path.lstrip("/")
        headers = {
            "Content-Type": "application/json",
            "x-openclaw-message-channel": channel,
        }
        gateway_token = os.environ.get("AGENT_GATEWAY_TOKEN", "")
        if gateway_token:
            headers["Authorization"] = f"Bearer {gateway_token}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=GATEWAY_PROBE_TIMEOUT_SECONDS
            ) as response:
                body = response.read(GATEWAY_PROBE_MAX_BYTES + 1)
                status_code = int(response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read(GATEWAY_PROBE_MAX_BYTES + 1)
            status_code = int(exc.code)
        except (OSError, TimeoutError, urllib.error.URLError):
            return CommandResult(503, "", "Gateway capability request failed")
        if len(body) > GATEWAY_PROBE_MAX_BYTES:
            return CommandResult(502, "", "Gateway capability response was too large")
        try:
            rendered = body.decode("utf-8")
        except UnicodeError:
            return CommandResult(502, "", "Gateway capability response was not UTF-8")
        return CommandResult(status_code, rendered, "")

    def probe_client_tools(self, *, agent_id: str, nonce: str) -> CommandResult:
        chat_path = os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions")
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": f"setup-capability:{nonce}",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Call the one provided client function exactly once with the "
                        "required nonce. Do not use any internal tool."
                    ),
                },
                {"role": "user", "content": f"Capability nonce: {nonce}"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": SETUP_PROBE_TOOL,
                        "description": "Return the setup capability nonce without side effects.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["nonce"],
                            "properties": {"nonce": {"const": nonce}},
                        },
                    },
                }
            ],
            "tool_choice": "required",
            "max_completion_tokens": 128,
        }
        return self._post_gateway_json(
            chat_path, payload, channel=DASHBOARD_CHANNEL
        )

    def probe_dashboard_tool_block(
        self, *, agent_id: str, nonce: str
    ) -> CommandResult:
        gateway_url = os.environ.get("AGENT_GATEWAY_URL", "http://localhost:18789")
        hostname = (urllib.parse.urlsplit(gateway_url).hostname or "").lower()
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return CommandResult(
                503, "", "Dashboard hook diagnostic requires a loopback Gateway"
            )
        payload = {
            "tool": PLUGIN_TOOL,
            "args": {
                "operation": SETUP_BLOCK_PROBE_OPERATION,
                "arguments": {},
            },
            "agentId": agent_id,
            "sessionKey": f"dashboard:setup-capability:{nonce}",
            "idempotencyKey": f"setup-capability:{nonce}",
        }
        return self._post_gateway_json(
            "/tools/invoke", payload, channel=DASHBOARD_CHANNEL
        )


class SetupConflict(RuntimeError):
    pass


class _DuplicateJSONKey(ValueError):
    pass


def _redact_api_token(value: str) -> str:
    tokens = {
        os.environ.get(name, "")
        for name in (
            "OHI_API_TOKEN",
            "AGENT_GATEWAY_TOKEN",
            "OPENCLAW_GATEWAY_TOKEN",
            "OPENCLAW_GATEWAY_PASSWORD",
        )
    }
    forms: set[str] = set()
    for token in tokens:
        if not token:
            continue
        forms.add(token)
        encoded = token
        for _ in range(3):
            encoded = json.dumps(encoded)
            forms.add(encoded)
            forms.add(encoded[1:-1])
    for form in sorted((item for item in forms if item), key=len, reverse=True):
        value = value.replace(form, "<redacted>")
    return value


def _validate_api_token(token: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise SetupConflict(
            "OHI_API_TOKEN must use a nonempty dotenv-safe generated-token "
            "alphabet: letters, digits, dot, underscore, tilde, plus, slash, "
            "equals, and hyphen"
        )


def _token_secretref_argv(*, dry_run: bool) -> list[str]:
    argv = [
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
    ]
    if dry_run:
        argv.append("--dry-run")
    return argv


def _gateway_env_path() -> Path:
    state_override = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if state_override:
        state_dir = Path(state_override).expanduser()
    else:
        home_override = os.environ.get("OPENCLAW_HOME", "").strip()
        openclaw_home = Path(home_override).expanduser() if home_override else Path.home()
        profile = os.environ.get("OPENCLAW_PROFILE", "").strip()
        if profile and not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            raise SetupConflict(
                "OPENCLAW_PROFILE must use only letters, numbers, underscore, or hyphen"
            )
        suffix = "" if not profile or profile.lower() == "default" else f"-{profile}"
        state_dir = openclaw_home / f".openclaw{suffix}"
    if not state_dir.is_absolute():
        state_dir = state_dir.resolve(strict=False)
    return state_dir / ".env"


def _updated_gateway_env(contents: str, token: str) -> str:
    assignment = f"OHI_API_TOKEN={token}"
    output: list[str] = []
    replaced = False
    for line in contents.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if re.fullmatch(
            r"[ \t]*(?:export[ \t]+)?OHI_API_TOKEN[ \t]*=.*", body
        ):
            if not replaced:
                output.append(assignment + (ending or "\n"))
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if contents and not contents.endswith(("\r", "\n")):
            output.append("\n")
        output.append(assignment + "\n")
    return "".join(output)


def _read_gateway_env_token(contents: str) -> str | None:
    values = []
    for line in contents.splitlines():
        if not line.startswith("OHI_API_TOKEN="):
            continue
        value = line.split("=", 1)[1]
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
            raise SetupConflict(
                "OpenClaw gateway environment contains an invalid OHI_API_TOKEN"
            )
        values.append(value)
    if len(values) > 1:
        raise SetupConflict(
            "OpenClaw gateway environment contains duplicate OHI_API_TOKEN entries"
        )
    return values[0] if values else None


def _read_gateway_env_no_follow(env_path: Path) -> str:
    try:
        path_stat = os.lstat(env_path)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise SetupConflict(f"could not inspect OpenClaw gateway environment: {exc}") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise SetupConflict(
            f"OpenClaw gateway environment must not be a symlink: {env_path}"
        )
    if not stat.S_ISREG(path_stat.st_mode):
        raise SetupConflict(
            f"OpenClaw gateway environment must be a regular file: {env_path}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(env_path, flags)
    except OSError as exc:
        if env_path.is_symlink():
            raise SetupConflict(
                f"OpenClaw gateway environment must not be a symlink: {env_path}"
            ) from exc
        raise SetupConflict(f"could not open OpenClaw gateway environment: {exc}") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SetupConflict(
                f"OpenClaw gateway environment must be a regular file: {env_path}"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    except UnicodeError as exc:
        raise SetupConflict(
            "OpenClaw gateway environment is not valid UTF-8"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_gateway_env_no_follow(env_path: Path, token: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(env_path, flags)
    except OSError as exc:
        raise SetupConflict(
            "could not open OpenClaw gateway environment for verification"
        ) from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SetupConflict(
                f"OpenClaw gateway environment must be a regular file: {env_path}"
            )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            persisted = stream.read()
    except UnicodeError as exc:
        raise SetupConflict(
            "OpenClaw gateway environment is not valid UTF-8"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _read_gateway_env_token(persisted) != token:
        raise SetupConflict("OpenClaw gateway environment token verification failed")


def _upsert_gateway_env(env_path: Path, token: str) -> None:
    _validate_api_token(token)
    _create_directory_chain(env_path.parent, "OpenClaw state directory")
    existing = _read_gateway_env_no_follow(env_path)
    updated = _updated_gateway_env(existing, token)
    if updated != existing:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".openhouse-env-", dir=env_path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            if env_path.is_symlink():
                raise SetupConflict(
                    f"OpenClaw gateway environment must not be a symlink: {env_path}"
                )
            os.replace(temporary, env_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()
    _verify_gateway_env_no_follow(env_path, token)


def _load_repo_env(repo: Path) -> None:
    """Load simple .env assignments without overriding exported values.

    This deliberately mirrors scripts/load-env.sh: no shell expansion, only
    valid environment keys, and an existing process value always wins.
    """
    env_file = repo / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _default_crm_api_url() -> str:
    configured = os.environ.get("CRM_API_URL")
    if configured:
        return configured
    port = os.environ.get("PORT") or "8080"
    return f"http://localhost:{port}/api"


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
                "Remove the legacy CRM command wrapper approval",
                [
                    "openclaw",
                    "approvals",
                    "allowlist",
                    "remove",
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


def _validate_skill_tree(path: Path, label: str) -> None:
    if path.is_symlink():
        raise SetupConflict(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise SetupConflict(f"{label} must be a directory: {path}")
    if not path.exists():
        return
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise SetupConflict(f"{label} contains a symlink: {entry}")


def _validate_directory_node(path: Path, label: str) -> None:
    if path.is_symlink():
        raise SetupConflict(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise SetupConflict(f"{label} must be a directory: {path}")


def _remove_installed_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _create_directory_chain(path: Path, label: str) -> list[Path]:
    missing: list[Path] = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise SetupConflict(f"{label} must not contain a symlink: {current}")
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise SetupConflict(f"{label} has no usable existing ancestor: {path}")
        current = parent
    _validate_directory_node(current, f"{label} ancestor")
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir(parents=False)
            created.append(directory)
    except OSError:
        for directory in reversed(created):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise
    return created


def _remove_empty_directories(paths: list[Path]) -> None:
    for path in reversed(paths):
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _contract_digest_path(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise SetupConflict(f"{label} is missing or is not a regular file: {path}")
    try:
        contents = path.read_bytes()
    except OSError as exc:
        raise SetupConflict(f"could not read {label}: {exc}") from exc
    if len(contents) > 1024 * 1024:
        raise SetupConflict(f"{label} is unexpectedly large")
    try:
        raw = contents.decode("utf-8")
    except UnicodeError as exc:
        raise SetupConflict(f"{label} is not valid UTF-8") from exc
    payload = _decode_json(raw, label)
    operations = payload.get("operations") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(operations, dict)
        or not operations
        or not all(
            isinstance(name, str)
            and name
            and isinstance(operation, dict)
            for name, operation in operations.items()
        )
    ):
        raise SetupConflict(f"{label} has an unsupported contract shape")
    return hashlib.sha256(contents).hexdigest()


def _canonical_contract_digest(repo: Path) -> str:
    return _contract_digest_path(
        repo / CONTRACT_RELATIVE_PATH,
        "canonical CRM operation contract",
    )


def _verify_installed_contract(workspace: Path, expected_digest: str) -> None:
    installed = workspace / CONTRACT_RELATIVE_PATH
    actual_digest = _contract_digest_path(
        installed,
        "installed CRM operation contract",
    )
    if actual_digest != expected_digest:
        raise SetupConflict(
            "installed CRM operation contract digest does not match the canonical contract"
        )
    legacy = installed.parent / "operations.json"
    if legacy.exists() or legacy.is_symlink():
        raise SetupConflict(
            "stale installed CRM operations.json was not removed during skill synchronization"
        )


def _missing_directory_chain(path: Path) -> list[Path]:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return list(reversed(missing))


def _snapshot_installed_skills(workspace: Path) -> SkillRollback:
    skills_root = workspace / "skills"
    for name in SKILL_NAMES:
        _validate_skill_tree(skills_root / name, "installed skill directory")
    backup_root = Path(tempfile.mkdtemp(prefix="openhouse-skill-rollback-"))
    existing_names: set[str] = set()
    try:
        for name in SKILL_NAMES:
            target = skills_root / name
            if target.exists():
                shutil.copytree(target, backup_root / name)
                existing_names.add(name)
    except OSError as exc:
        shutil.rmtree(backup_root, ignore_errors=True)
        raise SetupConflict(f"could not snapshot installed CRM skills: {exc}") from exc
    return SkillRollback(
        workspace=workspace,
        backup_root=backup_root,
        existing_names=existing_names,
        workspace_existed=workspace.exists(),
        skills_root_existed=skills_root.exists(),
        missing_parent_dirs=_missing_directory_chain(workspace.parent),
    )


def _restore_installed_skills(snapshot: SkillRollback) -> bool:
    skills_root = snapshot.workspace / "skills"
    try:
        for name in SKILL_NAMES:
            target = skills_root / name
            if target.exists() or target.is_symlink():
                _remove_installed_tree(target)
            if name in snapshot.existing_names:
                shutil.copytree(snapshot.backup_root / name, target)
        if (
            not snapshot.skills_root_existed
            and skills_root.exists()
            and not any(skills_root.iterdir())
        ):
            skills_root.rmdir()
        if (
            not snapshot.workspace_existed
            and snapshot.workspace.exists()
            and not any(snapshot.workspace.iterdir())
        ):
            snapshot.workspace.rmdir()
        _remove_empty_directories(snapshot.missing_parent_dirs)
        return True
    except OSError:
        return False


def _discard_skill_snapshot(snapshot: SkillRollback) -> None:
    shutil.rmtree(snapshot.backup_root, ignore_errors=True)


def sync_skills(repo: Path, workspace: Path, *, dry_run: bool) -> list[Path]:
    sources = [repo / "skills" / name for name in SKILL_NAMES]
    skills_root = workspace / "skills"
    targets = [skills_root / name for name in SKILL_NAMES]
    created_parent_dirs: list[Path] = []
    try:
        for source in sources:
            if not source.exists():
                raise SetupConflict(f"shipped skill directory is missing: {source}")
            _validate_skill_tree(source, "shipped skill directory")
        _validate_directory_node(workspace, "OpenClaw workspace")
        _validate_directory_node(skills_root, "OpenClaw skills directory")
        for target in targets:
            _validate_skill_tree(target, "installed skill directory")
        if dry_run:
            return targets

        parent = workspace.parent
        created_parent_dirs = _create_directory_chain(
            parent, "OpenClaw workspace parent"
        )

        with tempfile.TemporaryDirectory(
            prefix=".openhouse-skills-", dir=parent, ignore_cleanup_errors=True
        ) as staging_value:
            staging_root = Path(staging_value)
            staged_skills = staging_root / "staged"
            backups = staging_root / "backups"
            for name, source in zip(SKILL_NAMES, sources):
                shutil.copytree(source, staged_skills / name)
            for path in (
                staged_skills / "crm-db-operations" / "cli.py",
                staged_skills / "daily-brief" / "scripts" / "run_daily_brief.py",
            ):
                path.chmod(path.stat().st_mode | 0o111)

            workspace_created = not workspace.exists()
            skills_root_created = not skills_root.exists()
            moved_targets: list[Path] = []
            backup_paths: dict[Path, Path] = {}
            try:
                workspace.mkdir(parents=False, exist_ok=True)
                skills_root.mkdir(parents=False, exist_ok=True)
                backups.mkdir()
                for name, target in zip(SKILL_NAMES, targets):
                    if target.exists():
                        backup = backups / name
                        target.rename(backup)
                        backup_paths[target] = backup
                    (staged_skills / name).rename(target)
                    moved_targets.append(target)
            except OSError:
                for target in reversed(moved_targets):
                    _remove_installed_tree(target)
                for target, backup in reversed(list(backup_paths.items())):
                    if backup.exists() and not target.exists():
                        backup.rename(target)
                if skills_root_created and skills_root.exists() and not any(
                    skills_root.iterdir()
                ):
                    skills_root.rmdir()
                if workspace_created and workspace.exists() and not any(
                    workspace.iterdir()
                ):
                    workspace.rmdir()
                raise
        return targets
    except SetupConflict:
        _remove_empty_directories(created_parent_dirs)
        raise
    except OSError as exc:
        _remove_empty_directories(created_parent_dirs)
        raise SetupConflict(f"skill synchronization failed: {exc}") from exc


def _decode_json(raw: str, label: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise _DuplicateJSONKey(key)
            decoded[key] = value
        return decoded

    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateJSONKey) as exc:
        raise SetupConflict(f"{label} returned invalid JSON") from exc


def _json(result: CommandResult, label: str) -> Any:
    return _decode_json(result.stdout or "null", label)


def _canonical_agent_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not VALID_AGENT_ID_RE.fullmatch(value):
        raise SetupConflict(f"OpenClaw returned an invalid agent ID at {label}")
    return value.lower()


def _require_canonical_agent_id(value: Any, label: str) -> str:
    canonical = _canonical_agent_id(value, label)
    if value != canonical:
        raise SetupConflict(
            f"{label} must already use canonical lowercase OpenClaw agent-ID syntax"
        )
    return canonical


def _validate_requested_agent_id(value: Any) -> str:
    try:
        canonical = _require_canonical_agent_id(value, "requested agent ID")
    except SetupConflict as exc:
        raise SetupConflict(
            "AGENT_ID must be nonblank, use 1-64 letters, numbers, underscore, "
            "or hyphen, start with a letter or number, and already be canonical "
            "lowercase"
        ) from exc
    if canonical in RESERVED_AGENT_IDS:
        raise SetupConflict(f'AGENT_ID "{canonical}" is reserved by OpenClaw')
    return canonical


def _agent_id(value: Any, label: str) -> str:
    return _canonical_agent_id(value, label)


def _agent_list_records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SetupConflict(f"OpenClaw returned an unsupported agents JSON shape at {label}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise SetupConflict(
                f"OpenClaw returned an unsupported agent record at {label}[{index}]"
            )
        record = dict(entry)
        agent_id = _agent_id(record.get("id"), f"{label}[{index}].id")
        if agent_id in seen:
            raise SetupConflict(f"OpenClaw returned duplicate agent ID: {agent_id}")
        seen.add(agent_id)
        record["id"] = agent_id
        records.append(record)
    return records


def _agent_entry_records(
    value: Any, label: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(value, dict):
        raise SetupConflict(f"OpenClaw returned an unsupported agents JSON shape at {label}")
    records: list[dict[str, Any]] = []
    prefixes: dict[str, str] = {}
    seen: set[str] = set()
    for raw_agent_id, entry in value.items():
        agent_id = _agent_id(raw_agent_id, f"{label} key")
        rendered_raw_id = json.dumps(raw_agent_id)
        if not isinstance(entry, dict):
            raise SetupConflict(
                f"OpenClaw returned an unsupported agent record at {label}[{rendered_raw_id}]"
            )
        record = dict(entry)
        if "id" in record and _agent_id(
            record["id"], f"{label}[{rendered_raw_id}].id"
        ) != agent_id:
            raise SetupConflict(
                f"OpenClaw returned mismatched agent IDs at {label}[{rendered_raw_id}]"
            )
        if agent_id in seen:
            raise SetupConflict(f"OpenClaw returned duplicate agent ID: {agent_id}")
        seen.add(agent_id)
        record["id"] = agent_id
        records.append(record)
        prefixes[agent_id] = f"{label}[{rendered_raw_id}]"
    return records, prefixes


def _configured_agent_roster(payload: Any) -> AgentRoster:
    if not isinstance(payload, dict):
        raise SetupConflict("OpenClaw returned an unsupported agents config JSON shape")
    if "defaults" in payload and not isinstance(payload["defaults"], dict):
        raise SetupConflict("OpenClaw returned an unsupported agents defaults JSON shape")
    has_list = "list" in payload
    has_entries = "entries" in payload
    if has_list and has_entries:
        raise SetupConflict("OpenClaw returned ambiguous legacy and modern agents config")
    if has_list:
        records = _agent_list_records(payload["list"], "agents.list")
        return AgentRoster(
            "list",
            records,
            {agent["id"]: f"agents.list[{index}]" for index, agent in enumerate(records)},
        )
    if has_entries:
        records, prefixes = _agent_entry_records(payload["entries"], "agents.entries")
        return AgentRoster("entries", records, prefixes)
    if set(payload) == {"defaults"}:
        return AgentRoster(None, [], {})
    raise SetupConflict("OpenClaw returned an unsupported agents config JSON shape")


def _cli_agents(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _agent_list_records(payload, "agents")
    if not isinstance(payload, dict):
        raise SetupConflict("OpenClaw returned an unsupported agents JSON shape")
    shapes = [key for key in ("agents", "list", "entries") if key in payload]
    if len(shapes) != 1:
        raise SetupConflict("OpenClaw returned an ambiguous agents JSON shape")
    shape = shapes[0]
    if shape == "entries":
        records, _ = _agent_entry_records(payload[shape], "entries")
        return records
    return _agent_list_records(payload[shape], shape)


def _agents(payload: Any) -> list[dict[str, Any]]:
    return _cli_agents(payload)


def _is_missing_config_path(result: CommandResult, path: str) -> bool:
    if result.returncode != 1:
        return False
    expected_text = f"Config path not found: {path}"
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout and stderr:
        return False
    if stdout:
        if stdout == expected_text:
            return True
        try:
            return _decode_json(stdout, "config missing-path diagnostic") == {
                "error": expected_text
            }
        except SetupConflict:
            return False
    return stderr == expected_text


def _read_agent_roster(
    cli: OpenClawCLI, *, allow_missing: bool, label: str
) -> AgentRoster:
    argv = ["openclaw", "config", "get", "agents", "--json"]
    result = cli.run(argv)
    if result.returncode != 0:
        if allow_missing and _is_missing_config_path(result, "agents"):
            return AgentRoster(None, [], {})
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise SetupConflict(f"unsupported OpenClaw installation: {label} failed{suffix}")
    return _configured_agent_roster(_json(result, label))


def _revalidate_agent_target(
    cli: OpenClawCLI,
    *,
    agent_id: str,
    prefix: str,
    workspace: Path,
    label: str,
) -> None:
    roster = _read_agent_roster(cli, allow_missing=False, label=label)
    current_prefix = roster.prefixes.get(agent_id)
    if LEGACY_AGENT_PREFIX_RE.fullmatch(prefix) and (
        roster.schema != "list" or current_prefix != prefix
    ):
        raise SetupConflict(
            "legacy agent roster changed during setup; stop concurrent OpenClaw "
            "configuration writes and rerun setup"
        )
    if current_prefix != prefix:
        raise SetupConflict(
            "dedicated agent configuration changed during setup; stop concurrent "
            "OpenClaw configuration writes and rerun setup"
        )
    agent = next(
        (record for record in roster.records if record.get("id") == agent_id), None
    )
    configured_workspace = (
        agent.get("workspace") or agent.get("workspacePath")
        if agent is not None
        else None
    )
    if not _same_workspace(configured_workspace, workspace):
        raise SetupConflict(
            f"agent {agent_id} workspace changed during setup; setup stopped before "
            "writing to an agent it no longer owns"
        )


def _eligible_skills(payload: Any) -> set[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("eligible"), list):
        raise SetupConflict("OpenClaw skills check did not return an eligible skill set")
    eligible = payload["eligible"]
    if not all(isinstance(name, str) and name for name in eligible):
        raise SetupConflict("OpenClaw skills check returned unsupported eligible entries")
    return set(eligible)


_ALLOWLIST_ENTRY_KEYS = {
    "id",
    "pattern",
    "source",
    "lastUsedAt",
    "lastUsedCommand",
    "lastResolvedPath",
}


def _require_mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise SetupConflict(
            f"gateway approval policy has unsupported {label} JSON"
        )
    return value


def _validate_auto_allow(policy: dict, label: str) -> None:
    auto_allow = policy.get("autoAllowSkills")
    if auto_allow not in (None, False):
        raise SetupConflict(
            "dedicated CRM agent has an incompatible gateway approval policy: "
            f"{label}.autoAllowSkills must be disabled"
        )


def _validate_policy_shape(
    policy: dict, label: str, *, allow_allowlist: bool
) -> None:
    allowed = {"security", "ask", "askFallback", "autoAllowSkills"}
    if allow_allowlist:
        allowed.add("allowlist")
    unknown = set(policy) - allowed
    if unknown:
        raise SetupConflict(
            "gateway approval policy has unsupported "
            f"{label} fields: {', '.join(sorted(unknown))}"
        )
    for field in ("security", "ask", "askFallback"):
        if field in policy and not isinstance(policy[field], str):
            raise SetupConflict(
                f"gateway approval policy has unsupported {label}.{field} value"
            )


def _parse_allowlist_entries(value: Any, label: str) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise SetupConflict(
            f"gateway approval policy has unsupported {label} allowlist JSON"
        )
    patterns: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict) or set(entry) - _ALLOWLIST_ENTRY_KEYS:
            raise SetupConflict(
                "gateway approval policy has unsupported allowlist entry shape at "
                f"{label}[{index}]"
            )
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise SetupConflict(
                "gateway approval policy has unsupported allowlist entry shape at "
                f"{label}[{index}]"
            )
        for key in ("id", "source", "lastUsedCommand", "lastResolvedPath"):
            if key in entry and not isinstance(entry[key], str):
                raise SetupConflict(
                    "gateway approval policy has unsupported allowlist entry shape at "
                    f"{label}[{index}]"
                )
        if "lastUsedAt" in entry and not isinstance(entry["lastUsedAt"], (int, float)):
            raise SetupConflict(
                "gateway approval policy has unsupported allowlist entry shape at "
                f"{label}[{index}]"
            )
        patterns.add(pattern)
    return patterns


def _effective_agent_scope(payload: dict, agent_id: str) -> dict | None:
    effective = payload.get("effectivePolicy")
    if not isinstance(effective, dict):
        return None
    scopes = effective.get("scopes")
    if not isinstance(scopes, list):
        return None
    matches = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        scoped_agent = scope.get("agentId")
        if scoped_agent == agent_id:
            matches.append(scope)
        elif scoped_agent is None and scope.get("scopeLabel") == f"agent:{agent_id}":
            matches.append(scope)
    if len(matches) > 1:
        raise SetupConflict(
            "gateway effective approval policy contains duplicate dedicated-agent scopes"
        )
    return matches[0] if matches else None


def _validate_effective_gateway_policy(
    payload: dict, agent_id: str, *, required: bool
) -> None:
    if not required:
        return
    scope = _effective_agent_scope(payload, agent_id)
    if scope is None:
        raise SetupConflict(
            "gateway effective approval policy is unavailable for the dedicated agent"
        )
    host = _require_mapping(scope.get("host"), "effective host")
    mode = _require_mapping(scope.get("mode"), "effective mode")
    security = _require_mapping(scope.get("security"), "effective security")
    ask = _require_mapping(scope.get("ask"), "effective ask")
    fallback = _require_mapping(scope.get("askFallback"), "effective askFallback")
    if host.get("requested") != "gateway":
        raise SetupConflict(
            "gateway effective approval policy does not request the gateway host"
        )
    if mode.get("effective") != "allowlist":
        raise SetupConflict(
            "gateway effective approval policy mode is not allowlist-only"
        )
    if security.get("effective") != "allowlist":
        raise SetupConflict(
            "gateway effective approval policy security is not allowlist-only"
        )
    if ask.get("effective") != "off":
        raise SetupConflict(
            "gateway effective approval policy ask mode is not unambiguously off"
        )
    if ask.get("requested") not in (None, "off"):
        raise SetupConflict(
            "gateway effective approval policy has contradictory requested and "
            "effective ask modes"
        )
    if fallback.get("effective") not in {"deny", "allowlist"}:
        raise SetupConflict(
            "gateway effective approval policy has an unsafe ask fallback"
        )


def _validate_gateway_approval_payload(
    payload: Any, agent_id: str, *, require_effective: bool
) -> set[str]:
    root = _require_mapping(payload, "root")
    file_policy = _require_mapping(root.get("file"), "file")
    if file_policy.get("version") != 1:
        raise SetupConflict("gateway approval policy has unsupported file version")
    defaults = _require_mapping(file_policy.get("defaults", {}), "defaults")
    agents = _require_mapping(file_policy.get("agents", {}), "agents")
    _validate_policy_shape(defaults, "defaults", allow_allowlist=False)
    _validate_auto_allow(defaults, "defaults")
    for inherited_key in ("*", "default"):
        if inherited_key not in agents:
            continue
        inherited = _require_mapping(
            agents[inherited_key], f"agents.{inherited_key}"
        )
        _validate_policy_shape(
            inherited, f"agents.{inherited_key}", allow_allowlist=True
        )
        _validate_auto_allow(inherited, f"agents.{inherited_key}")
        inherited_patterns = _parse_allowlist_entries(
            inherited.get("allowlist"), f"agents.{inherited_key}.allowlist"
        )
        if inherited_patterns:
            raise SetupConflict(
                "dedicated CRM agent has unexpected inherited executable allowlist entries"
            )
    agent_policy = _require_mapping(agents.get(agent_id, {}), f"agents.{agent_id}")
    _validate_policy_shape(agent_policy, f"agents.{agent_id}", allow_allowlist=True)
    _validate_auto_allow(agent_policy, f"agents.{agent_id}")
    patterns = _parse_allowlist_entries(
        agent_policy.get("allowlist"), f"agents.{agent_id}.allowlist"
    )
    _validate_effective_gateway_policy(root, agent_id, required=require_effective)
    return patterns


def _validate_sandbox_explain(payload: Any, agent_id: str) -> None:
    """Validate direct sandbox state without inferring exec policy from it."""
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


def _run_required(cli: OpenClawCLI, argv: list[str], label: str) -> CommandResult:
    result = cli.run(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise SetupConflict(f"unsupported OpenClaw installation: {label} failed{suffix}")
    return result


def _run_sensitive_required(
    cli: OpenClawCLI, argv: list[str], label: str
) -> CommandResult:
    result = cli.run(argv)
    if result.returncode != 0:
        raise SetupConflict(f"{label} failed; sensitive output was suppressed")
    return result


def _command_entries(output: str) -> set[str]:
    lines = output.splitlines()
    commands_indent: int | None = None
    candidates: list[tuple[int, str]] = []
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
        candidates.append((indent, token))
    if not candidates:
        return set()
    direct_indent = min(indent for indent, _ in candidates)
    return {token for indent, token in candidates if indent == direct_indent}


def _require_help(
    cli: OpenClawCLI, argv: list[str], label: str, required: tuple[str, ...]
) -> None:
    result = _run_required(cli, argv, label)
    output = f"{result.stdout}\n{result.stderr}"
    option_tokens = set(
        re.findall(
            r"(?<![A-Za-z0-9_-])--[A-Za-z0-9][A-Za-z0-9-]*(?![A-Za-z0-9_-])",
            output,
        )
    )
    command_entries = _command_entries(output)
    missing = [
        token
        for token in required
        if token not in (option_tokens if token.startswith("--") else command_entries)
    ]
    if missing:
        raise SetupConflict(
            f"unsupported OpenClaw installation: {label} help is missing "
            + ", ".join(missing)
        )


def _preflight(cli: OpenClawCLI, options: SetupOptions) -> None:
    agents_commands = ("add", "list") + (("bind",) if options.bind_discord else ())
    secretref_options = (
        "--ref-provider",
        "--ref-source",
        "--ref-id",
        "--dry-run",
    )
    config_set_options = ("--strict-json",)
    token_enabled = bool(os.environ.get("OHI_API_TOKEN", ""))
    if token_enabled:
        config_set_options += secretref_options
    config_commands = ("get", "set", "unset", "validate")
    if token_enabled:
        config_commands += ("file",)
    checks = [
        (["openclaw", "agents", "--help"], "agents", agents_commands),
        (
            ["openclaw", "agents", "add", "--help"],
            "agents add",
            ("--workspace", "--non-interactive", "--json"),
        ),
        (["openclaw", "agents", "list", "--help"], "agents list", ("--json",)),
        (["openclaw", "skills", "--help"], "skills", ("check",)),
        (
            ["openclaw", "skills", "check", "--help"],
            "skills check",
            ("--agent", "--json"),
        ),
        (
            ["openclaw", "config", "--help"],
            "config",
            config_commands,
        ),
        (["openclaw", "config", "get", "--help"], "config get", ("--json",)),
        (
            ["openclaw", "config", "set", "--help"],
            "config set",
            config_set_options,
        ),
        (
            ["openclaw", "config", "validate", "--help"],
            "config validate",
            ("--json",),
        ),
        (
            ["openclaw", "config", "unset", "--help"],
            "config unset",
            (),
        ),
        (
            ["openclaw", "approvals", "--help"],
            "approvals",
            ("get", "allowlist"),
        ),
        (
            ["openclaw", "approvals", "allowlist", "--help"],
            "approvals allowlist",
            ("add", "remove"),
        ),
        (
            ["openclaw", "approvals", "allowlist", "add", "--help"],
            "approvals allowlist add",
            ("--agent", "--gateway"),
        ),
        (
            ["openclaw", "approvals", "allowlist", "remove", "--help"],
            "approvals allowlist remove",
            ("--agent", "--gateway"),
        ),
        (
            ["openclaw", "approvals", "get", "--help"],
            "approvals get",
            ("--gateway", "--json"),
        ),
        (["openclaw", "exec-policy", "--help"], "exec-policy", ("show",)),
        (
            ["openclaw", "exec-policy", "show", "--help"],
            "exec-policy show",
            ("--json",),
        ),
        (["openclaw", "sandbox", "--help"], "sandbox", ("explain",)),
        (
            ["openclaw", "sandbox", "explain", "--help"],
            "sandbox explain",
            ("--agent", "--json"),
        ),
        (
            ["openclaw", "plugins", "--help"],
            "plugins",
            ("list", "install", "inspect", "enable", "disable", "uninstall"),
        ),
        (
            ["openclaw", "plugins", "list", "--help"],
            "plugins list",
            ("--json",),
        ),
        (
            ["openclaw", "plugins", "install", "--help"],
            "plugins install",
            ("--link", "--force"),
        ),
        (
            ["openclaw", "plugins", "inspect", "--help"],
            "plugins inspect",
            ("--runtime", "--json"),
        ),
        (
            ["openclaw", "plugins", "uninstall", "--help"],
            "plugins uninstall",
            ("--keep-files", "--force"),
        ),
        (["openclaw", "gateway", "--help"], "gateway", ("restart",)),
    ]
    if options.bind_discord:
        checks.append(
            (
                ["openclaw", "agents", "bind", "--help"],
                "agents bind",
                ("--agent", "--bind", "--json"),
            )
        )
    for argv, label, required in checks:
        try:
            _require_help(cli, argv, label, required)
        except SetupConflict as exc:
            if token_enabled and label == "config set" and any(
                option in str(exc) for option in secretref_options
            ):
                raise SetupConflict(
                    "This OpenClaw version cannot configure an environment SecretRef "
                    "for OHI_API_TOKEN. Upgrade OpenClaw or configure that SecretRef "
                    "manually; setup will not store the token as plaintext."
                ) from exc
            raise


def _detect_version(cli: OpenClawCLI) -> str:
    result = _run_required(cli, ["openclaw", "--version"], "openclaw --version")
    raw = result.stdout or result.stderr
    version = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    if not version:
        raise SetupConflict(
            "unsupported OpenClaw installation: openclaw --version returned no version"
        )
    return version[:200]


def _validate_authoritative_tools(payload: Any) -> None:
    """Require the post-write agent tool policy to expose only CRM and brief tools."""
    if not isinstance(payload, dict):
        raise SetupConflict(
            "unsupported OpenClaw installation: authoritative agent tools "
            "were not exposed as a JSON object"
        )
    allow = payload.get("allow")
    deny = payload.get("deny")
    exec_policy = payload.get("exec")
    if payload.get("profile") != DESIRED_TOOLS["profile"]:
        raise SetupConflict(
            "dedicated CRM agent authoritative tool profile is not the scoped full base"
        )
    if (
        not isinstance(allow, list)
        or not isinstance(deny, list)
        or not all(isinstance(item, str) for item in allow + deny)
    ):
        raise SetupConflict(
            "unsupported OpenClaw installation: authoritative agent tools "
            "did not expose allow and deny lists"
        )
    if sorted(allow) != sorted([PLUGIN_TOOL, "exec"]):
        raise SetupConflict(
            "dedicated CRM agent authoritative tool policy does not expose exactly "
            "openhouse_crm and exec"
        )
    if "exec" in deny:
        raise SetupConflict(
            "dedicated CRM agent authoritative tool policy contradicts itself "
            "by both allowing and denying exec"
        )
    if sorted(deny) != sorted(DESIRED_TOOL_DENY):
        raise SetupConflict(
            "dedicated CRM agent authoritative deny policy does not exactly "
            "match the intended general-tool deny set"
        )
    if exec_policy != DESIRED_TOOLS["exec"]:
        raise SetupConflict(
            "dedicated CRM agent authoritative exec policy is not gateway allowlist-only"
        )


def _config_actions(options: SetupOptions, prefix: str) -> list[Action]:
    token = os.environ.get("OHI_API_TOKEN", "")
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
                "Configure the CRM API token from environment SecretRef OHI_API_TOKEN",
                _token_secretref_argv(dry_run=False),
            )
        )
    return actions


def _deferred_agent_config_messages(options: SetupOptions) -> list[str]:
    messages = [
        "Would configure the dedicated CRM agent's shipped skills after agent creation, "
        "once OpenClaw selects the exact roster path.",
        "Would configure the dedicated CRM agent's native CRM tool and daily-brief exec policy after agent creation, "
        "once OpenClaw selects the exact roster path.",
        "Would configure the dedicated CRM agent's sandbox mode after agent creation, "
        "once OpenClaw selects the exact roster path.",
        "Would configure the CRM API URL.",
    ]
    if os.environ.get("OHI_API_TOKEN", ""):
        messages.append(
            "Would configure the CRM API token from environment SecretRef OHI_API_TOKEN."
        )
    return messages


def _render_action(action: Action) -> str:
    return _redact_api_token(f"{action.description}: {' '.join(action.argv)}")


def _run_action(cli: OpenClawCLI, action: Action) -> CommandResult:
    return cli.run(action.argv, mutate=action.mutates)


def _same_workspace(configured: Any, requested: Path) -> bool:
    if not isinstance(configured, str) or not configured.strip():
        return False
    try:
        return Path(configured).expanduser().resolve() == requested.expanduser().resolve()
    except OSError:
        return False


def _managed_agent_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        field: agent[field]
        for field in ("skills", "tools", "sandbox")
        if field in agent
    }


def _restore_managed_agent_fields(
    cli: OpenClawCLI,
    *,
    agent_id: str,
    workspace: Path,
    snapshot: dict[str, Any],
    changed_fields: list[str],
) -> list[str]:
    if not changed_fields:
        return []
    errors: list[str] = []
    pending = list(reversed(changed_fields))
    for index, field in enumerate(pending):
        try:
            roster = _read_agent_roster(
                cli, allow_missing=False, label="agent rollback roster"
            )
        except (SetupConflict, OSError):
            errors.extend(pending[index:])
            break
        prefix = roster.prefixes.get(agent_id)
        agent = next(
            (record for record in roster.records if record.get("id") == agent_id),
            None,
        )
        configured_workspace = (
            agent.get("workspace") or agent.get("workspacePath")
            if agent is not None
            else None
        )
        if prefix is None or not _same_workspace(configured_workspace, workspace):
            errors.extend(pending[index:])
            break
        path = f"{prefix}.{field}"
        if field in snapshot:
            argv = [
                "openclaw",
                "config",
                "set",
                path,
                json.dumps(snapshot[field]),
                "--strict-json",
            ]
        else:
            argv = ["openclaw", "config", "unset", path]
        try:
            result = cli.run(argv, mutate=True)
        except OSError:
            errors.append(field)
            continue
        if result.returncode != 0:
            errors.append(field)
    return errors


def _plugin_source(repo: Path) -> Path:
    source = repo / "openclaw-plugins" / PLUGIN_ID
    for required in (
        source / "openclaw.plugin.json",
        source / "package.json",
        source / "dist" / "index.js",
    ):
        if not required.is_file() or required.is_symlink():
            raise SetupConflict(f"bundled OpenClaw CRM plugin is incomplete: {required}")
    return source.resolve()


def _plugin_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise SetupConflict("OpenClaw plugins list returned unsupported JSON")
    records = payload["plugins"]
    if not all(isinstance(record, dict) for record in records):
        raise SetupConflict("OpenClaw plugins list returned unsupported plugin entries")
    return records


def _plugin_record_path(record: dict[str, Any]) -> Path | None:
    for key in ("rootDir", "path", "sourcePath", "installPath"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve(strict=False)
    source = record.get("source")
    if isinstance(source, str) and (source.startswith("/") or source.startswith("~")):
        return Path(source).expanduser().resolve(strict=False)
    return None


def _path_belongs_to_plugin(path: Path, source: Path) -> bool:
    return path == source or source in path.parents


def _inspect_plugin_inventory(
    payload: Any,
    source: Path,
    *,
    require_present: bool,
) -> tuple[bool, bool]:
    matches = [record for record in _plugin_records(payload) if record.get("id") == PLUGIN_ID]
    if len(matches) > 1:
        raise SetupConflict("OpenClaw reports duplicate openhouse-crm plugin records")
    if not matches:
        if require_present:
            raise SetupConflict("OpenClaw did not retain the openhouse-crm plugin installation")
        return False, False
    record = matches[0]
    record_path = _plugin_record_path(record)
    if record_path is None:
        raise SetupConflict(
            "OpenClaw did not expose the installed openhouse-crm plugin source path"
        )
    if not _path_belongs_to_plugin(record_path, source):
        raise SetupConflict(
            "OpenClaw already has an openhouse-crm plugin from a different source; "
            "remove or rename it explicitly before running setup"
        )
    if record.get("format") not in (None, "openclaw"):
        raise SetupConflict("openhouse-crm is not installed as a native OpenClaw plugin")
    enabled = record.get("enabled")
    if not isinstance(enabled, bool):
        raise SetupConflict("OpenClaw did not expose openhouse-crm plugin enablement")
    dependency_status = record.get("dependencyStatus")
    if isinstance(dependency_status, dict) and dependency_status.get("ok") is False:
        raise SetupConflict("openhouse-crm plugin dependencies are not available")
    return True, enabled


def _read_plugin_allowlist(cli: OpenClawCLI) -> list[str] | None:
    path = "plugins.allow"
    result = cli.run(["openclaw", "config", "get", path, "--json"])
    if result.returncode != 0:
        if _is_missing_config_path(result, path):
            return None
        detail = (result.stderr or result.stdout).strip()
        raise SetupConflict(f"plugins.allow inspection failed: {detail}")
    value = _json(result, "plugins.allow")
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise SetupConflict("plugins.allow has an unsupported JSON shape")
    return value


def _registered_hook_names(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise SetupConflict(f"OpenClaw returned unsupported {label}")
    names: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry:
            names.append(entry)
            continue
        if isinstance(entry, dict):
            name = entry.get("name", entry.get("hookName"))
            if isinstance(name, str) and name:
                names.append(name)
                continue
        raise SetupConflict(f"OpenClaw returned unsupported {label}")
    if len(names) != len(set(names)):
        raise SetupConflict(f"OpenClaw returned duplicate {label}")
    return names


def _validate_runtime_plugin(payload: Any, source: Path) -> bool:
    if not isinstance(payload, dict):
        raise SetupConflict("OpenClaw plugin runtime inspection returned unsupported JSON")
    plugin = payload.get("plugin")
    runtime = payload.get("runtime")
    if not isinstance(plugin, dict) or plugin.get("id") != PLUGIN_ID:
        raise SetupConflict("OpenClaw runtime inspection returned the wrong plugin")
    if plugin.get("enabled") is not True:
        raise SetupConflict("openhouse-crm plugin is not enabled")
    record_path = _plugin_record_path(plugin)
    if record_path is not None and not _path_belongs_to_plugin(record_path, source):
        raise SetupConflict("OpenClaw runtime loaded openhouse-crm from a different source")
    if runtime is None:
        runtime = payload
    if not isinstance(runtime, dict) or not isinstance(runtime.get("tools"), list):
        raise SetupConflict("OpenClaw did not expose runtime plugin tools")
    names: list[str] = []
    for entry in runtime["tools"]:
        if isinstance(entry, str):
            names.append(entry)
            continue
        if not isinstance(entry, dict):
            raise SetupConflict("OpenClaw returned an unsupported runtime tool entry")
        if entry.get("optional") is True:
            raise SetupConflict("openhouse_crm runtime tool must not be optional")
        if "optional" in entry and not isinstance(entry["optional"], bool):
            raise SetupConflict("OpenClaw returned an unsupported runtime tool entry")
        if "name" in entry:
            name = entry["name"]
            if not isinstance(name, str) or not name:
                raise SetupConflict("OpenClaw returned an unsupported runtime tool entry")
            names.append(name)
            continue
        factory_names = entry.get("names")
        if (
            not isinstance(factory_names, list)
            or not all(isinstance(name, str) and name for name in factory_names)
        ):
            raise SetupConflict("OpenClaw returned an unsupported runtime tool entry")
        names.extend(factory_names)
    for holder, label in (
        (runtime, "runtime tool names"),
        (plugin, "plugin tool names"),
    ):
        if "toolNames" not in holder:
            continue
        reported_names = holder["toolNames"]
        if (
            not isinstance(reported_names, list)
            or not all(isinstance(name, str) and name for name in reported_names)
            or len(reported_names) != len(set(reported_names))
        ):
            raise SetupConflict(f"OpenClaw returned unsupported {label}")
        if sorted(reported_names) != sorted(names):
            raise SetupConflict(
                "OpenClaw runtime tool inventory is internally inconsistent"
            )
    if len(names) != len(set(names)):
        raise SetupConflict("OpenClaw returned duplicate runtime tool names")
    if names != [PLUGIN_TOOL]:
        raise SetupConflict(
            "openhouse-crm runtime must register exactly the openhouse_crm tool"
        )
    diagnostics = runtime.get("diagnostics", [])
    if not isinstance(diagnostics, list) or diagnostics:
        raise SetupConflict("openhouse-crm runtime inspection reported diagnostics")

    hook_inventories: list[list[str]] = []
    seen_inventory_keys: set[tuple[int, str]] = set()
    for holder, key, label in (
        (runtime, "typedHooks", "runtime typed hooks"),
        (runtime, "hooks", "runtime hooks"),
        (runtime, "hookNames", "runtime hook names"),
        (plugin, "hookNames", "plugin hook names"),
    ):
        marker = (id(holder), key)
        if marker in seen_inventory_keys or key not in holder:
            continue
        seen_inventory_keys.add(marker)
        hook_inventories.append(_registered_hook_names(holder[key], label))
    for hook_names in hook_inventories:
        if sorted(hook_names) != sorted(REQUIRED_PLUGIN_HOOKS):
            raise SetupConflict(
                "OpenClaw runtime did not expose exactly the required CRM outcome hooks: "
                + ", ".join(REQUIRED_PLUGIN_HOOKS)
            )
    if hook_inventories and any(
        sorted(names) != sorted(hook_inventories[0])
        for names in hook_inventories[1:]
    ):
        raise SetupConflict("OpenClaw runtime hook inventory is internally inconsistent")
    hook_count = plugin.get("hookCount")
    if hook_count is not None and (
        not isinstance(hook_count, int) or hook_count != len(REQUIRED_PLUGIN_HOOKS)
    ):
        raise SetupConflict(
            "OpenClaw runtime did not expose exactly the required CRM outcome hooks"
        )
    return bool(hook_inventories)


def _verify_client_tool_capability(cli: OpenClawCLI, agent_id: str) -> None:
    nonce = secrets.token_hex(16)
    result = cli.probe_client_tools(agent_id=agent_id, nonce=nonce)
    if result.returncode == 400:
        raise SetupConflict(
            "unsupported OpenClaw installation: Chat Completions does not support "
            "required request-scoped function tools with tool_choice:\"required\""
        )
    if result.returncode in {401, 403}:
        raise SetupConflict(
            "OpenClaw client-tool capability was not proven because Gateway "
            "authentication failed; configure the matching AGENT_GATEWAY_TOKEN"
        )
    if result.returncode != 200:
        raise SetupConflict(
            "OpenClaw provider/model capability was not proven by the bounded "
            "request-scoped client-tool probe"
        )
    try:
        payload = _decode_json(result.stdout, "client-tool capability probe")
    except SetupConflict as exc:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        ) from exc
    try:
        choices = payload["choices"]
        choice = choices[0] if len(choices) == 1 else None
        message = choice["message"]
        tool_calls = message["tool_calls"]
        call = tool_calls[0] if len(tool_calls) == 1 else None
        function = call["function"]
        arguments = _decode_json(
            function["arguments"], "client-tool capability arguments"
        )
        valid = (
            choice["finish_reason"] == "tool_calls"
            and call["type"] == "function"
            and function["name"] == SETUP_PROBE_TOOL
            and arguments == {"nonce": nonce}
        )
    except (KeyError, IndexError, TypeError, SetupConflict):
        valid = False
    if not valid:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )


def _verify_dashboard_tool_block(cli: OpenClawCLI, agent_id: str) -> None:
    nonce = secrets.token_hex(16)
    result = cli.probe_dashboard_tool_block(agent_id=agent_id, nonce=nonce)
    response = f"{result.stdout}\n{result.stderr}".lower()
    expected_reason = "dashboard crm calls must use the verified tool invocation path"
    if result.returncode != 403 or expected_reason not in response:
        raise SetupConflict(
            "OpenClaw could not authoritatively enumerate hooks and the bounded "
            "loopback diagnostic did not prove the dashboard CRM tool block; "
            "no supported CRM operation was executed"
        )


def _restore_approval_changes(
    cli: OpenClawCLI,
    *,
    agent_id: str,
    added: set[str],
    removed: set[str],
) -> list[str]:
    failures: list[str] = []
    for pattern in sorted(added):
        result = cli.run(
            [
                "openclaw",
                "approvals",
                "allowlist",
                "remove",
                "--agent",
                agent_id,
                "--gateway",
                pattern,
            ],
            mutate=True,
        )
        if result.returncode != 0:
            failures.append(pattern)
    for pattern in sorted(removed):
        result = cli.run(
            [
                "openclaw",
                "approvals",
                "allowlist",
                "add",
                "--agent",
                agent_id,
                "--gateway",
                pattern,
            ],
            mutate=True,
        )
        if result.returncode != 0:
            failures.append(pattern)
    return failures


def configure_openclaw(options: SetupOptions, cli: OpenClawCLI) -> SetupResult:
    messages: list[str] = []
    rollback: AgentRollback | None = None
    skill_rollback: SkillRollback | None = None
    plugin_source: Path | None = None
    plugin_preexisting = False
    plugin_previously_enabled = False
    plugin_installed_this_run = False
    plugin_enabled_this_run = False
    plugin_allow_original: list[str] | None = None
    plugin_allow_changed = False
    approval_added: set[str] = set()
    approval_removed: set[str] = set()
    gateway_restarted = False
    try:
        _validate_requested_agent_id(options.agent_id)
        token = os.environ.get("OHI_API_TOKEN", "")
        gateway_env_path: Path | None = None
        if token:
            _validate_api_token(token)
        version = _detect_version(cli)
        messages.append(f"OpenClaw version: {version}")
        _preflight(cli, options)
        repo = Path(__file__).resolve().parents[1]
        contract_digest = _canonical_contract_digest(repo)
        plugin_source = _plugin_source(repo)
        plugin_list = _run_required(
            cli,
            ["openclaw", "plugins", "list", "--json"],
            "plugins list --json",
        )
        plugin_preexisting, plugin_previously_enabled = _inspect_plugin_inventory(
            _json(plugin_list, "plugins list"),
            plugin_source,
            require_present=False,
        )
        plugin_allow_original = _read_plugin_allowlist(cli)
        if token:
            _run_required(
                cli,
                _token_secretref_argv(dry_run=True),
                "environment SecretRef validation",
            )
            _run_required(cli, ["openclaw", "config", "file"], "config file")
            gateway_env_path = _gateway_env_path()
        listed = _run_required(
            cli, ["openclaw", "agents", "list", "--json"], "agents list --json"
        )
        agents = _cli_agents(_json(listed, "agents list"))
        configured_roster = _read_agent_roster(
            cli, allow_missing=True, label="initial agents config"
        )
        configured_agents = configured_roster.records
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
        for source, agent in (
            ("agent list", listed_agent),
            ("agent configuration", configured_agent),
        ):
            if agent is None:
                continue
            workspace = agent.get("workspace") or agent.get("workspacePath")
            if not _same_workspace(workspace, options.workspace):
                raise SetupConflict(
                    f"agent {options.agent_id} has a different workspace in {source}; "
                    "setup will not overwrite an agent it does not own"
                )
        initial_actions = build_setup_actions(options, agents)
        approvals = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "approvals get --gateway --json",
        )
        approvals_payload = _json(approvals, "gateway approvals")
        existing_patterns = _validate_gateway_approval_payload(
            approvals_payload,
            options.agent_id,
            require_effective=False,
        )
        wrapper, daily = _entrypoints(options)
        expected_patterns = {str(daily)}
        repairable_patterns = {str(wrapper), str(daily)}
        unexpected = existing_patterns - repairable_patterns
        if unexpected:
            raise SetupConflict(
                "dedicated CRM agent has unexpected executable allowlist entries: "
                + ", ".join(sorted(unexpected))
            )

        if options.dry_run:
            planned = [
                Action(
                    "Link or refresh the bundled OpenClaw CRM plugin",
                    [
                        "openclaw",
                        "plugins",
                        "install",
                        "--link",
                        str(plugin_source),
                        "--force",
                    ],
                ),
                Action(
                    "Enable the bundled OpenClaw CRM plugin",
                    ["openclaw", "plugins", "enable", PLUGIN_ID],
                ),
            ]
            if (
                plugin_allow_original is not None
                and PLUGIN_ID not in plugin_allow_original
            ):
                planned.append(
                    Action(
                        "Include the bundled CRM plugin in plugins.allow",
                        [
                            "openclaw",
                            "config",
                            "set",
                            "plugins.allow",
                            json.dumps([*plugin_allow_original, PLUGIN_ID]),
                            "--strict-json",
                        ],
                    )
                )
            planned.extend(
                action
                for action in initial_actions
                if not (
                    action.argv[1:4] == ["approvals", "allowlist", "add"]
                    and action.argv[-1] in existing_patterns
                )
                and not (
                    action.argv[1:4] == ["approvals", "allowlist", "remove"]
                    and action.argv[-1] not in existing_patterns
                )
            )
            prefix = configured_roster.prefixes.get(options.agent_id)
            if prefix is None:
                messages.extend(_deferred_agent_config_messages(options))
            else:
                planned.extend(_config_actions(options, prefix))
            messages.append("Dry run only. No files or OpenClaw configuration were changed.")
            messages.append(
                "Would install and verify skills/crm-db-operations/contract.json "
                f"with canonical SHA-256 {contract_digest}."
            )
            messages.append(
                "Would remove any stale installed skills/crm-db-operations/operations.json."
            )
            messages.append(
                "Would verify exact openhouse_crm registration and the required CRM outcome hooks."
            )
            messages.append(
                "Would verify Chat Completions request-scoped function tools with "
                'tool_choice:"required" using one bounded no-write probe.'
            )
            if gateway_env_path is not None:
                messages.append(
                    "Would securely store OHI_API_TOKEN at "
                    f"{gateway_env_path} with file mode 0600."
                )
            messages.extend(_render_action(action) for action in planned)
            if not options.bind_discord:
                messages.append(
                    "Optional Discord binding: openclaw agents bind --agent "
                    f"{options.agent_id} --bind discord:ACCOUNT --json"
                )
            return SetupResult(True, messages)

        skill_rollback = _snapshot_installed_skills(options.workspace)

        if token and gateway_env_path is not None:
            _upsert_gateway_env(gateway_env_path, token)

        sync_skills(repo, options.workspace, dry_run=False)
        _verify_installed_contract(options.workspace, contract_digest)
        messages.append(
            f"Installed CRM skills in {options.workspace / 'skills'} and verified "
            f"contract.json SHA-256 {contract_digest}"
        )

        install_plugin = cli.run(
            [
                "openclaw",
                "plugins",
                "install",
                "--link",
                str(plugin_source),
                "--force",
            ],
            mutate=True,
        )
        if install_plugin.returncode != 0:
            raise SetupConflict(
                "Bundled OpenClaw CRM plugin installation failed: "
                + (install_plugin.stderr or install_plugin.stdout).strip()
            )
        plugin_installed_this_run = not plugin_preexisting
        messages.append("Linked the bundled OpenClaw CRM plugin")

        enable_plugin = cli.run(
            ["openclaw", "plugins", "enable", PLUGIN_ID],
            mutate=True,
        )
        if enable_plugin.returncode != 0:
            raise SetupConflict(
                "Bundled OpenClaw CRM plugin enablement failed: "
                + (enable_plugin.stderr or enable_plugin.stdout).strip()
            )
        plugin_enabled_this_run = not plugin_previously_enabled
        messages.append("Enabled the bundled OpenClaw CRM plugin")

        if plugin_allow_original is not None and PLUGIN_ID not in plugin_allow_original:
            plugin_allow = cli.run(
                [
                    "openclaw",
                    "config",
                    "set",
                    "plugins.allow",
                    json.dumps([*plugin_allow_original, PLUGIN_ID]),
                    "--strict-json",
                ],
                mutate=True,
            )
            if plugin_allow.returncode != 0:
                raise SetupConflict(
                    "Could not include openhouse-crm in plugins.allow: "
                    + (plugin_allow.stderr or plugin_allow.stdout).strip()
                )
            plugin_allow_changed = True
            messages.append("Included the bundled CRM plugin in plugins.allow")

        create_actions = [
            action for action in initial_actions if action.argv[1:3] == ["agents", "add"]
        ]
        for action in create_actions:
            result = _run_action(cli, action)
            if result.returncode != 0:
                raise SetupConflict(
                    f"{action.description} failed: {(result.stderr or result.stdout).strip()}"
                )
            created_payload = _json(result, "agents add --json")
            if not isinstance(created_payload, dict):
                raise SetupConflict(
                    "agents add --json returned an unsupported JSON shape"
                )
            created_agent_id = _require_canonical_agent_id(
                created_payload.get("agentId"), "agents add --json agentId"
            )
            if created_agent_id != options.agent_id:
                raise SetupConflict(
                    "agents add --json returned a different agentId than requested"
                )
            messages.append(action.description)

        refreshed_roster = _read_agent_roster(
            cli, allow_missing=False, label="agents config after agent creation"
        )
        prefix = refreshed_roster.prefixes.get(options.agent_id)
        if prefix is None:
            raise SetupConflict(
                f"OpenClaw did not expose the {options.agent_id} agent after creation"
            )
        refreshed_agent = next(
            (
                agent
                for agent in refreshed_roster.records
                if agent.get("id") == options.agent_id
            ),
            None,
        )
        if refreshed_agent is None:
            raise SetupConflict(
                f"OpenClaw did not expose the {options.agent_id} agent after creation"
            )
        rollback = AgentRollback(
            snapshot=_managed_agent_snapshot(refreshed_agent), changed_fields=[]
        )

        actions = _config_actions(options, prefix)
        actions.extend(
            action
            for action in initial_actions
            if action.argv[1:3] != ["agents", "add"]
            and not (
                action.argv[1:4] == ["approvals", "allowlist", "add"]
                and action.argv[-1] in existing_patterns
            )
            and not (
                action.argv[1:4] == ["approvals", "allowlist", "remove"]
                and action.argv[-1] not in existing_patterns
            )
        )
        for action in actions:
            targets_agent_config = (
                action.argv[1:3] == ["config", "set"]
                and action.argv[3].startswith(f"{prefix}.")
            )
            try:
                agent_flag = action.argv.index("--agent")
            except ValueError:
                targets_agent_flag = False
            else:
                targets_agent_flag = (
                    agent_flag + 1 < len(action.argv)
                    and action.argv[agent_flag + 1] == options.agent_id
                )
            if targets_agent_config or targets_agent_flag:
                _revalidate_agent_target(
                    cli,
                    agent_id=options.agent_id,
                    prefix=prefix,
                    workspace=options.workspace,
                    label=f"dedicated agent revalidation before {action.description}",
                )
            result = _run_action(cli, action)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise SetupConflict(f"{action.description} failed: {detail}")
            if (
                action.argv[1:3] == ["config", "set"]
                and action.argv[3].startswith(f"{prefix}.")
            ):
                field = action.argv[3][len(prefix) + 1 :]
                if field in {"skills", "tools", "sandbox"}:
                    rollback.changed_fields.append(field)
            if action.argv[1:4] == ["approvals", "allowlist", "add"]:
                approval_added.add(action.argv[-1])
            elif action.argv[1:4] == ["approvals", "allowlist", "remove"]:
                approval_removed.add(action.argv[-1])
            messages.append(action.description)

        if token:
            token_readback = _run_required(
                cli,
                ["openclaw", "config", "get", TOKEN_CONFIG_PATH, "--json"],
                "CRM API token SecretRef readback",
            )
            token_ref = _json(token_readback, "CRM API token SecretRef readback")
            if token_ref not in (TOKEN_SECRETREF, TOKEN_SECRETREF_REDACTED):
                raise SetupConflict(
                    "OpenClaw did not persist the expected OHI_API_TOKEN SecretRef shape"
                )

            legacy_readback = _run_sensitive_required(
                cli,
                ["openclaw", "config", "get", TOKEN_ENTRY_CONFIG_PATH, "--json"],
                "legacy CRM API token inspection",
            )
            skill_entry = _json(legacy_readback, "legacy CRM API token inspection")
            if not isinstance(skill_entry, dict):
                raise SetupConflict(
                    "OpenClaw returned an unsupported CRM skill configuration shape"
                )
            legacy_env = skill_entry.get("env", {})
            if not isinstance(legacy_env, dict):
                raise SetupConflict(
                    "OpenClaw returned an unsupported legacy skill environment shape"
                )
            if "OHI_API_TOKEN" in legacy_env:
                unset = cli.run(
                    ["openclaw", "config", "unset", LEGACY_TOKEN_CONFIG_PATH],
                    mutate=True,
                )
                if unset.returncode != 0:
                    raise SetupConflict(
                        "Could not remove the legacy plaintext CRM API token setting"
                    )
                legacy_verify = _run_sensitive_required(
                    cli,
                    [
                        "openclaw",
                        "config",
                        "get",
                        TOKEN_ENTRY_CONFIG_PATH,
                        "--json",
                    ],
                    "legacy CRM API token cleanup verification",
                )
                verified_entry = _json(
                    legacy_verify, "legacy CRM API token cleanup verification"
                )
                if not isinstance(verified_entry, dict):
                    raise SetupConflict(
                        "OpenClaw returned an unsupported CRM skill configuration shape"
                    )
                verified_legacy_env = verified_entry.get("env", {})
                if not isinstance(verified_legacy_env, dict) or (
                    "OHI_API_TOKEN" in verified_legacy_env
                ):
                    raise SetupConflict(
                        "OpenClaw did not remove the legacy plaintext CRM API token setting"
                    )

        _revalidate_agent_target(
            cli,
            agent_id=options.agent_id,
            prefix=prefix,
            workspace=options.workspace,
            label="legacy roster revalidation before authoritative tools readback",
        )
        authoritative_tools = _run_required(
            cli,
            [
                "openclaw",
                "config",
                "get",
                f"{prefix}.tools",
                "--json",
            ],
            "authoritative dedicated-agent tools",
        )
        _validate_authoritative_tools(
            _json(authoritative_tools, "authoritative dedicated-agent tools")
        )

        installed_plugins = _run_required(
            cli,
            ["openclaw", "plugins", "list", "--json"],
            "installed plugin inventory",
        )
        _inspect_plugin_inventory(
            _json(installed_plugins, "installed plugin inventory"),
            plugin_source,
            require_present=True,
        )
        runtime_plugin = _run_required(
            cli,
            [
                "openclaw",
                "plugins",
                "inspect",
                PLUGIN_ID,
                "--runtime",
                "--json",
            ],
            "openhouse-crm runtime inspection",
        )
        hooks_enumerated = _validate_runtime_plugin(
            _json(runtime_plugin, "openhouse-crm runtime inspection"),
            plugin_source,
        )

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
        if "crm-db-operations" not in _eligible_skills(
            _json(skills, "skills check")
        ):
            raise SetupConflict(
                "OpenClaw did not report crm-db-operations in the eligible skill set"
            )
        sandbox = _run_required(
            cli,
            ["openclaw", "sandbox", "explain", "--agent", options.agent_id, "--json"],
            "sandbox explain",
        )
        sandbox_payload = _json(sandbox, "sandbox explain")
        _validate_sandbox_explain(sandbox_payload, options.agent_id)
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
        final_patterns = _validate_gateway_approval_payload(
            final_approvals_payload, options.agent_id, require_effective=True
        )
        if final_patterns != expected_patterns:
            raise SetupConflict(
                "dedicated CRM agent executable allowlist is not exactly the "
                "deterministic daily-brief runner"
            )

        restart = cli.run(["openclaw", "gateway", "restart"], mutate=True)
        if restart.returncode != 0:
            raise SetupConflict(
                f"Gateway restart failed: {(restart.stderr or restart.stdout).strip()}"
            )
        gateway_restarted = True
        _verify_client_tool_capability(cli, options.agent_id)
        if not hooks_enumerated:
            _verify_dashboard_tool_block(cli, options.agent_id)
        rollback = None
        if skill_rollback is not None:
            _discard_skill_snapshot(skill_rollback)
            skill_rollback = None
        messages.append(
            "Validated the native CRM tool, required CRM outcome hooks, dashboard "
            "tool block, request-scoped client tools, and restricted agent configuration, "
            "then restarted the OpenClaw Gateway. Runtime CRM verification is still required: "
            "python scripts/doctor.py --live-agent --live-crm"
        )
        if not options.bind_discord:
            messages.append(
                "Optional Discord binding: openclaw agents bind --agent "
                f"{options.agent_id} --bind discord:ACCOUNT --json"
            )
        return SetupResult(True, messages)
    except (SetupConflict, OSError) as exc:
        if rollback is not None and rollback.changed_fields:
            rollback_errors = _restore_managed_agent_fields(
                cli,
                agent_id=options.agent_id,
                workspace=options.workspace,
                snapshot=rollback.snapshot,
                changed_fields=rollback.changed_fields,
            )
            if rollback_errors:
                messages.append(
                    "Could not fully restore the previous dedicated-agent "
                    "configuration. Failed fields: " + ", ".join(rollback_errors)
                )
            else:
                messages.append(
                    "Restored the previous dedicated-agent configuration after "
                    "setup failed."
                )
        if approval_added or approval_removed:
            approval_failures = _restore_approval_changes(
                cli,
                agent_id=options.agent_id,
                added=approval_added,
                removed=approval_removed,
            )
            if approval_failures:
                messages.append(
                    "Could not fully restore gateway executable approvals after setup failed."
                )
            else:
                messages.append("Restored gateway executable approvals after setup failed.")
        if plugin_allow_changed and plugin_allow_original is not None:
            restored_allow = cli.run(
                [
                    "openclaw",
                    "config",
                    "set",
                    "plugins.allow",
                    json.dumps(plugin_allow_original),
                    "--strict-json",
                ],
                mutate=True,
            )
            if restored_allow.returncode != 0:
                messages.append("Could not restore the previous plugins.allow value.")
        if plugin_installed_this_run:
            removed_plugin = cli.run(
                [
                    "openclaw",
                    "plugins",
                    "uninstall",
                    PLUGIN_ID,
                    "--keep-files",
                    "--force",
                ],
                mutate=True,
            )
            if removed_plugin.returncode != 0:
                messages.append("Could not remove the newly linked CRM plugin after setup failed.")
        elif plugin_enabled_this_run:
            disabled_plugin = cli.run(
                ["openclaw", "plugins", "disable", PLUGIN_ID],
                mutate=True,
            )
            if disabled_plugin.returncode != 0:
                messages.append("Could not restore the previous CRM plugin enablement.")
        if skill_rollback is not None:
            if _restore_installed_skills(skill_rollback):
                messages.append("Restored the previous installed CRM skills after setup failed.")
            else:
                messages.append("Could not fully restore the previous installed CRM skills.")
            _discard_skill_snapshot(skill_rollback)
            skill_rollback = None
        if gateway_restarted:
            restored_gateway = cli.run(
                ["openclaw", "gateway", "restart"], mutate=True
            )
            if restored_gateway.returncode != 0:
                messages.append(
                    "Could not restart the OpenClaw Gateway after restoring setup state."
                )
        messages.append(str(exc))
        return SetupResult(False, messages)


def _parse_args(
    argv: list[str] | None = None, *, repo: Path | None = None
) -> SetupOptions:
    _load_repo_env(repo or Path(__file__).resolve().parents[1])
    configured_agent_id = os.environ.get("AGENT_ID")
    default_agent_id = (
        configured_agent_id if configured_agent_id is not None else "openhouse-crm"
    )
    parser = argparse.ArgumentParser(
        description="Safely configure a dedicated OpenClaw CRM agent"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent-id")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("~/.openclaw/workspace-openhouse-crm"),
    )
    parser.add_argument("--crm-api-url")
    parser.add_argument("--bind-discord", metavar="ACCOUNT")
    args = parser.parse_args(argv)
    if configured_agent_id is not None and not configured_agent_id.strip():
        parser.error(
            "AGENT_ID must not be blank; set AGENT_ID=openhouse-crm in .env "
            "so setup and runtime target the dedicated CRM agent"
        )
    try:
        _validate_requested_agent_id(default_agent_id)
    except SetupConflict as exc:
        parser.error(str(exc))
    if args.agent_id is not None:
        try:
            _validate_requested_agent_id(args.agent_id)
        except SetupConflict as exc:
            parser.error(str(exc))
    if args.agent_id is not None and args.agent_id != default_agent_id:
        parser.error(
            f"--agent-id {args.agent_id!r} conflicts with runtime "
            f"AGENT_ID={default_agent_id!r}; set AGENT_ID={args.agent_id} in .env "
            "so setup and runtime target the same agent"
        )
    return SetupOptions(
        agent_id=(
            args.agent_id if args.agent_id is not None else default_agent_id
        ),
        workspace=args.workspace.expanduser(),
        crm_api_url=args.crm_api_url or _default_crm_api_url(),
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
