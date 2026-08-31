#!/usr/bin/env python3
"""Configure a dedicated, restricted OpenClaw agent for this CRM."""

from __future__ import annotations

import sys as _bootstrap_sys

if __name__ == "__main__" and not _bootstrap_sys.flags.isolated:
    _bootstrap_sys.stderr.write(
        "Safe startup requires isolated Python mode. Run exactly:\n"
        "  python3 -I scripts/setup_openclaw.py\n"
        "Add any options you need after the script name.\n"
    )
    raise SystemExit(2)


_BOOTSTRAP_ORIGINAL_PATH = _bootstrap_sys.path[:]
_BOOTSTRAP_VERSION = (
    f"python{_bootstrap_sys.version_info.major}.{_bootstrap_sys.version_info.minor}"
)
_BOOTSTRAP_ZIP = (
    f"python{_bootstrap_sys.version_info.major}{_bootstrap_sys.version_info.minor}.zip"
)
_BOOTSTRAP_NORMALIZED = [
    item.replace("\\", "/").rstrip("/")
    for item in _bootstrap_sys.path
    if isinstance(item, str) and item
]
_BOOTSTRAP_STDLIB = ""
for _bootstrap_index, _bootstrap_item in enumerate(_BOOTSTRAP_NORMALIZED):
    if not _bootstrap_item.endswith("/" + _BOOTSTRAP_VERSION):
        continue
    _bootstrap_parent = _bootstrap_item[: -len(_BOOTSTRAP_VERSION)].rstrip("/")
    if (
        _bootstrap_parent + "/" + _BOOTSTRAP_ZIP
        in _BOOTSTRAP_NORMALIZED[:_bootstrap_index]
    ):
        _BOOTSTRAP_STDLIB = _bootstrap_item
if not _BOOTSTRAP_STDLIB:
    raise RuntimeError("could not establish an isolated Python standard library")
_BOOTSTRAP_STDLIB_PARENT = _BOOTSTRAP_STDLIB.rsplit("/", 1)[0]
_BOOTSTRAP_ALLOWED = {
    _BOOTSTRAP_STDLIB_PARENT + "/" + _BOOTSTRAP_ZIP,
    _BOOTSTRAP_STDLIB,
    _BOOTSTRAP_STDLIB + "/lib-dynload",
}
_bootstrap_sys.path[:] = [
    item
    for item in _BOOTSTRAP_ORIGINAL_PATH
    if isinstance(item, str)
    and item
    and item.replace("\\", "/").rstrip("/") in _BOOTSTRAP_ALLOWED
]
_bootstrap_sys.dont_write_bytecode = True
_bootstrap_sys.pycache_prefix = _BOOTSTRAP_STDLIB + "/.openhouse-disabled-pycache"

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
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

if __name__ != "__main__":
    sys.path[:] = _BOOTSTRAP_ORIGINAL_PATH


_SOURCE_ONLY_PYCACHE = tempfile.TemporaryDirectory(prefix="openhouse-source-only-")
sys.dont_write_bytecode = True
sys.pycache_prefix = _SOURCE_ONLY_PYCACHE.name
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONPYCACHEPREFIX"] = _SOURCE_ONLY_PYCACHE.name


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
    "before_prompt_build",
    "before_tool_call",
    "gateway_stop",
    "reply_payload_sending",
)
BEHAVIORAL_RUNTIME_CAPABILITIES = (
    "configured_agent_guard",
    "dashboard_channel_tool_block",
    "full_client_tool_schema",
    "internal_analysis_channel_tool_block",
    "internal_analysis_tool_free",
)
DASHBOARD_CHANNEL = "openhouse-dashboard"
SETUP_CAPABILITY_CHANNEL = "openhouse-setup-capability"
SETUP_AGENT_GUARD_CHANNEL = "openhouse-setup-agent-guard"
INTERNAL_ANALYSIS_CHANNEL = "openhouse-analysis"
SETUP_MARKER_TOOL = "openhouse_setup_marker_probe"
SETUP_AGENT_GUARD_OPERATION = "__openhouse_agent_guard_probe__"
GATEWAY_PROBE_TIMEOUT_SECONDS = 30
GATEWAY_PROBE_MAX_BYTES = 256 * 1024
SETUP_DEADLINE_SECONDS = 15 * 60
ROLLBACK_DEADLINE_SECONDS = 3 * 60
SETUP_STATE_FD_ENV = "OPENHOUSE_SETUP_STATE_FD"
MAX_SETUP_STATE_BYTES = 16 * 1024 * 1024
CONTRACT_MAX_BYTES = 1024 * 1024
CONTRACT_RELATIVE_PATH = Path("skills") / "crm-db-operations" / "contract.json"
CLIENT_TOOLS_RELATIVE_PATH = (
    Path("skills") / "crm-db-operations" / "client_tools.py"
)
CRM_URL_CONFIG_PATH = 'skills.entries["crm-db-operations"].env.CRM_API_URL'
PLUGIN_CONFIG_PATH = 'plugins.entries["openhouse-crm"].config'
PLUGIN_HOOKS_PATH = 'plugins.entries["openhouse-crm"].hooks'
TOOL_SEARCH_CONFIG_PATH = "tools.toolSearch"
DIAGNOSTIC_TOOL_POLICY = {
    "profile": "full",
    "allow": [SETUP_MARKER_TOOL],
    "deny": [PLUGIN_TOOL, "exec"],
}
DESIRED_SANDBOX = {"mode": "off"}
CRM_THINKING_DEFAULT = "off"
CRM_LOCAL_MODEL_LEAN = False
MANAGED_AGENT_FIELDS = (
    "skills",
    "tools",
    "sandbox",
    "thinkingDefault",
    "experimental",
)
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
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MATERIAL_SHARED_PATHS = (
    Path("scripts"),
    Path("backend/app/briefing_contract.py"),
)
MAX_MATERIAL_ENTRIES = 2048
MAX_BINDINGS = 100
MAX_PRIVATE_RUNTIME_VALUE = 256
INERT_PYCACHE_FILE_RE = re.compile(r"[A-Za-z0-9_.-]{1,255}\.pyc")


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
class DiagnosticSessionHandle:
    agent_id: str
    key: str
    session_id: str


@dataclass
class DiagnosticSessionTracker:
    active: DiagnosticSessionHandle | None = None


@dataclass(frozen=True)
class AgentDeletionReport:
    complete: bool
    retry_restart_performed: bool
    retained_paths: tuple[str, ...]


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
    skill_anchor_sha256: dict[str, str] = field(default_factory=dict)
    manifest_anchor_sha256: str | None = None
    manifest_schema_version: int | None = None


@dataclass(frozen=True)
class ContractSnapshot:
    path: Path
    contents: bytes
    digest: str
    identity: tuple[int, int, int, int]
    operations: frozenset[str]


@dataclass(frozen=True)
class ClientToolsSnapshot:
    path: Path
    contents: bytes
    digest: str
    identity: tuple[int, int, int, int]
    tools: list[dict[str, Any]]


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
    runtime_verification: dict[str, Any] | None = None

    def render(self) -> str:
        return _redact_api_token("\n".join(self.messages))


class OpenClawCLI:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        setup_timeout_seconds: float = SETUP_DEADLINE_SECONDS,
        rollback_timeout_seconds: float = ROLLBACK_DEADLINE_SECONDS,
    ) -> None:
        if (
            not isinstance(setup_timeout_seconds, (int, float))
            or isinstance(setup_timeout_seconds, bool)
            or not math.isfinite(setup_timeout_seconds)
            or setup_timeout_seconds <= 0
            or not isinstance(rollback_timeout_seconds, (int, float))
            or isinstance(rollback_timeout_seconds, bool)
            or not math.isfinite(rollback_timeout_seconds)
            or rollback_timeout_seconds <= 0
        ):
            raise ValueError("setup and rollback time limits must be positive seconds")
        self._clock = clock
        self._rollback_timeout_seconds = float(rollback_timeout_seconds)
        self._phase = "setup"
        self._deadline = self._clock() + float(setup_timeout_seconds)

    def begin_rollback(self) -> None:
        """Start a fresh bounded window so an expired setup can still recover."""
        self._phase = "rollback"
        self._deadline = self._clock() + self._rollback_timeout_seconds

    def _timeout_result(self) -> CommandResult:
        if self._phase == "rollback":
            detail = (
                "OpenClaw rollback time limit expired; automatic recovery could not "
                "finish. Follow the retained recovery-backup instructions."
            )
        else:
            detail = (
                "OpenClaw setup time limit expired; automatic rollback will use its "
                "separate bounded recovery window."
            )
        return CommandResult(124, "", detail)

    def _remaining_timeout(self, *, cap: float | None = None) -> float | None:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            return None
        return remaining if cap is None else min(remaining, cap)

    def require_time(self) -> float:
        """Return the current phase budget or fail before local blocking work."""
        remaining = self._remaining_timeout()
        if remaining is None:
            raise SetupConflict(self._timeout_result().stderr)
        return remaining

    def run(self, args: list[str], *, mutate: bool = False) -> CommandResult:
        del mutate
        command = args if args and args[0] == "openclaw" else ["openclaw", *args]
        timeout = self._remaining_timeout()
        if timeout is None:
            return self._timeout_result()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return self._timeout_result()
        except FileNotFoundError as exc:
            return CommandResult(127, "", str(exc))
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def _post_gateway_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        channel: str,
        session_key: str | None = None,
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
        if session_key is not None:
            headers["x-openclaw-session-key"] = session_key
        gateway_token = os.environ.get("AGENT_GATEWAY_TOKEN", "")
        if gateway_token:
            headers["Authorization"] = f"Bearer {gateway_token}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = self._remaining_timeout(cap=GATEWAY_PROBE_TIMEOUT_SECONDS)
        if timeout is None:
            return self._timeout_result()
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=timeout) as response:
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

    def probe_client_tools(
        self,
        *,
        agent_id: str,
        nonce: str,
        tools: list[dict[str, Any]],
        production: bool = False,
        session_key: str | None = None,
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
        chat_path = os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions")
        if production:
            system_content = (
                f"First call the native {PLUGIN_TOOL} tool with operation generate_dashboard_insights "
                "and arguments containing only "
                f"probe_nonce {nonce}. After that attempt is blocked, "
                "call finish_crm_response exactly once with classification "
                "needs_clarification, the nonce as its message, and an empty "
                "evidence_call_ids array."
            )
        else:
            system_content = (
                "Call finish_crm_response exactly "
                "once with classification needs_clarification, the nonce as its "
                "message, and an empty evidence_call_ids array."
            )
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": f"setup-capability:{nonce}",
            "messages": [
                {
                    "role": "system",
                    "content": system_content,
                },
                {"role": "user", "content": f"Capability nonce: {nonce}"},
            ],
            "tools": tools,
            "tool_choice": "required",
            "max_completion_tokens": 256,
        }
        return self._post_gateway_json(
            chat_path,
            payload,
            channel=DASHBOARD_CHANNEL,
            session_key=session_key,
        )

    def probe_channel_prompt(
        self,
        *,
        agent_id: str,
        nonce: str,
        channel: str,
        session_key: str,
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
        chat_path = os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions")
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": f"setup-channel-marker:{channel}:{nonce}",
            "messages": [
                {"role": "user", "content": f"Setup channel probe {nonce}"}
            ],
            "tools": [],
            "tool_choice": "none",
            "max_completion_tokens": 32,
        }
        return self._post_gateway_json(
            chat_path,
            payload,
            channel=channel,
            session_key=session_key,
        )

    def probe_channel_marker_attempt(
        self,
        *,
        agent_id: str,
        nonce: str,
        channel: str,
        session_key: str,
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
        payload = {
            "tool": SETUP_MARKER_TOOL,
            "args": {"action": "attempt", "channel": channel, "nonce": nonce},
            "agentId": agent_id,
            "sessionKey": session_key,
            "idempotencyKey": f"setup-marker-attempt:{nonce}:{channel}",
        }
        return self._post_gateway_json("/tools/invoke", payload, channel=channel)

    def probe_analysis_tool_block(
        self,
        *,
        agent_id: str,
        nonce: str,
        production: bool = False,
        session_key: str | None = None,
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
        chat_path = os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions")
        content = (
            (
                f"Call the native {PLUGIN_TOOL} tool with operation generate_dashboard_insights "
                "and arguments containing only "
                f"probe_nonce {nonce}. Then reply briefly after the "
                "attempt is blocked."
            )
            if production
            else (
                "Reply briefly without calling tools. "
                f"Include this nonce in the reply: {nonce}."
            )
        )
        payload = {
            "model": f"openclaw/{agent_id}",
            "user": f"setup-analysis-capability:{nonce}",
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "tools": [],
            "tool_choice": "none",
            "max_completion_tokens": 128,
        }
        return self._post_gateway_json(
            chat_path,
            payload,
            channel=INTERNAL_ANALYSIS_CHANNEL,
            session_key=session_key,
        )

    def probe_channel_status(
        self,
        *,
        agent_id: str,
        nonce: str,
        channel: str,
        session_key: str | None = None,
    ) -> CommandResult:
        try:
            _loopback_gateway_base_url()
        except SetupConflict as exc:
            return CommandResult(503, "", str(exc))
        payload = {
            "tool": SETUP_MARKER_TOOL,
            "args": {"action": "status", "channel": channel, "nonce": nonce},
            "agentId": agent_id,
            "sessionKey": session_key
            or f"marker-status:setup-capability:{nonce}:{channel}",
            "idempotencyKey": f"setup-marker-status:{nonce}:{channel}",
        }
        return self._post_gateway_json(
            "/tools/invoke", payload, channel=SETUP_CAPABILITY_CHANNEL
        )

    def probe_configured_agent_guard(
        self, *, agent_id: str, nonce: str, session_key: str | None = None
    ) -> CommandResult:
        payload = {
            "tool": PLUGIN_TOOL,
            "args": {
                "operation": SETUP_AGENT_GUARD_OPERATION,
                "arguments": {},
            },
            "agentId": agent_id,
            "sessionKey": session_key or f"agent-guard:setup-capability:{nonce}",
            "idempotencyKey": f"setup-agent-guard:{nonce}",
        }
        return self._post_gateway_json(
            "/tools/invoke", payload, channel=SETUP_AGENT_GUARD_CHANNEL
        )

    def probe_production_channel_guard(
        self,
        *,
        agent_id: str,
        nonce: str,
        channel: str,
        session_key: str | None = None,
    ) -> CommandResult:
        payload = {
            "tool": PLUGIN_TOOL,
            "args": {
                "operation": "__openhouse_behavior_probe_status__",
                "arguments": {"nonce": nonce, "channel": channel},
            },
            "agentId": agent_id,
            "sessionKey": session_key
            or f"production-status:setup-capability:{nonce}:{channel}",
            "idempotencyKey": f"production-status:{nonce}:{channel}",
        }
        return self._post_gateway_json(
            "/tools/invoke", payload, channel=SETUP_CAPABILITY_CHANNEL
        )


class SetupConflict(RuntimeError):
    pass


LocalOperationRunner = Callable[[str, dict[str, Any], float], bool]
LocalQueryRunner = Callable[[str, dict[str, Any], float], dict[str, Any] | None]
MAX_LOCAL_WORKER_HEADER_BYTES = 1024 * 1024
MAX_LOCAL_WORKER_REQUEST_BYTES = MAX_SETUP_STATE_BYTES + (4 * 1024 * 1024)
MAX_LOCAL_WORKER_RESULT_BYTES = 8 * 1024 * 1024


_LOCAL_OPERATION_SOURCE = r"""
import hashlib, json, os, re, shutil, stat, sys
from pathlib import Path

MAX_HEADER = 1024 * 1024
MAX_REQUEST = 20 * 1024 * 1024
MAX_RESULT = 8 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_FILE = 16 * 1024 * 1024

def read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise OSError("truncated worker request")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

def decode(value, blobs):
    if isinstance(value, dict) and set(value) == {"__blob__"}:
        index = value["__blob__"]
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(blobs):
            raise ValueError("invalid worker blob reference")
        return blobs[index]
    if isinstance(value, dict):
        return {key: decode(item, blobs) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item, blobs) for item in value]
    return value

def request():
    raw_length = read_exact(sys.stdin.buffer, 8)
    header_length = int.from_bytes(raw_length, "big")
    if not 0 < header_length <= MAX_HEADER:
        raise ValueError("invalid worker header size")
    header_bytes = read_exact(sys.stdin.buffer, header_length)
    header = json.loads(header_bytes)
    if not isinstance(header, dict) or set(header) != {"version", "payload", "blobs"} or header["version"] != 1:
        raise ValueError("invalid worker header")
    descriptors = header["blobs"]
    if not isinstance(descriptors, list):
        raise ValueError("invalid worker blobs")
    total = 8 + header_length
    blobs = []
    for item in descriptors:
        if (
            not isinstance(item, dict)
            or set(item) != {"size", "sha256"}
            or not isinstance(item["size"], int)
            or isinstance(item["size"], bool)
            or item["size"] < 0
            or not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
        ):
            raise ValueError("invalid worker blob descriptor")
        total += item["size"]
        if total > MAX_REQUEST:
            raise ValueError("worker request too large")
        contents = read_exact(sys.stdin.buffer, item["size"])
        if hashlib.sha256(contents).hexdigest() != item["sha256"]:
            raise ValueError("worker blob digest mismatch")
        blobs.append(contents)
    if sys.stdin.buffer.read(1):
        raise ValueError("trailing worker request bytes")
    return decode(header["payload"], blobs)

def emit(result):
    body = json.dumps({"ok": True, "result": result}, separators=(",", ":")).encode()
    if len(body) > MAX_RESULT:
        raise ValueError("worker result too large")
    header = json.dumps(
        {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()},
        separators=(",", ":"),
    ).encode()
    sys.stdout.buffer.write(len(header).to_bytes(8, "big"))
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

def read_file(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        node = os.fstat(descriptor)
        if not stat.S_ISREG(node.st_mode):
            raise OSError("not a regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE:
                raise OSError("file exceeds worker bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (node.st_dev, node.st_ino, node.st_size, node.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OSError("file changed while read")
        return node, b"".join(chunks)
    finally:
        os.close(descriptor)

def normalized_mode(mode):
    return "100755" if mode & 0o111 else "100644"

def scan_tree(base, roots, *, include_directories=False, ignore_inert_pycache=False):
    base = Path(base)
    entries = []
    for raw_root in roots:
        relative_root = Path(raw_root)
        if relative_root.is_absolute() or ".." in relative_root.parts:
            raise OSError("invalid tree root")
        root = base if relative_root == Path(".") else base / relative_root
        root_node = os.lstat(root)
        if stat.S_ISLNK(root_node.st_mode):
            raise OSError("tree contains symlink")
        if stat.S_ISREG(root_node.st_mode):
            node, contents = read_file(root)
            entries.append({"path": relative_root.as_posix(), "kind": "file", "mode": normalized_mode(node.st_mode), "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()})
            continue
        if not stat.S_ISDIR(root_node.st_mode):
            raise OSError("unsupported tree root")
        if include_directories:
            entries.append({"path": relative_root.as_posix(), "kind": "directory", "mode": stat.S_IMODE(root_node.st_mode)})
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            directory_names.sort()
            file_names.sort()
            for name in list(directory_names):
                child = current_path / name
                child_node = os.lstat(child)
                if stat.S_ISLNK(child_node.st_mode) or not stat.S_ISDIR(child_node.st_mode):
                    raise OSError("tree contains unsupported directory")
                if name == "__pycache__" and ignore_inert_pycache:
                    cache_entries = list(child.iterdir())
                    if len(cache_entries) > MAX_ENTRIES:
                        raise OSError("cache too large")
                    for cache_entry in cache_entries:
                        cache_node = os.lstat(cache_entry)
                        if not stat.S_ISREG(cache_node.st_mode) or re.fullmatch(r"[A-Za-z0-9_.-]{1,255}\.pyc", cache_entry.name) is None:
                            raise OSError("unsupported cache entry")
                    directory_names.remove(name)
                    continue
                if include_directories:
                    entries.append({"path": child.relative_to(base).as_posix(), "kind": "directory", "mode": stat.S_IMODE(child_node.st_mode)})
            for name in file_names:
                child = current_path / name
                node, contents = read_file(child)
                entry = {"path": child.relative_to(base).as_posix(), "kind": "file", "mode": normalized_mode(node.st_mode), "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}
                if include_directories:
                    entry["permission"] = stat.S_IMODE(node.st_mode)
                entries.append(entry)
                if len(entries) > MAX_ENTRIES:
                    raise OSError("tree contains too many entries")
    entries.sort(key=lambda item: item["path"])
    return entries

def full_manifest(path):
    root = Path(path)
    entries = scan_tree(root, ["."], include_directories=True)
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def execute(payload):
    operation = payload.pop("operation")
    if operation == "copytree":
        source = Path(payload["source"])
        target = Path(payload["target"])
        ignore_kind = payload.get("ignore")
        def ignored(directory, names):
            result = ["__pycache__"] if "__pycache__" in names else []
            if ignore_kind == "inert_and_contract" and Path(directory) == source and "contract.json" in names:
                result.append("contract.json")
            return result
        shutil.copytree(source, target, ignore=ignored if ignore_kind else None)
        return True
    if operation == "rmtree":
        shutil.rmtree(Path(payload["path"]))
        return True
    if operation == "unlink":
        Path(payload["path"]).unlink()
        return True
    if operation == "verify_file":
        node, contents = read_file(Path(payload["path"]))
        identity = payload.get("identity")
        if stat.S_IMODE(node.st_mode) != int(payload["mode"]):
            raise OSError("file metadata mismatch")
        if identity is not None and [node.st_dev, node.st_ino] != identity:
            raise OSError("file identity changed")
        if contents != payload["contents"]:
            raise OSError("file contents mismatch")
        return True
    if operation in {"write_exclusive", "rewrite_existing"}:
        path = Path(payload["path"])
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= os.O_CREAT | os.O_EXCL if operation == "write_exclusive" else os.O_TRUNC
        descriptor = os.open(path, flags, int(payload["mode"]))
        try:
            node = os.fstat(descriptor)
            identity = payload.get("identity")
            if not stat.S_ISREG(node.st_mode):
                raise OSError("target is not regular")
            if identity is not None and [node.st_dev, node.st_ino] != identity:
                raise OSError("target identity changed")
            os.fchmod(descriptor, int(payload["mode"]))
            view = memoryview(payload["contents"])
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name != "nt":
            parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        return True
    if operation == "tree_manifest":
        return {"entries": scan_tree(payload["base"], payload["roots"], ignore_inert_pycache=bool(payload.get("ignore_inert_pycache")))}
    if operation == "trees_match":
        return {"match": full_manifest(payload["left"]) == full_manifest(payload["right"])}
    if operation == "skill_snapshot_anchor":
        left = full_manifest(payload["source"])
        right = full_manifest(payload["backup"])
        return {"match": left == right, "left_sha256": left, "right_sha256": right}
    if operation == "gateway_env_matches":
        path = Path(payload["path"])
        if not payload["existed"]:
            try:
                os.lstat(path)
            except FileNotFoundError:
                return {"match": True}
            return {"match": False}
        node, contents = read_file(path)
        return {"match": stat.S_IMODE(node.st_mode) == int(payload["mode"]) and contents == payload["contents"]}
    if operation == "gateway_env_snapshot":
        path = Path(payload["path"])
        try:
            node, contents = read_file(path)
        except FileNotFoundError:
            return {"existed": False, "mode": None, "size": 0, "sha256": hashlib.sha256(b"").hexdigest(), "contents_hex": ""}
        return {"existed": True, "mode": stat.S_IMODE(node.st_mode), "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest(), "contents_hex": contents.hex()}
    if operation == "recovery_backup_status":
        root = Path(payload["path"])
        root_node = os.lstat(root)
        if not stat.S_ISDIR(root_node.st_mode) or stat.S_IMODE(root_node.st_mode) & 0o077:
            return {"complete": False}
        manifest_node, manifest_contents = read_file(root / "transaction-state.json")
        if stat.S_IMODE(manifest_node.st_mode) != 0o600:
            return {"complete": False}
        manifest = json.loads(manifest_contents)
        names = payload["existing_names"]
        if manifest.get("skills", {}).get("existingNames") != names or manifest.get("skills", {}).get("workspace") != payload["workspace"]:
            return {"complete": False}
        children = sorted(item.name for item in root.iterdir() if item.name != "transaction-state.json")
        if children != names:
            return {"complete": False}
        return {
            "complete": True,
            "manifest_sha256": hashlib.sha256(manifest_contents).hexdigest(),
            "manifest_schema_version": manifest.get("schemaVersion"),
            "skill_sha256": {name: full_manifest(root / name) for name in names},
        }
    raise ValueError("unsupported operation")

try:
    emit(execute(request()))
except Exception:
    try:
        body = json.dumps({"ok": False, "result": None}, separators=(",", ":")).encode()
        header = json.dumps({"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}, separators=(",", ":")).encode()
        sys.stdout.buffer.write(len(header).to_bytes(8, "big") + header + body)
        sys.stdout.buffer.flush()
    except Exception:
        pass
    raise
"""


_STATE_HANDOFF_SOURCE = r"""
import hashlib, json, os, sys

MAX_HEADER = 1024 * 1024
MAX_REQUEST = 20 * 1024 * 1024

def read_exact(size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sys.stdin.buffer.read(remaining)
        if not chunk:
            raise OSError("truncated handoff request")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

header_size = int.from_bytes(read_exact(8), "big")
if not 0 < header_size <= MAX_HEADER:
    raise ValueError("invalid handoff header")
header = json.loads(read_exact(header_size))
if (
    not isinstance(header, dict)
    or set(header) != {"version", "payload", "blobs"}
    or header["version"] != 1
    or header["payload"] != {"operation": "write_handoff", "contents": {"__blob__": 0}}
    or not isinstance(header["blobs"], list)
    or len(header["blobs"]) != 1
):
    raise ValueError("invalid handoff request")
blob = header["blobs"][0]
if (
    not isinstance(blob, dict)
    or set(blob) != {"size", "sha256"}
    or not isinstance(blob["size"], int)
    or isinstance(blob["size"], bool)
    or not 0 <= blob["size"] <= MAX_REQUEST
    or not isinstance(blob["sha256"], str)
    or len(blob["sha256"]) != 64
):
    raise ValueError("invalid handoff payload")
contents = read_exact(blob["size"])
if hashlib.sha256(contents).hexdigest() != blob["sha256"] or sys.stdin.buffer.read(1):
    raise ValueError("handoff payload integrity failure")
os.lseek(1, 0, os.SEEK_SET)
os.ftruncate(1, 0)
view = memoryview(contents)
while view:
    written = os.write(1, view)
    if written <= 0:
        raise OSError("handoff write made no progress")
    view = view[written:]
os.fsync(1)
"""


def _local_wire_value(value: Any, blobs: list[bytes]) -> Any:
    if isinstance(value, bytes):
        blobs.append(value)
        return {"__blob__": len(blobs) - 1}
    if isinstance(value, dict):
        return {key: _local_wire_value(item, blobs) for key, item in value.items()}
    if isinstance(value, list):
        return [_local_wire_value(item, blobs) for item in value]
    return value


def _encode_local_worker_request(operation: str, payload: dict[str, Any]) -> bytes:
    blobs: list[bytes] = []
    wire_payload = _local_wire_value({"operation": operation, **payload}, blobs)
    header = json.dumps(
        {
            "version": 1,
            "payload": wire_payload,
            "blobs": [
                {"size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
                for blob in blobs
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > MAX_LOCAL_WORKER_HEADER_BYTES:
        raise ValueError("local worker header is too large")
    request = len(header).to_bytes(8, "big") + header + b"".join(blobs)
    if len(request) > MAX_LOCAL_WORKER_REQUEST_BYTES:
        raise ValueError("local worker request is too large")
    return request


def _decode_local_worker_result(raw: bytes) -> Any:
    if len(raw) < 8 or len(raw) > MAX_LOCAL_WORKER_RESULT_BYTES:
        raise ValueError("local worker result has invalid size")
    header_size = int.from_bytes(raw[:8], "big")
    if not 0 < header_size <= MAX_LOCAL_WORKER_HEADER_BYTES:
        raise ValueError("local worker result header has invalid size")
    body_offset = 8 + header_size
    if body_offset > len(raw):
        raise ValueError("local worker result is truncated")
    header = json.loads(raw[8:body_offset])
    body = raw[body_offset:]
    if (
        not isinstance(header, dict)
        or set(header) != {"size", "sha256"}
        or header["size"] != len(body)
        or not isinstance(header["sha256"], str)
        or SHA256_RE.fullmatch(header["sha256"]) is None
        or hashlib.sha256(body).hexdigest() != header["sha256"]
    ):
        raise ValueError("local worker result integrity check failed")
    envelope = json.loads(body)
    if not isinstance(envelope, dict) or set(envelope) != {"ok", "result"}:
        raise ValueError("local worker result is malformed")
    if envelope["ok"] is not True:
        raise ValueError("local worker operation failed")
    return envelope["result"]


def _terminate_local_worker(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            process.wait(timeout=0.25)
        except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError):
            pass
    except (OSError, RuntimeError, ValueError):
        pass


def _close_local_worker_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except (OSError, RuntimeError, ValueError):
                pass


def _finalize_local_worker(process: subprocess.Popen[bytes]) -> None:
    """Terminate any live child, reap it, and close every owned pipe."""
    try:
        live = process.poll() is None
    except (OSError, RuntimeError, ValueError):
        live = True
    if live:
        _terminate_local_worker(process)
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        _terminate_local_worker(process)
    except (OSError, RuntimeError, ValueError):
        pass
    _close_local_worker_pipes(process)


def _run_bounded_local_worker(
    operation: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Any | None:
    """Supervise one isolated filesystem worker over private anonymous pipes."""
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return None
    deadline = clock() + float(timeout)
    try:
        request = _encode_local_worker_request(operation, payload)
    except (TypeError, ValueError, OverflowError):
        return None
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in {"systemroot", "windir", "path"}
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _LOCAL_OPERATION_SOURCE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        stdout, _ = process.communicate(
            input=request,
            timeout=max(0.001, deadline - clock()),
        )
        if process.returncode != 0:
            return None
        result = _decode_local_worker_result(stdout)
        if clock() >= deadline:
            return None
        return result
    except subprocess.TimeoutExpired:
        return None
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    finally:
        if process is not None:
            _finalize_local_worker(process)


def _run_bounded_local_operation(
    operation: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    return _run_bounded_local_worker(
        operation, payload, timeout, clock=clock
    ) is True


def _run_bounded_local_query(
    operation: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    result = _run_bounded_local_worker(
        operation, payload, timeout, clock=clock
    )
    if not isinstance(result, dict):
        return None
    return result


def _write_setup_state_handoff(
    descriptor: int,
    contents: bytes,
    *,
    deadline_check: Callable[[], float],
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    if len(contents) > MAX_SETUP_STATE_BYTES:
        return False
    try:
        timeout = deadline_check()
        request = _encode_local_worker_request(
            "write_handoff", {"contents": contents}
        )
    except (SetupConflict, TypeError, ValueError, OverflowError):
        return False
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return False
    deadline = clock() + float(timeout)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in {"systemroot", "windir", "path"}
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _STATE_HANDOFF_SOURCE],
            stdin=subprocess.PIPE,
            stdout=descriptor,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        process.communicate(
            input=request,
            timeout=max(0.001, deadline - clock()),
        )
        valid_size = (
            process.returncode == 0
            and os.fstat(descriptor).st_size == len(contents)
        )
        return valid_size and clock() < deadline
    except subprocess.TimeoutExpired:
        return False
    except (OSError, RuntimeError, ValueError, TypeError):
        return False
    finally:
        if process is not None:
            _finalize_local_worker(process)


def _run_local_if_bounded(
    operation: str,
    payload: dict[str, Any],
    *,
    deadline_check: Callable[[], float] | None,
    local_runner: LocalOperationRunner | None,
) -> bool | None:
    """Return None only for legacy read-only/unit callers without a phase budget."""
    if deadline_check is None and local_runner is None:
        return None
    try:
        timeout = deadline_check() if deadline_check is not None else 30.0
    except SetupConflict:
        return False
    return (local_runner or _run_bounded_local_operation)(
        operation, payload, timeout
    )


def _run_local_query_if_bounded(
    operation: str,
    payload: dict[str, Any],
    *,
    deadline_check: Callable[[], float] | None,
    local_query_runner: LocalQueryRunner | None,
) -> dict[str, Any] | None:
    if deadline_check is None and local_query_runner is None:
        return None
    timeout = deadline_check() if deadline_check is not None else 30.0
    return (local_query_runner or _run_bounded_local_query)(
        operation, payload, timeout
    )


def _verify_local_file(
    path: Path,
    contents: bytes,
    mode: int,
    *,
    identity: list[int] | None = None,
    deadline_check: Callable[[], float] | None,
    local_runner: LocalOperationRunner | None,
) -> bool:
    bounded = _run_local_if_bounded(
        "verify_file",
        {
            "path": str(path),
            "contents": contents,
            "mode": mode,
            "identity": identity,
        },
        deadline_check=deadline_check,
        local_runner=local_runner,
    )
    if bounded is not None:
        return bounded
    try:
        node = os.lstat(path)
        return (
            stat.S_ISREG(node.st_mode)
            and stat.S_IMODE(node.st_mode) == mode
            and (identity is None or [node.st_dev, node.st_ino] == identity)
            and path.read_bytes() == contents
        )
    except OSError:
        return False


@contextmanager
def _bounded_temporary_directory(
    *,
    prefix: str,
    directory: Path,
    deadline_check: Callable[[], float] | None,
    local_runner: LocalOperationRunner | None,
):
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    try:
        yield root
    finally:
        if root.exists() or root.is_symlink():
            bounded = _run_local_if_bounded(
                "rmtree",
                {"path": str(root)},
                deadline_check=deadline_check,
                local_runner=local_runner,
            )
            if bounded is None:
                shutil.rmtree(root)
            elif not bounded:
                raise SetupConflict(
                    f"bounded temporary-directory cleanup did not complete: {root}"
                )


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


def _upsert_gateway_env(
    env_path: Path,
    token: str,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> None:
    _validate_api_token(token)
    _create_directory_chain(env_path.parent, "OpenClaw state directory")
    if deadline_check is not None:
        existing_snapshot = _snapshot_gateway_env(
            env_path,
            deadline_check=deadline_check,
        )
        try:
            existing = existing_snapshot.contents.decode("utf-8")
        except UnicodeError as exc:
            raise SetupConflict(
                "OpenClaw gateway environment is not valid UTF-8"
            ) from exc
    else:
        existing = _read_gateway_env_no_follow(env_path)
    updated = _updated_gateway_env(existing, token)
    if updated != existing:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".openhouse-env-", dir=env_path.parent
        )
        temporary = Path(temporary_name)
        try:
            node = os.fstat(descriptor)
            identity = [node.st_dev, node.st_ino]
            os.close(descriptor)
            descriptor = -1
            bounded = _run_local_if_bounded(
                "rewrite_existing",
                {
                    "path": str(temporary),
                    "contents": updated.encode("utf-8"),
                    "mode": 0o600,
                    "identity": identity,
                },
                deadline_check=deadline_check,
                local_runner=local_runner,
            )
            if bounded is None:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                )
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    descriptor = -1
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
            elif not bounded:
                raise SetupConflict(
                    "bounded OpenClaw gateway environment write did not complete"
                )
            if not _verify_local_file(
                temporary,
                updated.encode("utf-8"),
                0o600,
                identity=identity,
                deadline_check=deadline_check,
                local_runner=local_runner,
            ):
                raise SetupConflict(
                    "bounded OpenClaw gateway environment write was not verified"
                )
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
    if deadline_check is not None:
        verified = _snapshot_gateway_env(
            env_path,
            deadline_check=deadline_check,
        )
        try:
            persisted = verified.contents.decode("utf-8")
        except UnicodeError as exc:
            raise SetupConflict(
                "OpenClaw gateway environment is not valid UTF-8"
            ) from exc
        if not verified.existed or _read_gateway_env_token(persisted) != token:
            raise SetupConflict(
                "OpenClaw gateway environment token verification failed"
            )
    else:
        _verify_gateway_env_no_follow(env_path, token)


def _snapshot_gateway_env(
    env_path: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> GatewayEnvSnapshot:
    absolute = _validate_no_symlink_components(
        env_path, "OpenClaw gateway environment", leaf_directory=False
    )
    if deadline_check is not None or local_query_runner is not None:
        result = _run_local_query_if_bounded(
            "gateway_env_snapshot",
            {"path": str(absolute)},
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
        if (
            not isinstance(result, dict)
            or set(result)
            != {"existed", "mode", "size", "sha256", "contents_hex"}
            or not isinstance(result["existed"], bool)
            or not isinstance(result["size"], int)
            or isinstance(result["size"], bool)
            or result["size"] < 0
            or not isinstance(result["sha256"], str)
            or SHA256_RE.fullmatch(result["sha256"]) is None
            or not isinstance(result["contents_hex"], str)
        ):
            raise SetupConflict(
                "bounded OpenClaw gateway environment snapshot did not complete"
            )
        try:
            contents = bytes.fromhex(result["contents_hex"])
        except ValueError as exc:
            raise SetupConflict(
                "bounded OpenClaw gateway environment snapshot was malformed"
            ) from exc
        if (
            len(contents) != result["size"]
            or hashlib.sha256(contents).hexdigest() != result["sha256"]
            or (
                result["existed"]
                and (
                    not isinstance(result["mode"], int)
                    or isinstance(result["mode"], bool)
                    or not 0 <= result["mode"] <= 0o777
                )
            )
            or (not result["existed"] and (result["mode"] is not None or contents))
        ):
            raise SetupConflict(
                "bounded OpenClaw gateway environment snapshot was malformed"
            )
        return GatewayEnvSnapshot(
            absolute,
            result["existed"],
            contents,
            result["mode"],
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


def _restore_gateway_env(
    snapshot: GatewayEnvSnapshot,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> bool:
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
            bounded = _run_local_if_bounded(
                "unlink",
                {"path": str(snapshot.path)},
                deadline_check=deadline_check,
                local_runner=local_runner,
            )
            if bounded is None:
                snapshot.path.unlink()
            elif not bounded:
                return False
            return not snapshot.path.exists() and not snapshot.path.is_symlink()

        snapshot.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".openhouse-env-restore-", dir=snapshot.path.parent
        )
        temporary = Path(temporary_name)
        try:
            node = os.fstat(descriptor)
            identity = [node.st_dev, node.st_ino]
            os.close(descriptor)
            descriptor = -1
            bounded = _run_local_if_bounded(
                "rewrite_existing",
                {
                    "path": str(temporary),
                    "contents": snapshot.contents,
                    "mode": snapshot.mode or 0o600,
                    "identity": identity,
                },
                deadline_check=deadline_check,
                local_runner=local_runner,
            )
            if bounded is None:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                )
                os.fchmod(descriptor, snapshot.mode or 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = -1
                    stream.write(snapshot.contents)
                    stream.flush()
                    os.fsync(stream.fileno())
            elif not bounded:
                return False
            if not _verify_local_file(
                temporary,
                snapshot.contents,
                snapshot.mode or 0o600,
                identity=identity,
                deadline_check=deadline_check,
                local_runner=local_runner,
            ):
                return False
            os.replace(temporary, snapshot.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()
        return _gateway_env_snapshot_matches(
            snapshot,
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
    except (OSError, SetupConflict):
        return False


def _read_repo_env_values(repo: Path) -> dict[str, str]:
    """Parse the simple dotenv assignments accepted by the launch scripts."""
    env_file = repo / ".env"
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if key in values:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_repo_env(repo: Path) -> None:
    """Load simple .env assignments without overriding exported values.

    This deliberately mirrors scripts/load-env.sh: no shell expansion, only
    valid environment keys, and an existing process value always wins.
    """
    for key, value in _read_repo_env_values(repo).items():
        if key not in os.environ:
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


def _validate_skill_tree(
    path: Path,
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> None:
    if path.is_symlink():
        raise SetupConflict(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise SetupConflict(f"{label} must be a directory: {path}")
    if not path.exists():
        return
    if deadline_check is not None or local_query_runner is not None:
        _bounded_tree_file_entries(
            path,
            (Path("."),),
            label,
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
        return
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise SetupConflict(f"{label} contains a symlink: {entry}")
        if entry.name == "__pycache__" and entry.is_dir():
            _validate_inert_pycache(entry, label)


def _validate_directory_node(path: Path, label: str) -> None:
    if path.is_symlink():
        raise SetupConflict(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise SetupConflict(f"{label} must be a directory: {path}")


def _remove_installed_tree(
    path: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> None:
    if path.is_symlink() or path.is_file():
        operation = "unlink"
    elif path.is_dir():
        operation = "rmtree"
    else:
        return
    bounded = _run_local_if_bounded(
        operation,
        {"path": str(path)},
        deadline_check=deadline_check,
        local_runner=local_runner,
    )
    if bounded is None:
        path.unlink() if operation == "unlink" else shutil.rmtree(path)
    elif not bounded:
        raise SetupConflict(f"bounded installed-tree removal did not complete: {path}")


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


def _capture_dashboard_client_tools(
    repo: Path, contract_snapshot: ContractSnapshot
) -> ClientToolsSnapshot:
    path = repo / CLIENT_TOOLS_RELATIVE_PATH
    absolute = _validate_no_symlink_components(
        path, "canonical dashboard client-tool builder", leaf_directory=False
    )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except FileNotFoundError as exc:
        raise SetupConflict("canonical dashboard client-tool builder is missing") from exc
    except OSError as exc:
        raise SetupConflict(
            "could not open canonical dashboard client-tool builder"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SetupConflict(
                "canonical dashboard client-tool builder is not a regular file"
            )
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
        raise SetupConflict("canonical dashboard client-tool builder is unexpectedly large")
    try:
        source = contents.decode("utf-8")
        contract = _decode_json(
            contract_snapshot.contents.decode("utf-8"),
            "captured canonical CRM operation contract",
        )
        namespace: dict[str, Any] = {
            "__file__": str(absolute),
            "__name__": "_openhouse_setup_dashboard_client_tools",
        }
        exec(compile(source, str(absolute), "exec"), namespace)
        builder = namespace.get("build_dashboard_client_tools")
        if not callable(builder):
            raise TypeError("builder is unavailable")
        tools = builder(contract)
        encoded = json.dumps(
            tools, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except Exception as exc:
        raise SetupConflict(
            "canonical dashboard client-tool builder is incompatible"
        ) from exc
    if (
        not isinstance(tools, list)
        or len(tools) != 2
        or len(encoded) > GATEWAY_PROBE_MAX_BYTES
        or [
            tool.get("function", {}).get("name")
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            else None
            for tool in tools
        ]
        != ["openhouse_crm_request", "finish_crm_response"]
    ):
        raise SetupConflict("canonical dashboard client-tool builder is incompatible")
    request_parameters = tools[0]["function"].get("parameters")
    finish_parameters = tools[1]["function"].get("parameters")
    if (
        not isinstance(request_parameters, dict)
        or not isinstance(request_parameters.get("oneOf"), list)
        or len(request_parameters["oneOf"]) != len(contract_snapshot.operations)
        or not isinstance(finish_parameters, dict)
        or finish_parameters.get("additionalProperties") is not False
    ):
        raise SetupConflict("canonical dashboard client-tool builder is incompatible")
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    return ClientToolsSnapshot(
        path=absolute,
        contents=contents,
        digest=hashlib.sha256(contents).hexdigest(),
        identity=identity,
        tools=tools,
    )


def _verify_client_tools_source_unchanged(
    snapshot: ClientToolsSnapshot, contract_snapshot: ContractSnapshot
) -> None:
    current = _capture_dashboard_client_tools(
        snapshot.path.parents[2], contract_snapshot
    )
    if current.identity != snapshot.identity or current.digest != snapshot.digest:
        raise SetupConflict("canonical dashboard client-tool builder changed after validation")


def _verify_installed_client_tools(
    workspace: Path,
    snapshot: ClientToolsSnapshot,
    contract_snapshot: ContractSnapshot,
) -> None:
    installed = _capture_dashboard_client_tools(workspace, contract_snapshot)
    if installed.digest != snapshot.digest or installed.tools != snapshot.tools:
        raise SetupConflict(
            "installed dashboard client-tool builder does not match the canonical source"
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


def _snapshot_installed_skills(
    workspace: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> SkillRollback:
    skills_root = workspace / "skills"
    _validate_no_symlink_components(
        workspace, "OpenClaw workspace", leaf_directory=True
    )
    _validate_no_symlink_components(
        skills_root, "OpenClaw skills directory", leaf_directory=True
    )
    for name in SKILL_NAMES:
        _validate_skill_tree(
            skills_root / name,
            "installed skill directory",
            deadline_check=deadline_check,
        )
    backup_root = Path(
        tempfile.mkdtemp(prefix="openhouse-skill-rollback-")
    ).resolve(strict=True)
    existing_names: set[str] = set()
    skill_anchors: dict[str, str] = {}
    try:
        for name in SKILL_NAMES:
            target = skills_root / name
            if target.exists():
                bounded = _run_local_if_bounded(
                    "copytree",
                    {
                        "source": str(target),
                        "target": str(backup_root / name),
                    },
                    deadline_check=deadline_check,
                    local_runner=local_runner,
                )
                if bounded is None:
                    shutil.copytree(target, backup_root / name)
                elif not bounded:
                    raise SetupConflict(
                        "could not complete the bounded installed-skill snapshot; "
                        f"incomplete private data remains at {backup_root} and must "
                        "not be treated as a recovery backup"
                    )
                anchor = _run_local_query_if_bounded(
                    "skill_snapshot_anchor",
                    {
                        "source": str(target),
                        "backup": str(backup_root / name),
                    },
                    deadline_check=deadline_check or (lambda: 30.0),
                    local_query_runner=local_query_runner,
                )
                if (
                    not isinstance(anchor, dict)
                    or set(anchor)
                    != {"match", "left_sha256", "right_sha256"}
                    or anchor["match"] is not True
                    or not isinstance(anchor["left_sha256"], str)
                    or not isinstance(anchor["right_sha256"], str)
                    or SHA256_RE.fullmatch(anchor["left_sha256"]) is None
                    or anchor["left_sha256"] != anchor["right_sha256"]
                ):
                    raise SetupConflict(
                        "installed skill backup did not match its source before setup mutation"
                    )
                skill_anchors[name] = anchor["left_sha256"]
                existing_names.add(name)
    except SetupConflict:
        raise
    except OSError as exc:
        raise SetupConflict(f"could not snapshot installed CRM skills: {exc}") from exc
    return SkillRollback(
        workspace=workspace,
        backup_root=backup_root,
        existing_names=existing_names,
        workspace_existed=workspace.exists(),
        skills_root_existed=skills_root.exists(),
        missing_parent_dirs=_missing_directory_chain(workspace.parent),
        skill_anchor_sha256=skill_anchors,
    )


def _restore_installed_skills(
    snapshot: SkillRollback,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> bool:
    skills_root = snapshot.workspace / "skills"
    try:
        if deadline_check is not None:
            deadline_check()
        _validate_no_symlink_components(
            snapshot.workspace, "OpenClaw workspace", leaf_directory=True
        )
        _validate_no_symlink_components(
            skills_root, "OpenClaw skills directory", leaf_directory=True
        )
        skills_root.mkdir(parents=True, exist_ok=True)
        for name in SKILL_NAMES:
            if deadline_check is not None:
                deadline_check()
            target = skills_root / name
            if name in snapshot.existing_names:
                with _bounded_temporary_directory(
                    prefix=".openhouse-skill-restore-",
                    directory=skills_root.parent,
                    deadline_check=deadline_check,
                    local_runner=local_runner,
                ) as staging_root:
                    staged = staging_root / name
                    quarantine = staging_root / f"current-{name}"
                    bounded = _run_local_if_bounded(
                        "copytree",
                        {
                            "source": str(snapshot.backup_root / name),
                            "target": str(staged),
                        },
                        deadline_check=deadline_check,
                        local_runner=local_runner,
                    )
                    if bounded is None:
                        shutil.copytree(snapshot.backup_root / name, staged)
                    elif not bounded:
                        return False
                    if deadline_check is not None:
                        deadline_check()
                    _validate_skill_tree(
                        staged,
                        "staged restored skill directory",
                        deadline_check=deadline_check,
                    )
                    _validate_no_symlink_components(
                        target, "installed skill directory", leaf_directory=True
                    )
                    if deadline_check is not None:
                        deadline_check()
                    if target.exists():
                        target.rename(quarantine)
                    staged.rename(target)
                    if quarantine.exists():
                        _remove_installed_tree(
                            quarantine,
                            deadline_check=deadline_check,
                            local_runner=local_runner,
                        )
                    if deadline_check is not None:
                        deadline_check()
                if not _skill_trees_match(
                    snapshot.backup_root / name,
                    target,
                    deadline_check=deadline_check,
                    local_query_runner=(
                        _run_bounded_local_query
                        if deadline_check is not None
                        else None
                    ),
                ):
                    return False
            elif target.exists() or target.is_symlink():
                _validate_no_symlink_components(
                    target, "installed skill directory", leaf_directory=True
                )
                _remove_installed_tree(
                    target,
                    deadline_check=deadline_check,
                    local_runner=local_runner,
                )
                if deadline_check is not None:
                    deadline_check()
                if target.exists() or target.is_symlink():
                    return False
        if deadline_check is not None:
            deadline_check()
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


def _skill_trees_match(
    left: Path,
    right: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> bool:
    if deadline_check is not None or local_query_runner is not None:
        try:
            result = _run_local_query_if_bounded(
                "trees_match",
                {"left": str(left), "right": str(right)},
                deadline_check=deadline_check,
                local_query_runner=local_query_runner,
            )
        except SetupConflict:
            return False
        return (
            isinstance(result, dict)
            and set(result) == {"match"}
            and isinstance(result["match"], bool)
            and result["match"]
        )

    def manifest(root: Path) -> dict[str, tuple[str, int, bytes]]:
        result: dict[str, tuple[str, int, bytes]] = {}
        if deadline_check is not None:
            deadline_check()
        _validate_skill_tree(root, "skill restoration verification tree")
        root_node = os.lstat(root)
        result["."] = ("directory", stat.S_IMODE(root_node.st_mode), b"")
        for path in sorted(root.rglob("*")):
            if deadline_check is not None:
                deadline_check()
            relative = path.relative_to(root).as_posix()
            node = os.lstat(path)
            if stat.S_ISDIR(node.st_mode):
                result[relative] = (
                    "directory",
                    stat.S_IMODE(node.st_mode),
                    b"",
                )
            elif stat.S_ISREG(node.st_mode):
                result[relative] = (
                    "file",
                    stat.S_IMODE(node.st_mode),
                    path.read_bytes(),
                )
            else:
                raise SetupConflict(
                    f"skill restoration verification found unsupported node: {path}"
                )
        return result

    try:
        return manifest(left) == manifest(right)
    except (OSError, SetupConflict):
        return False


def _gateway_env_snapshot_matches(
    snapshot: GatewayEnvSnapshot,
    *,
    deadline_check: Callable[[], float],
    local_query_runner: LocalQueryRunner | None = None,
) -> bool:
    try:
        result = _run_local_query_if_bounded(
            "gateway_env_matches",
            {
                "path": str(snapshot.path),
                "existed": snapshot.existed,
                "contents": snapshot.contents,
                "mode": snapshot.mode,
            },
            deadline_check=deadline_check,
            local_query_runner=local_query_runner,
        )
    except SetupConflict:
        return False
    return (
        isinstance(result, dict)
        and set(result) == {"match"}
        and isinstance(result["match"], bool)
        and result["match"]
    )


def _recovery_snapshot_is_complete(
    snapshot: SkillRollback,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> bool:
    if (
        snapshot.manifest_schema_version != 1
        or not isinstance(snapshot.manifest_anchor_sha256, str)
        or SHA256_RE.fullmatch(snapshot.manifest_anchor_sha256) is None
        or set(snapshot.skill_anchor_sha256) != snapshot.existing_names
        or any(
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            for digest in snapshot.skill_anchor_sha256.values()
        )
    ):
        return False
    try:
        result = _run_local_query_if_bounded(
            "recovery_backup_status",
            {
                "path": str(snapshot.backup_root),
                "workspace": str(snapshot.workspace),
                "existing_names": sorted(snapshot.existing_names),
            },
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
    except SetupConflict:
        return False
    return (
        isinstance(result, dict)
        and set(result)
        == {
            "complete",
            "manifest_sha256",
            "manifest_schema_version",
            "skill_sha256",
        }
        and result["complete"] is True
        and result["manifest_sha256"] == snapshot.manifest_anchor_sha256
        and result["manifest_schema_version"] == snapshot.manifest_schema_version
        and result["skill_sha256"] == snapshot.skill_anchor_sha256
    )


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
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> None:
    payload = {
        "schemaVersion": 1,
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
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _write_bytes_exclusive(
        path,
        encoded,
        0o600,
        deadline_check=deadline_check,
        local_runner=local_runner,
    )
    skill_snapshot.manifest_schema_version = payload["schemaVersion"]
    skill_snapshot.manifest_anchor_sha256 = hashlib.sha256(encoded).hexdigest()


def _write_bytes_exclusive(
    path: Path,
    contents: bytes,
    mode: int = 0o644,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> None:
    bounded = _run_local_if_bounded(
        "write_exclusive",
        {"path": str(path), "contents": contents, "mode": mode},
        deadline_check=deadline_check,
        local_runner=local_runner,
    )
    if bounded is not None:
        if not bounded:
            raise SetupConflict("bounded private file write did not complete")
        if not _verify_local_file(
            path,
            contents,
            mode,
            deadline_check=deadline_check,
            local_runner=local_runner,
        ):
            raise SetupConflict("bounded private file write was not verified")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_regular_file_digest(
    path: Path,
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
) -> tuple[int, str]:
    """Hash one regular file without following its leaf or accepting a race."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SetupConflict(f"could not inspect {label}") from exc
    try:
        if deadline_check is not None:
            deadline_check()
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SetupConflict(f"{label} must contain only regular files")
        digest = hashlib.sha256()
        size = 0
        while True:
            if deadline_check is not None:
                deadline_check()
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or size != after.st_size:
            raise SetupConflict(f"{label} changed while it was inspected")
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _normalized_git_mode(node_mode: int) -> str:
    return "100755" if node_mode & 0o111 else "100644"


def _git_bytes(
    repo: Path,
    argv: list[str],
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
) -> bytes:
    timeout = 30.0
    if deadline_check is not None:
        timeout = min(timeout, deadline_check())
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *argv],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupConflict(f"could not inspect {label}") from exc
    if result.returncode != 0:
        raise SetupConflict(f"could not inspect {label}")
    return result.stdout


def _filesystem_material_files(
    repo: Path,
    roots: tuple[Path, ...],
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for relative_root in roots:
        if deadline_check is not None:
            deadline_check()
        if relative_root.is_absolute() or ".." in relative_root.parts:
            raise SetupConflict(f"could not inspect {label}")
        root = repo / relative_root
        try:
            node = os.lstat(root)
        except OSError as exc:
            raise SetupConflict(f"could not inspect {label}") from exc
        if stat.S_ISLNK(node.st_mode):
            raise SetupConflict(f"{label} must not contain symlinks")
        if stat.S_ISREG(node.st_mode):
            files[relative_root.as_posix()] = root
            continue
        if not stat.S_ISDIR(node.st_mode):
            raise SetupConflict(f"{label} must contain only regular files and directories")
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            if deadline_check is not None:
                deadline_check()
            current_path = Path(current)
            for name in list(directory_names):
                child = current_path / name
                try:
                    child_node = os.lstat(child)
                except OSError as exc:
                    raise SetupConflict(f"could not inspect {label}") from exc
                if stat.S_ISLNK(child_node.st_mode) or not stat.S_ISDIR(child_node.st_mode):
                    raise SetupConflict(f"{label} must contain only real directories")
                if name == "__pycache__":
                    _validate_inert_pycache(
                        child, label, deadline_check=deadline_check
                    )
                    directory_names.remove(name)
            for name in file_names:
                child = current_path / name
                try:
                    child_node = os.lstat(child)
                except OSError as exc:
                    raise SetupConflict(f"could not inspect {label}") from exc
                if not stat.S_ISREG(child_node.st_mode):
                    raise SetupConflict(f"{label} must contain only regular files")
                relative = child.relative_to(repo).as_posix()
                files[relative] = child
                if len(files) > MAX_MATERIAL_ENTRIES:
                    raise SetupConflict(f"{label} contains too many files")
    return files


def _validate_inert_pycache(
    path: Path,
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
) -> None:
    """Accept only bounded regular bytecode files that this process never loads."""
    try:
        entries = list(path.iterdir())
    except OSError as exc:
        raise SetupConflict(f"could not inspect {label} cache") from exc
    if len(entries) > MAX_MATERIAL_ENTRIES:
        raise SetupConflict(f"{label} cache contains too many files")
    for entry in entries:
        if deadline_check is not None:
            deadline_check()
        try:
            node = os.lstat(entry)
        except OSError as exc:
            raise SetupConflict(f"could not inspect {label} cache") from exc
        if (
            not stat.S_ISREG(node.st_mode)
            or INERT_PYCACHE_FILE_RE.fullmatch(entry.name) is None
        ):
            raise SetupConflict(f"{label} contains an unsupported cache entry")


def _ignore_inert_pycache(directory: str, names: list[str]) -> list[str]:
    if "__pycache__" not in names:
        return []
    cache = Path(directory) / "__pycache__"
    _validate_inert_pycache(cache, "shipped skill directory")
    return ["__pycache__"]


def _bounded_tree_file_entries(
    base: Path,
    roots: tuple[Path, ...],
    label: str,
    *,
    deadline_check: Callable[[], float],
    local_query_runner: LocalQueryRunner | None = None,
) -> dict[str, dict[str, Any]]:
    result = _run_local_query_if_bounded(
        "tree_manifest",
        {
            "base": str(base),
            "roots": [root.as_posix() for root in roots],
            "ignore_inert_pycache": True,
        },
        deadline_check=deadline_check,
        local_query_runner=local_query_runner,
    )
    if not isinstance(result, dict) or set(result) != {"entries"}:
        raise SetupConflict(f"bounded {label} tree inspection did not complete")
    raw_entries = result["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_MATERIAL_ENTRIES:
        raise SetupConflict(f"bounded {label} tree inspection was malformed")
    entries: dict[str, dict[str, Any]] = {}
    for entry in raw_entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "kind", "mode", "size", "sha256"}
            or entry["kind"] != "file"
            or not isinstance(entry["path"], str)
            or not entry["path"]
            or Path(entry["path"]).is_absolute()
            or ".." in Path(entry["path"]).parts
            or entry["path"] in entries
            or entry["mode"] not in {"100644", "100755"}
            or not isinstance(entry["size"], int)
            or isinstance(entry["size"], bool)
            or entry["size"] < 0
            or not isinstance(entry["sha256"], str)
            or SHA256_RE.fullmatch(entry["sha256"]) is None
        ):
            raise SetupConflict(f"bounded {label} tree inspection was malformed")
        entries[entry["path"]] = {
            "mode": entry["mode"],
            "size": entry["size"],
            "sha256": entry["sha256"],
        }
    return entries


def _tracked_head_manifest(
    repo: Path,
    roots: tuple[Path, ...],
    label: str,
    *,
    paths_relative_to: Path | None = None,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> dict[str, Any]:
    """Return a HEAD-authoritative manifest and reject every non-HEAD filesystem entry."""
    pathspecs = [root.as_posix() for root in roots]
    raw = _git_bytes(
        repo,
        ["ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *pathspecs],
        label,
        deadline_check=deadline_check,
    )
    tracked: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            relative = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SetupConflict(f"could not inspect {label}") from exc
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise SetupConflict(f"{label} contains an unsupported tracked entry")
        tracked[relative] = (mode, object_id)
    if not tracked or len(tracked) > MAX_MATERIAL_ENTRIES:
        raise SetupConflict(f"{label} did not resolve to a bounded tracked tree")
    bounded_files: dict[str, dict[str, Any]] | None = None
    if deadline_check is not None or local_query_runner is not None:
        bounded_files = _bounded_tree_file_entries(
            repo,
            roots,
            label,
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
        filesystem_paths = set(bounded_files)
        filesystem: dict[str, Path] = {}
    else:
        filesystem = _filesystem_material_files(repo, roots, label)
        filesystem_paths = set(filesystem)
    extras = sorted(filesystem_paths - set(tracked))
    missing = sorted(set(tracked) - filesystem_paths)
    if extras:
        raise SetupConflict(f"{label} contains an extra non-HEAD file")
    if missing:
        raise SetupConflict(f"{label} is missing a tracked HEAD file")
    entries: list[dict[str, Any]] = []
    for relative in sorted(tracked):
        if deadline_check is not None:
            deadline_check()
        expected_mode, object_id = tracked[relative]
        path = filesystem.get(relative)
        if bounded_files is not None:
            bounded_entry = bounded_files[relative]
            actual_mode = bounded_entry["mode"]
            size = bounded_entry["size"]
            digest = bounded_entry["sha256"]
        else:
            assert path is not None
            node = os.lstat(path)
            actual_mode = _normalized_git_mode(node.st_mode)
            size, digest = _read_regular_file_digest(path, label)
        if actual_mode != expected_mode:
            raise SetupConflict(f"{label} file mode does not match HEAD")
        contents = _git_bytes(
            repo,
            ["cat-file", "blob", object_id],
            label,
            deadline_check=deadline_check,
        )
        if size != len(contents) or digest != hashlib.sha256(contents).hexdigest():
            raise SetupConflict(f"{label} contents do not match HEAD")
        display_path = relative
        if paths_relative_to is not None:
            try:
                display_path = Path(relative).relative_to(paths_relative_to).as_posix()
            except ValueError as exc:
                raise SetupConflict(f"could not inspect {label}") from exc
        entries.append(
            {"path": display_path, "mode": actual_mode, "size": size, "sha256": digest}
        )
    return {
        "sha256": hashlib.sha256(_canonical_json_bytes(entries)).hexdigest(),
        "entries": entries,
    }


def _tracked_head_tree(
    repo: Path,
    relative_root: Path,
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> dict[str, Any]:
    return _tracked_head_manifest(
        repo,
        (relative_root,),
        label,
        paths_relative_to=relative_root,
        deadline_check=deadline_check,
        local_query_runner=local_query_runner,
    )


def _material_head_state(
    repo: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> dict[str, Any]:
    if deadline_check is not None:
        deadline_check()
    skills = {
        name: _tracked_head_tree(
            repo,
            Path("skills") / name,
            f"shipped {name} skill",
            deadline_check=deadline_check,
            local_query_runner=local_query_runner,
        )
        for name in SKILL_NAMES
    }
    plugin = _tracked_head_tree(
        repo,
        Path("openclaw-plugins") / PLUGIN_ID,
        "bundled OpenClaw CRM plugin",
        deadline_check=deadline_check,
        local_query_runner=local_query_runner,
    )
    shared = _tracked_head_manifest(
        repo,
        MATERIAL_SHARED_PATHS,
        "shared setup sources",
        deadline_check=deadline_check,
        local_query_runner=local_query_runner,
    )
    material = {"skills": skills, "plugin": plugin, "shared": shared}
    return {
        **material,
        "material_tree_sha256": hashlib.sha256(
            _canonical_json_bytes(material)
        ).hexdigest(),
    }


def _installed_tree_manifest(
    root: Path,
    expected: dict[str, Any],
    label: str,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_query_runner: LocalQueryRunner | None = None,
) -> dict[str, Any]:
    validated = _validate_tree_manifest(expected, label)
    bounded_files: dict[str, dict[str, Any]] | None = None
    if deadline_check is not None or local_query_runner is not None:
        bounded_files = _bounded_tree_file_entries(
            root,
            (Path("."),),
            label,
            deadline_check=deadline_check or (lambda: 30.0),
            local_query_runner=local_query_runner,
        )
        actual_paths: dict[str, Any] = bounded_files
    else:
        files = _filesystem_material_files(root.parent, (Path(root.name),), label)
        actual_paths = {
            Path(path).relative_to(root.name).as_posix(): value
            for path, value in files.items()
        }
    expected_by_path = {entry["path"]: entry for entry in validated["entries"]}
    if set(actual_paths) != set(expected_by_path):
        raise SetupConflict(f"{label} has files outside the shipped HEAD tree")
    entries: list[dict[str, Any]] = []
    for relative in sorted(expected_by_path):
        expected_entry = expected_by_path[relative]
        if bounded_files is not None:
            actual = bounded_files[relative]
            mode = actual["mode"]
            size = actual["size"]
            digest = actual["sha256"]
        else:
            path = actual_paths[relative]
            node = os.lstat(path)
            mode = _normalized_git_mode(node.st_mode)
            size, digest = _read_regular_file_digest(path, label)
        entry = {"path": relative, "mode": mode, "size": size, "sha256": digest}
        if entry != expected_entry:
            field = "mode" if mode != expected_entry["mode"] else "content"
            raise SetupConflict(f"installed skill {field} does not match the shipped source")
        entries.append(entry)
    return {"sha256": hashlib.sha256(_canonical_json_bytes(entries)).hexdigest(), "entries": entries}


def sync_skills(
    repo: Path,
    workspace: Path,
    *,
    dry_run: bool,
    contract_snapshot: ContractSnapshot | None = None,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> list[Path]:
    sources = [repo / "skills" / name for name in SKILL_NAMES]
    skills_root = workspace / "skills"
    targets = [skills_root / name for name in SKILL_NAMES]
    created_parent_dirs: list[Path] = []
    try:
        for source in sources:
            if not source.exists():
                raise SetupConflict(f"shipped skill directory is missing: {source}")
            _validate_skill_tree(
                source,
                "shipped skill directory",
                deadline_check=deadline_check,
            )
        _validate_no_symlink_components(
            workspace, "OpenClaw workspace", leaf_directory=True
        )
        _validate_no_symlink_components(
            skills_root, "OpenClaw skills directory", leaf_directory=True
        )
        for target in targets:
            _validate_skill_tree(
                target,
                "installed skill directory",
                deadline_check=deadline_check,
            )
        if dry_run:
            return targets

        parent = workspace.parent
        created_parent_dirs = _create_directory_chain(
            parent, "OpenClaw workspace parent"
        )

        with _bounded_temporary_directory(
            prefix=".openhouse-skills-",
            directory=parent,
            deadline_check=deadline_check,
            local_runner=local_runner,
        ) as staging_root:
            staged_skills = staging_root / "staged"
            backups = staging_root / "backups"
            for name, source in zip(SKILL_NAMES, sources):
                if name == "crm-db-operations" and contract_snapshot is not None:
                    source_root = _absolute_lexical_path(source)

                    def ignore_captured_contract(directory, names):
                        ignored = set(_ignore_inert_pycache(directory, names))
                        if (
                            _absolute_lexical_path(Path(directory)) == source_root
                            and "contract.json" in names
                        ):
                            ignored.add("contract.json")
                        return sorted(ignored)

                    bounded = _run_local_if_bounded(
                        "copytree",
                        {
                            "source": str(source),
                            "target": str(staged_skills / name),
                            "ignore": "inert_and_contract",
                        },
                        deadline_check=deadline_check,
                        local_runner=local_runner,
                    )
                    if bounded is None:
                        shutil.copytree(
                            source,
                            staged_skills / name,
                            ignore=ignore_captured_contract,
                        )
                    elif not bounded:
                        raise SetupConflict("bounded CRM skill copy did not complete")
                    _write_bytes_exclusive(
                        staged_skills / name / "contract.json",
                        contract_snapshot.contents,
                        deadline_check=deadline_check,
                        local_runner=local_runner,
                    )
                else:
                    bounded = _run_local_if_bounded(
                        "copytree",
                        {
                            "source": str(source),
                            "target": str(staged_skills / name),
                            "ignore": "inert",
                        },
                        deadline_check=deadline_check,
                        local_runner=local_runner,
                    )
                    if bounded is None:
                        shutil.copytree(
                            source,
                            staged_skills / name,
                            ignore=_ignore_inert_pycache,
                        )
                    elif not bounded:
                        raise SetupConflict("bounded CRM skill copy did not complete")
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
                    _remove_installed_tree(
                        target,
                        deadline_check=deadline_check,
                        local_runner=local_runner,
                    )
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


def _quote_openclaw_cli_arg(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_/:=.,@%+-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def _is_missing_config_path(result: CommandResult, path: str) -> bool:
    if result.returncode != 1:
        return False
    expected_text = f"Config path not found: {path}"
    expected_unset_prefix = (
        f"Config path is valid but unset: {path}. The runtime default applies until "
        "you set an authored value with "
    )
    rendered_paths = {path, _quote_openclaw_cli_arg(path)}
    expected_unset_texts = {
        expected_unset_prefix + rendered_command
        for rendered_path in rendered_paths
        for rendered_command in (
            f"`openclaw config set {rendered_path} <value>`.",
            f"openclaw config set {rendered_path} <value>.",
        )
    }
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout and stderr:
        return False
    if stdout:
        if stdout == expected_text:
            return True
        try:
            payload = _decode_json(stdout, "config missing-path diagnostic")
        except SetupConflict:
            return False
        if payload == {"error": expected_text}:
            return True
        if not isinstance(payload, dict) or set(payload) != {"ok", "error"}:
            return False
        error = payload.get("error")
        return (
            payload.get("ok") is False
            and isinstance(error, dict)
            and set(error) == {"type", "message"}
            and error.get("type") == "cli_error"
            and error.get("message") in expected_unset_texts
        )
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


def _desired_agent_experimental(agent: dict[str, Any] | None) -> dict[str, Any]:
    current = agent.get("experimental") if agent is not None else None
    preserved = dict(current) if isinstance(current, dict) else {}
    preserved["localModelLean"] = CRM_LOCAL_MODEL_LEAN
    return preserved


def _config_actions(
    options: SetupOptions, prefix: str, agent: dict[str, Any] | None = None
) -> list[Action]:
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
            "Disable thinking for reliable structured CRM tool calls",
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.thinkingDefault",
                json.dumps(CRM_THINKING_DEFAULT),
                "--strict-json",
            ],
        ),
        Action(
            "Keep caller-supplied CRM functions directly visible to the local model",
            [
                "openclaw",
                "config",
                "set",
                f"{prefix}.experimental",
                json.dumps(_desired_agent_experimental(agent)),
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
        "Would disable lean local-model tool compaction for only the dedicated CRM agent.",
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


def _canonical_workspace_key(workspace: Any) -> str | None:
    if not isinstance(workspace, str) or not workspace.strip():
        return None
    expanded = Path(workspace).expanduser()
    try:
        resolved = expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(str(expanded)))
    normalized = os.path.normcase(
        os.path.normpath(os.path.abspath(str(resolved)))
    )
    if sys.platform == "darwin":
        return unicodedata.normalize("NFC", normalized).casefold()
    return normalized


def _workspace_paths_same(left: Any, right: Any) -> bool:
    if (
        not isinstance(left, str)
        or not left.strip()
        or not isinstance(right, str)
        or not right.strip()
    ):
        return False
    left_path = Path(left).expanduser()
    right_path = Path(right).expanduser()
    try:
        if os.path.samefile(left_path, right_path):
            return True
    except (OSError, ValueError):
        pass
    return _canonical_workspace_key(left) == _canonical_workspace_key(right)


def _reject_workspace_collisions(
    agent_id: str, workspace: Path, rosters: tuple[list[dict], ...]
) -> None:
    requested = str(workspace)
    if _canonical_workspace_key(requested) is None:
        raise SetupConflict("requested agent workspace could not be resolved")
    for roster in rosters:
        for agent in roster:
            other_id = agent.get("id")
            if not isinstance(other_id, str) or other_id == agent_id:
                continue
            configured = agent.get("workspace") or agent.get("workspacePath")
            if _workspace_paths_same(configured, requested):
                raise SetupConflict(
                    f"requested workspace already belongs to agent {other_id}; "
                    "choose a dedicated workspace before running setup"
                )


def _managed_agent_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        field: agent[field]
        for field in MANAGED_AGENT_FIELDS
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

    typed_hook_inventories: list[list[str]] = []
    seen_inventory_keys: set[tuple[int, str]] = set()
    for holder, key, label in (
        (runtime, "typedHooks", "runtime typed hooks"),
        (payload, "typedHooks", "plugin inspection typed hooks"),
    ):
        marker = (id(holder), key)
        if marker in seen_inventory_keys or key not in holder:
            continue
        seen_inventory_keys.add(marker)
        typed_hook_inventories.append(_registered_hook_names(holder[key], label))

    if typed_hook_inventories:
        hook_inventories = typed_hook_inventories
    else:
        hook_inventories = []
        for holder, key, label in (
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
    hook_count = plugin.get("hookCount") if not typed_hook_inventories else None
    if hook_count is not None and (
        not isinstance(hook_count, int) or hook_count != len(REQUIRED_PLUGIN_HOOKS)
    ):
        raise SetupConflict(
            "OpenClaw runtime did not expose exactly the required CRM outcome hooks"
        )
    return bool(hook_inventories)


def _setup_checks(model_tool_behavior: str) -> dict[str, Any]:
    return {
        "channel_policy": [DASHBOARD_CHANNEL, INTERNAL_ANALYSIS_CHANNEL],
        "schema_transport": "accepted",
        "model_tool_behavior": model_tool_behavior,
        "diagnostic_cleanup": "verified",
    }


def _inventory_runtime_verification(
    agent_id: str, model_tool_behavior: str = "verified"
) -> dict[str, Any]:
    return {
        "mode": "authoritative_inventory",
        "agent_id": agent_id,
        "hooks": sorted(REQUIRED_PLUGIN_HOOKS),
        "setup_checks": _setup_checks(model_tool_behavior),
    }


def _behavioral_runtime_verification(
    agent_id: str, model_tool_behavior: str = "verified"
) -> dict[str, Any]:
    return {
        "mode": "production_behavioral",
        "agent_id": agent_id,
        "capabilities": sorted(BEHAVIORAL_RUNTIME_CAPABILITIES),
        "setup_checks": _setup_checks(model_tool_behavior),
    }


def _validate_runtime_verification(value: Any, agent_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("agent_id") != agent_id:
        raise SetupConflict(
            "unsupported installed-state snapshot: runtime verification identity"
        )
    setup_checks = value.get("setup_checks")
    valid_model_tool_behaviors = {
        "verified",
        "warning_no_tool_call",
        "warning_invalid_tool_call",
    }
    if (
        not isinstance(setup_checks, dict)
        or set(setup_checks)
        != {
            "channel_policy",
            "schema_transport",
            "model_tool_behavior",
            "diagnostic_cleanup",
        }
        or setup_checks.get("channel_policy")
        != [DASHBOARD_CHANNEL, INTERNAL_ANALYSIS_CHANNEL]
        or setup_checks.get("schema_transport") != "accepted"
        or setup_checks.get("model_tool_behavior")
        not in valid_model_tool_behaviors
        or setup_checks.get("diagnostic_cleanup") != "verified"
    ):
        raise SetupConflict(
            "unsupported installed-state snapshot: setup verification checks"
        )
    mode = value.get("mode")
    if mode == "authoritative_inventory":
        if set(value) != {"mode", "agent_id", "hooks", "setup_checks"} or value.get(
            "hooks"
        ) != sorted(REQUIRED_PLUGIN_HOOKS):
            raise SetupConflict(
                "unsupported installed-state snapshot: runtime hook inventory"
            )
    elif mode == "production_behavioral":
        if set(value) != {
            "mode",
            "agent_id",
            "capabilities",
            "setup_checks",
        } or value.get("capabilities") != sorted(BEHAVIORAL_RUNTIME_CAPABILITIES):
            raise SetupConflict(
                "unsupported installed-state snapshot: runtime behavioral proof"
            )
    else:
        raise SetupConflict(
            "unsupported installed-state snapshot: runtime verification mode"
        )
    return value


def _read_config_snapshot(cli: OpenClawCLI, path: str) -> ConfigValueSnapshot:
    result = cli.run(["openclaw", "config", "get", path, "--json"])
    if result.returncode != 0:
        if _is_missing_config_path(result, path):
            return ConfigValueSnapshot(path, False, None)
        detail = (result.stderr or result.stdout).strip()
        raise SetupConflict(f"could not snapshot {path}: {detail}")
    return ConfigValueSnapshot(path, True, _json(result, f"{path} snapshot"))


def _validate_global_tool_search_for_client_functions(cli: OpenClawCLI) -> None:
    snapshot = _read_config_snapshot(cli, TOOL_SEARCH_CONFIG_PATH)
    if not snapshot.existed:
        return
    value = snapshot.value
    if value is False:
        return
    if value is True:
        enabled = True
        mode = "code"
    elif isinstance(value, dict):
        raw_enabled = value.get("enabled")
        if raw_enabled is not None and not isinstance(raw_enabled, bool):
            raise SetupConflict("global Tool Search has an unsupported enabled value")
        configured = any(key != "enabled" for key in value)
        enabled = raw_enabled if isinstance(raw_enabled, bool) else configured
        mode = value.get("mode", "code")
        if not isinstance(mode, str):
            raise SetupConflict("global Tool Search has an unsupported mode value")
    else:
        raise SetupConflict("global Tool Search has an unsupported configuration")
    if not enabled or mode == "directory":
        return
    raise SetupConflict(
        "global Tool Search would hide caller-supplied CRM functions from the local "
        "model. Disable tools.toolSearch or use directory mode before setup; this "
        "installer will not change unrelated agents' global tool surface"
    )


def _require_exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SetupConflict(f"unsupported installed-state snapshot: {label}")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise SetupConflict(f"unsupported installed-state snapshot: {label}")
    return value


def _validate_tree_manifest(value: Any, label: str) -> dict[str, Any]:
    manifest = _require_exact_keys(value, {"sha256", "entries"}, label)
    entries = manifest["entries"]
    if not isinstance(entries, list) or not entries or len(entries) > MAX_MATERIAL_ENTRIES:
        raise SetupConflict(f"unsupported installed-state snapshot: {label}")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        entry = _require_exact_keys(
            item, {"path", "mode", "size", "sha256"}, f"{label} entry"
        )
        path = entry["path"]
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 512
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(ord(character) < 32 for character in path)
            or path in seen
        ):
            raise SetupConflict(f"unsupported installed-state snapshot: {label} path")
        if entry["mode"] not in {"100644", "100755"}:
            raise SetupConflict(f"unsupported installed-state snapshot: {label} mode")
        if (
            not isinstance(entry["size"], int)
            or isinstance(entry["size"], bool)
            or entry["size"] < 0
            or entry["size"] > 64 * 1024 * 1024
        ):
            raise SetupConflict(f"unsupported installed-state snapshot: {label} size")
        _require_sha256(entry["sha256"], f"{label} entry digest")
        seen.add(path)
        validated.append(entry)
    if [entry["path"] for entry in validated] != sorted(seen):
        raise SetupConflict(f"unsupported installed-state snapshot: {label} order")
    digest = _require_sha256(manifest["sha256"], f"{label} digest")
    if digest != hashlib.sha256(_canonical_json_bytes(validated)).hexdigest():
        raise SetupConflict(f"unsupported installed-state snapshot: {label} digest")
    return manifest


def validate_installed_state_snapshot(value: Any) -> dict[str, Any]:
    """Validate the complete privacy-safe state used for setup idempotence proof."""
    root = _require_exact_keys(
        value,
        {
            "schema_version",
            "sources",
            "installed",
            "plugin",
            "agent",
            "bindings",
            "approvals",
            "gateway",
        },
        "root",
    )
    if root["schema_version"] != 2:
        raise SetupConflict("unsupported installed-state snapshot: schema version")

    sources = _require_exact_keys(
        root["sources"],
        {"material_tree_sha256", "skills", "plugin", "shared"},
        "sources",
    )
    installed = _require_exact_keys(root["installed"], {"skills"}, "installed")
    for holder, label in ((sources, "sources"), (installed, "installed")):
        skills = holder["skills"]
        if not isinstance(skills, dict) or set(skills) != set(SKILL_NAMES):
            raise SetupConflict(f"unsupported installed-state snapshot: {label} skills")
        for name in SKILL_NAMES:
            _validate_tree_manifest(skills[name], f"{label} {name} skill")
    plugin_source = _validate_tree_manifest(sources["plugin"], "plugin source")
    shared_source = _validate_tree_manifest(sources["shared"], "shared source")
    if sources["skills"] != installed["skills"]:
        raise SetupConflict("unsupported installed-state snapshot: installed skill digest")
    daily_entries = {
        entry["path"]: entry
        for entry in sources["skills"]["daily-brief"]["entries"]
    }
    if daily_entries.get("scripts/run_daily_brief.py", {}).get("mode") != "100755":
        raise SetupConflict("unsupported installed-state snapshot: daily brief mode")
    crm_entries = {
        entry["path"]: entry
        for entry in sources["skills"]["crm-db-operations"]["entries"]
    }
    if crm_entries.get("cli.py", {}).get("mode") != "100755":
        raise SetupConflict("unsupported installed-state snapshot: CRM CLI mode")
    plugin_paths = {entry["path"] for entry in plugin_source["entries"]}
    if not {
        "dist/index.js",
        "openclaw.plugin.json",
        "package.json",
    }.issubset(plugin_paths):
        raise SetupConflict("unsupported installed-state snapshot: plugin source")
    shared_paths = {entry["path"] for entry in shared_source["entries"]}
    if not {
        "backend/app/briefing_contract.py",
        "scripts/acceptance_openclaw.py",
        "scripts/capture_setup_evidence.py",
        "scripts/doctor.py",
        "scripts/setup_openclaw.py",
    }.issubset(shared_paths):
        raise SetupConflict("unsupported installed-state snapshot: shared source")
    setup_entry = next(
        entry
        for entry in shared_source["entries"]
        if entry["path"] == "scripts/setup_openclaw.py"
    )
    if setup_entry["mode"] != "100755":
        raise SetupConflict("unsupported installed-state snapshot: setup entrypoint mode")
    material = {
        "skills": sources["skills"],
        "plugin": sources["plugin"],
        "shared": sources["shared"],
    }
    if _require_sha256(
        sources["material_tree_sha256"], "material tree digest"
    ) != hashlib.sha256(_canonical_json_bytes(material)).hexdigest():
        raise SetupConflict("unsupported installed-state snapshot: material tree digest")

    plugin = _require_exact_keys(
        root["plugin"],
        {
            "registered",
            "enabled",
            "allowlist",
            "config",
            "runtime_tools",
            "runtime_verification",
        },
        "plugin",
    )
    if plugin["registered"] is not True or plugin["enabled"] is not True:
        raise SetupConflict("unsupported installed-state snapshot: plugin status")
    allowlist = _require_exact_keys(
        plugin["allowlist"], {"configured", "entries"}, "plugin allowlist"
    )
    if not isinstance(allowlist["configured"], bool):
        raise SetupConflict("unsupported installed-state snapshot: plugin allowlist")
    entries = allowlist["entries"]
    if (
        not isinstance(entries, list)
        or not all(isinstance(item, str) and item for item in entries)
        or entries != sorted(set(entries))
        or (allowlist["configured"] and PLUGIN_ID not in entries)
        or (not allowlist["configured"] and entries)
    ):
        raise SetupConflict("unsupported installed-state snapshot: plugin allowlist")
    plugin_config = _require_exact_keys(plugin["config"], {"agent_id"}, "plugin config")
    _require_canonical_agent_id(plugin_config["agent_id"], "snapshot plugin agent ID")
    if plugin["runtime_tools"] != [PLUGIN_TOOL]:
        raise SetupConflict("unsupported installed-state snapshot: plugin runtime tools")
    runtime_verification = _validate_runtime_verification(
        plugin["runtime_verification"], plugin_config["agent_id"]
    )

    agent = _require_exact_keys(
        root["agent"],
        {
            "id",
            "workspace_matches",
            "skills",
            "tools",
            "sandbox",
            "thinking_default",
            "local_model_lean",
        },
        "agent",
    )
    _require_canonical_agent_id(agent["id"], "snapshot agent ID")
    if agent["id"] != plugin_config["agent_id"] or agent["workspace_matches"] is not True:
        raise SetupConflict("unsupported installed-state snapshot: agent identity")
    if runtime_verification["agent_id"] != agent["id"]:
        raise SetupConflict(
            "unsupported installed-state snapshot: runtime verification identity"
        )
    if agent["skills"] != list(SKILL_NAMES):
        raise SetupConflict("unsupported installed-state snapshot: agent skills")
    _validate_authoritative_tools(agent["tools"])
    if agent["sandbox"] != DESIRED_SANDBOX:
        raise SetupConflict("unsupported installed-state snapshot: agent sandbox")
    if agent["thinking_default"] != CRM_THINKING_DEFAULT:
        raise SetupConflict("unsupported installed-state snapshot: agent thinking default")
    if agent["local_model_lean"] is not CRM_LOCAL_MODEL_LEAN:
        raise SetupConflict("unsupported installed-state snapshot: agent local model mode")

    bindings = _require_exact_keys(root["bindings"], {"count", "sha256"}, "bindings")
    if (
        not isinstance(bindings["count"], int)
        or isinstance(bindings["count"], bool)
        or not 0 <= bindings["count"] <= MAX_BINDINGS
    ):
        raise SetupConflict("unsupported installed-state snapshot: bindings")
    _require_sha256(bindings["sha256"], "bindings digest")

    approvals = _require_exact_keys(
        root["approvals"],
        {"patterns", "daily_brief_sha256", "daily_brief_mode", "effective"},
        "approvals",
    )
    if approvals["patterns"] != ["daily-brief"]:
        raise SetupConflict("unsupported installed-state snapshot: approval patterns")
    _require_sha256(approvals["daily_brief_sha256"], "daily brief digest")
    if approvals["daily_brief_mode"] != "100755":
        raise SetupConflict("unsupported installed-state snapshot: daily brief mode")
    effective = _require_exact_keys(
        approvals["effective"],
        {"host", "mode", "security", "ask", "ask_fallback"},
        "effective approval policy",
    )
    if (
        effective["host"] != "gateway"
        or effective["mode"] != "allowlist"
        or effective["security"] != "allowlist"
        or effective["ask"] != "off"
        or effective["ask_fallback"] not in {"deny", "allowlist"}
    ):
        raise SetupConflict("unsupported installed-state snapshot: effective approval policy")

    gateway = _require_exact_keys(
        root["gateway"],
        {
            "crm_api_url_sha256",
            "api_token_ref",
            "gateway_env",
            "gateway_url_sha256",
            "chat_path_sha256",
        },
        "gateway",
    )
    _require_sha256(gateway["crm_api_url_sha256"], "CRM URL digest")
    _require_sha256(gateway["gateway_url_sha256"], "gateway URL digest")
    token_ref = _require_exact_keys(
        gateway["api_token_ref"], {"configured", "value"}, "API token reference"
    )
    if token_ref not in (
        {"configured": False, "value": None},
        {"configured": True, "value": TOKEN_SECRETREF},
    ):
        raise SetupConflict("unsupported installed-state snapshot: API token reference")
    gateway_env = _require_exact_keys(
        gateway["gateway_env"],
        {"configured", "mode", "token_present", "matches_process_token"},
        "gateway environment",
    )
    if not isinstance(gateway_env["configured"], bool) or not isinstance(
        gateway_env["token_present"], bool
    ):
        raise SetupConflict("unsupported installed-state snapshot: gateway environment")
    if gateway_env["configured"]:
        if gateway_env["mode"] != 0o600:
            raise SetupConflict("unsupported installed-state snapshot: gateway environment mode")
    elif gateway_env["mode"] is not None or gateway_env["token_present"]:
        raise SetupConflict("unsupported installed-state snapshot: gateway environment")
    if gateway_env["matches_process_token"] not in (None, True):
        raise SetupConflict("unsupported installed-state snapshot: gateway environment")
    if token_ref["configured"] and (
        not gateway_env["configured"]
        or not gateway_env["token_present"]
        or gateway_env["matches_process_token"] is not True
    ):
        raise SetupConflict(
            "unsupported installed-state snapshot: gateway token environment"
        )
    _require_sha256(gateway["chat_path_sha256"], "chat path digest")
    return root


def canonical_installed_state_digest(value: Any) -> str:
    validated = validate_installed_state_snapshot(value)
    return hashlib.sha256(_canonical_json_bytes(validated)).hexdigest()


def _validated_private_path(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > MAX_PRIVATE_RUNTIME_VALUE
        or "?" in value
        or "#" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise SetupConflict(f"{label} has an unsupported value")
    return value


def _configured_bindings_snapshot(cli: OpenClawCLI) -> dict[str, Any]:
    snapshot = _read_config_snapshot(cli, "bindings")
    if not snapshot.existed:
        return {"count": 0, "sha256": hashlib.sha256(b"[]").hexdigest()}
    if not isinstance(snapshot.value, list) or len(snapshot.value) > MAX_BINDINGS:
        raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
    result: list[dict[str, str]] = []
    for binding in snapshot.value:
        if not isinstance(binding, dict):
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
        agent_id = _require_canonical_agent_id(
            binding.get("agentId"), "OpenClaw binding agentId"
        )
        match = binding.get("match")
        if not isinstance(match, dict):
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
        channel = match.get("channel")
        if (
            not isinstance(channel, str)
            or not channel
            or len(channel) > MAX_PRIVATE_RUNTIME_VALUE
            or any(ord(character) < 32 for character in channel)
        ):
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
        try:
            encoded = _canonical_json_bytes(binding)
        except (TypeError, ValueError) as exc:
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape") from exc
        if len(encoded) > 16 * 1024:
            raise SetupConflict("OpenClaw bindings have an unsupported JSON shape")
        result.append(
            {
                "agent_id": agent_id,
                "binding_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    canonical = sorted(
        result,
        key=lambda item: (
            item["agent_id"],
            item["binding_sha256"],
        ),
    )
    return {
        "count": len(canonical),
        "sha256": hashlib.sha256(_canonical_json_bytes(canonical)).hexdigest(),
    }


def capture_installed_state(
    options: SetupOptions,
    cli: OpenClawCLI,
    *,
    runtime_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture the complete, canonical, secret-free state established by setup."""
    _validate_requested_agent_id(options.agent_id)
    model_tool_behavior = "verified"
    if runtime_verification is not None:
        prior_runtime_verification = _validate_runtime_verification(
            runtime_verification, options.agent_id
        )
        model_tool_behavior = prior_runtime_verification["setup_checks"][
            "model_tool_behavior"
        ]
    _validate_global_tool_search_for_client_functions(cli)
    repo = Path(__file__).resolve().parents[1]
    sources = _material_head_state(
        repo, deadline_check=getattr(cli, "require_time", None)
    )
    source_skills = sources["skills"]
    installed_skills = {
        name: _installed_tree_manifest(
            options.workspace / "skills" / name,
            source_skills[name],
            f"installed {name} skill",
            deadline_check=getattr(cli, "require_time", None),
        )
        for name in SKILL_NAMES
    }
    if source_skills != installed_skills:
        raise SetupConflict("installed skill digest does not match the shipped source")

    plugin_source = _plugin_source(repo)
    inventory = _run_required(
        cli, ["openclaw", "plugins", "list", "--json"], "installed plugin inventory"
    )
    registered, enabled = _inspect_plugin_inventory(
        _json(inventory, "installed plugin inventory"),
        plugin_source,
        require_present=True,
    )
    runtime = _run_required(
        cli,
        ["openclaw", "plugins", "inspect", PLUGIN_ID, "--runtime", "--json"],
        "openhouse-crm runtime inspection",
    )
    has_hook_inventory = _validate_runtime_plugin(
        _json(runtime, "openhouse-crm runtime inspection"), plugin_source
    )
    if has_hook_inventory:
        current_runtime_verification = _inventory_runtime_verification(
            options.agent_id, model_tool_behavior
        )
    else:
        current_runtime_verification = _fresh_production_behavioral_verification(
            cli, options.agent_id, model_tool_behavior
        )

    plugin_allow = _read_plugin_allowlist(cli)
    plugin_config = _read_config_snapshot(cli, PLUGIN_CONFIG_PATH)
    if not plugin_config.existed or plugin_config.value != {"agentId": options.agent_id}:
        raise SetupConflict("OpenClaw plugin configuration does not target the CRM agent")
    plugin_hooks = _read_config_snapshot(cli, PLUGIN_HOOKS_PATH)
    if (
        not plugin_hooks.existed
        or not isinstance(plugin_hooks.value, dict)
        or plugin_hooks.value.get("allowConversationAccess") is not True
    ):
        raise SetupConflict(
            "OpenClaw plugin conversation hook permission is not enabled"
        )

    roster = _read_agent_roster(
        cli, allow_missing=False, label="installed-state agent config"
    )
    agents = [record for record in roster.records if record.get("id") == options.agent_id]
    if len(agents) != 1:
        raise SetupConflict("installed-state snapshot could not identify exactly one CRM agent")
    agent = agents[0]
    configured_workspace = agent.get("workspace") or agent.get("workspacePath")
    if not _same_workspace(configured_workspace, options.workspace):
        raise SetupConflict("installed-state CRM agent workspace does not match")
    tools = agent.get("tools")
    _validate_authoritative_tools(tools)
    experimental = agent.get("experimental")
    if (
        agent.get("skills") != list(SKILL_NAMES)
        or agent.get("sandbox") != DESIRED_SANDBOX
        or agent.get("thinkingDefault") != CRM_THINKING_DEFAULT
        or not isinstance(experimental, dict)
        or experimental.get("localModelLean") is not CRM_LOCAL_MODEL_LEAN
    ):
        raise SetupConflict("installed-state CRM agent policy does not match setup")

    _, daily = _entrypoints(options)
    daily_digest = _read_regular_file_digest(daily, "installed daily-brief runner")[1]
    approvals_result = _run_required(
        cli,
        ["openclaw", "approvals", "get", "--gateway", "--json"],
        "gateway approvals inspection",
    )
    approvals_payload = _json(approvals_result, "gateway approvals inspection")
    patterns = _validate_gateway_approval_payload(
        approvals_payload, options.agent_id, require_effective=True
    )
    if patterns != {str(daily)}:
        raise SetupConflict("installed-state approval patterns do not match setup")
    scope = _effective_agent_scope(approvals_payload, options.agent_id)
    if scope is None:
        raise SetupConflict("installed-state effective approval policy is unavailable")

    crm_url = _read_config_snapshot(cli, CRM_URL_CONFIG_PATH)
    if not crm_url.existed or crm_url.value != options.crm_api_url:
        raise SetupConflict("installed-state CRM API URL does not match setup")
    token = _read_config_snapshot(cli, TOKEN_CONFIG_PATH)
    if token.existed and token.value not in (TOKEN_SECRETREF, TOKEN_SECRETREF_REDACTED):
        raise SetupConflict("installed-state CRM API token reference is unsupported")
    gateway_env_snapshot = _snapshot_gateway_env(
        _gateway_env_path(),
        deadline_check=getattr(cli, "require_time", None),
    )
    try:
        gateway_env_contents = gateway_env_snapshot.contents.decode("utf-8")
    except UnicodeError as exc:
        raise SetupConflict("OpenClaw gateway environment is not valid UTF-8") from exc
    env_token = _read_gateway_env_token(gateway_env_contents)
    process_token = os.environ.get("OHI_API_TOKEN")
    matches_process = None if not process_token else env_token == process_token

    state = {
        "schema_version": 2,
        "sources": sources,
        "installed": {"skills": installed_skills},
        "plugin": {
            "registered": registered,
            "enabled": enabled,
            "allowlist": {
                "configured": plugin_allow is not None,
                "entries": sorted(plugin_allow or []),
            },
            "config": {"agent_id": options.agent_id},
            "runtime_tools": [PLUGIN_TOOL],
            "runtime_verification": current_runtime_verification,
        },
        "agent": {
            "id": options.agent_id,
            "workspace_matches": True,
            "skills": list(SKILL_NAMES),
            "tools": tools,
            "sandbox": agent["sandbox"],
            "thinking_default": agent["thinkingDefault"],
            "local_model_lean": experimental["localModelLean"],
        },
        "bindings": _configured_bindings_snapshot(cli),
        "approvals": {
            "patterns": ["daily-brief"],
            "daily_brief_sha256": daily_digest,
            "daily_brief_mode": _normalized_git_mode(os.lstat(daily).st_mode),
            "effective": {
                "host": scope["host"]["requested"],
                "mode": scope["mode"]["effective"],
                "security": scope["security"]["effective"],
                "ask": scope["ask"]["effective"],
                "ask_fallback": scope["askFallback"]["effective"],
            },
        },
        "gateway": {
            "crm_api_url_sha256": hashlib.sha256(options.crm_api_url.encode("utf-8")).hexdigest(),
            "api_token_ref": {
                "configured": token.existed,
                "value": TOKEN_SECRETREF if token.existed else None,
            },
            "gateway_env": {
                "configured": gateway_env_snapshot.existed,
                "mode": gateway_env_snapshot.mode,
                "token_present": env_token is not None,
                "matches_process_token": matches_process,
            },
            "gateway_url_sha256": hashlib.sha256(
                _loopback_gateway_base_url().encode("utf-8")
            ).hexdigest(),
            "chat_path_sha256": hashlib.sha256(
                _validated_private_path(
                    os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions"),
                    "AGENT_CHAT_PATH",
                ).encode("utf-8")
            ).hexdigest(),
        },
    }
    validate_installed_state_snapshot(state)
    return state


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


def _parse_agent_deletion_result(
    result: CommandResult,
    agent_id: str,
    *,
    expected_workspace: Path,
) -> AgentDeletionReport:
    payload = _json(result, "agent deletion")
    if not isinstance(payload, dict):
        raise SetupConflict("agent deletion returned an unsupported JSON shape")
    if payload.get("agentId") != agent_id:
        raise SetupConflict("agent deletion returned the wrong agent ID")
    if not _same_workspace(payload.get("workspace"), expected_workspace):
        raise SetupConflict("agent deletion returned the wrong workspace")
    for field in ("agentDir", "sessionsDir"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SetupConflict(f"agent deletion returned an invalid {field}")
    removed = payload.get("removed")
    if not isinstance(removed, list) or not all(
        isinstance(path, str) for path in removed
    ):
        raise SetupConflict("agent deletion returned an invalid removed-path list")
    failed = payload.get("failed")
    if not isinstance(failed, list):
        raise SetupConflict("agent deletion returned an invalid failed-path list")
    retained_paths: list[str] = []
    for entry in failed:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "reason"}
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("reason"), str)
        ):
            raise SetupConflict("agent deletion returned an invalid failed-path entry")
        retained_paths.append(entry["path"])
    purge_failed = payload.get("purgeFailed", False)
    if not isinstance(purge_failed, bool):
        raise SetupConflict("agent deletion returned an invalid purgeFailed value")
    if "transport" in payload:
        transport = payload["transport"]
        if not isinstance(transport, str) or not transport.strip():
            raise SetupConflict("agent deletion returned an invalid transport")
    return AgentDeletionReport(
        complete=not retained_paths and not purge_failed,
        retry_restart_performed=False,
        retained_paths=tuple(retained_paths),
    )


def _agent_cleanup_inventory(
    cli: OpenClawCLI, agent_id: str, *, label: str
) -> tuple[list[dict[str, Any]], AgentRoster, bool]:
    listed = _run_required(
        cli, ["openclaw", "agents", "list", "--json"], f"{label} list"
    )
    listed_records = _cli_agents(_json(listed, f"{label} list"))
    roster = _read_agent_roster(cli, allow_missing=True, label=f"{label} config")
    absent = agent_id not in {record["id"] for record in listed_records} and (
        agent_id not in {record["id"] for record in roster.records}
    )
    return listed_records, roster, absent


def _delete_agent_and_verify(
    cli: OpenClawCLI, agent_id: str, *, expected_workspace: Path
) -> AgentDeletionReport:
    incomplete = AgentDeletionReport(False, False, ())
    delete_argv = [
        "openclaw",
        "agents",
        "delete",
        agent_id,
        "--force",
        "--json",
    ]
    try:
        listed_records, roster, initially_absent = _agent_cleanup_inventory(
            cli, agent_id, label="agent cleanup"
        )
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
            return incomplete
        if initially_absent:
            return AgentDeletionReport(True, False, ())

        deleted = cli.run(delete_argv, mutate=True)
        if deleted.returncode != 0:
            return incomplete
        first_report = _parse_agent_deletion_result(
            deleted, agent_id, expected_workspace=expected_workspace
        )
        _, _, first_absent = _agent_cleanup_inventory(
            cli, agent_id, label="agent cleanup verification"
        )
        if not first_absent:
            return incomplete
        if first_report.complete:
            return first_report

        restart = cli.run(["openclaw", "gateway", "restart"], mutate=True)
        if restart.returncode != 0:
            return AgentDeletionReport(False, True, first_report.retained_paths)
        retried = cli.run(delete_argv, mutate=True)
        if retried.returncode != 0:
            return AgentDeletionReport(False, True, first_report.retained_paths)
        retry_report = _parse_agent_deletion_result(
            retried, agent_id, expected_workspace=expected_workspace
        )
        _, _, finally_absent = _agent_cleanup_inventory(
            cli, agent_id, label="agent cleanup retry verification"
        )
        return AgentDeletionReport(
            complete=retry_report.complete and finally_absent,
            retry_restart_performed=True,
            retained_paths=retry_report.retained_paths,
        )
    except (OSError, SetupConflict):
        return incomplete


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
        ("thinkingDefault", CRM_THINKING_DEFAULT),
        ("experimental", {"localModelLean": CRM_LOCAL_MODEL_LEAN}),
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


def _remove_diagnostic_workspace(
    root: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
    local_runner: LocalOperationRunner | None = None,
) -> bool:
    try:
        if not root.exists() and not root.is_symlink():
            return True
        _validate_no_symlink_components(
            root, "setup diagnostic workspace", leaf_directory=True
        )
        _validate_skill_tree(
            root,
            "setup diagnostic workspace",
            deadline_check=deadline_check,
        )
        bounded = _run_local_if_bounded(
            "rmtree",
            {"path": str(root)},
            deadline_check=deadline_check,
            local_runner=local_runner,
        )
        if bounded is None:
            shutil.rmtree(root)
        elif not bounded:
            return False
        return not root.exists() and not root.is_symlink()
    except (OSError, SetupConflict):
        return False


def _create_diagnostic_session(cli: OpenClawCLI, agent_id: str) -> tuple[str, str]:
    requested_key = (
        f"agent:{agent_id}:dashboard:openhouse-setup-{secrets.token_hex(12)}"
    )
    params = json.dumps(
        {"agentId": agent_id, "key": requested_key}, separators=(",", ":")
    )
    result = cli.run(
        [
            "openclaw",
            "gateway",
            "call",
            "sessions.create",
            "--params",
            params,
            "--timeout",
            "15000",
            "--json",
        ],
        mutate=True,
    )
    if result.returncode != 0:
        raise SetupConflict(
            "Could not create the setup diagnostic session: "
            + (result.stderr or result.stdout).strip()
        )
    payload = _json(result, "diagnostic session creation")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SetupConflict("OpenClaw returned an unsupported diagnostic session record")
    session_key = payload.get("key")
    session_id = payload.get("sessionId")
    if (
        session_key != requested_key
        or not isinstance(session_id, str)
        or not session_id
        or payload.get("runStarted") is not False
    ):
        raise SetupConflict("OpenClaw returned an unsupported diagnostic session record")
    return session_key, session_id


def _delete_diagnostic_session(
    cli: OpenClawCLI, agent_id: str, session_key: str, session_id: str
) -> None:
    params = json.dumps(
        {
            "key": session_key,
            "agentId": agent_id,
            "deleteTranscript": True,
            "expectedSessionId": session_id,
        },
        separators=(",", ":"),
    )
    result = cli.run(
        [
            "openclaw",
            "gateway",
            "call",
            "sessions.delete",
            "--params",
            params,
            "--timeout",
            "15000",
            "--json",
        ],
        mutate=True,
    )
    if result.returncode != 0:
        raise SetupConflict(
            "Could not delete the setup diagnostic session: "
            + (result.stderr or result.stdout).strip()
        )
    payload = _json(result, "diagnostic session deletion")
    archived = payload.get("archived") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("key") != session_key
        or payload.get("deleted") is not True
        or not isinstance(archived, list)
        or not all(isinstance(item, str) and item for item in archived)
    ):
        raise SetupConflict("OpenClaw did not confirm diagnostic session deletion")


def _create_tracked_diagnostic_session(
    cli: OpenClawCLI,
    agent_id: str,
    tracker: DiagnosticSessionTracker,
) -> DiagnosticSessionHandle:
    if tracker.active is not None:
        raise SetupConflict(
            "A setup-owned diagnostic session is still active; refusing to create "
            "another session before cleanup is verified"
        )
    session_key, session_id = _create_diagnostic_session(cli, agent_id)
    handle = DiagnosticSessionHandle(agent_id, session_key, session_id)
    tracker.active = handle
    return handle


def _delete_tracked_diagnostic_session(
    cli: OpenClawCLI,
    tracker: DiagnosticSessionTracker,
) -> None:
    handle = tracker.active
    if handle is None:
        return
    _delete_diagnostic_session(
        cli,
        handle.agent_id,
        handle.key,
        handle.session_id,
    )
    tracker.active = None


def _verify_diagnostic_effective_tools(
    cli: OpenClawCLI, agent_id: str, session_key: str | None = None
) -> None:
    owned_session_id: str | None = None
    if session_key is None:
        session_key, owned_session_id = _create_diagnostic_session(cli, agent_id)
    params = json.dumps(
        {"sessionKey": session_key, "agentId": agent_id}, separators=(",", ":")
    )
    try:
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
            or payload.get("profile") != "full"
        ):
            raise SetupConflict(
                "OpenClaw did not expose an authoritative setup sentinel tool inventory"
            )
        effective_names: list[str] = []
        if "groups" in payload:
            groups = payload["groups"]
            if not isinstance(groups, list):
                raise SetupConflict(
                    "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                )
            group_ids: list[str] = []
            for group in groups:
                if (
                    not isinstance(group, dict)
                    or not isinstance(group.get("id"), str)
                    or not group["id"]
                    or not isinstance(group.get("source"), str)
                    or not group["source"]
                    or not isinstance(group.get("tools"), list)
                ):
                    raise SetupConflict(
                        "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                    )
                group_ids.append(group["id"])
                for tool in group["tools"]:
                    if (
                        not isinstance(tool, dict)
                        or not isinstance(tool.get("id"), str)
                        or not tool["id"]
                    ):
                        raise SetupConflict(
                            "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                        )
                    effective_names.append(tool["id"])
            if len(group_ids) != len(set(group_ids)):
                raise SetupConflict(
                    "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                )
        else:
            found = payload.get("found")
            if not isinstance(found, list):
                raise SetupConflict(
                    "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                )
            for group in found:
                if (
                    not isinstance(group, list)
                    or len(group) != 2
                    or not isinstance(group[0], str)
                    or not group[0]
                    or not isinstance(group[1], list)
                    or not all(isinstance(name, str) and name for name in group[1])
                ):
                    raise SetupConflict(
                        "OpenClaw did not expose an authoritative setup sentinel tool inventory"
                    )
                effective_names.extend(group[1])
        if len(effective_names) != len(set(effective_names)):
            raise SetupConflict(
                "OpenClaw did not expose an authoritative setup sentinel tool inventory"
            )
        effective = set(effective_names)
        if effective != {SETUP_MARKER_TOOL}:
            raise SetupConflict(
                "OpenClaw diagnostic agent must expose exactly the setup sentinel tool"
            )
    finally:
        if owned_session_id is not None:
            _delete_diagnostic_session(
                cli, agent_id, session_key, owned_session_id
            )


def _request_client_tool_capability(
    cli: OpenClawCLI,
    agent_id: str,
    nonce: str,
    tools: list[dict[str, Any]],
    *,
    production: bool = False,
    session_key: str | None = None,
) -> CommandResult:
    result = cli.probe_client_tools(
        agent_id=agent_id,
        nonce=nonce,
        tools=tools,
        production=production,
        session_key=session_key,
    )
    if result.returncode == 400:
        raise SetupConflict(
            "unsupported OpenClaw installation: Chat Completions rejected the full "
            "production CRM and finish schemas with tool_choice:\"required\""
        )
    if result.returncode in {401, 403}:
        raise SetupConflict(
            "OpenClaw client-tool capability was not proven because Gateway "
            "authentication failed; configure the matching AGENT_GATEWAY_TOKEN"
        )
    if result.returncode != 200:
        raise SetupConflict(
            "OpenClaw provider/model capability was not proven by the bounded "
            "full production CRM and finish schemas probe"
        )
    return result


def _classify_client_tool_completion(result: CommandResult, nonce: str) -> str:
    """Return verified, warning_no_tool_call, or warning_invalid_tool_call."""
    try:
        payload = _decode_json(result.stdout, "client-tool capability probe")
    except SetupConflict as exc:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        ) from exc
    if not isinstance(payload, dict):
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    message = choice["message"]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "stop":
        if not isinstance(message.get("content"), str) or "tool_calls" in message:
            raise SetupConflict(
                "OpenClaw returned a structurally incompatible client-tool response"
            )
        return "warning_no_tool_call"
    if finish_reason != "tool_calls":
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    call = tool_calls[0]
    if (
        not isinstance(call, dict)
        or call.get("type") != "function"
        or not isinstance(call.get("id"), str)
        or not call["id"]
        or not isinstance(call.get("function"), dict)
    ):
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    function = call["function"]
    if (
        not isinstance(function.get("name"), str)
        or not function["name"]
        or not isinstance(function.get("arguments"), str)
    ):
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        )
    try:
        arguments = _decode_json(
            function["arguments"], "client-tool capability arguments"
        )
    except SetupConflict as exc:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible client-tool response"
        ) from exc
    if function["name"] != "finish_crm_response" or arguments != {
        "classification": "needs_clarification",
        "message": nonce,
        "evidence_call_ids": [],
    }:
        return "warning_invalid_tool_call"
    return "verified"


def _request_analysis_completion(
    cli: OpenClawCLI,
    agent_id: str,
    nonce: str,
    *,
    production: bool = False,
    session_key: str | None = None,
) -> CommandResult:
    result = cli.probe_analysis_tool_block(
        agent_id=agent_id,
        nonce=nonce,
        production=production,
        session_key=session_key,
    )
    if result.returncode in {401, 403}:
        raise SetupConflict(
            "OpenClaw internal-analysis channel capability was not proven because "
            "Gateway authentication failed"
        )
    if result.returncode != 200:
        raise SetupConflict(
            "OpenClaw internal-analysis Chat Completions path was not proven"
        )
    return result


def _verify_analysis_completion(result: CommandResult) -> None:
    try:
        payload = _decode_json(result.stdout, "internal-analysis capability probe")
        choices = payload["choices"]
        choice = choices[0] if len(choices) == 1 else None
        message = choice["message"]
        valid = (
            isinstance(message.get("content"), str)
            and bool(message["content"].strip())
            and not message.get("tool_calls")
        )
    except (KeyError, IndexError, TypeError, SetupConflict):
        valid = False
    if not valid:
        raise SetupConflict(
            "OpenClaw returned a structurally incompatible internal-analysis response"
        )


def _channel_probe_details(payload: Any) -> dict[str, Any] | None:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"ok", "result"}
        or payload.get("ok") is not True
    ):
        return None
    result = payload.get("result")
    if (
        not isinstance(result, dict)
        or set(result) != {"content", "details"}
        or not isinstance(result.get("details"), dict)
    ):
        return None
    content = result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or set(content[0]) != {"type", "text"}
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        return None
    try:
        mirrored = _decode_json(
            content[0]["text"], "mirrored channel marker status"
        )
    except SetupConflict:
        return None
    details = result["details"]
    if mirrored != details or set(details) != {
        "schema_version",
        "channel",
        "nonce",
        "prompt_seen",
        "tool_blocked",
        "sentinel_executed",
    }:
        return None
    return details


def _safe_gateway_error_evidence(result: CommandResult) -> str:
    evidence = f"HTTP {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        return evidence
    error = payload.get("error") if isinstance(payload, dict) else None
    error_type = error.get("type") if isinstance(error, dict) else None
    if (
        isinstance(error_type, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_type) is not None
    ):
        evidence += f", type {error_type}"
    return evidence


def _verify_channel_marker(
    cli: OpenClawCLI,
    agent_id: str,
    nonce: str,
    channel: str,
    *,
    session_key: str | None = None,
) -> None:
    label = "dashboard" if channel == DASHBOARD_CHANNEL else "internal-analysis"
    prompt = cli.probe_channel_prompt(
        agent_id=agent_id,
        nonce=nonce,
        channel=channel,
        session_key=session_key,
    )
    if prompt.returncode != 200:
        raise SetupConflict(
            f"the {label} channel prompt request was not accepted "
            f"({_safe_gateway_error_evidence(prompt)})"
        )
    try:
        prompt_payload = _decode_json(
            prompt.stdout, f"{label} channel prompt response"
        )
        choices = prompt_payload["choices"]
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        message = choice["message"] if isinstance(choice, dict) else None
        prompt_valid = (
            isinstance(choice, dict)
            and choice.get("finish_reason") == "stop"
            and isinstance(message, dict)
            and isinstance(message.get("content"), str)
            and bool(message["content"].strip())
            and not message.get("tool_calls")
        )
    except (KeyError, TypeError, SetupConflict):
        prompt_valid = False
    if not prompt_valid:
        raise SetupConflict(
            f"OpenClaw returned a structurally incompatible {label} channel prompt response"
        )
    attempt = cli.probe_channel_marker_attempt(
        agent_id=agent_id,
        nonce=nonce,
        channel=channel,
        session_key=session_key,
    )
    if attempt.returncode != 403:
        raise SetupConflict(
            f"the {label} setup sentinel attempt was not blocked "
            f"({_safe_gateway_error_evidence(attempt)})"
        )
    result = cli.probe_channel_status(
        agent_id=agent_id,
        nonce=nonce,
        channel=channel,
        session_key=session_key,
    )
    if result.returncode != 200:
        raise SetupConflict(
            f"the {label} channel marker proof status could not be read "
            f"({_safe_gateway_error_evidence(result)})"
        )
    try:
        payload = _decode_json(result.stdout, f"{label} channel marker status")
    except SetupConflict as exc:
        raise SetupConflict(
            f"the {label} channel marker proof returned an incompatible status"
        ) from exc
    details = _channel_probe_details(payload)
    if details is None:
        raise SetupConflict(
            f"the {label} channel marker proof returned an incompatible status"
        )
    if details["schema_version"] != 2:
        raise SetupConflict(
            f"the {label} channel marker schema version was incompatible"
        )
    if details["channel"] != channel:
        raise SetupConflict(
            f"the {label} channel marker channel was not correlated"
        )
    if details["nonce"] != nonce:
        raise SetupConflict(f"the {label} channel marker nonce was not correlated")
    if details["prompt_seen"] is not True:
        raise SetupConflict(f"the {label} prompt was not observed")
    if details["tool_blocked"] is not True:
        raise SetupConflict(f"the {label} setup sentinel was not blocked")
    if details["sentinel_executed"] is not False:
        raise SetupConflict(f"the {label} setup sentinel executed")


def _verify_setup_probe_behavior(
    cli: OpenClawCLI,
    agent_id: str,
    nonce: str,
    tools: list[dict[str, Any]],
    session_key: str,
) -> str:
    _verify_diagnostic_effective_tools(cli, agent_id, session_key)
    _verify_channel_marker(
        cli,
        agent_id,
        nonce,
        DASHBOARD_CHANNEL,
        session_key=session_key,
    )
    _verify_channel_marker(
        cli,
        agent_id,
        nonce,
        INTERNAL_ANALYSIS_CHANNEL,
        session_key=session_key,
    )
    dashboard_completion = _request_client_tool_capability(
        cli,
        agent_id,
        nonce,
        tools,
        session_key=session_key,
    )
    return _classify_client_tool_completion(dashboard_completion, nonce)


def _verify_plugin_config(
    cli: OpenClawCLI, expected: dict[str, Any], label: str
) -> None:
    result = _run_required(
        cli,
        ["openclaw", "config", "get", PLUGIN_CONFIG_PATH, "--json"],
        label,
    )
    if _json(result, label) != expected:
        raise SetupConflict(
            "OpenClaw did not retain the exact bundled CRM plugin configuration"
        )


def _verify_plugin_agent_config(cli: OpenClawCLI, agent_id: str) -> None:
    _verify_plugin_config(
        cli,
        {"agentId": agent_id},
        "configured CRM agent plugin readback",
    )


def _required_plugin_hooks(snapshot: ConfigValueSnapshot) -> dict[str, Any]:
    if snapshot.existed and not isinstance(snapshot.value, dict):
        raise SetupConflict(
            "OpenClaw returned an unsupported CRM plugin hooks configuration"
        )
    hooks = dict(snapshot.value) if snapshot.existed else {}
    hooks["allowConversationAccess"] = True
    return hooks


def _verify_plugin_hooks(cli: OpenClawCLI, expected: dict[str, Any]) -> None:
    snapshot = _read_config_snapshot(cli, PLUGIN_HOOKS_PATH)
    if not snapshot.existed or snapshot.value != expected:
        raise SetupConflict(
            "OpenClaw did not retain the required conversation hook permission "
            "and existing CRM plugin hook settings"
        )


def _verify_configured_agent_guard(
    cli: OpenClawCLI, agent_id: str, *, session_key: str | None = None
) -> None:
    nonce = secrets.token_hex(16)
    result = cli.probe_configured_agent_guard(
        agent_id=agent_id, nonce=nonce, session_key=session_key
    )
    response = f"{result.stdout}\n{result.stderr}".lower()
    expected = f"configured crm agent {agent_id} is protected."
    if result.returncode != 403 or expected not in response:
        raise SetupConflict(
            "the bounded loopback diagnostic did not prove that the configured CRM "
            "agent is protected by the bundled plugin; no supported CRM operation "
            "was executed"
        )


def _verify_production_channel_guard(
    cli: OpenClawCLI,
    agent_id: str,
    nonce: str,
    channel: str,
    *,
    session_key: str | None = None,
) -> None:
    result = cli.probe_production_channel_guard(
        agent_id=agent_id,
        nonce=nonce,
        channel=channel,
        session_key=session_key,
    )
    expected = f"Production CRM channel {channel} nonce {nonce} is protected."
    response = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 403 or expected not in response:
        raise SetupConflict(
            "fresh production CRM channel protection could not be proven for the "
            f"configured agent on {channel}"
        )


def _fresh_production_behavioral_verification_in_session(
    cli: OpenClawCLI,
    agent_id: str,
    model_tool_behavior: str,
    session_key: str,
) -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    _verify_production_channel_guard(
        cli,
        agent_id,
        nonce,
        DASHBOARD_CHANNEL,
        session_key=session_key,
    )
    analysis = _request_analysis_completion(
        cli,
        agent_id,
        nonce,
        production=True,
        session_key=session_key,
    )
    _verify_analysis_completion(analysis)
    _verify_production_channel_guard(
        cli,
        agent_id,
        nonce,
        INTERNAL_ANALYSIS_CHANNEL,
        session_key=session_key,
    )
    _verify_configured_agent_guard(cli, agent_id, session_key=session_key)
    return _behavioral_runtime_verification(agent_id, model_tool_behavior)


def _fresh_production_behavioral_verification(
    cli: OpenClawCLI,
    agent_id: str,
    model_tool_behavior: str,
    tracker: DiagnosticSessionTracker | None = None,
) -> dict[str, Any]:
    owned_tracker = tracker or DiagnosticSessionTracker()
    handle = _create_tracked_diagnostic_session(cli, agent_id, owned_tracker)
    try:
        return _fresh_production_behavioral_verification_in_session(
            cli, agent_id, model_tool_behavior, handle.key
        )
    finally:
        _delete_tracked_diagnostic_session(cli, owned_tracker)


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
    deadline_check: Callable[[], float],
    local_query_runner: LocalQueryRunner | None = None,
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
            for field in MANAGED_AGENT_FIELDS:
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
                if not _skill_trees_match(
                    skill_snapshot.backup_root / name,
                    target,
                    deadline_check=deadline_check,
                    local_query_runner=local_query_runner,
                ):
                    return False
            elif target.exists() or target.is_symlink():
                return False

        if gateway_env_snapshot is not None:
            if not _gateway_env_snapshot_matches(
                gateway_env_snapshot,
                deadline_check=deadline_check,
                local_query_runner=local_query_runner,
            ):
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
    plugin_config_snapshot: ConfigValueSnapshot | None = None
    plugin_config_mutation_attempted = False
    plugin_hooks_snapshot: ConfigValueSnapshot | None = None
    plugin_hooks_mutation_attempted = False
    required_plugin_hooks: dict[str, Any] | None = None
    approvals_original: set[str] = set()
    approvals_mutation_attempted = False
    crm_agent_preexisting = False
    crm_agent_creation_attempted = False
    diagnostic_agent_id: str | None = None
    diagnostic_nonce: str | None = None
    diagnostic_root: Path | None = None
    diagnostic_session_tracker = DiagnosticSessionTracker()
    diagnostic_agent_creation_attempted = False
    diagnostic_cleanup_report: AgentDeletionReport | None = None
    gateway_restart_attempted = False
    model_tool_behavior = "verified"
    try:
        _validate_requested_agent_id(options.agent_id)
        token = os.environ.get("OHI_API_TOKEN", "")
        gateway_env_path: Path | None = None
        if token:
            _validate_api_token(token)
        version = _detect_version(cli)
        messages.append(f"OpenClaw version: {version}")
        _preflight(cli, options)
        _validate_global_tool_search_for_client_functions(cli)
        repo = Path(__file__).resolve().parents[1]
        contract_snapshot = _capture_canonical_contract(repo)
        client_tools_snapshot = _capture_dashboard_client_tools(
            repo, contract_snapshot
        )
        contract_digest = contract_snapshot.digest
        if SETUP_AGENT_GUARD_OPERATION in contract_snapshot.operations:
            raise SetupConflict(
                "setup diagnostic sentinel unexpectedly exists in the canonical contract"
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
        plugin_hooks_snapshot = _read_config_snapshot(cli, PLUGIN_HOOKS_PATH)
        required_plugin_hooks = _required_plugin_hooks(plugin_hooks_snapshot)
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
        _reject_workspace_collisions(
            options.agent_id,
            options.workspace,
            (agents, configured_agents),
        )
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
                    f"Configure the CRM plugin for agent {options.agent_id}",
                    [
                        "openclaw",
                        "config",
                        "set",
                        PLUGIN_CONFIG_PATH,
                        json.dumps(
                            {"agentId": options.agent_id},
                            separators=(",", ":"),
                        ),
                        "--strict-json",
                    ],
                ),
                Action(
                    "Allow the CRM plugin to protect trusted conversation channels",
                    [
                        "openclaw",
                        "config",
                        "set",
                        PLUGIN_HOOKS_PATH,
                        json.dumps(required_plugin_hooks, separators=(",", ":")),
                        "--strict-json",
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
                planned.extend(_config_actions(options, prefix, configured_agent))
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
                "Would prove protected channel propagation and native-tool blocking "
                "through one isolated diagnostic session."
            )
            messages.append(
                "Would verify Chat Completions with the full production CRM and finish "
                'schemas and tool_choice:"required" separately; model behavior is '
                "reported, not trusted."
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
        local_deadline_check = getattr(cli, "require_time", None)
        skill_rollback = _snapshot_installed_skills(
            options.workspace, deadline_check=local_deadline_check
        )
        plugin_config_snapshot = _read_config_snapshot(cli, PLUGIN_CONFIG_PATH)
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
            gateway_env_snapshot = _snapshot_gateway_env(
                gateway_env_path,
                deadline_check=local_deadline_check,
            )
        binding_snapshot = _read_config_snapshot(cli, "bindings")
        diagnostic_agent_id = _new_diagnostic_agent_id(
            {
                *(record["id"] for record in agents),
                *(record["id"] for record in configured_agents),
                *_binding_agent_ids(binding_snapshot),
            }
        )
        diagnostic_nonce = secrets.token_hex(16)
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
                plugin_config_snapshot,
                plugin_hooks_snapshot,
            ],
            crm_agent_id=options.agent_id,
            agent=configured_agent,
            approvals=approvals_original,
            diagnostic_agent_id=diagnostic_agent_id,
            plugin_preexisting=plugin_preexisting,
            plugin_enabled=plugin_previously_enabled,
            plugin_source=plugin_source,
            deadline_check=local_deadline_check,
        )
        if not _recovery_snapshot_is_complete(
            skill_rollback, deadline_check=local_deadline_check
        ):
            raise SetupConflict(
                "The initial private recovery backup did not match its captured anchors"
            )

        if token and gateway_env_path is not None:
            _upsert_gateway_env(
                gateway_env_path,
                token,
                deadline_check=local_deadline_check,
            )

        sync_skills(
            repo,
            options.workspace,
            dry_run=False,
            contract_snapshot=contract_snapshot,
            deadline_check=local_deadline_check,
        )
        _verify_contract_source_unchanged(contract_snapshot)
        _verify_client_tools_source_unchanged(
            client_tools_snapshot, contract_snapshot
        )
        _verify_installed_contract(options.workspace, contract_digest)
        _verify_installed_client_tools(
            options.workspace, client_tools_snapshot, contract_snapshot
        )
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

        plugin_config_mutation_attempted = True
        configure_plugin = cli.run(
            [
                "openclaw",
                "config",
                "set",
                PLUGIN_CONFIG_PATH,
                json.dumps(
                    {"agentId": options.agent_id},
                    separators=(",", ":"),
                ),
                "--strict-json",
            ],
            mutate=True,
        )
        if configure_plugin.returncode != 0:
            raise SetupConflict(
                "Bundled OpenClaw CRM plugin agent configuration failed: "
                + (configure_plugin.stderr or configure_plugin.stdout).strip()
            )
        _verify_plugin_agent_config(cli, options.agent_id)
        messages.append(f"Configured the CRM plugin for agent {options.agent_id}")

        if required_plugin_hooks is None:
            raise SetupConflict("CRM plugin hook policy was not captured before setup")
        plugin_hooks_mutation_attempted = True
        configure_plugin_hooks = cli.run(
            [
                "openclaw",
                "config",
                "set",
                PLUGIN_HOOKS_PATH,
                json.dumps(required_plugin_hooks, separators=(",", ":")),
                "--strict-json",
            ],
            mutate=True,
        )
        if configure_plugin_hooks.returncode != 0:
            raise SetupConflict(
                "Bundled OpenClaw CRM plugin conversation hook permission failed: "
                + (configure_plugin_hooks.stderr or configure_plugin_hooks.stdout).strip()
            )
        _verify_plugin_hooks(cli, required_plugin_hooks)
        messages.append(
            "Enabled the CRM plugin permission required to protect conversation channels"
        )

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
        actions = _config_actions(options, prefix, refreshed_agent)
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
                    field in MANAGED_AGENT_FIELDS
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
        _verify_plugin_agent_config(cli, options.agent_id)
        _verify_plugin_hooks(cli, required_plugin_hooks)

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
        if diagnostic_nonce is None:
            raise SetupConflict("could not allocate a setup marker probe nonce")
        probe_plugin_config = {
            "agentId": options.agent_id,
            "setupProbe": {
                "agentId": diagnostic_agent_id,
                "nonce": diagnostic_nonce,
            },
        }
        configure_probe = cli.run(
            [
                "openclaw",
                "config",
                "set",
                PLUGIN_CONFIG_PATH,
                json.dumps(probe_plugin_config, separators=(",", ":")),
                "--strict-json",
            ],
            mutate=True,
        )
        if configure_probe.returncode != 0:
            raise SetupConflict(
                "Could not enable the temporary setup marker probe: "
                + (configure_probe.stderr or configure_probe.stdout).strip()
            )
        _verify_plugin_config(
            cli, probe_plugin_config, "temporary setup marker probe readback"
        )
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
        _verify_client_tools_source_unchanged(
            client_tools_snapshot, contract_snapshot
        )
        _verify_installed_contract(options.workspace, contract_snapshot.digest)
        _verify_installed_client_tools(
            options.workspace, client_tools_snapshot, contract_snapshot
        )
        diagnostic_session = _create_tracked_diagnostic_session(
            cli, diagnostic_agent_id, diagnostic_session_tracker
        )
        try:
            model_tool_behavior = _verify_setup_probe_behavior(
                cli,
                diagnostic_agent_id,
                diagnostic_nonce,
                client_tools_snapshot.tools,
                diagnostic_session.key,
            )
        finally:
            _delete_tracked_diagnostic_session(cli, diagnostic_session_tracker)
        _verify_plugin_config(
            cli, probe_plugin_config, "temporary setup marker probe readback"
        )
        _verify_configured_agent_guard(cli, options.agent_id)
        remove_probe = cli.run(
            [
                "openclaw",
                "config",
                "set",
                PLUGIN_CONFIG_PATH,
                json.dumps(
                    {"agentId": options.agent_id}, separators=(",", ":")
                ),
                "--strict-json",
            ],
            mutate=True,
        )
        if remove_probe.returncode != 0:
            raise SetupConflict(
                "Could not remove the temporary setup marker probe: "
                + (remove_probe.stderr or remove_probe.stdout).strip()
            )
        _verify_plugin_agent_config(cli, options.agent_id)
        diagnostic_cleanup_report = _delete_agent_and_verify(
            cli, diagnostic_agent_id, expected_workspace=diagnostic_workspace
        )
        if not diagnostic_cleanup_report.complete:
            raise SetupConflict(
                "Could not delete and verify absence of the setup diagnostic agent"
            )
        if not _remove_diagnostic_workspace(
            diagnostic_root,
            deadline_check=local_deadline_check,
        ):
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
        final_runtime_plugin = _run_required(
            cli,
            ["openclaw", "plugins", "inspect", PLUGIN_ID, "--runtime", "--json"],
            "final openhouse-crm runtime inspection",
        )
        final_has_hook_inventory = _validate_runtime_plugin(
            _json(final_runtime_plugin, "final openhouse-crm runtime inspection"),
            plugin_source,
        )
        _verify_plugin_agent_config(cli, options.agent_id)
        _verify_plugin_hooks(cli, required_plugin_hooks)
        runtime_verification = (
            _inventory_runtime_verification(options.agent_id, model_tool_behavior)
            if final_has_hook_inventory
            else _fresh_production_behavioral_verification(
                cli,
                options.agent_id,
                model_tool_behavior,
                diagnostic_session_tracker,
            )
        )
        if skill_rollback is not None:
            if not _recovery_snapshot_is_complete(
                skill_rollback, deadline_check=local_deadline_check
            ):
                raise SetupConflict(
                    "The private recovery backup could not be revalidated as complete"
                )
            messages.append(
                f"Recovery backup retained at {skill_rollback.backup_root}"
            )
            messages.append(
                "This private backup was revalidated as complete. Keep it until you "
                "confirm dashboard chat works, then securely remove that exact directory."
            )
            skill_rollback = None
        rollback = None
        if model_tool_behavior != "verified":
            messages.append(
                "Compatibility warning: the configured model accepted the production "
                "schemas but did not produce a valid CRM client-tool call. Setup proved "
                "channel policy only. Run doctor and live acceptance before using CRM chat."
            )
        messages.append(
            "Validated the native CRM tool, required CRM outcome hooks, full production "
            "CRM and finish schemas transport separately, protected channel propagation and native-tool "
            "blocking through an isolated session, "
            "configured-agent guard, and restricted agent configuration, "
            "then restarted the OpenClaw Gateway for validation and diagnostic cleanup. "
            "Model behavior is reported, not trusted. "
            "Runtime CRM verification is still required: "
            "python scripts/doctor.py --live-agent --live-crm"
        )
        if not options.bind_discord:
            messages.append(
                "Optional Discord binding: openclaw agents bind --agent "
                f"{options.agent_id} --bind discord:ACCOUNT --json"
            )
        return SetupResult(True, messages, runtime_verification)
    except (SetupConflict, OSError) as exc:
        rollback_failed = False
        begin_rollback = getattr(cli, "begin_rollback", None)
        if callable(begin_rollback):
            try:
                begin_rollback()
            except Exception:
                rollback_failed = True
                messages.append(
                    "Could not start the bounded automatic rollback window; "
                    "manual recovery may be required."
                )

        # Reverse the disposable diagnostic state first. Its random ID and exact
        # workspace are both checked before any destructive agent operation.
        if diagnostic_session_tracker.active is not None:
            try:
                _delete_tracked_diagnostic_session(cli, diagnostic_session_tracker)
            except (OSError, SetupConflict):
                rollback_failed = True
                messages.append(
                    "Could not delete the setup diagnostic session before agent cleanup."
                )
        retained_diagnostic_agent = (
            diagnostic_session_tracker.active is not None
            and diagnostic_session_tracker.active.agent_id == diagnostic_agent_id
        )
        diagnostic_workspace = (
            diagnostic_root / "workspace"
            if diagnostic_root is not None
            else Path("/__openhouse_missing_diagnostic_workspace__")
        )
        if retained_diagnostic_agent:
            rollback_failed = True
            messages.append(
                "OpenClaw could not verify diagnostic session deletion, so setup "
                "retained the setup diagnostic agent and workspace for safe manual "
                f"recovery: agent {diagnostic_agent_id}, workspace "
                f"{diagnostic_workspace}."
            )
        elif diagnostic_agent_creation_attempted and diagnostic_agent_id is not None:
            if diagnostic_cleanup_report is None:
                diagnostic_cleanup_report = _delete_agent_and_verify(
                    cli,
                    diagnostic_agent_id,
                    expected_workspace=diagnostic_workspace,
                )
            if not diagnostic_cleanup_report.complete:
                rollback_failed = True
                messages.append(
                    "Could not delete or verify absence of the setup diagnostic agent."
                )
                if diagnostic_cleanup_report.retained_paths:
                    messages.append(
                        "OpenClaw retained diagnostic state paths: "
                        + ", ".join(diagnostic_cleanup_report.retained_paths)
                    )
        if (
            not retained_diagnostic_agent
            and (
                diagnostic_cleanup_report is None
                or diagnostic_cleanup_report.complete
            )
            and diagnostic_root is not None
            and not _remove_diagnostic_workspace(
                diagnostic_root,
                deadline_check=getattr(cli, "require_time", None),
            )
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
                crm_agent_deletion = _delete_agent_and_verify(
                    cli,
                    options.agent_id,
                    expected_workspace=options.workspace,
                )
                if not crm_agent_deletion.complete:
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

        # OpenClaw plugin install and uninstall can rewrite the plugin entry.
        # Restore setup-owned entry fields only after the plugin lifecycle is back
        # at its original state so an uninstall cannot discard orphaned settings.
        if plugin_config_mutation_attempted and plugin_config_snapshot is not None:
            if not _restore_config_snapshot(cli, plugin_config_snapshot):
                rollback_failed = True
                messages.append(
                    "Could not restore the previous CRM plugin agent configuration."
                )
        if plugin_hooks_mutation_attempted and plugin_hooks_snapshot is not None:
            if not _restore_config_snapshot(cli, plugin_hooks_snapshot):
                rollback_failed = True
                messages.append(
                    "Could not restore the previous CRM plugin hook permissions."
                )

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
            if _restore_installed_skills(
                skill_rollback,
                deadline_check=getattr(cli, "require_time", None),
            ):
                messages.append(
                    "Restored the previous installed CRM skills after setup failed."
                )
            else:
                rollback_failed = True
                messages.append(
                    "Could not fully restore the previous installed CRM skills."
                )
        if gateway_env_snapshot is not None and not _restore_gateway_env(
            gateway_env_snapshot,
            deadline_check=getattr(cli, "require_time", None),
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
                        *(
                            [plugin_config_snapshot]
                            if plugin_config_snapshot is not None
                            else []
                        ),
                        *(
                            [plugin_hooks_snapshot]
                            if plugin_hooks_snapshot is not None
                            else []
                        ),
                    ],
                    approvals=approvals_original,
                    plugin_source=plugin_source,
                    plugin_preexisting=plugin_preexisting,
                    plugin_enabled=plugin_previously_enabled,
                    skill_snapshot=skill_rollback,
                    gateway_env_snapshot=gateway_env_snapshot,
                    deadline_check=(
                        getattr(cli, "require_time", None)
                        or (lambda: ROLLBACK_DEADLINE_SECONDS)
                    ),
                )
            ):
                rollback_failed = True
                messages.append(
                    "Could not verify restored setup state after restarting the OpenClaw Gateway."
                )

        if skill_rollback is not None:
            recovery_deadline = (
                getattr(cli, "require_time", None)
                or (lambda: ROLLBACK_DEADLINE_SECONDS)
            )
            if _recovery_snapshot_is_complete(
                skill_rollback,
                deadline_check=recovery_deadline,
            ):
                messages.append(
                    f"Recovery backup retained at {skill_rollback.backup_root}"
                )
                messages.append(
                    "This private backup was revalidated as complete. Restore only after "
                    "removing symlinks and verifying the target workspace."
                )
                messages.append(
                    "Keep it until recovery is confirmed, then securely remove that exact "
                    "private backup directory."
                )
            else:
                rollback_failed = True
                messages.append(
                    f"Recovery backup at {skill_rollback.backup_root} could not be "
                    "revalidated as complete; do not rely on it for automatic recovery."
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
    state_descriptor: int | None = None
    state_descriptor_value = os.environ.pop(SETUP_STATE_FD_ENV, None)
    if state_descriptor_value is not None:
        try:
            state_descriptor = int(state_descriptor_value)
            node = os.fstat(state_descriptor)
        except (OSError, TypeError, ValueError):
            print("setup state handoff was unavailable", file=sys.stderr)
            return 1
        if (
            state_descriptor <= 2
            or not stat.S_ISREG(node.st_mode)
            or stat.S_IMODE(node.st_mode) != 0o600
        ):
            print("setup state handoff was unavailable", file=sys.stderr)
            return 1
    options = _parse_args(argv)
    cli = OpenClawCLI()
    try:
        _material_head_state(
            Path(__file__).resolve().parents[1], deadline_check=cli.require_time
        )
    except SetupConflict as exc:
        print(_redact_api_token(str(exc)), file=sys.stderr)
        return 1
    result = configure_openclaw(options, cli)
    state_capture_failed = False
    if result.ok and state_descriptor is not None:
        try:
            state = capture_installed_state(
                options,
                cli,
                runtime_verification=result.runtime_verification,
            )
            envelope = {
                "schema_version": 1,
                "state_capture_exit_code": 0,
                "state": state,
            }
        except (OSError, SetupConflict):
            state_capture_failed = True
            envelope = {
                "schema_version": 1,
                "state_capture_exit_code": 1,
                "state": None,
            }
        try:
            encoded = json.dumps(
                envelope, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) > MAX_SETUP_STATE_BYTES:
                raise OSError("setup state handoff was too large")
            if not _write_setup_state_handoff(
                state_descriptor,
                encoded,
                deadline_check=cli.require_time,
            ):
                raise OSError("bounded setup state handoff did not complete")
        except OSError:
            print("setup state handoff could not be completed", file=sys.stderr)
            return 1
    stream = sys.stdout if result.ok else sys.stderr
    print(result.render(), file=stream)
    if state_capture_failed:
        print(
            "Setup completed, but its installed state could not be verified for "
            "setup-twice evidence.",
            file=sys.stderr,
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
