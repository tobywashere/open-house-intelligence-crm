#!/usr/bin/env python3
"""Read-only operator readiness checks for a local deployment."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
MIN_LOCAL_AI_MEMORY_BYTES = 16 * 1024**3


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def _platform_label(system: str, machine: str, release: str) -> str:
    architecture = machine or "unknown architecture"
    if system == "Darwin":
        return f"macOS {architecture}"
    if system == "Linux" and "microsoft" in release.lower():
        return f"Windows WSL2 {architecture}"
    if system == "Linux":
        return f"Linux {architecture}"
    if system == "Windows":
        return f"native Windows {architecture} (unsupported; use WSL2)"
    return f"{system or 'Unknown OS'} {architecture}"


def _linux_memory_bytes(contents: str) -> int | None:
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB\s*$", contents, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _command_version(argv: list[str], *, cwd: Path | None = None) -> str | None:
    if not argv or shutil.which(argv[0]) is None:
        return None
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            shell=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout or result.stderr
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    if not first_line:
        return None
    home = str(Path.home())
    if home and home != os.path.sep:
        first_line = first_line.replace(home, "<home>")
    return first_line[:160]


def _total_memory_bytes() -> int | None:
    system = platform.system()
    if system == "Linux":
        try:
            return _linux_memory_bytes(Path("/proc/meminfo").read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return None
    if system == "Darwin":
        value = _command_version(["sysctl", "-n", "hw.memsize"])
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None
    return None


def _memory_check(total_bytes: int | None) -> Check:
    if total_bytes is None:
        return Check("WARN", "Memory", "unknown; 16 GiB is the local-AI baseline")
    gib = total_bytes / 1024**3
    detail = f"{gib:.1f} GiB"
    if total_bytes < MIN_LOCAL_AI_MEMORY_BYTES:
        return Check("WARN", "Memory", f"{detail}; below the 16 GiB local-AI baseline")
    return Check("PASS", "Memory", detail)


def system_checks() -> list[Check]:
    system = platform.system()
    platform_detail = _platform_label(system, platform.machine(), platform.release())
    platform_level = "WARN" if system == "Windows" else "PASS"
    revision = _command_version(["git", "rev-parse", "--short", "HEAD"], cwd=REPO)
    node = _command_version(["node", "--version"])
    npm = _command_version(["npm", "--version"])
    openclaw = _command_version(["openclaw", "--version"])
    ollama = _command_version(["ollama", "--version"])
    return [
        Check(
            "PASS" if revision else "WARN",
            "Product revision",
            revision or "unavailable",
        ),
        Check(platform_level, "Platform", platform_detail),
        _memory_check(_total_memory_bytes()),
        Check("PASS", "Python", platform.python_version()),
        Check("PASS" if node else "FAIL", "Node.js", node or "not found"),
        Check("PASS" if npm else "FAIL", "npm", npm or "not found"),
        Check(
            "PASS" if openclaw else "WARN",
            "OpenClaw CLI",
            openclaw or "not found; required for real local AI and voice transcription",
        ),
        Check(
            "PASS" if ollama else "WARN",
            "Ollama (optional)",
            ollama or "not found; another OpenClaw model provider may be used",
        ),
    ]


def render_report(checks: list[Check], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "schema_version": 1,
                "checks": [asdict(check) for check in checks],
            },
            indent=2,
            sort_keys=True,
        )
    return "\n".join(
        f"{check.level:4}  {check.name}: {check.detail}" for check in checks
    )


def _request_json(
    url: str,
    method: str = "GET",
    timeout: float = 5,
) -> dict:
    headers = {"Accept": "application/json"}
    token = os.environ.get("OHI_API_TOKEN")
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def run_checks(
    base_url: str,
    live_agent: bool = False,
    live_crm: bool = False,
    live_timeout: float | None = None,
) -> list[Check]:
    operation_timeout = (
        live_timeout
        if live_timeout is not None
        else float(os.environ.get("AGENT_TIMEOUT_SECONDS", "120")) + 5
    )
    checks = system_checks()
    if sys.version_info < (3, 11):
        checks = [
            Check("FAIL", item.name, item.detail) if item.name == "Python" else item
            for item in checks
        ]
    checks.extend(
        [
            Check(
                "PASS" if (REPO / "dashboard/package.json").is_file() else "FAIL",
                "Dashboard source",
                (
                    "available"
                    if (REPO / "dashboard/package.json").is_file()
                    else "missing"
                ),
            ),
        ]
    )

    db_path = Path(os.environ.get("DB_PATH", REPO / "backend/data/crm.db"))
    db_parent = db_path.parent
    checks.append(
        Check(
            "PASS" if db_parent.is_dir() and os.access(db_parent, os.W_OK) else "FAIL",
            "Database directory",
            (
                "writable"
                if db_parent.is_dir() and os.access(db_parent, os.W_OK)
                else "not writable"
            ),
        )
    )

    health_url = base_url.rstrip("/") + "/health"
    try:
        health = _request_json(health_url)
        status = (health.get("agent_status") or {}).get("status", "unknown")
        level = "PASS" if status in {
            "mock", "endpoint_enabled", "chat_verified", "crm_verified", "degraded"
        } else "FAIL"
        checks.append(Check(level, "Agent endpoint", status))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        checks.append(Check("FAIL", "Application API", exc.__class__.__name__))
        return checks

    if live_agent:
        try:
            result = _request_json(
                base_url.rstrip("/") + "/health/agent-check",
                method="POST",
                timeout=operation_timeout,
            )
            status = result.get("status", "unknown")
            checks.append(
                Check(
                    (
                        "PASS"
                        if status == "crm_verified"
                        else "WARN" if status == "chat_verified" else "FAIL"
                    ),
                    "Live chat completion",
                    status,
                )
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            checks.append(Check("FAIL", "Live chat completion", exc.__class__.__name__))
    if live_crm:
        try:
            result = _request_json(
                base_url.rstrip("/") + "/health/crm-check",
                method="POST",
                timeout=operation_timeout,
            )
            status = result.get("status", "unknown")
            checks.append(
                Check(
                    "PASS" if status == "crm_verified" else "FAIL",
                    "CRM capability",
                    status,
                )
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            checks.append(Check("FAIL", "CRM capability", exc.__class__.__name__))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check this machine without changing configuration or CRM data."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080/api",
        help="Running application API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--live-agent",
        action="store_true",
        help="send one harmless completion to verify the configured OpenClaw chat endpoint",
    )
    parser.add_argument(
        "--live-crm",
        action="store_true",
        help="directly invoke one audited, read-only CRM call through OpenClaw",
    )
    parser.add_argument(
        "--live-timeout",
        type=float,
        default=None,
        help=(
            "seconds allowed for each live agent check "
            "(default: AGENT_TIMEOUT_SECONDS plus 5)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a sanitized JSON report that is safe to inspect and share",
    )
    args = parser.parse_args()

    checks = run_checks(
        args.base_url,
        args.live_agent,
        args.live_crm,
        args.live_timeout,
    )
    print(render_report(checks, as_json=args.json))
    return 1 if any(check.level == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
