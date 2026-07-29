"""Thin Composio client (only touched when INTEGRATIONS_MODE=live).

Two transports, picked by COMPOSIO_TRANSPORT:
- "api" (default): POST {base}/api/v3/tools/execute/{slug} with x-api-key header
  and {"user_id": ..., "arguments": {...}} body → {"successful": bool,
  "data": {...}, "error": str|null}. Needs a project API key (ak_...).
- "cli": shell out to the locally-authed `composio` CLI (managed OAuth — the
  CLI's uak_ session key is NOT valid for the REST API, hence this path).

Tool schemas: `composio execute <SLUG> --get-schema`.
"""
import json
import os
import shutil
import subprocess
import threading

import httpx


class IntegrationError(Exception):
    pass


# Real usage counter for the "no cloud calls in off mode" pitch (dashboard
# metrics). Counts calls to composio_client.execute() only — i.e. Composio
# tool calls (Gmail/Calendar), NOT local-LLM inference; the openclaw driver's
# requests are deliberately excluded since they never leave the box.
# Incremented only once execute() has confirmed we're actually live
# (mode == "live" and a key/CLI session is present) — never in off mode, and
# never per-retry (one user-visible call == one increment). hooks/router/
# poller reach execute() from FastAPI's threadpool, so the increment is
# guarded by a lock rather than a bare `+= 1` (non-atomic read-modify-write).
_REQUEST_COUNT = 0
_REQUEST_COUNT_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_LAST_OPERATION: str | None = None
_LAST_DETAIL: str | None = None


def request_count() -> int:
    return _REQUEST_COUNT


def _record_operation(result: str, detail: str | None = None) -> None:
    global _LAST_OPERATION, _LAST_DETAIL
    with _STATUS_LOCK:
        _LAST_OPERATION = result
        _LAST_DETAIL = detail


def status() -> dict:
    with _STATUS_LOCK:
        configured = is_live()
        return {
            "mode": mode(),
            "configured": configured,
            "last_operation": _LAST_OPERATION if configured else None,
            "detail": _LAST_DETAIL if configured else None,
        }


# Every slug this backend actually calls (hooks.py, router.py, poller.py) —
# the single gate all cc.execute() traffic passes through. Destructive/
# unreviewed slugs (e.g. GMAIL_DELETE_MESSAGE) are refused even if the model
# tries to invoke them via a future direct-execute path.
ALLOWED_SLUGS = frozenset({
    "GOOGLECALENDAR_CREATE_EVENT",   # hooks.py: lead-created call block, tour booked, reminder
    "GMAIL_CREATE_EMAIL_DRAFT",      # hooks.py: lead-created intro draft
    "GMAIL_SEND_EMAIL",              # router.py: /api/email/send
    "GMAIL_FETCH_EMAILS",            # poller.py: inbox polling
    "GOOGLECALENDAR_FREE_BUSY_QUERY",  # poller.py: busy cache
})

READ_ONLY_SLUGS = frozenset({
    "GMAIL_FETCH_EMAILS",
    "GOOGLECALENDAR_FREE_BUSY_QUERY",
})


def max_attempts(slug: str) -> int:
    """Only replay operations that cannot create an external side effect."""
    return 2 if slug in READ_ONLY_SLUGS else 1


def mode() -> str:
    return os.environ.get("INTEGRATIONS_MODE", "off")


def transport() -> str:
    return os.environ.get("COMPOSIO_TRANSPORT", "api")


def is_live() -> bool:
    if mode() != "live":
        return False
    return transport() == "cli" or bool(os.environ.get("COMPOSIO_API_KEY"))


def _execute_cli(slug: str, arguments: dict) -> dict:
    path = shutil.which("composio") or os.path.expanduser("~/.composio/composio")
    if not os.path.exists(path):
        raise IntegrationError(f"{slug}: composio CLI not found")
    try:
        proc = subprocess.run([path, "execute", slug, "-d", json.dumps(arguments)],
                              capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise IntegrationError(f"{slug}: CLI timed out")
    payload = {}
    for line in reversed([l for l in proc.stdout.splitlines() if l.strip()]):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if proc.returncode == 0 and payload.get("successful"):
        return payload.get("data") or {}
    # never surface raw stderr (may contain tokens/paths/tracebacks) into chat
    err = payload.get("error") or (
        "composio CLI failed — check `composio link` / logs" if proc.stderr.strip()
        else f"exit {proc.returncode}")
    raise IntegrationError(f"{slug}: {err}")


def execute(slug: str, arguments: dict) -> dict:
    if slug not in ALLOWED_SLUGS:
        raise IntegrationError(f"{slug}: not in the approved catalog")
    if not is_live():
        raise IntegrationError("integrations disabled (INTEGRATIONS_MODE != live or no key)")
    global _REQUEST_COUNT
    with _REQUEST_COUNT_LOCK:
        _REQUEST_COUNT += 1
    if transport() == "cli":
        try:
            result = _execute_cli(slug, arguments)
        except IntegrationError:
            _record_operation("failed", "provider_error")
            raise
        _record_operation("succeeded")
        return result
    key = os.environ.get("COMPOSIO_API_KEY")
    if not key:
        raise IntegrationError("COMPOSIO_API_KEY not set")
    base = os.environ.get("COMPOSIO_BASE_URL", "https://backend.composio.dev")
    body = {"user_id": os.environ.get("COMPOSIO_USER_ID", "default"),
            "arguments": arguments}
    last_err = None
    last_detail = "provider_error"
    for _ in range(max_attempts(slug)):
        try:
            r = httpx.post(f"{base}/api/v3/tools/execute/{slug}",
                           headers={"x-api-key": key}, json=body, timeout=15)
            payload = r.json() if r.status_code < 500 else {}
            if r.status_code == 200 and payload.get("successful"):
                _record_operation("succeeded")
                return payload.get("data") or {}
            last_err = payload.get("error") or f"HTTP {r.status_code}"
            last_detail = "provider_error"
        except httpx.TimeoutException as e:
            last_err = str(e)
            last_detail = "timeout"
        except httpx.HTTPError as e:
            last_err = str(e)
            last_detail = "network_error"
        except ValueError as e:
            last_err = str(e)
            last_detail = "invalid_response"
    _record_operation("failed", last_detail)
    raise IntegrationError(f"{slug}: {last_err}")
