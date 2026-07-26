"""Thin Composio v3 REST client (only touched when INTEGRATIONS_MODE=live).

Docs: POST {base}/api/v3/tools/execute/{slug} with x-api-key header and
{"user_id": ..., "arguments": {...}} body → {"successful": bool, "data": {...},
"error": str|null}. Tool schemas: `composio execute <SLUG> --get-schema`.
"""
import os

import httpx


class IntegrationError(Exception):
    pass


def mode() -> str:
    return os.environ.get("INTEGRATIONS_MODE", "off")


def is_live() -> bool:
    return mode() == "live" and bool(os.environ.get("COMPOSIO_API_KEY"))


def execute(slug: str, arguments: dict) -> dict:
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
