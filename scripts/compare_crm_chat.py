#!/usr/bin/env python3
"""Opt-in, read-only comparison; never changes installed OpenClaw configuration.

Requires the existing dedicated CRM agent and verified dashboard-channel guards.
Default JSON contains no CRM records or raw model replies. Private captures do.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app.agent import crm_chat
from app.agent.openclaw_gateway import OpenClawGateway, OpenClawGatewayError

READ_OPERATIONS = frozenset({"list_lead_directory", "get_lead_context"})
PROMPTS = (
    "How many CRM leads do I have? List the lead directory using verified CRM facts.",
    "Show my CRM lead directory and its total number of leads.",
    "Please list the people in my CRM and tell me the total lead count.",
)
COUNT_RE = re.compile(r"^\s*(\d+) leads total\.", re.IGNORECASE)


class ReadOnlyGateway:
    """Keep both candidates' operation vocabulary and dispatch read-only.

    The installed plugin owns blocking native tools during model completions;
    this boundary does not replace that installation prerequisite.
    """
    def __init__(self, delegate):
        self.delegate = delegate
        self.model_calls = 0
        self.tool_calls = 0
        self.rejected_calls = 0
        self.last_receipt = None

    async def chat_completion(self, payload, *, channel=None, **kwargs):
        if channel != crm_chat.DASHBOARD_CHANNEL:
            raise OpenClawGatewayError("protected dashboard channel required", definite_pre_dispatch=True)
        narrowed = deepcopy(payload)
        for tool in narrowed.get("tools", []):
            function = tool.get("function", {})
            if function.get("name") == crm_chat.CRM_REQUEST_TOOL:
                branches = function["parameters"]["oneOf"]
                function["parameters"]["oneOf"] = [
                    branch for branch in branches
                    if branch["properties"]["operation"]["const"] in READ_OPERATIONS
                ]
        self.model_calls += 1
        return await self.delegate.chat_completion(narrowed, channel=channel, **kwargs)

    async def invoke_tool(self, name, args, **kwargs):
        try:
            if name != "openhouse_crm" or not isinstance(args, dict):
                raise ValueError
            if set(args) != {"operation", "arguments"} or args["operation"] not in READ_OPERATIONS:
                raise ValueError
            validated = crm_chat._CONTRACT_MODULE.validate_arguments(args["operation"], args["arguments"])
        except (TypeError, ValueError, KeyError):
            self.rejected_calls += 1
            raise OpenClawGatewayError("experiment permits validated CRM reads only", definite_pre_dispatch=True) from None
        self.tool_calls += 1
        receipt = await self.delegate.invoke_tool(name, {"operation": args["operation"], "arguments": validated}, **kwargs)
        self.last_receipt = crm_chat._normalize_gateway_receipt("comparison", args["operation"], receipt)
        return receipt


def validate_local_url(url: str) -> str:
    """Restrict the pilot to a local gateway and API, without URL credentials."""
    try:
        parts = urlsplit(url)
        _ = parts.port  # Reject malformed port values before any request.
        local = parts.hostname == "localhost"
        if not local:
            local = ipaddress.ip_address(parts.hostname or "").is_loopback
        if (parts.scheme not in {"http", "https"} or not local
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path not in {"", "/", "/api", "/api/"}):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Use a loopback HTTP(S) URL without credentials, query, or custom path") from None
    return url.rstrip("/")


def summarize_reply(path: str, reply: str, expected_count: int, model_calls: int,
                    tool_calls: int, elapsed: float, *, outcome=None) -> dict:
    """Do not copy any free-text response or receipt fields into shared output."""
    match = COUNT_RE.search(reply)
    count = int(match.group(1)) if match else None
    stage = None if count == expected_count else "no_verified_count" if count is None else "count_mismatch"
    if outcome is not None:
        receipt = outcome.receipt
        count = None
        if (outcome.status == "answered" and receipt is not None and receipt.ok
                and receipt.operation == "list_lead_directory" and isinstance(receipt.result, dict)):
            count = receipt.result.get("total")
        stage = None if count == expected_count else "candidate_did_not_answer_directory"
    return {
        "path": path, "passed": stage is None, "expected_count": expected_count,
        "count": count, "failure_stage": stage,
        "model_calls": model_calls, "tool_calls": tool_calls,
        "elapsed_seconds": round(elapsed, 3),
    }


def write_private_capture(path: Path, records: list[dict]) -> None:
    """Create a new mode-0600 file; never overwrite or follow an existing link."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(records, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


async def _api_count(base_url: str, timeout: float) -> int:
    headers = {}
    token = os.environ.get("OHI_API_TOKEN")
    if token:
        headers["X-API-Token"] = token
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(base_url.rstrip("/") + "/leads", headers=headers)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("API response exceeds experiment limit")
        leads = response.json()
    if not isinstance(leads, list) or not all(isinstance(lead, dict) for lead in leads):
        raise ValueError("invalid lead list")
    return len(leads)


async def compare(args) -> tuple[dict, list[dict]]:
    from app.agent.single_action import run_single_action

    expected = await _api_count(args.api_url, args.timeout)
    rows, captures = [], []
    for index, prompt in enumerate(PROMPTS):
        # Alternate order to reduce the simplest warm-cache ordering bias.
        paths = ("baseline", "single_action") if index % 2 == 0 else ("single_action", "baseline")
        for path in paths:
            gateway = ReadOnlyGateway(OpenClawGateway(gateway_url=args.gateway_url, timeout=args.timeout))
            session = f"ohi-compare-{os.urandom(8).hex()}"
            started = time.monotonic()
            outcome = None
            try:
                if path == "baseline":
                    reply = await crm_chat.run_verified_crm_chat(
                        gateway, prompt, session, args.agent_id, deadline_seconds=args.timeout)
                else:
                    outcome = await run_single_action(
                        gateway, prompt, session, args.agent_id,
                        allowed_operations=READ_OPERATIONS, deadline_seconds=args.timeout)
                    reply = outcome.reply
                row = summarize_reply(path, reply, expected, gateway.model_calls,
                                      gateway.tool_calls, time.monotonic() - started, outcome=outcome)
            except Exception as exc:
                # Exception messages can contain provider responses, URLs, or records.
                reply = ""
                row = {"path": path, "passed": False, "failure_stage": "runtime_exception",
                       "error_type": type(exc).__name__, "model_calls": gateway.model_calls,
                       "tool_calls": gateway.tool_calls,
                       "elapsed_seconds": round(time.monotonic() - started, 3)}
            row.update(case=index + 1, rejected_calls=gateway.rejected_calls)
            if not row["passed"] and row["failure_stage"] != "runtime_exception":
                if gateway.tool_calls == 0:
                    row["failure_stage"] = "model_selection_or_validation"
                elif gateway.last_receipt is not None and gateway.last_receipt.ok:
                    row["failure_stage"] = "finish_or_render" if path == "baseline" else "different_read_operation"
                else:
                    row["failure_stage"] = "tool_or_receipt"
            if gateway.last_receipt is not None:
                row["operation"] = gateway.last_receipt.operation
                row["receipt_ok"] = gateway.last_receipt.ok
            rows.append(row)
            captures.append({"case": index + 1, "path": path, "session_id": session,
                             "reply": reply[:8000], "reply_truncated": len(reply) > 8000})
    snapshot_error = None
    try:
        after = await _api_count(args.api_url, args.timeout)
        stable = after == expected
    except Exception:
        stable = False
        snapshot_error = "final_api_snapshot_unavailable"
    if not stable:
        for row in rows:
            row["passed"] = False
    revision = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                               capture_output=True, text=True, check=True).stdout.strip())
    return {"experiment": "openclaw-single-action", "revision": revision, "dirty": dirty,
            "scope": "read-only; narrowed catalog; existing guarded OpenClaw installation",
            "live_model": True, "api_count_stable": stable, "results": rows,
            "failure_stage": snapshot_error or (None if stable else "api_count_changed_during_comparison"),
            "passed": stable and all(row["passed"] for row in rows)}, captures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-read-only", action="store_true", help="Run three prompts through each local chat path; no CRM writes")
    parser.add_argument("--api-url", default=os.environ.get("CRM_API_URL", "http://127.0.0.1:8080/api"))
    parser.add_argument("--gateway-url", default=os.environ.get("AGENT_GATEWAY_URL", "http://127.0.0.1:18789"))
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_ID", "openhouse-crm"))
    parser.add_argument("--timeout", type=float, default=30.0, help="Total deadline per chat path, 1–120 seconds")
    parser.add_argument("--capture-private-replies", type=Path, help="NEW private file with exact bounded replies; do not share without redaction")
    args = parser.parse_args(argv)
    if not args.live_read_only:
        print("No requests sent. Use --live-read-only on an already verified local OpenClaw installation.", file=sys.stderr)
        return 2
    try:
        args.api_url = validate_local_url(args.api_url)
        args.gateway_url = validate_local_url(args.gateway_url)
        if urlsplit(args.gateway_url).path:
            raise ValueError("Gateway URL must not contain a path")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.agent_id):
            raise ValueError("Invalid agent ID")
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 120:
            raise ValueError("Timeout must be from 1 to 120 seconds")
        if args.capture_private_replies and os.path.lexists(args.capture_private_replies):
            raise ValueError("Private capture must be a new file")
        report, captures = asyncio.run(compare(args))
        if args.capture_private_replies:
            write_private_capture(args.capture_private_replies, captures)
    except Exception as exc:
        print(json.dumps({"passed": False, "failure_stage": "preflight_or_report",
                          "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
