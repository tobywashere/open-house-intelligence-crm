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

import httpx


class IntegrationError(Exception):
    pass


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
                              capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise IntegrationError(f"{slug}: CLI timed out")
    out = proc.stdout.strip()
    try:
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        payload = {}
    if proc.returncode == 0 and payload.get("successful"):
        return payload.get("data") or {}
    err = payload.get("error") or proc.stderr.strip()[:300] or f"exit {proc.returncode}"
    raise IntegrationError(f"{slug}: {err}")


def execute(slug: str, arguments: dict) -> dict:
    if not is_live():
        raise IntegrationError("integrations disabled (INTEGRATIONS_MODE != live or no key)")
    if transport() == "cli":
        return _execute_cli(slug, arguments)
    key = os.environ.get("COMPOSIO_API_KEY")
    if not key:
        raise IntegrationError("COMPOSIO_API_KEY not set")
    base = os.environ.get("COMPOSIO_BASE_URL", "https://backend.composio.dev")
    body = {"user_id": os.environ.get("COMPOSIO_USER_ID", "default"),
            "arguments": arguments}
    last_err = None
    for _ in range(2):  # one retry
        try:
            r = httpx.post(f"{base}/api/v3/tools/execute/{slug}",
                           headers={"x-api-key": key}, json=body, timeout=15)
            payload = r.json() if r.status_code < 500 else {}
            if r.status_code == 200 and payload.get("successful"):
                return payload.get("data") or {}
            last_err = payload.get("error") or f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last_err = str(e)
    raise IntegrationError(f"{slug}: {last_err}")
