#!/usr/bin/env python3
"""Read-only operator readiness checks for a local deployment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def _request_json(url: str, method: str = "GET") -> dict:
    headers = {"Accept": "application/json"}
    token = os.environ.get("OHI_API_TOKEN")
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def run_checks(
    base_url: str,
    live_agent: bool = False,
    live_crm: bool = False,
) -> list[Check]:
    checks = [
        Check(
            "PASS" if sys.version_info >= (3, 11) else "FAIL",
            "Python",
            sys.version.split()[0],
        ),
        Check(
            "PASS" if shutil.which("node") else "FAIL",
            "Node.js",
            shutil.which("node") or "not found",
        ),
        Check(
            "PASS" if (REPO / "dashboard/package.json").is_file() else "FAIL",
            "Dashboard source",
            str(REPO / "dashboard/package.json"),
        ),
    ]

    db_path = Path(os.environ.get("DB_PATH", REPO / "backend/data/crm.db"))
    db_parent = db_path.parent
    checks.append(
        Check(
            "PASS" if db_parent.is_dir() and os.access(db_parent, os.W_OK) else "FAIL",
            "Database directory",
            str(db_parent),
        )
    )

    openclaw = shutil.which("openclaw")
    checks.append(
        Check(
            "PASS" if openclaw else "WARN",
            "OpenClaw CLI",
            openclaw or "not found; required for real agent and voice transcription",
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
        checks.append(Check("FAIL", "Application API", f"{health_url}: {exc.__class__.__name__}"))
        return checks

    if live_agent:
        try:
            result = _request_json(
                base_url.rstrip("/") + "/health/agent-check",
                method="POST",
            )
            status = result.get("status", "unknown")
            checks.append(
                Check(
                    "PASS" if status == "crm_verified" else "WARN" if status == "chat_verified" else "FAIL",
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
        help="ask OpenClaw to make one audited, read-only CRM capability call",
    )
    args = parser.parse_args()

    checks = run_checks(args.base_url, args.live_agent, args.live_crm)
    for check in checks:
        print(f"{check.level:4}  {check.name}: {check.detail}")
    return 1 if any(check.level == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
