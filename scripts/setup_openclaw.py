#!/usr/bin/env python3
"""Configure a dedicated, restricted OpenClaw agent for this CRM."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
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
SETUP_CAPABILITY_CHANNEL = "openhouse-setup-capability"
SETUP_PROBE_TOOL = "openhouse_setup_capability_probe"
SETUP_BLOCK_PROBE_OPERATION = "__openhouse_setup_probe__"
GATEWAY_PROBE_TIMEOUT_SECONDS = 30
GATEWAY_PROBE_MAX_BYTES = 256 * 1024
CONTRACT_MAX_BYTES = 1024 * 1024
CONTRACT_RELATIVE_PATH = Path("skills") / "crm-db-operations" / "contract.json"
CRM_URL_CONFIG_PATH = 'skills.entries["crm-db-operations"].env.CRM_API_URL'
DIAGNOSTIC_TOOL_POLICY = {
    "profile": "minimal",
    "deny": ["session_status"],
}
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
class ContractSnapshot:
    path: Path
    contents: bytes
    digest: str
    identity: tuple[int, int, int, int]
    operations: frozenset[str]


@dataclass(frozen=True)
class GatewayEnvSnapshot:
    path: Path
    existed: bool
    contents: bytes
    mode: int | None


@dataclass(frozen=True)
class ConfigValueSnapshot:
    path: str
    existed: bool
    value: Any


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
        try:
            gateway_url = _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
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
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=GATEWAY_PROBE_TIMEOUT_SECONDS) as response:
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
        if 300 <= status_code < 400:
            return CommandResult(
                status_code, "", "Gateway capability redirects are unsupported"
            )
        return CommandResult(status_code, rendered, "")

    def probe_client_tools(self, *, agent_id: str, nonce: str) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
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
            chat_path, payload, channel=SETUP_CAPABILITY_CHANNEL
        )

    def probe_dashboard_tool_block(
        self, *, agent_id: str, nonce: str
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _loopback_gateway_base_url() -> str:
    raw = os.environ.get("AGENT_GATEWAY_URL", "http://localhost:18789")
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise SetupConflict("Gateway capability probes require an exact loopback URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
    ):
        raise SetupConflict("Gateway capability probes require an exact loopback URL")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"{parsed.scheme}://{host}:{port}"


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


def _snapshot_gateway_env(env_path: Path) -> GatewayEnvSnapshot:
    absolute = _validate_no_symlink_components(
        env_path, "OpenClaw gateway environment", leaf_directory=False
    )
    try:
        node = os.lstat(absolute)
    except FileNotFoundError:
        return GatewayEnvSnapshot(absolute, False, b"", None)
    except OSError as exc:
        raise SetupConflict(
            f"could not inspect OpenClaw gateway environment: {exc}"
        ) from exc
    if not stat.S_ISREG(node.st_mode):
        raise SetupConflict(
            f"OpenClaw gateway environment must be a regular file: {absolute}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            contents = stream.read()
    except OSError as exc:
        raise SetupConflict(
            f"could not snapshot OpenClaw gateway environment: {exc}"
        ) from exc
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
    return GatewayEnvSnapshot(
        path=absolute,
        existed=True,
        contents=contents,
        mode=stat.S_IMODE(node.st_mode),
    )


def _restore_gateway_env(snapshot: GatewayEnvSnapshot) -> bool:
    try:
        _validate_no_symlink_components(
            snapshot.path, "OpenClaw gateway environment", leaf_directory=False
        )
        if not snapshot.existed:
            try:
                node = os.lstat(snapshot.path)
            except FileNotFoundError:
                return True
            if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode):
                return False
            snapshot.path.unlink()
            return not snapshot.path.exists() and not snapshot.path.is_symlink()

        snapshot.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".openhouse-env-restore-", dir=snapshot.path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, snapshot.mode or 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(snapshot.contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, snapshot.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()
        restored = _snapshot_gateway_env(snapshot.path)
        return (
            restored.existed
            and restored.contents == snapshot.contents
            and restored.mode == snapshot.mode
        )
    except (OSError, SetupConflict):
        return False


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


def _absolute_lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _validate_no_symlink_components(
    path: Path, label: str, *, leaf_directory: bool | None = None
) -> Path:
    absolute = _absolute_lexical_path(path)
    current = Path(absolute.anchor)
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    for index, part in enumerate(components):
        current /= part
        try:
            node = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SetupConflict(f"could not inspect {label}: {exc}") from exc
        if stat.S_ISLNK(node.st_mode):
            raise SetupConflict(f"{label} must not contain a symlink: {current}")
        is_leaf = index == len(components) - 1
        if (not is_leaf or leaf_directory is True) and not stat.S_ISDIR(node.st_mode):
            raise SetupConflict(f"{label} must contain only directories: {current}")
        if is_leaf and leaf_directory is False and not stat.S_ISREG(node.st_mode):
            raise SetupConflict(f"{label} must be a regular file: {current}")
    return absolute


_CONTRACT_EFFECTS = frozenset({"read", "proposal", "narrative", "validated_write"})
_CONTRACT_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_CONTRACT_SCHEMA_KEYS = frozenset(
    {
        "type",
        "additionalProperties",
        "required",
        "properties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "anyOf",
    }
)


def _validate_contract_schema(schema: Any) -> None:
    def contains_non_finite_number(value: Any) -> bool:
        if isinstance(value, float):
            return not math.isfinite(value)
        if isinstance(value, dict):
            return any(contains_non_finite_number(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_non_finite_number(child) for child in value)
        return False

    if not isinstance(schema, dict) or set(schema) - _CONTRACT_SCHEMA_KEYS:
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if contains_non_finite_number(schema):
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    schema_type = schema.get("type")
    if "type" in schema and schema_type not in _CONTRACT_TYPES:
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "additionalProperties" in schema and (
        schema_type != "object" or not isinstance(schema["additionalProperties"], bool)
    ):
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    properties = schema.get("properties")
    if "properties" in schema:
        if schema_type != "object" or not isinstance(properties, dict):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
            _validate_contract_schema(child)
    if "required" in schema:
        required = schema["required"]
        if (
            schema_type != "object"
            or not isinstance(required, list)
            or len(required) != len(set(required))
            or not all(isinstance(name, str) and name for name in required)
            or not isinstance(properties, dict)
            or not all(name in properties for name in required)
        ):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "items" in schema:
        if schema_type != "array":
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
        _validate_contract_schema(schema["items"])
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not schema["enum"]
    ):
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if (
            schema_type != "object"
            or not isinstance(alternatives, list)
            or not alternatives
            or not isinstance(properties, dict)
        ):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
        for alternative in alternatives:
            if not isinstance(alternative, dict) or set(alternative) != {"required"}:
                raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
            required = alternative["required"]
            if (
                not isinstance(required, list)
                or not required
                or len(required) != len(set(required))
                or not all(
                    isinstance(name, str) and name in properties for name in required
                )
            ):
                raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    for key in ("minimum", "maximum"):
        if key in schema and (
            schema_type not in {"integer", "number"}
            or not isinstance(schema[key], (int, float))
            or isinstance(schema[key], bool)
        ):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    for key in ("minLength", "maxLength"):
        if key in schema and (
            schema_type != "string"
            or not isinstance(schema[key], int)
            or isinstance(schema[key], bool)
            or schema[key] < 0
        ):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "minLength" in schema and "maxLength" in schema and schema["minLength"] > schema["maxLength"]:
        raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
    if "pattern" in schema:
        if schema_type != "string" or not isinstance(schema["pattern"], str):
            raise SetupConflict("canonical CRM operation contract has an unsupported contract shape")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise SetupConflict(
                "canonical CRM operation contract has an unsupported contract shape"
            ) from exc


def _validate_contract_payload(payload: Any, label: str) -> frozenset[str]:
    if not isinstance(payload, dict) or set(payload) != {"version", "operations"}:
        raise SetupConflict(f"{label} has an unsupported contract shape")
    operations = payload.get("operations")
    version = payload.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != 1
        or not isinstance(operations, dict)
        or not operations
    ):
        raise SetupConflict(f"{label} has an unsupported contract shape")
    for name, entry in operations.items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None
            or not isinstance(entry, dict)
            or set(entry) != {"description", "effect", "arguments"}
            or not isinstance(entry["description"], str)
            or not entry["description"]
            or entry["effect"] not in _CONTRACT_EFFECTS
        ):
            raise SetupConflict(f"{label} has an unsupported contract shape")
        try:
            _validate_contract_schema(entry["arguments"])
        except SetupConflict as exc:
            raise SetupConflict(f"{label} has an unsupported contract shape") from exc
        arguments = entry["arguments"]
        if arguments.get("type") != "object" or arguments.get("additionalProperties") is not False:
            raise SetupConflict(f"{label} has an unsupported contract shape")
    return frozenset(operations)


def _read_contract_snapshot(path: Path, label: str) -> ContractSnapshot:
    absolute = _validate_no_symlink_components(path, label, leaf_directory=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except FileNotFoundError as exc:
        raise SetupConflict(f"{label} is missing: {absolute}") from exc
    except OSError as exc:
        raise SetupConflict(f"could not open {label}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SetupConflict(f"{label} is missing or is not a regular file: {absolute}")
        chunks: list[bytes] = []
        remaining = CONTRACT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(contents) > CONTRACT_MAX_BYTES:
        raise SetupConflict(f"{label} is unexpectedly large")
    try:
        raw = contents.decode("utf-8")
    except UnicodeError as exc:
        raise SetupConflict(f"{label} is not valid UTF-8") from exc
    payload = _decode_json(raw, label)
    operations = _validate_contract_payload(payload, label)
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    return ContractSnapshot(
        path=absolute,
        contents=contents,
        digest=hashlib.sha256(contents).hexdigest(),
        identity=identity,
        operations=operations,
    )


def _contract_digest_path(path: Path, label: str) -> str:
    return _read_contract_snapshot(path, label).digest


def _capture_canonical_contract(repo: Path) -> ContractSnapshot:
    return _read_contract_snapshot(
        repo / CONTRACT_RELATIVE_PATH,
        "canonical CRM operation contract",
    )


def _canonical_contract_digest(repo: Path) -> str:
    return _capture_canonical_contract(repo).digest


def _verify_contract_source_unchanged(snapshot: ContractSnapshot) -> None:
    current = _read_contract_snapshot(snapshot.path, "canonical CRM operation contract")
    if current.identity != snapshot.identity or current.digest != snapshot.digest:
        raise SetupConflict("canonical CRM operation contract changed after validation")


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
    _validate_no_symlink_components(
        workspace, "OpenClaw workspace", leaf_directory=True
    )
    _validate_no_symlink_components(
        skills_root, "OpenClaw skills directory", leaf_directory=True
    )
    for name in SKILL_NAMES:
        _validate_skill_tree(skills_root / name, "installed skill directory")
    backup_root = Path(
        tempfile.mkdtemp(prefix="openhouse-skill-rollback-")
    ).resolve(strict=True)
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
        _validate_no_symlink_components(
            snapshot.workspace, "OpenClaw workspace", leaf_directory=True
        )
        _validate_no_symlink_components(
            skills_root, "OpenClaw skills directory", leaf_directory=True
        )
        skills_root.mkdir(parents=True, exist_ok=True)
        for name in SKILL_NAMES:
            target = skills_root / name
            if name in snapshot.existing_names:
                with tempfile.TemporaryDirectory(
                    prefix=".openhouse-skill-restore-", dir=skills_root.parent
                ) as staging_value:
                    staging_root = Path(staging_value)
                    staged = staging_root / name
                    quarantine = staging_root / f"current-{name}"
                    shutil.copytree(snapshot.backup_root / name, staged)
                    _validate_skill_tree(staged, "staged restored skill directory")
                    _validate_no_symlink_components(
                        target, "installed skill directory", leaf_directory=True
                    )
                    if target.exists():
                        target.rename(quarantine)
                    staged.rename(target)
                    if quarantine.exists():
                        _remove_installed_tree(quarantine)
                if not _skill_trees_match(snapshot.backup_root / name, target):
                    return False
            elif target.exists() or target.is_symlink():
                _validate_no_symlink_components(
                    target, "installed skill directory", leaf_directory=True
                )
                _remove_installed_tree(target)
                if target.exists() or target.is_symlink():
                    return False
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
    except (OSError, SetupConflict):
        return False


def _skill_trees_match(left: Path, right: Path) -> bool:
    def manifest(root: Path) -> dict[str, tuple[str, bytes]]:
        result: dict[str, tuple[str, bytes]] = {}
        _validate_skill_tree(root, "skill restoration verification tree")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            node = os.lstat(path)
            if stat.S_ISDIR(node.st_mode):
                result[relative] = ("directory", b"")
            elif stat.S_ISREG(node.st_mode):
                result[relative] = ("file", path.read_bytes())
            else:
                raise SetupConflict(
                    f"skill restoration verification found unsupported node: {path}"
                )
        return result

    try:
        return manifest(left) == manifest(right)
    except (OSError, SetupConflict):
        return False


def _discard_skill_snapshot(snapshot: SkillRollback) -> bool:
    deletion_failed = False
    try:
        shutil.rmtree(snapshot.backup_root)
    except OSError:
        deletion_failed = True
    try:
        os.lstat(snapshot.backup_root)
    except FileNotFoundError:
        return not deletion_failed
    except OSError:
        return False
    return False


def _write_recovery_manifest(
    skill_snapshot: SkillRollback,
    *,
    gateway_env: GatewayEnvSnapshot | None,
    config_values: list[ConfigValueSnapshot],
    crm_agent_id: str,
    agent: dict[str, Any] | None,
    approvals: set[str],
    diagnostic_agent_id: str,
    plugin_preexisting: bool,
    plugin_enabled: bool,
    plugin_source: Path,
) -> None:
    payload = {
        "gatewayEnv": (
            {
                "path": str(gateway_env.path),
                "existed": gateway_env.existed,
                "mode": gateway_env.mode,
                "contentsBase64": base64.b64encode(gateway_env.contents).decode("ascii"),
            }
            if gateway_env is not None
            else None
        ),
        "config": [
            {"path": item.path, "existed": item.existed, "value": item.value}
            for item in config_values
        ],
        "agent": agent,
        "crmAgent": {
            "id": crm_agent_id,
            "existed": agent is not None,
            "snapshot": agent,
        },
        "diagnosticAgent": {"id": diagnostic_agent_id, "existed": False},
        "skills": {
            "workspace": str(skill_snapshot.workspace),
            "workspaceExisted": skill_snapshot.workspace_existed,
            "skillsRootExisted": skill_snapshot.skills_root_existed,
            "existingNames": sorted(skill_snapshot.existing_names),
        },
        "approvals": sorted(approvals),
        "plugin": {
            "preexisting": plugin_preexisting,
            "enabled": plugin_enabled,
            "source": str(plugin_source) if plugin_preexisting else None,
        },
    }
    path = skill_snapshot.backup_root / "transaction-state.json"
    _write_bytes_exclusive(
        path,
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        0o600,
    )


def _write_bytes_exclusive(path: Path, contents: bytes, mode: int = 0o644) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_skills(
    repo: Path,
    workspace: Path,
    *,
    dry_run: bool,
    contract_snapshot: ContractSnapshot | None = None,
) -> list[Path]:
    sources = [repo / "skills" / name for name in SKILL_NAMES]
    skills_root = workspace / "skills"
    targets = [skills_root / name for name in SKILL_NAMES]
    created_parent_dirs: list[Path] = []
    try:
        for source in sources:
            if not source.exists():
                raise SetupConflict(f"shipped skill directory is missing: {source}")
            _validate_skill_tree(source, "shipped skill directory")
        _validate_no_symlink_components(
            workspace, "OpenClaw workspace", leaf_directory=True
        )
        _validate_no_symlink_components(
            skills_root, "OpenClaw skills directory", leaf_directory=True
        )
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
                if name == "crm-db-operations" and contract_snapshot is not None:
                    source_root = _absolute_lexical_path(source)

                    def ignore_captured_contract(directory, names):
                        return (
                            ["contract.json"]
                            if _absolute_lexical_path(Path(directory)) == source_root
                            and "contract.json" in names
                            else []
                        )

                    shutil.copytree(
                        source,
                        staged_skills / name,
                        ignore=ignore_captured_contract,
                    )
                    _write_bytes_exclusive(
                        staged_skills / name / "contract.json",
                        contract_snapshot.contents,
                    )
                else:
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
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"unsupported JSON constant: {value}")
            ),
        )
    except ValueError as exc:
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
    agents_commands = ("add", "delete", "list") + (
        ("bind",) if options.bind_discord else ()
    )
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
        (
            ["openclaw", "agents", "delete", "--help"],
            "agents delete",
            ("--force", "--json"),
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
        (["openclaw", "gateway", "--help"], "gateway", ("restart", "call")),
        (
            ["openclaw", "gateway", "call", "--help"],
            "gateway call",
            ("--params", "--timeout", "--json"),
        ),
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
        configured_path = _validate_no_symlink_components(
            Path(configured), "configured agent workspace", leaf_directory=True
        )
        requested_path = _validate_no_symlink_components(
            requested, "requested agent workspace", leaf_directory=True
        )
        return configured_path == requested_path
    except (OSError, SetupConflict):
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
    if errors:
        return errors
    try:
        roster = _read_agent_roster(
            cli, allow_missing=False, label="agent rollback verification roster"
        )
        agent = next(
            (record for record in roster.records if record.get("id") == agent_id),
            None,
        )
        configured_workspace = (
            agent.get("workspace") or agent.get("workspacePath")
            if agent is not None
            else None
        )
        if agent is None or not _same_workspace(configured_workspace, workspace):
            return list(changed_fields)
        for field in changed_fields:
            existed = field in snapshot
            if (field in agent) != existed or (
                existed and agent[field] != snapshot[field]
            ):
                errors.append(field)
    except (OSError, SetupConflict):
        return list(changed_fields)
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


def _read_config_snapshot(cli: OpenClawCLI, path: str) -> ConfigValueSnapshot:
    result = cli.run(["openclaw", "config", "get", path, "--json"])
    if result.returncode != 0:
        if _is_missing_config_path(result, path):
            return ConfigValueSnapshot(path, False, None)
        detail = (result.stderr or result.stdout).strip()
        raise SetupConflict(f"could not snapshot {path}: {detail}")
    return ConfigValueSnapshot(path, True, _json(result, f"{path} snapshot"))


def _binding_agent_ids(snapshot: ConfigValueSnapshot) -> set[str]:
    if not snapshot.existed:
        return set()
    if not isinstance(snapshot.value, list):
        raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
    agent_ids: set[str] = set()
    for entry in snapshot.value:
        if not isinstance(entry, dict):
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
        agent_id = _require_canonical_agent_id(
            entry.get("agentId"), "OpenClaw binding agentId"
        )
        agent_ids.add(agent_id)
    return agent_ids


def _verify_diagnostic_agent_unbound(cli: OpenClawCLI, agent_id: str) -> None:
    current = _read_config_snapshot(cli, "bindings")
    if agent_id in _binding_agent_ids(current):
        raise SetupConflict(
            "OpenClaw did not keep the setup diagnostic agent unbound"
        )


def _config_value_matches(cli: OpenClawCLI, snapshot: ConfigValueSnapshot) -> bool:
    try:
        current = _read_config_snapshot(cli, snapshot.path)
    except (OSError, SetupConflict):
        return False
    return current.existed == snapshot.existed and current.value == snapshot.value


def _restore_config_snapshot(
    cli: OpenClawCLI, snapshot: ConfigValueSnapshot
) -> bool:
    if snapshot.existed:
        argv = [
            "openclaw",
            "config",
            "set",
            snapshot.path,
            json.dumps(snapshot.value, separators=(",", ":")),
            "--strict-json",
        ]
    else:
        argv = ["openclaw", "config", "unset", snapshot.path]
    try:
        result = cli.run(argv, mutate=True)
    except OSError:
        return False
    if result.returncode != 0 and snapshot.existed:
        return False
    # Missing-path unsets can legitimately report nonzero; authoritative readback
    # is what determines whether restoration succeeded.
    return _config_value_matches(cli, snapshot)


def _delete_agent_and_verify(
    cli: OpenClawCLI, agent_id: str, *, expected_workspace: Path
) -> bool:
    try:
        listed = _run_required(
            cli, ["openclaw", "agents", "list", "--json"], "agent cleanup list"
        )
        listed_records = _cli_agents(_json(listed, "agent cleanup list"))
        listed_ids = {record["id"] for record in listed_records}
        roster = _read_agent_roster(
            cli, allow_missing=True, label="agent cleanup config"
        )
        configured_ids = {record["id"] for record in roster.records}
        owned_records = [
            record
            for record in [*listed_records, *roster.records]
            if record["id"] == agent_id
        ]
        if any(
            not _same_workspace(
                record.get("workspace") or record.get("workspacePath"),
                expected_workspace,
            )
            for record in owned_records
        ):
            return False
        if agent_id in listed_ids or agent_id in configured_ids:
            deleted = cli.run(
                [
                    "openclaw",
                    "agents",
                    "delete",
                    agent_id,
                    "--force",
                    "--json",
                ],
                mutate=True,
            )
            if deleted.returncode != 0:
                return False
        listed = _run_required(
            cli,
            ["openclaw", "agents", "list", "--json"],
            "agent cleanup verification list",
        )
        roster = _read_agent_roster(
            cli, allow_missing=True, label="agent cleanup verification config"
        )
        return agent_id not in {
            record["id"]
            for record in _cli_agents(_json(listed, "agent cleanup verification list"))
        } and agent_id not in {record["id"] for record in roster.records}
    except (OSError, SetupConflict):
        return False


def _agent_is_absent(cli: OpenClawCLI, agent_id: str) -> bool:
    try:
        listed = _run_required(
            cli,
            ["openclaw", "agents", "list", "--json"],
            "agent absence verification list",
        )
        roster = _read_agent_roster(
            cli, allow_missing=True, label="agent absence verification config"
        )
        return agent_id not in {
            record["id"]
            for record in _cli_agents(_json(listed, "agent absence verification list"))
        } and agent_id not in {record["id"] for record in roster.records}
    except (OSError, SetupConflict):
        return False


def _new_diagnostic_agent_id(existing_ids: set[str]) -> str:
    for _ in range(32):
        candidate = f"openhouse-setup-probe-{secrets.token_hex(6)}"
        if candidate not in existing_ids:
            return candidate
    raise SetupConflict("could not allocate a unique setup diagnostic agent ID")


def _create_diagnostic_agent(
    cli: OpenClawCLI, agent_id: str, workspace: Path
) -> None:
    created = cli.run(
        [
            "openclaw",
            "agents",
            "add",
            agent_id,
            "--workspace",
            str(workspace),
            "--non-interactive",
            "--json",
        ],
        mutate=True,
    )
    if created.returncode != 0:
        raise SetupConflict(
            "Could not create the isolated setup diagnostic agent: "
            + (created.stderr or created.stdout).strip()
        )
    payload = _json(created, "diagnostic agents add --json")
    if not isinstance(payload, dict) or _require_canonical_agent_id(
        payload.get("agentId"), "diagnostic agents add --json agentId"
    ) != agent_id:
        raise SetupConflict("diagnostic agents add returned an incompatible agent record")
    roster = _read_agent_roster(
        cli, allow_missing=False, label="diagnostic agent configuration"
    )
    prefix = roster.prefixes.get(agent_id)
    if prefix is None:
        raise SetupConflict("OpenClaw did not expose the setup diagnostic agent")
    diagnostic = next(
        (record for record in roster.records if record.get("id") == agent_id), None
    )
    configured_workspace = (
        diagnostic.get("workspace") or diagnostic.get("workspacePath")
        if diagnostic is not None
        else None
    )
    if not _same_workspace(configured_workspace, workspace):
        raise SetupConflict("OpenClaw exposed the diagnostic agent with another workspace")
    for field, value in (
        ("skills", []),
        ("tools", DIAGNOSTIC_TOOL_POLICY),
    ):
        result = cli.run(
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.{field}",
                json.dumps(value, separators=(",", ":")),
                "--strict-json",
            ],
            mutate=True,
        )
        if result.returncode != 0:
            raise SetupConflict(
                f"Could not restrict the setup diagnostic agent {field}: "
                + (result.stderr or result.stdout).strip()
            )
def _remove_diagnostic_workspace(root: Path) -> bool:
    try:
        if not root.exists() and not root.is_symlink():
            return True
        _validate_no_symlink_components(
            root, "setup diagnostic workspace", leaf_directory=True
        )
        _validate_skill_tree(root, "setup diagnostic workspace")
        shutil.rmtree(root)
        return not root.exists() and not root.is_symlink()
    except (OSError, SetupConflict):
        return False


def _verify_empty_effective_tools(cli: OpenClawCLI, agent_id: str) -> None:
    params = json.dumps(
        {"sessionKey": f"agent:{agent_id}:main"}, separators=(",", ":")
    )
    result = _run_required(
        cli,
        [
            "openclaw",
            "gateway",
            "call",
            "tools.effective",
            "--params",
            params,
            "--timeout",
            "15000",
            "--json",
        ],
        "diagnostic effective tool inventory",
    )
    payload = _json(result, "diagnostic effective tool inventory")
    if (
        not isinstance(payload, dict)
        or payload.get("agentId") != agent_id
        or payload.get("profile") != "minimal"
        or not isinstance(payload.get("found"), list)
    ):
        raise SetupConflict(
            "OpenClaw did not expose an authoritative empty effective native-tool inventory"
        )
    for group in payload["found"]:
        if (
            not isinstance(group, list)
            or len(group) != 2
            or not isinstance(group[0], str)
            or not group[0]
            or not isinstance(group[1], list)
            or not all(isinstance(name, str) and name for name in group[1])
        ):
            raise SetupConflict(
                "OpenClaw did not expose an authoritative empty effective native-tool inventory"
            )
        if group[1]:
            raise SetupConflict(
                "OpenClaw diagnostic agent does not have an empty effective native-tool inventory"
            )


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


def _verify_dashboard_tool_block(
    cli: OpenClawCLI, agent_id: str, contract_operations: frozenset[str]
) -> None:
    if SETUP_BLOCK_PROBE_OPERATION in contract_operations:
        raise SetupConflict(
            "dashboard diagnostic sentinel unexpectedly exists in the canonical contract"
        )
    nonce = secrets.token_hex(16)
    result = cli.probe_dashboard_tool_block(agent_id=agent_id, nonce=nonce)
    response = f"{result.stdout}\n{result.stderr}".lower()
    expected_reason = "dashboard crm calls must use the verified tool invocation path"
    if result.returncode != 403 or expected_reason not in response:
        raise SetupConflict(
            "the bounded loopback diagnostic did not prove the dashboard CRM tool block; "
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


def _restore_exact_approvals(
    cli: OpenClawCLI, *, agent_id: str, original: set[str]
) -> bool:
    try:
        current_result = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "gateway approvals rollback inspection",
        )
        current = _validate_gateway_approval_payload(
            _json(current_result, "gateway approvals rollback inspection"),
            agent_id,
            require_effective=False,
        )
        for pattern in sorted(current - original):
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
                return False
        for pattern in sorted(original - current):
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
                return False
        verify = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "gateway approvals rollback verification",
        )
        restored = _validate_gateway_approval_payload(
            _json(verify, "gateway approvals rollback verification"),
            agent_id,
            require_effective=False,
        )
        return restored == original
    except (OSError, SetupConflict):
        return False


def _rollback_state_matches(
    cli: OpenClawCLI,
    *,
    options: SetupOptions,
    crm_agent_preexisting: bool,
    agent_snapshot: dict[str, Any] | None,
    diagnostic_agent_id: str | None,
    config_snapshots: list[ConfigValueSnapshot],
    approvals: set[str],
    plugin_source: Path,
    plugin_preexisting: bool,
    plugin_enabled: bool,
    skill_snapshot: SkillRollback,
    gateway_env_snapshot: GatewayEnvSnapshot | None,
) -> bool:
    try:
        listed = _run_required(
            cli,
            ["openclaw", "agents", "list", "--json"],
            "post-restart rollback agent list",
        )
        listed_records = _cli_agents(_json(listed, "post-restart rollback agent list"))
        roster = _read_agent_roster(
            cli, allow_missing=True, label="post-restart rollback agent config"
        )
        listed_by_id = {record["id"]: record for record in listed_records}
        configured_by_id = {record["id"]: record for record in roster.records}
        if diagnostic_agent_id is not None and (
            diagnostic_agent_id in listed_by_id
            or diagnostic_agent_id in configured_by_id
        ):
            return False
        if crm_agent_preexisting:
            listed_agent = listed_by_id.get(options.agent_id)
            configured_agent = configured_by_id.get(options.agent_id)
            if (
                listed_agent is None
                or configured_agent is None
                or not _same_workspace(
                    listed_agent.get("workspace")
                    or listed_agent.get("workspacePath"),
                    options.workspace,
                )
                or not _same_workspace(
                    configured_agent.get("workspace")
                    or configured_agent.get("workspacePath"),
                    options.workspace,
                )
            ):
                return False
            expected_fields = agent_snapshot or {}
            for field in ("skills", "tools", "sandbox"):
                if (field in configured_agent) != (field in expected_fields):
                    return False
                if field in expected_fields and (
                    configured_agent[field] != expected_fields[field]
                ):
                    return False
        elif options.agent_id in listed_by_id or options.agent_id in configured_by_id:
            return False

        for snapshot in config_snapshots:
            if not _config_value_matches(cli, snapshot):
                return False

        approvals_result = _run_required(
            cli,
            ["openclaw", "approvals", "get", "--gateway", "--json"],
            "post-restart rollback approvals",
        )
        restored_approvals = _validate_gateway_approval_payload(
            _json(approvals_result, "post-restart rollback approvals"),
            options.agent_id,
            require_effective=False,
        )
        if restored_approvals != approvals:
            return False

        plugins = _run_required(
            cli,
            ["openclaw", "plugins", "list", "--json"],
            "post-restart rollback plugin inventory",
        )
        present, enabled = _inspect_plugin_inventory(
            _json(plugins, "post-restart rollback plugin inventory"),
            plugin_source,
            require_present=plugin_preexisting,
        )
        if present != plugin_preexisting or (
            present and enabled != plugin_enabled
        ):
            return False

        skills_root = skill_snapshot.workspace / "skills"
        for name in SKILL_NAMES:
            target = skills_root / name
            if name in skill_snapshot.existing_names:
                if not _skill_trees_match(skill_snapshot.backup_root / name, target):
                    return False
            elif target.exists() or target.is_symlink():
                return False

        if gateway_env_snapshot is not None:
            current_env = _snapshot_gateway_env(gateway_env_snapshot.path)
            if current_env != gateway_env_snapshot:
                return False
        return True
    except (OSError, SetupConflict):
        return False


def configure_openclaw(options: SetupOptions, cli: OpenClawCLI) -> SetupResult:
    messages: list[str] = []
    rollback: AgentRollback | None = None
    skill_rollback: SkillRollback | None = None
    gateway_env_snapshot: GatewayEnvSnapshot | None = None
    config_snapshots: dict[str, ConfigValueSnapshot] = {}
    config_mutations: list[str] = []
    binding_snapshot: ConfigValueSnapshot | None = None
    binding_mutation_attempted = False
    legacy_token_mutation_attempted = False
    plugin_source: Path | None = None
    plugin_preexisting = False
    plugin_previously_enabled = False
    plugin_install_attempted = False
    plugin_enable_attempted = False
    plugin_allow_original: list[str] | None = None
    plugin_allow_snapshot: ConfigValueSnapshot | None = None
    plugin_allow_mutation_attempted = False
    approvals_original: set[str] = set()
    approvals_mutation_attempted = False
    crm_agent_preexisting = False
    crm_agent_creation_attempted = False
    diagnostic_agent_id: str | None = None
    diagnostic_root: Path | None = None
    diagnostic_agent_creation_attempted = False
    gateway_restart_attempted = False
    skill_snapshot_cleanup_failed = False
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
        contract_snapshot = _capture_canonical_contract(repo)
        contract_digest = contract_snapshot.digest
        if SETUP_BLOCK_PROBE_OPERATION in contract_snapshot.operations:
            raise SetupConflict(
                "dashboard diagnostic sentinel unexpectedly exists in the canonical contract"
            )
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
        plugin_allow_snapshot = ConfigValueSnapshot(
            "plugins.allow",
            plugin_allow_original is not None,
            plugin_allow_original,
        )
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
        crm_agent_preexisting = configured_agent is not None
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
        approvals_original = set(existing_patterns)
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
                "Would run the bounded dashboard-block behavior diagnostic with an "
                "operation absent from the production contract."
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

        # Capture every setup-owned surface before the first target mutation.
        skill_rollback = _snapshot_installed_skills(options.workspace)
        config_snapshots[CRM_URL_CONFIG_PATH] = _read_config_snapshot(
            cli, CRM_URL_CONFIG_PATH
        )
        if token:
            config_snapshots[TOKEN_CONFIG_PATH] = _read_config_snapshot(
                cli, TOKEN_CONFIG_PATH
            )
            config_snapshots[LEGACY_TOKEN_CONFIG_PATH] = _read_config_snapshot(
                cli, LEGACY_TOKEN_CONFIG_PATH
            )
            gateway_env_snapshot = _snapshot_gateway_env(gateway_env_path)
        binding_snapshot = _read_config_snapshot(cli, "bindings")
        diagnostic_agent_id = _new_diagnostic_agent_id(
            {
                *(record["id"] for record in agents),
                *(record["id"] for record in configured_agents),
                *_binding_agent_ids(binding_snapshot),
            }
        )
        if configured_agent is not None:
            rollback = AgentRollback(
                snapshot=_managed_agent_snapshot(configured_agent), changed_fields=[]
            )
        _write_recovery_manifest(
            skill_rollback,
            gateway_env=gateway_env_snapshot,
            config_values=[
                *config_snapshots.values(),
                binding_snapshot,
                plugin_allow_snapshot,
            ],
            crm_agent_id=options.agent_id,
            agent=configured_agent,
            approvals=approvals_original,
            diagnostic_agent_id=diagnostic_agent_id,
            plugin_preexisting=plugin_preexisting,
            plugin_enabled=plugin_previously_enabled,
            plugin_source=plugin_source,
        )

        if token and gateway_env_path is not None:
            _upsert_gateway_env(gateway_env_path, token)

        sync_skills(
            repo,
            options.workspace,
            dry_run=False,
            contract_snapshot=contract_snapshot,
        )
        _verify_contract_source_unchanged(contract_snapshot)
        _verify_installed_contract(options.workspace, contract_digest)
        messages.append(
            f"Installed CRM skills in {options.workspace / 'skills'} and verified "
            f"contract.json SHA-256 {contract_digest}"
        )

        plugin_install_attempted = True
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
        messages.append("Linked the bundled OpenClaw CRM plugin")

        plugin_enable_attempted = True
        enable_plugin = cli.run(
            ["openclaw", "plugins", "enable", PLUGIN_ID],
            mutate=True,
        )
        if enable_plugin.returncode != 0:
            raise SetupConflict(
                "Bundled OpenClaw CRM plugin enablement failed: "
                + (enable_plugin.stderr or enable_plugin.stdout).strip()
            )
        messages.append("Enabled the bundled OpenClaw CRM plugin")

        if plugin_allow_original is not None and PLUGIN_ID not in plugin_allow_original:
            plugin_allow_mutation_attempted = True
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
            messages.append("Included the bundled CRM plugin in plugins.allow")

        expected_plugin_allow = (
            None
            if plugin_allow_original is None
            else list(dict.fromkeys([*plugin_allow_original, PLUGIN_ID]))
        )
        if _read_plugin_allowlist(cli) != expected_plugin_allow:
            if plugin_allow_original is None and plugin_allow_snapshot is not None:
                plugin_allow_mutation_attempted = True
                if not _restore_config_snapshot(cli, plugin_allow_snapshot):
                    raise SetupConflict(
                        "OpenClaw changed plugins.allow outside the exact setup policy"
                    )
                messages.append(
                    "Preserved the previously absent global plugins.allow policy"
                )
            else:
                raise SetupConflict(
                    "OpenClaw changed plugins.allow outside the exact setup policy"
                )

        create_actions = [
            action for action in initial_actions if action.argv[1:3] == ["agents", "add"]
        ]
        for action in create_actions:
            crm_agent_creation_attempted = True
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
            if targets_agent_config and rollback is not None:
                field = action.argv[3][len(prefix) + 1 :]
                if (
                    field in {"skills", "tools", "sandbox"}
                    and field not in rollback.changed_fields
                ):
                    rollback.changed_fields.append(field)
            if (
                action.argv[1:3] == ["config", "set"]
                and action.argv[3] in config_snapshots
                and action.argv[3] not in config_mutations
            ):
                config_mutations.append(action.argv[3])
            if action.argv[1:3] == ["agents", "bind"]:
                binding_mutation_attempted = True
            if action.argv[1:3] == ["approvals", "allowlist"]:
                approvals_mutation_attempted = True
            result = _run_action(cli, action)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise SetupConflict(f"{action.description} failed: {detail}")
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
                legacy_token_mutation_attempted = True
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

        _verify_contract_source_unchanged(contract_snapshot)
        _verify_installed_contract(options.workspace, contract_snapshot.digest)
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
        _validate_runtime_plugin(
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

        diagnostic_root = Path(
            tempfile.mkdtemp(prefix="openhouse-setup-probe-")
        ).resolve(strict=True)
        diagnostic_root.chmod(0o700)
        diagnostic_workspace = diagnostic_root / "workspace"
        diagnostic_workspace.mkdir(mode=0o700)
        diagnostic_agent_creation_attempted = True
        _create_diagnostic_agent(cli, diagnostic_agent_id, diagnostic_workspace)
        _verify_diagnostic_agent_unbound(cli, diagnostic_agent_id)
        diagnostic_validate = _run_required(
            cli,
            ["openclaw", "config", "validate", "--json"],
            "diagnostic config validate --json",
        )
        _json(diagnostic_validate, "diagnostic config validate")

        gateway_restart_attempted = True
        restart = cli.run(["openclaw", "gateway", "restart"], mutate=True)
        if restart.returncode != 0:
            raise SetupConflict(
                f"Gateway restart failed: {(restart.stderr or restart.stdout).strip()}"
            )
        _verify_contract_source_unchanged(contract_snapshot)
        _verify_installed_contract(options.workspace, contract_snapshot.digest)
        _verify_empty_effective_tools(cli, diagnostic_agent_id)
        _verify_client_tool_capability(cli, diagnostic_agent_id)
        _verify_dashboard_tool_block(
            cli, options.agent_id, contract_snapshot.operations
        )
        if not _delete_agent_and_verify(
            cli, diagnostic_agent_id, expected_workspace=diagnostic_workspace
        ):
            raise SetupConflict(
                "Could not delete and verify absence of the setup diagnostic agent"
            )
        if not _remove_diagnostic_workspace(diagnostic_root):
            raise SetupConflict("Could not remove the setup diagnostic workspace")
        cleanup_restart = cli.run(
            ["openclaw", "gateway", "restart"], mutate=True
        )
        if cleanup_restart.returncode != 0:
            raise SetupConflict(
                "Gateway restart after diagnostic-agent cleanup failed: "
                + (cleanup_restart.stderr or cleanup_restart.stdout).strip()
            )
        if not _agent_is_absent(cli, diagnostic_agent_id):
            raise SetupConflict(
                "OpenClaw did not retain diagnostic-agent cleanup after restart"
            )
        if skill_rollback is not None:
            if not _discard_skill_snapshot(skill_rollback):
                skill_snapshot_cleanup_failed = True
                raise SetupConflict(
                    "Could not securely remove and verify deletion of the private setup "
                    "recovery backup"
                )
            skill_rollback = None
        rollback = None
        messages.append(
            "Validated the native CRM tool, required CRM outcome hooks, dashboard "
            "tool block, request-scoped client tools, and restricted agent configuration, "
            "then restarted the OpenClaw Gateway for validation and diagnostic cleanup. "
            "Runtime CRM verification is still required: "
            "python scripts/doctor.py --live-agent --live-crm"
        )
        if not options.bind_discord:
            messages.append(
                "Optional Discord binding: openclaw agents bind --agent "
                f"{options.agent_id} --bind discord:ACCOUNT --json"
            )
        return SetupResult(True, messages)
    except (SetupConflict, OSError) as exc:
        rollback_failed = skill_snapshot_cleanup_failed

        # Reverse the disposable diagnostic state first. Its random ID and exact
        # workspace are both checked before any destructive agent operation.
        if diagnostic_agent_creation_attempted and diagnostic_agent_id is not None:
            diagnostic_workspace = (
                diagnostic_root / "workspace"
                if diagnostic_root is not None
                else Path("/__openhouse_missing_diagnostic_workspace__")
            )
            if not _delete_agent_and_verify(
                cli,
                diagnostic_agent_id,
                expected_workspace=diagnostic_workspace,
            ):
                rollback_failed = True
                messages.append(
                    "Could not delete or verify absence of the setup diagnostic agent."
                )
        if diagnostic_root is not None and not _remove_diagnostic_workspace(
            diagnostic_root
        ):
            rollback_failed = True
            messages.append("Could not remove the setup diagnostic workspace.")

        agent_target_safe = True
        if crm_agent_preexisting and (
            rollback is not None
            or config_mutations
            or binding_mutation_attempted
            or approvals_mutation_attempted
            or legacy_token_mutation_attempted
        ):
            try:
                current_roster = _read_agent_roster(
                    cli, allow_missing=False, label="rollback ownership check"
                )
                current_agent = next(
                    (
                        record
                        for record in current_roster.records
                        if record.get("id") == options.agent_id
                    ),
                    None,
                )
                agent_target_safe = current_agent is not None and _same_workspace(
                    current_agent.get("workspace")
                    or current_agent.get("workspacePath"),
                    options.workspace,
                )
            except (OSError, SetupConflict):
                agent_target_safe = False
            if not agent_target_safe:
                rollback_failed = True
                messages.append(
                    "Could not fully restore setup-owned CRM configuration because "
                    "the dedicated-agent workspace changed during setup."
                )

        if agent_target_safe:
            if legacy_token_mutation_attempted:
                snapshot = config_snapshots.get(LEGACY_TOKEN_CONFIG_PATH)
                if snapshot is None or not _restore_config_snapshot(cli, snapshot):
                    rollback_failed = True
                    messages.append(
                        "Could not restore the previous legacy CRM token setting."
                    )
            if approvals_mutation_attempted:
                if _restore_exact_approvals(
                    cli, agent_id=options.agent_id, original=approvals_original
                ):
                    messages.append(
                        "Restored gateway executable approvals after setup failed."
                    )
                else:
                    rollback_failed = True
                    messages.append(
                        "Could not fully restore gateway executable approvals after setup failed."
                    )
            if binding_mutation_attempted and binding_snapshot is not None:
                if not _restore_config_snapshot(cli, binding_snapshot):
                    rollback_failed = True
                    messages.append("Could not restore the previous Discord bindings.")
            for path in reversed(config_mutations):
                snapshot = config_snapshots[path]
                if not _restore_config_snapshot(cli, snapshot):
                    rollback_failed = True
                    messages.append(f"Could not restore the previous {path} value.")
            if crm_agent_preexisting and rollback is not None and rollback.changed_fields:
                rollback_errors = _restore_managed_agent_fields(
                    cli,
                    agent_id=options.agent_id,
                    workspace=options.workspace,
                    snapshot=rollback.snapshot,
                    changed_fields=rollback.changed_fields,
                )
                if rollback_errors:
                    rollback_failed = True
                    messages.append(
                        "Could not fully restore the previous dedicated-agent "
                        "configuration. Failed fields: " + ", ".join(rollback_errors)
                    )
                else:
                    messages.append(
                        "Restored the previous dedicated-agent configuration after "
                        "setup failed."
                    )
            elif not crm_agent_preexisting and crm_agent_creation_attempted:
                if not _delete_agent_and_verify(
                    cli,
                    options.agent_id,
                    expected_workspace=options.workspace,
                ):
                    rollback_failed = True
                    messages.append(
                        "Could not delete or verify absence of the newly created CRM agent."
                    )

        if plugin_allow_mutation_attempted and plugin_allow_snapshot is not None:
            if not _restore_config_snapshot(cli, plugin_allow_snapshot):
                rollback_failed = True
                messages.append("Could not restore the previous plugins.allow value.")
        if plugin_install_attempted and not plugin_preexisting:
            try:
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
            except OSError:
                removed_plugin = None
            if removed_plugin is None or removed_plugin.returncode != 0:
                rollback_failed = True
                messages.append(
                    "Could not remove the newly linked CRM plugin after setup failed."
                )
        elif plugin_preexisting and (
            plugin_install_attempted or plugin_enable_attempted
        ):
            if plugin_install_attempted:
                if plugin_source is None:
                    restored_source = CommandResult(1, "", "missing plugin source")
                else:
                    try:
                        restored_source = cli.run(
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
                    except OSError:
                        restored_source = None
                if restored_source is None or restored_source.returncode != 0:
                    rollback_failed = True
                    messages.append(
                        "Could not restore the previous CRM plugin source link."
                    )
            try:
                restore_enablement = cli.run(
                    [
                        "openclaw",
                        "plugins",
                        "enable" if plugin_previously_enabled else "disable",
                        PLUGIN_ID,
                    ],
                    mutate=True,
                )
            except OSError:
                restore_enablement = None
            if restore_enablement is None or restore_enablement.returncode != 0:
                rollback_failed = True
                messages.append("Could not restore the previous CRM plugin enablement.")

        if (
            (plugin_install_attempted or plugin_enable_attempted)
            and plugin_allow_snapshot is not None
            and not _restore_config_snapshot(cli, plugin_allow_snapshot)
        ):
            rollback_failed = True
            messages.append("Could not restore the exact previous plugins.allow value.")

        if plugin_install_attempted and plugin_source is not None:
            try:
                inventory = _run_required(
                    cli,
                    ["openclaw", "plugins", "list", "--json"],
                    "plugin rollback verification",
                )
                restored_present, restored_enabled = _inspect_plugin_inventory(
                    _json(inventory, "plugin rollback verification"),
                    plugin_source,
                    require_present=plugin_preexisting,
                )
                if restored_present != plugin_preexisting or (
                    restored_present and restored_enabled != plugin_previously_enabled
                ):
                    raise SetupConflict("plugin state did not match its snapshot")
            except (OSError, SetupConflict):
                rollback_failed = True
                messages.append("Could not verify the previous CRM plugin state.")

        if skill_rollback is not None:
            if _restore_installed_skills(skill_rollback):
                messages.append(
                    "Restored the previous installed CRM skills after setup failed."
                )
            else:
                rollback_failed = True
                messages.append(
                    "Could not fully restore the previous installed CRM skills."
                )
        if gateway_env_snapshot is not None and not _restore_gateway_env(
            gateway_env_snapshot
        ):
            rollback_failed = True
            messages.append("Could not restore the OpenClaw gateway environment.")

        # A nonzero restart can still have partially reloaded configuration, so
        # restart after restoration whenever the first restart was attempted.
        if gateway_restart_attempted:
            try:
                restored_gateway = cli.run(
                    ["openclaw", "gateway", "restart"], mutate=True
                )
            except OSError:
                restored_gateway = None
            if restored_gateway is None or restored_gateway.returncode != 0:
                rollback_failed = True
                messages.append(
                    "Could not restart the OpenClaw Gateway after restoring setup state."
                )
            elif (
                skill_rollback is None
                or plugin_source is None
                or not _rollback_state_matches(
                    cli,
                    options=options,
                    crm_agent_preexisting=crm_agent_preexisting,
                    agent_snapshot=rollback.snapshot if rollback is not None else None,
                    diagnostic_agent_id=diagnostic_agent_id,
                    config_snapshots=[
                        *config_snapshots.values(),
                        *(
                            [binding_snapshot]
                            if binding_snapshot is not None
                            else []
                        ),
                        *(
                            [plugin_allow_snapshot]
                            if plugin_allow_snapshot is not None
                            else []
                        ),
                    ],
                    approvals=approvals_original,
                    plugin_source=plugin_source,
                    plugin_preexisting=plugin_preexisting,
                    plugin_enabled=plugin_previously_enabled,
                    skill_snapshot=skill_rollback,
                    gateway_env_snapshot=gateway_env_snapshot,
                )
            ):
                rollback_failed = True
                messages.append(
                    "Could not verify restored setup state after restarting the OpenClaw Gateway."
                )

        if skill_rollback is not None:
            if rollback_failed:
                messages.append(
                    f"Recovery backup retained at {skill_rollback.backup_root}"
                )
                messages.append(
                    "Restore only after removing symlinks and verifying the target workspace."
                )
                messages.append(
                    "After recovery, securely remove that exact private backup directory."
                )
            else:
                if not _discard_skill_snapshot(skill_rollback):
                    rollback_failed = True
                    messages.append(
                        "Could not securely remove and verify deletion of the private "
                        "setup recovery backup."
                    )
                    messages.append(
                        f"Recovery backup retained at {skill_rollback.backup_root}"
                    )
                    messages.append(
                        "Restore only after removing symlinks and verifying the target workspace."
                    )
                    messages.append(
                        "After recovery, securely remove that exact private backup directory."
                    )
            skill_rollback = None
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
