#!/usr/bin/env python3
"""Sanitized, one-command acceptance checks for a running local CRM."""

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
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Protocol

try:
    from scripts import doctor
except ModuleNotFoundError:  # Direct execution puts scripts/, not the repo, on sys.path.
    import doctor  # type: ignore[no-redef]


REPO = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 1024 * 1024
MAX_TEXT = 500
PENDING_RE = re.compile(r"\bQueued Pending approval #(\d+)\b")
DIRECTORY_RE = re.compile(r"^\s*(\d+) leads total\.", re.IGNORECASE)
FORBIDDEN_BRIEFING_KEYS = {
    "ai_insights",
    "citations",
    "headlines",
    "market",
    "market_watch",
    "news",
    "sources",
}
CANONICAL_BRIEFING_KEYS = {
    "date",
    "generated_at",
    "source",
    "greeting",
    "schedule",
    "meeting_briefs",
    "suggested_actions",
}


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


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


def _capture_discord_binding(agent_id: str = "openhouse-crm") -> bool | None:
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


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_number_or_none(value: object) -> bool:
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _is_text(value: object, *, allow_none: bool = False) -> bool:
    return (allow_none and value is None) or (
        isinstance(value, str) and bool(value.strip())
    )


def _valid_schedule_item(value: object) -> bool:
    keys = {"appointment_id", "start", "end", "kind", "title", "lead_id"}
    return (
        isinstance(value, dict)
        and set(value) == keys
        and _is_positive_int(value.get("appointment_id"))
        and _is_positive_int(value.get("lead_id"))
        and isinstance(value.get("start"), str)
        and re.fullmatch(r"\d{2}:\d{2}", value["start"]) is not None
        and isinstance(value.get("end"), str)
        and re.fullmatch(r"\d{2}:\d{2}", value["end"]) is not None
        and value.get("kind") == "meeting"
        and _is_text(value.get("title"))
    )


def _valid_assistant_advice(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"prepare", "recommendation"}
        and isinstance(value.get("prepare"), list)
        and all(_is_text(item) for item in value["prepare"])
        and _is_text(value.get("recommendation"), allow_none=True)
    )


def _valid_meeting_brief(value: object) -> bool:
    keys = {
        "appointment_id",
        "lead_id",
        "name",
        "area",
        "budget",
        "timeline",
        "intent",
        "preferences",
        "persona",
        "score",
        "summary",
        "assistant_advice",
    }
    return (
        isinstance(value, dict)
        and set(value) == keys
        and _is_positive_int(value.get("appointment_id"))
        and _is_positive_int(value.get("lead_id"))
        and _is_text(value.get("name"))
        and _is_text(value.get("area"), allow_none=True)
        and _is_number_or_none(value.get("budget"))
        and _is_text(value.get("timeline"), allow_none=True)
        and _is_text(value.get("intent"), allow_none=True)
        and isinstance(value.get("preferences"), list)
        and all(_is_text(item) for item in value["preferences"])
        and _is_text(value.get("persona"), allow_none=True)
        and _is_number_or_none(value.get("score"))
        and _is_text(value.get("summary"))
        and _valid_assistant_advice(value.get("assistant_advice"))
    )


def _valid_suggested_action(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "lead_id",
        "name",
        "channel",
        "action",
        "reason",
        "evidence",
    }:
        return False
    evidence = value.get("evidence")
    return (
        _is_positive_int(value.get("lead_id"))
        and _is_text(value.get("name"))
        and value.get("channel") in {"email", "call", "text"}
        and _is_text(value.get("action"))
        and _is_text(value.get("reason"))
        and isinstance(evidence, dict)
        and set(evidence) == {"kind", "id"}
        and evidence.get("kind") in {"reminder", "lead"}
        and _is_positive_int(evidence.get("id"))
    )


def _valid_nested_briefing(briefing: dict) -> bool:
    schedule = briefing.get("schedule")
    meeting_briefs = briefing.get("meeting_briefs")
    suggested_actions = briefing.get("suggested_actions")
    return (
        isinstance(schedule, list)
        and all(_valid_schedule_item(item) for item in schedule)
        and isinstance(meeting_briefs, list)
        and all(_valid_meeting_brief(item) for item in meeting_briefs)
        and isinstance(suggested_actions, list)
        and all(_valid_suggested_action(item) for item in suggested_actions)
    )


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
        unexpected = sorted(set(briefing) - CANONICAL_BRIEFING_KEYS)
        date_matches = briefing.get("date") == briefing_date
        nested_shape_valid = _valid_nested_briefing(briefing)
        truthful = (
            summary_missing
            and set(briefing) == CANONICAL_BRIEFING_KEYS
            and date_matches
            and briefing.get("source") == "crm"
            and _is_text(briefing.get("generated_at"))
            and _is_text(briefing.get("greeting"))
            and nested_shape_valid
            and not forbidden
            and not unexpected
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
    try:
        baseline_ids = set(_pending_snapshot(api))
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
        owned = _owned_proposals(_pending_snapshot(api), baseline_ids, name)
        lead_applied = _lead_exists(_request_list(api, "GET", "/leads"), name)
    except Exception as exc:
        error = _safe_error(exc)
        if baseline_ids is not None:
            try:
                owned = _owned_proposals(
                    _pending_snapshot(api), baseline_ids, name
                )
            except Exception:
                pass
    truthful = "nothing was queued or changed" in reply.casefold()
    passed = error is None and truthful and not owned and not lead_applied
    checks.append(
        _entry(
            "PASS" if passed else "FAIL",
            "Invalid write",
            (
                "invalid arguments changed nothing"
                if passed
                else error or "invalid write did not prove that nothing changed"
            ),
            {"new_pending_count": len(owned), "lead_applied": lead_applied},
        )
    )
    if owned:
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
) -> None:
    name = f"OHI ACCEPTANCE TEST {test_id}"
    baseline_ids: set[int] | None = None
    owned: dict[int, dict] = {}
    reported_pending_id: int | None = None
    pending_id: int | None = None
    absent_before = False
    absent_after = False
    denied = False
    error: str | None = None
    try:
        baseline_ids = set(_pending_snapshot(api))
        reply = _chat(
            api,
            session_id,
            (
                "Create exactly one disposable CRM lead for acceptance testing. "
                f"Use the exact name {name}. The change must wait for human review. "
                f"NAME={name}"
            ),
        )
        match = PENDING_RE.search(reply)
        if match:
            reported_pending_id = int(match.group(1))
        snapshot = _pending_snapshot(api)
        owned = _owned_proposals(snapshot, baseline_ids, name)
        if len(owned) != 1 or reported_pending_id not in owned:
            raise ValueError("chat did not return exactly one real pending proposal ID")
        pending_id = reported_pending_id
        absent_before = not _lead_exists(_request_list(api, "GET", "/leads"), name)
        if not absent_before:
            raise ValueError("disposable lead was applied before review")
    except Exception as exc:
        error = _safe_error(exc)
        if baseline_ids is not None:
            try:
                owned = _owned_proposals(
                    _pending_snapshot(api), baseline_ids, name
                )
            except Exception:
                pass
    finally:
        failures: list[int] = []
        for candidate in sorted(owned):
            try:
                _deny_pending(api, candidate, "Automated disposable acceptance lead")
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
                    else "no disposable proposal was created"
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
) -> dict:
    revision = revision or _capture_revision()
    dependencies = dict(dependencies or _capture_dependencies())
    session_id = session_id or f"openhouse-acceptance-{uuid.uuid4().hex}"
    test_id = test_id or uuid.uuid4().hex[:12]
    briefing_date = briefing_date or "2099-12-31"
    if discord_bound is None:
        discord_bound = _capture_discord_binding(
            os.environ.get("OPENCLAW_AGENT_ID", "openhouse-crm")
        )
    tracked_api = _SessionTrackingAPI(api)

    checks: list[dict] = []
    cleanup: list[dict] = []
    warnings: list[str] = []

    checks.append(
        _entry(
            "PASS" if revision != "unavailable" else "FAIL",
            "Product revision",
            "git revision captured" if revision != "unavailable" else "git revision unavailable",
            {"revision": revision},
        )
    )
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
                _run_reviewed_write(
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
                        "Reviewed write",
                        "skipped because the invalid-write safety check failed",
                        {},
                    )
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
                "Discord",
                "no Discord account is bound to the CRM agent",
                {"bound": False},
            )
        )
    elif discord_bound is True:
        checks.append(
            _entry(
                "WARN",
                "Discord",
                "binding detected; verify channel delivery after dashboard acceptance",
                {"bound": True},
            )
        )
        warnings.append("Discord is bound but this local command cannot prove channel delivery.")
    else:
        checks.append(
            _entry(
                "WARN",
                "Discord",
                "Discord binding status could not be determined",
                {"bound": None},
            )
        )

    return {
        "schema_version": 1,
        "revision": revision,
        "checks": checks,
        "cleanup": cleanup,
        "warnings": warnings,
    }


def _sanitize_text(value: str) -> str:
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
    return text[:MAX_TEXT]


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
            "authorize one invalid-write check and one disposable Pending proposal; "
            "the proposal is denied, never approved"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the sanitized machine-readable report",
    )
    args = parser.parse_args()

    result = run_acceptance(
        HttpAPI(args.base_url, timeout=args.timeout),
        allow_test_write=args.allow_test_write,
    )
    print(render_report(result, as_json=args.json))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
