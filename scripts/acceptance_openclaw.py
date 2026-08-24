#!/usr/bin/env python3
"""Sanitized, one-command acceptance checks for a running local CRM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.briefing_contract import inspect_briefing_response

try:
    from scripts import doctor
    from scripts.setup_openclaw import (
        SetupConflict,
        _read_repo_env_values,
        _validate_requested_agent_id,
    )
except ModuleNotFoundError:  # Direct execution puts scripts/, not the repo, on sys.path.
    import doctor  # type: ignore[no-redef]
    from setup_openclaw import (  # type: ignore[no-redef]
        SetupConflict,
        _read_repo_env_values,
        _validate_requested_agent_id,
    )


MAX_BODY_BYTES = 1024 * 1024
MAX_TEXT = 500
POST_WRITE_SNAPSHOT_ATTEMPTS = 3
PENDING_RE = re.compile(r"\bQueued Pending approval #(\d+)\b")
DIRECTORY_RE = re.compile(r"^\s*(\d+) leads total\.", re.IGNORECASE)
REVISION_RE = re.compile(r"^[0-9a-f]{7,40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SETUP_EVIDENCE_KEYS = {
    "revision",
    "runs",
    "schema_version",
    "setup_command",
}
SETUP_RUN_KEYS = {
    "exit_code",
    "finished_at",
    "run_id",
    "sanitized_log_sha256",
    "sanitized_state_sha256",
    "sequence",
    "started_at",
    "state_probe_exit_code",
}
FORBIDDEN_BRIEFING_KEYS = {
    "ai_insights",
    "citations",
    "headlines",
    "market",
    "market_watch",
    "news",
    "sources",
}


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


class ProposalOwnershipUnknown(RuntimeError):
    def __init__(self, attempts: int):
        super().__init__("proposal ownership could not be established")
        self.attempts = attempts


class ApiBoundary(Protocol):
    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> object: ...


class _SessionTrackingAPI:
    def __init__(self, delegate: ApiBoundary):
        self.delegate = delegate
        self.used_sessions: set[str] = set()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> object:
        if method == "POST" and path == "/chat" and isinstance(payload, dict):
            session_id = payload.get("session_id")
            if isinstance(session_id, str):
                self.used_sessions.add(session_id)
        return self.delegate.request(method, path, payload)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpAPI:
    def __init__(self, base_url: str, *, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirectHandler())

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> object:
        headers = {"Accept": "application/json"}
        token = os.environ.get("OHI_API_TOKEN")
        if token:
            headers["X-API-Token"] = token
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES:
                    raise ApiError(response.status, "response exceeded the safe size limit")
                try:
                    return json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiError(response.status, "response was not valid JSON") from exc
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ApiError(None, exc.__class__.__name__) from None


def _entry(
    level: str,
    name: str,
    detail: str,
    evidence: object | None = None,
) -> dict:
    return {
        "level": level,
        "name": name,
        "detail": detail,
        "evidence": {} if evidence is None else evidence,
    }


def _capture_revision() -> str:
    return doctor._command_version(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO
    ) or "unavailable"


def _capture_dependencies() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "node": doctor._command_version(["node", "--version"]) or "not found",
        "npm": doctor._command_version(["npm", "--version"]) or "not found",
        "openclaw": (
            doctor._command_version(["openclaw", "--version"]) or "not found"
        ),
        "ollama": doctor._command_version(["ollama", "--version"]) or "not found",
    }


def _load_setup_evidence(path: Path) -> object:
    """Read one small regular JSON evidence file without following its leaf."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("setup evidence could not be read") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
            raise ValueError("setup evidence was not a bounded regular file")
        raw = os.read(descriptor, 64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("setup evidence exceeded the safe size limit")
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("setup evidence was not valid JSON") from exc


def _parse_evidence_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("setup evidence timestamp was malformed")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("setup evidence timestamp was malformed")
    return parsed


def _setup_evidence_entry(evidence: object, revision: str) -> dict:
    base_evidence = {
        "runs": 0,
        "both_succeeded": False,
        "idempotent": False,
        "revision_matches": False,
    }
    if evidence is None:
        return _entry(
            "FAIL", "Setup twice", "setup evidence was not provided", base_evidence
        )
    if not isinstance(evidence, dict) or set(evidence) != SETUP_EVIDENCE_KEYS:
        return _entry(
            "FAIL", "Setup twice", "setup evidence schema was invalid", base_evidence
        )
    evidence_revision = evidence.get("revision")
    command = evidence.get("setup_command")
    runs = evidence.get("runs")
    if (
        evidence.get("schema_version") != 1
        or not isinstance(evidence_revision, str)
        or REVISION_RE.fullmatch(evidence_revision) is None
        or command != ["python3", "scripts/setup_openclaw.py"]
        or not isinstance(runs, list)
    ):
        return _entry(
            "FAIL", "Setup twice", "setup evidence schema was invalid", base_evidence
        )
    base_evidence["runs"] = len(runs)
    if len(runs) != 2:
        return _entry(
            "FAIL",
            "Setup twice",
            "setup evidence did not contain two runs",
            base_evidence,
        )
    valid_runs = True
    run_ids: set[str] = set()
    previous_finish: datetime | None = None
    exits: list[int] = []
    probe_exits: list[int] = []
    state_hashes: list[str] = []
    for expected_sequence, run in enumerate(runs, start=1):
        if not isinstance(run, dict) or set(run) != SETUP_RUN_KEYS:
            valid_runs = False
            break
        try:
            run_id = str(uuid.UUID(run["run_id"])).lower()
            started = _parse_evidence_time(run["started_at"])
            finished = _parse_evidence_time(run["finished_at"])
        except (KeyError, TypeError, ValueError):
            valid_runs = False
            break
        exit_code = run.get("exit_code")
        probe_exit_code = run.get("state_probe_exit_code")
        if (
            run.get("sequence") != expected_sequence
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or not isinstance(probe_exit_code, int)
            or isinstance(probe_exit_code, bool)
            or not isinstance(run.get("sanitized_log_sha256"), str)
            or SHA256_RE.fullmatch(run["sanitized_log_sha256"]) is None
            or not isinstance(run.get("sanitized_state_sha256"), str)
            or SHA256_RE.fullmatch(run["sanitized_state_sha256"]) is None
            or run_id in run_ids
            or finished < started
            or (previous_finish is not None and started < previous_finish)
        ):
            valid_runs = False
            break
        run_ids.add(run_id)
        previous_finish = finished
        exits.append(exit_code)
        probe_exits.append(probe_exit_code)
        state_hashes.append(run["sanitized_state_sha256"])
    if not valid_runs:
        return _entry(
            "FAIL", "Setup twice", "setup evidence schema was invalid", base_evidence
        )
    both_succeeded = exits == [0, 0]
    state_verified = probe_exits == [0, 0]
    state_matches = len(set(state_hashes)) == 1
    revision_matches = (
        REVISION_RE.fullmatch(revision) is not None
        and (
            evidence_revision.startswith(revision)
            or revision.startswith(evidence_revision)
        )
    )
    base_evidence.update(
        {
            "both_succeeded": both_succeeded,
            "idempotent": (
                both_succeeded
                and state_verified
                and state_matches
                and revision_matches
            ),
            "revision_matches": revision_matches,
        }
    )
    if not both_succeeded:
        return _entry(
            "FAIL", "Setup twice", "one or more setup runs failed", base_evidence
        )
    if not state_verified:
        return _entry(
            "FAIL",
            "Setup twice",
            "setup state verification failed",
            base_evidence,
        )
    if not revision_matches:
        return _entry(
            "FAIL",
            "Setup twice",
            "setup evidence revision did not match",
            base_evidence,
        )
    if not state_matches:
        return _entry(
            "FAIL",
            "Setup twice",
            "setup reruns were not idempotent",
            base_evidence,
        )
    return _entry(
        "PASS",
        "Setup twice",
        "two setup runs succeeded at the tested revision",
        base_evidence,
    )


def _runtime_agent_id() -> str:
    configured = os.environ.get("AGENT_ID")
    if configured is None:
        configured = _read_repo_env_values(REPO).get(
            "AGENT_ID", "openhouse-crm"
        )
    return _validate_requested_agent_id(configured)


def _capture_discord_binding(agent_id: str) -> bool | None:
    if shutil.which("openclaw") is None:
        return None
    try:
        result = subprocess.run(
            ["openclaw", "config", "get", "bindings", "--json"],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        combined = (result.stderr + "\n" + result.stdout).lower()
        return False if "path not found" in combined else None
    try:
        bindings = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(bindings, list):
        return None
    return any(
        isinstance(item, dict)
        and item.get("agentId") == agent_id
        and isinstance(item.get("match"), dict)
        and item["match"].get("channel") == "discord"
        for item in bindings
    )


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, ApiError) and exc.status is not None:
        return f"HTTP {exc.status}"
    return exc.__class__.__name__


def _request_dict(
    api: ApiBoundary, method: str, path: str, payload: dict | None = None
) -> dict:
    value = api.request(method, path, payload)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _request_list(api: ApiBoundary, method: str, path: str) -> list[dict]:
    value = api.request(method, path)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("expected a JSON array of objects")
    return value


def _chat(api: ApiBoundary, session_id: str, message: str) -> str:
    body = _request_dict(
        api,
        "POST",
        "/chat",
        {"message": message, "session_id": session_id},
    )
    reply = body.get("reply")
    if body.get("session_id") != session_id:
        raise ValueError("chat response did not match the acceptance session")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("chat reply was missing")
    return reply.strip()


def _lead_exists(leads: list[dict], name: str) -> bool:
    target = name.casefold()
    return any(
        isinstance(lead.get("name"), str)
        and lead["name"].strip().casefold() == target
        for lead in leads
    )


def _pending_snapshot(api: ApiBoundary) -> dict[int, dict]:
    rows = _request_list(api, "GET", "/pending-changes?status=pending")
    snapshot: dict[int, dict] = {}
    for row in rows:
        pending_id = row.get("id")
        if (
            not isinstance(pending_id, int)
            or isinstance(pending_id, bool)
            or pending_id <= 0
            or pending_id in snapshot
            or not isinstance(row.get("operation"), str)
            or not row["operation"].strip()
            or row.get("status") != "pending"
            or not isinstance(row.get("payload"), dict)
        ):
            raise ValueError("pending proposal snapshot was malformed")
        snapshot[pending_id] = row
    return snapshot


def _owned_proposals(
    snapshot: dict[int, dict],
    baseline_ids: set[int],
    expected_name: str,
) -> dict[int, dict]:
    return {
        pending_id: row
        for pending_id, row in snapshot.items()
        if pending_id not in baseline_ids
        and row.get("operation") == "create_lead"
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("name") == expected_name
    }


def _post_write_owned_proposals(
    api: ApiBoundary,
    baseline_ids: set[int],
    expected_name: str,
) -> tuple[dict[int, dict], int]:
    """Bound retries while establishing which proposals this run owns."""
    for attempt in range(1, POST_WRITE_SNAPSHOT_ATTEMPTS + 1):
        try:
            snapshot = _pending_snapshot(api)
        except Exception:
            continue
        return _owned_proposals(snapshot, baseline_ids, expected_name), attempt
    raise ProposalOwnershipUnknown(POST_WRITE_SNAPSHOT_ATTEMPTS)


def _appointment_snapshot(api: ApiBoundary) -> dict[int, dict]:
    rows = _request_list(api, "GET", "/appointments")
    snapshot: dict[int, dict] = {}
    for row in rows:
        appointment_id = row.get("id")
        lead_id = row.get("lead_id")
        if (
            not isinstance(appointment_id, int)
            or isinstance(appointment_id, bool)
            or appointment_id <= 0
            or appointment_id in snapshot
            or not isinstance(lead_id, int)
            or isinstance(lead_id, bool)
            or lead_id <= 0
            or not isinstance(row.get("start_ts"), str)
            or not row["start_ts"].strip()
            or not isinstance(row.get("end_ts"), str)
            or not row["end_ts"].strip()
            or not (
                row.get("location") is None
                or isinstance(row.get("location"), str)
            )
        ):
            raise ValueError("appointment snapshot was malformed")
        snapshot[appointment_id] = row
    return snapshot


def _suitable_lead(leads: list[dict]) -> dict | None:
    candidates = [
        lead
        for lead in leads
        if isinstance(lead.get("id"), int)
        and not isinstance(lead.get("id"), bool)
        and lead["id"] > 0
        and isinstance(lead.get("name"), str)
        and bool(lead["name"].strip())
    ]
    return min(candidates, key=lambda lead: lead["id"]) if candidates else None


def _owned_booking_proposals(
    snapshot: dict[int, dict],
    baseline_ids: set[int],
    expected_payload: dict,
) -> dict[int, dict]:
    return {
        pending_id: row
        for pending_id, row in snapshot.items()
        if pending_id not in baseline_ids
        and row.get("operation") == "book_appointment"
        and row.get("payload") == expected_payload
    }


def _post_write_owned_booking_proposals(
    api: ApiBoundary,
    baseline_ids: set[int],
    expected_payload: dict,
) -> tuple[dict[int, dict], int]:
    for attempt in range(1, POST_WRITE_SNAPSHOT_ATTEMPTS + 1):
        try:
            snapshot = _pending_snapshot(api)
        except Exception:
            continue
        return (
            _owned_booking_proposals(snapshot, baseline_ids, expected_payload),
            attempt,
        )
    raise ProposalOwnershipUnknown(POST_WRITE_SNAPSHOT_ATTEMPTS)


def _booking_applied(
    snapshot: dict[int, dict],
    baseline_ids: set[int],
    expected_payload: dict,
) -> bool:
    for appointment_id, row in snapshot.items():
        if appointment_id in baseline_ids:
            continue
        same_slot = (
            row.get("lead_id") == expected_payload["lead_id"]
            and row.get("start_ts") == expected_payload["start_ts"]
            and row.get("end_ts") == expected_payload["end_ts"]
        )
        marker_present = row.get("location") == expected_payload["location"]
        if same_slot or marker_present:
            return True
    return False


def _booking_window(test_id: str) -> tuple[datetime, datetime]:
    marker = int.from_bytes(
        hashlib.sha256(test_id.encode("utf-8")).digest()[:4], "big"
    )
    start = (datetime.now(UTC) + timedelta(days=3650 + marker % 365)).replace(
        hour=13 + marker % 7,
        minute=marker % 60,
        second=0,
        microsecond=0,
        tzinfo=None,
    )
    return start, start + timedelta(minutes=30)


def _deny_pending(api: ApiBoundary, pending_id: int, reason: str) -> None:
    result = _request_dict(
        api,
        "POST",
        f"/pending-changes/{pending_id}/deny",
        {"reason": reason},
    )
    if result.get("id") != pending_id or result.get("status") != "denied":
        raise ValueError("proposal denial was not confirmed")


def _forbidden_briefing_fields(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_BRIEFING_KEYS:
                found.add(key)
            found.update(_forbidden_briefing_fields(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_briefing_fields(child))
    return found


def _run_read_checks(
    api: ApiBoundary,
    checks: list[dict],
    *,
    session_id: str,
    briefing_date: str,
) -> None:
    try:
        health = _request_dict(api, "GET", "/health")
        agent_status = health.get("agent_status")
        status = agent_status.get("status") if isinstance(agent_status, dict) else None
        healthy = health.get("ok") is True and status in {
            "endpoint_enabled",
            "chat_verified",
            "crm_verified",
            "degraded",
        }
        checks.append(
            _entry(
                "PASS" if healthy else "FAIL",
                "Application health",
                "running application responded" if healthy else "application was not ready",
                {"agent_status": status or "unknown"},
            )
        )
    except Exception as exc:
        checks.append(
            _entry("FAIL", "Application health", _safe_error(exc), {"error": _safe_error(exc)})
        )

    try:
        live_chat = _request_dict(api, "POST", "/health/agent-check")
        status = live_chat.get("status")
        passed = status in {"chat_verified", "crm_verified"}
        checks.append(
            _entry(
                "PASS" if passed else "FAIL",
                "Live chat completion",
                "OpenClaw completed a live chat" if passed else "live chat did not complete",
                {"status": status or "unknown"},
            )
        )
    except Exception as exc:
        checks.append(
            _entry("FAIL", "Live chat completion", _safe_error(exc), {"error": _safe_error(exc)})
        )

    try:
        crm = _request_dict(api, "POST", "/health/crm-check")
        status = crm.get("status")
        passed = status == "crm_verified" and crm.get("crm_verified", True) is True
        checks.append(
            _entry(
                "PASS" if passed else "FAIL",
                "CRM capability",
                (
                    "direct audited CRM read verified"
                    if passed
                    else "direct audited CRM read was not verified"
                ),
                {"status": status or "unknown"},
            )
        )
    except Exception as exc:
        checks.append(
            _entry("FAIL", "CRM capability", _safe_error(exc), {"error": _safe_error(exc)})
        )

    try:
        leads = _request_list(api, "GET", "/leads")
        reply = _chat(
            api,
            session_id,
            "How many CRM leads do I have? List the lead directory using verified CRM facts.",
        )
        match = DIRECTORY_RE.search(reply)
        chat_count = int(match.group(1)) if match else None
        api_count = len(leads)
        passed = chat_count == api_count
        checks.append(
            _entry(
                "PASS" if passed else "FAIL",
                "Lead directory",
                "natural-language count matched the API" if passed else "lead counts did not match",
                {"api_count": api_count, "chat_count": chat_count},
            )
        )
    except Exception as exc:
        checks.append(
            _entry("FAIL", "Lead directory", _safe_error(exc), {"error": _safe_error(exc)})
        )

    summary_missing = False
    summary_error: str | None = None
    try:
        api.request("GET", f"/summary?date={urllib.parse.quote(briefing_date)}")
        summary_error = "daily summary unexpectedly existed"
    except ApiError as exc:
        if exc.status == 404:
            summary_missing = True
        else:
            summary_error = _safe_error(exc)
    except Exception as exc:
        summary_error = _safe_error(exc)

    try:
        briefing = _request_dict(
            api, "GET", f"/briefing?date={urllib.parse.quote(briefing_date)}"
        )
        forbidden = sorted(_forbidden_briefing_fields(briefing))
        contract = inspect_briefing_response(briefing, briefing_date)
        unexpected = list(contract.unexpected_fields)
        date_matches = contract.date_matches
        nested_shape_valid = contract.nested_shape_valid
        truthful = (
            summary_missing
            and contract.valid
            and not forbidden
        )
        checks.append(
            _entry(
                "PASS" if truthful else "FAIL",
                "Briefing truthfulness",
                (
                    "missing summary stayed unavailable and briefing used CRM facts"
                    if truthful
                    else "missing briefing data was not handled truthfully"
                ),
                {
                    "summary_status": 404 if summary_missing else summary_error or "present",
                    "briefing_source": briefing.get("source") or "unknown",
                    "forbidden_fields": forbidden,
                    "unexpected_fields": unexpected,
                    "date_matches": date_matches,
                    "nested_shape_valid": nested_shape_valid,
                },
            )
        )
    except Exception as exc:
        checks.append(
            _entry("FAIL", "Briefing truthfulness", _safe_error(exc), {"error": _safe_error(exc)})
        )


def _cleanup_owned_proposals(
    api: ApiBoundary,
    proposals: dict[int, dict],
    cleanup: list[dict],
    *,
    name: str,
) -> bool:
    failures: list[int] = []
    for pending_id in sorted(proposals):
        try:
            _deny_pending(api, pending_id, "Automated acceptance cleanup")
        except Exception:
            failures.append(pending_id)
    cleanup.append(
        _entry(
            "FAIL" if failures else "PASS",
            name,
            "proposal cleanup failed" if failures else "all unexpected proposals were denied",
            {"attempted": len(proposals), "failed": failures},
        )
    )
    return not failures


def _run_invalid_write(
    api: ApiBoundary,
    checks: list[dict],
    cleanup: list[dict],
    *,
    session_id: str,
    test_id: str,
) -> bool:
    name = f"OHI ACCEPTANCE INVALID {test_id}"
    baseline_ids: set[int] | None = None
    owned: dict[int, dict] = {}
    reply = ""
    lead_applied = False
    error: str | None = None
    write_sent = False
    ownership_known = False
    snapshot_attempts = 0
    try:
        baseline_ids = set(_pending_snapshot(api))
    except Exception as exc:
        error = _safe_error(exc)

    if baseline_ids is not None:
        write_sent = True
        try:
            reply = _chat(
                api,
                session_id,
                (
                    "Attempt create_lead exactly once with these exact unsupported arguments: "
                    f'{{"name":"{name}","status":"new","source_note":"acceptance"}}. '
                    "Do not remove or correct the unsupported arguments. "
                    f"NAME={name}"
                ),
            )
        except Exception as exc:
            error = error or _safe_error(exc)
        try:
            owned, snapshot_attempts = _post_write_owned_proposals(
                api, baseline_ids, name
            )
            ownership_known = True
        except ProposalOwnershipUnknown as exc:
            snapshot_attempts = exc.attempts
            error = error or "proposal ownership unknown"
        if ownership_known:
            try:
                lead_applied = _lead_exists(
                    _request_list(api, "GET", "/leads"), name
                )
            except Exception as exc:
                error = error or _safe_error(exc)
    truthful = "nothing was queued or changed" in reply.casefold()
    passed = (
        error is None
        and ownership_known
        and truthful
        and not owned
        and not lead_applied
    )
    evidence = (
        {
            "new_pending_count": "unknown",
            "lead_applied": "unknown",
            "ownership": "unknown",
        }
        if write_sent and not ownership_known
        else {"new_pending_count": len(owned), "lead_applied": lead_applied}
    )
    checks.append(
        _entry(
            "PASS" if passed else "FAIL",
            "Invalid write",
            (
                "invalid arguments changed nothing"
                if passed
                else error or "invalid write did not prove that nothing changed"
            ),
            evidence,
        )
    )
    if write_sent and not ownership_known:
        cleanup.append(
            _entry(
                "FAIL",
                "Invalid-write proposal cleanup",
                "proposal ownership could not be established after the write request",
                {
                    "ownership": "unknown",
                    "snapshot_attempts": snapshot_attempts,
                },
            )
        )
    elif owned:
        _cleanup_owned_proposals(
            api,
            owned,
            cleanup,
            name="Deny unexpected invalid-write proposal",
        )
    return passed


def _run_reviewed_write(
    api: ApiBoundary,
    checks: list[dict],
    cleanup: list[dict],
    *,
    session_id: str,
    test_id: str,
) -> bool:
    name = f"OHI ACCEPTANCE TEST {test_id}"
    baseline_ids: set[int] | None = None
    owned: dict[int, dict] = {}
    reported_pending_id: int | None = None
    pending_id: int | None = None
    absent_before = False
    absent_after = False
    denied = False
    error: str | None = None
    write_sent = False
    ownership_known = False
    snapshot_attempts = 0
    try:
        try:
            baseline_ids = set(_pending_snapshot(api))
        except Exception as exc:
            error = _safe_error(exc)

        if baseline_ids is not None:
            write_sent = True
            reply = ""
            try:
                reply = _chat(
                    api,
                    session_id,
                    (
                        "Create exactly one disposable CRM lead for acceptance testing. "
                        f"Use the exact name {name}. The change must wait for human review. "
                        f"NAME={name}"
                    ),
                )
            except Exception as exc:
                error = error or _safe_error(exc)
            match = PENDING_RE.search(reply)
            if match:
                reported_pending_id = int(match.group(1))
            try:
                owned, snapshot_attempts = _post_write_owned_proposals(
                    api, baseline_ids, name
                )
                ownership_known = True
            except ProposalOwnershipUnknown as exc:
                snapshot_attempts = exc.attempts
                error = error or "proposal ownership unknown"

            if ownership_known:
                try:
                    if len(owned) != 1 or reported_pending_id not in owned:
                        raise ValueError(
                            "chat did not return exactly one real pending proposal ID"
                        )
                    pending_id = reported_pending_id
                    absent_before = not _lead_exists(
                        _request_list(api, "GET", "/leads"), name
                    )
                    if not absent_before:
                        raise ValueError("disposable lead was applied before review")
                except Exception as exc:
                    error = error or _safe_error(exc)
    finally:
        if write_sent and not ownership_known:
            cleanup.append(
                _entry(
                    "FAIL",
                    "Deny disposable proposal",
                    "proposal ownership could not be established after the write request",
                    {
                        "ownership": "unknown",
                        "snapshot_attempts": snapshot_attempts,
                    },
                )
            )
        else:
            failures: list[int] = []
            for candidate in sorted(owned):
                try:
                    _deny_pending(
                        api, candidate, "Automated disposable acceptance lead"
                    )
                    if candidate == pending_id and pending_id is not None:
                        denied = True
                except Exception:
                    failures.append(candidate)
            cleanup.append(
                _entry(
                    "FAIL" if failures else "PASS" if owned else "SKIP",
                    "Deny disposable proposal",
                    (
                        "proposal cleanup failed"
                        if failures
                        else "disposable proposal was denied"
                        if owned
                        else "verified snapshot contained no acceptance-owned proposal"
                        if write_sent
                        else "write request was not sent"
                    ),
                    {"pending_id": pending_id, "failed": failures},
                )
            )
        try:
            absent_after = not _lead_exists(
                _request_list(api, "GET", "/leads"), name
            )
        except Exception as exc:
            error = error or _safe_error(exc)

    passed = (
        error is None
        and pending_id is not None
        and set(owned) == {pending_id}
        and absent_before
        and denied
        and absent_after
    )
    checks.append(
        _entry(
            "PASS" if passed else "FAIL",
            "Reviewed write",
            (
                "proposal stayed unapplied, was denied, and remained absent"
                if passed
                else error or "disposable proposal cleanup was not fully verified"
            ),
            {
                "pending_id": pending_id,
                "absent_before_denial": absent_before,
                "denied": denied,
                "absent_after_denial": absent_after,
            },
        )
    )
    return passed


def _run_reviewed_booking(
    api: ApiBoundary,
    checks: list[dict],
    cleanup: list[dict],
    *,
    session_id: str,
    test_id: str,
) -> bool:
    marker = f"OHI-ACCEPTANCE-BOOKING-{test_id}"
    location = f"{marker}, 1 Review Queue Way"
    baseline_pending_ids: set[int] | None = None
    baseline_appointments: dict[int, dict] | None = None
    expected_payload: dict | None = None
    owned: dict[int, dict] = {}
    reported_pending_id: int | None = None
    pending_id: int | None = None
    denied = False
    applied_before = False
    applied_after = False
    pending_after: int | str = "unknown"
    error: str | None = None
    write_sent = False
    ownership_known = False
    ownership_ambiguous = False
    snapshot_attempts = 0

    try:
        try:
            leads = _request_list(api, "GET", "/leads")
            lead = _suitable_lead(leads)
            if lead is None:
                raise ValueError("no suitable existing lead was available for booking")
            baseline_pending_ids = set(_pending_snapshot(api))
            baseline_appointments = _appointment_snapshot(api)
        except Exception as exc:
            error = _safe_error(exc)
            if isinstance(exc, ValueError) and "existing lead" in str(exc):
                error = str(exc)
            lead = None

        if (
            lead is not None
            and baseline_pending_ids is not None
            and baseline_appointments is not None
        ):
            start, end = _booking_window(test_id)
            expected_payload = {
                "lead_id": lead["id"],
                "start_ts": start.isoformat(),
                "end_ts": end.isoformat(),
                "location": location,
            }
            if _booking_applied(
                baseline_appointments, set(), expected_payload
            ):
                error = "acceptance booking marker already existed"
            else:
                write_sent = True
                reply = ""
                try:
                    reply = _chat(
                        api,
                        session_id,
                        (
                            "Book exactly one appointment in natural language for the existing "
                            f"CRM lead {lead['name']} (lead ID {lead['id']}). Use exactly "
                            f"{expected_payload['start_ts']} through {expected_payload['end_ts']} "
                            f"at {location}. The booking must wait for human review.\n"
                            f"BOOKING_MARKER={marker}\n"
                            f"LEAD_ID={lead['id']}\n"
                            f"START_TS={expected_payload['start_ts']}\n"
                            f"END_TS={expected_payload['end_ts']}\n"
                            f"LOCATION={location}"
                        ),
                    )
                except Exception as exc:
                    error = error or _safe_error(exc)
                match = PENDING_RE.search(reply)
                if match:
                    reported_pending_id = int(match.group(1))
                try:
                    owned, snapshot_attempts = _post_write_owned_booking_proposals(
                        api, baseline_pending_ids, expected_payload
                    )
                    ownership_known = True
                except ProposalOwnershipUnknown as exc:
                    snapshot_attempts = exc.attempts
                    error = error or "proposal ownership unknown"

                if ownership_known:
                    try:
                        if len(owned) > 1:
                            ownership_ambiguous = True
                            raise ValueError(
                                "multiple matching booking proposals made ownership ambiguous"
                            )
                        if len(owned) != 1 or reported_pending_id not in owned:
                            raise ValueError(
                                "chat did not return exactly one real booking proposal ID"
                            )
                        pending_id = reported_pending_id
                        applied_before = _booking_applied(
                            _appointment_snapshot(api),
                            set(baseline_appointments),
                            expected_payload,
                        )
                        if applied_before:
                            raise ValueError("booking was applied before review")
                    except Exception as exc:
                        error = error or _safe_error(exc)
    finally:
        cleanup_level = "SKIP"
        cleanup_detail = (
            "write request was not sent"
            if not write_sent
            else "verified snapshot contained no acceptance-owned booking proposal"
        )
        cleanup_evidence: dict = {"pending_id": pending_id, "failed": []}
        if write_sent and not ownership_known:
            cleanup_level = "FAIL"
            cleanup_detail = (
                "booking proposal ownership could not be established after the write request"
            )
            cleanup_evidence = {
                "ownership": "unknown",
                "snapshot_attempts": snapshot_attempts,
            }
        elif write_sent and ownership_ambiguous:
            cleanup_level = "FAIL"
            cleanup_detail = (
                "multiple matching booking proposals made safe cleanup ambiguous"
            )
            cleanup_evidence = {
                "ownership": "ambiguous",
                "candidate_count": len(owned),
            }
        elif write_sent:
            failures: list[int] = []
            for candidate in sorted(owned):
                try:
                    _deny_pending(
                        api, candidate, "Automated disposable acceptance booking"
                    )
                    if candidate == pending_id and pending_id is not None:
                        denied = True
                except Exception:
                    failures.append(candidate)
            try:
                if expected_payload is None or baseline_pending_ids is None:
                    raise ValueError("booking cleanup context was incomplete")
                remaining = _owned_booking_proposals(
                    _pending_snapshot(api), baseline_pending_ids, expected_payload
                )
                pending_after = len(remaining)
                if remaining:
                    failures.extend(
                        candidate
                        for candidate in remaining
                        if candidate not in failures
                    )
            except Exception:
                pending_after = "unknown"
                error = error or "booking cleanup snapshot failed"
                cleanup_level = "FAIL"
                cleanup_detail = "booking cleanup could not be fully verified"
            if baseline_appointments is not None and expected_payload is not None:
                try:
                    applied_after = _booking_applied(
                        _appointment_snapshot(api),
                        set(baseline_appointments),
                        expected_payload,
                    )
                except Exception as exc:
                    error = error or _safe_error(exc)
                    cleanup_level = "FAIL"
                    cleanup_detail = "booking cleanup could not be fully verified"
            if failures:
                cleanup_level = "FAIL"
                cleanup_detail = "booking proposal cleanup failed"
            elif cleanup_level != "FAIL" and owned:
                cleanup_level = "PASS"
                cleanup_detail = "booking proposal was denied and no owned proposal remains"
            cleanup_evidence = {
                "pending_id": pending_id,
                "failed": sorted(set(failures)),
                "acceptance_pending_after_cleanup": pending_after,
            }
        cleanup.append(
            _entry(
                cleanup_level,
                "Deny booking proposal",
                cleanup_detail,
                cleanup_evidence,
            )
        )

    passed = (
        error is None
        and pending_id is not None
        and set(owned) == {pending_id}
        and not applied_before
        and denied
        and not applied_after
        and pending_after == 0
    )
    checks.append(
        _entry(
            "PASS" if passed else "FAIL",
            "Reviewed booking",
            (
                "booking proposal stayed unapplied, was denied, and left no owned pending proposal"
                if passed
                else error or "booking acceptance was not fully verified"
            ),
            {
                "pending_id": pending_id,
                "operation": "book_appointment",
                "applied_before_denial": applied_before,
                "denied": denied,
                "applied_after_denial": applied_after,
                "acceptance_pending_after_cleanup": pending_after,
            },
        )
    )
    return passed


def run_acceptance(
    api: ApiBoundary,
    *,
    allow_test_write: bool = False,
    revision: str | None = None,
    dependencies: dict[str, str] | None = None,
    discord_bound: bool | None = None,
    session_id: str | None = None,
    test_id: str | None = None,
    briefing_date: str | None = None,
    setup_evidence: object | None = None,
) -> dict:
    revision = revision or _capture_revision()
    dependencies = dict(dependencies or _capture_dependencies())
    session_id = session_id or f"openhouse-acceptance-{uuid.uuid4().hex}"
    test_id = test_id or uuid.uuid4().hex[:12]
    briefing_date = briefing_date or "2099-12-31"
    agent_config_error = False
    try:
        runtime_agent_id = _runtime_agent_id()
    except (OSError, UnicodeError, SetupConflict):
        agent_config_error = True
    else:
        if discord_bound is None:
            discord_bound = _capture_discord_binding(runtime_agent_id)
    tracked_api = _SessionTrackingAPI(api)

    checks: list[dict] = []
    cleanup: list[dict] = []
    warnings: list[str] = []

    if agent_config_error:
        checks.append(
            _entry(
                "FAIL",
                "CRM agent configuration",
                "AGENT_ID is invalid; correct .env and rerun OpenClaw setup",
            )
        )

    checks.append(
        _entry(
            "PASS" if revision != "unavailable" else "FAIL",
            "Product revision",
            "git revision captured" if revision != "unavailable" else "git revision unavailable",
            {"revision": revision},
        )
    )
    checks.append(_setup_evidence_entry(setup_evidence, revision))
    required_missing = [
        name
        for name in ("python", "node", "npm", "openclaw")
        if dependencies.get(name) in {None, "", "not found"}
    ]
    checks.append(
        _entry(
            "FAIL" if required_missing else "PASS",
            "Dependencies",
            (
                "missing required local dependencies"
                if required_missing
                else "required local dependencies detected"
            ),
            dependencies,
        )
    )
    if dependencies.get("ollama") in {None, "", "not found"}:
        warnings.append("Ollama was not detected; another configured model provider may be in use.")

    if agent_config_error:
        checks.extend(
            [
                _entry(
                    "SKIP",
                    "Invalid write",
                    "skipped because CRM agent configuration is invalid",
                ),
                _entry(
                    "SKIP",
                    "Reviewed write",
                    "skipped because CRM agent configuration is invalid",
                ),
                _entry(
                    "SKIP",
                    "Reviewed booking",
                    "skipped because CRM agent configuration is invalid",
                ),
                _entry(
                    "SKIP",
                    "Discord delivery (manual hardware)",
                    "binding inspection skipped because CRM agent configuration is invalid",
                    {"bound": None, "automated_delivery_proof": False},
                ),
            ]
        )
        warnings.append(
            "Acceptance stopped before CRM requests because AGENT_ID is invalid."
        )
        return {
            "schema_version": 1,
            "revision": revision,
            "checks": checks,
            "cleanup": cleanup,
            "warnings": warnings,
        }

    try:
        _run_read_checks(
            tracked_api,
            checks,
            session_id=session_id,
            briefing_date=briefing_date,
        )
        read_failures = [
            check["name"] for check in checks if check.get("level") == "FAIL"
        ]
        if allow_test_write and read_failures:
            checks.extend(
                [
                    _entry(
                        "SKIP",
                        "Invalid write",
                        "skipped until all required read-only checks pass",
                        {"failed_checks": read_failures},
                    ),
                    _entry(
                        "SKIP",
                        "Reviewed write",
                        "skipped until all required read-only checks pass",
                        {"failed_checks": read_failures},
                    ),
                    _entry(
                        "SKIP",
                        "Reviewed booking",
                        "skipped until all required prerequisites and read-only checks pass",
                        {"failed_checks": read_failures},
                    ),
                ]
            )
        elif allow_test_write:
            invalid_passed = _run_invalid_write(
                tracked_api,
                checks,
                cleanup,
                session_id=session_id,
                test_id=test_id,
            )
            if invalid_passed:
                reviewed_passed = _run_reviewed_write(
                    tracked_api,
                    checks,
                    cleanup,
                    session_id=session_id,
                    test_id=test_id,
                )
                if reviewed_passed:
                    _run_reviewed_booking(
                        tracked_api,
                        checks,
                        cleanup,
                        session_id=session_id,
                        test_id=test_id,
                    )
                else:
                    checks.append(
                        _entry(
                            "SKIP",
                            "Reviewed booking",
                            "skipped because the disposable create-lead safety check failed",
                            {},
                        )
                    )
            else:
                checks.extend(
                    [
                        _entry(
                            "SKIP",
                            "Reviewed write",
                            "skipped because the invalid-write safety check failed",
                            {},
                        ),
                        _entry(
                            "SKIP",
                            "Reviewed booking",
                            "skipped because the invalid-write safety check failed",
                            {},
                        ),
                    ]
                )
        else:
            checks.extend(
                [
                    _entry(
                        "SKIP",
                        "Invalid write",
                        "requires --allow-test-write",
                        {"authorized": False},
                    ),
                    _entry(
                        "SKIP",
                        "Reviewed write",
                        "requires --allow-test-write",
                        {"authorized": False},
                    ),
                    _entry(
                        "SKIP",
                        "Reviewed booking",
                        "requires --allow-test-write",
                        {"authorized": False},
                    ),
                ]
            )
    finally:
        try:
            encoded_session = urllib.parse.quote(session_id, safe="")
            result = _request_dict(
                tracked_api,
                "DELETE",
                f"/chat/history?session_id={encoded_session}",
            )
            deleted = result.get("deleted")
            session_used = session_id in tracked_api.used_sessions
            if result.get("session_id") != session_id:
                raise ValueError("chat cleanup did not match the acceptance session")
            if (
                not isinstance(deleted, int)
                or isinstance(deleted, bool)
                or deleted < 0
                or (session_used and deleted == 0)
            ):
                raise ValueError("chat cleanup did not confirm deleted messages")
            cleanup.append(
                _entry(
                    "PASS",
                    "Delete acceptance chat session",
                    "acceptance chat history was removed",
                    {
                        "session_id": "<acceptance-session>",
                        "deleted": deleted,
                    },
                )
            )
        except Exception as exc:
            cleanup.append(
                _entry(
                    "FAIL",
                    "Delete acceptance chat session",
                    _safe_error(exc),
                    {"error": _safe_error(exc)},
                )
            )

    if discord_bound is False:
        checks.append(
            _entry(
                "SKIP",
                "Discord delivery (manual hardware)",
                "no Discord account is bound to the CRM agent",
                {"bound": False, "automated_delivery_proof": False},
            )
        )
    elif discord_bound is True:
        checks.append(
            _entry(
                "WARN",
                "Discord delivery (manual hardware)",
                "binding detected; manually verify Discord read and reviewed-write delivery",
                {"bound": True, "automated_delivery_proof": False},
            )
        )
        warnings.append("Discord is bound but this local command cannot prove channel delivery.")
    else:
        checks.append(
            _entry(
                "WARN",
                "Discord delivery (manual hardware)",
                "Discord binding status could not be determined",
                {"bound": None, "automated_delivery_proof": False},
            )
        )

    return {
        "schema_version": 1,
        "revision": revision,
        "checks": checks,
        "cleanup": cleanup,
        "warnings": warnings,
    }


def _sanitize_text(value: str, *, limit: int = MAX_TEXT) -> str:
    text = value
    home = str(Path.home())
    if home and home != os.path.sep:
        text = text.replace(home, "<home>")
    for name in (
        "OHI_API_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_API_TOKEN",
    ):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"https?://[^\s\"']+", "<url>", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+/)+[^\s]+", "<path>", text)
    text = re.sub(r"\b[A-Za-z]:\\(?:[^\s\\]+\\)+[^\s]+", "<path>", text)
    return text[:limit]


def _sanitize(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        return "<truncated>"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {
            _sanitize_text(str(key))[:100]: _sanitize(child, depth=depth + 1)
            for key, child in list(value.items())[:100]
        }
    if isinstance(value, list):
        return [_sanitize(child, depth=depth + 1) for child in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value))


def render_report(result: dict, *, as_json: bool) -> str:
    safe = _sanitize(result)
    if as_json:
        return json.dumps(safe, indent=2, sort_keys=True)
    assert isinstance(safe, dict)
    lines = [f"Revision: {safe.get('revision', 'unavailable')}"]
    for check in safe.get("checks", []):
        lines.append(
            f"{check['level']:4}  {check['name']}: {check['detail']}"
        )
    for item in safe.get("cleanup", []):
        lines.append(
            f"{item['level']:4}  Cleanup, {item['name']}: {item['detail']}"
        )
    for warning in safe.get("warnings", []):
        lines.append(f"WARN  {warning}")
    return "\n".join(lines)


def exit_code(result: dict) -> int:
    required = [*result.get("checks", []), *result.get("cleanup", [])]
    return 1 if any(item.get("level") == "FAIL" for item in required) else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run sanitized acceptance checks against a running local CRM. "
            "The default command is read-only."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080/api",
        help="running application API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("AGENT_TIMEOUT_SECONDS", "120")) + 5,
        help="seconds allowed for each application request",
    )
    parser.add_argument(
        "--allow-test-write",
        action="store_true",
        help=(
            "authorize one invalid-write check plus disposable lead and booking "
            "Pending proposals; both proposals are denied, never approved"
        ),
    )
    parser.add_argument(
        "--setup-evidence",
        type=Path,
        help=(
            "machine-readable evidence from two explicit setup runs, created by "
            "scripts/capture_setup_evidence.py"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the sanitized machine-readable report",
    )
    args = parser.parse_args()

    setup_evidence: object | None = None
    if args.setup_evidence is not None:
        try:
            setup_evidence = _load_setup_evidence(args.setup_evidence)
        except ValueError:
            setup_evidence = {"invalid_setup_evidence": True}

    result = run_acceptance(
        HttpAPI(args.base_url, timeout=args.timeout),
        allow_test_write=args.allow_test_write,
        setup_evidence=setup_evidence,
    )
    print(render_report(result, as_json=args.json))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
