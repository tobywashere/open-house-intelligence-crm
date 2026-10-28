"""Request identity for explicit demo and capability-token installs."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .auth_config import AuthConfig, AuthMode, AuthRole, CONFIG_ERROR, get_auth_config, is_loopback


def demo_request_allowed(request: Request) -> bool:
    """Tokenless demo is for direct loopback clients, never a remote proxy."""
    if not request.client or not is_loopback(request.client.host):
        return False
    if not is_loopback(request.url.hostname or ''):
        return False
    origin = request.headers.get('origin')
    if origin is not None:
        try:
            parsed = urlsplit(origin)
            return parsed.scheme in ('http', 'https') and is_loopback(parsed.hostname or '')
        except ValueError:
            return False
    return True


def _matches(presented: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    )


def identify(config: AuthConfig, presented_token: str) -> AuthRole | None:
    if config.mode == "local":
        return "human"
    if _matches(presented_token, config.human_token):
        return "human"
    if config.mode == "capabilities" and _matches(presented_token, config.agent_token):
        return "agent"
    return None


def _require_role(request: Request, role: AuthRole) -> AuthRole:
    try:
        config = get_auth_config()
    except RuntimeError as exc:
        raise HTTPException(503, CONFIG_ERROR) from exc
    if (
        config.mode != "capabilities"
        or getattr(request.state, "auth_mode", None) != "capabilities"
    ):
        raise HTTPException(403, "capability authentication is required")
    if getattr(request.state, "auth_role", None) != role:
        raise HTTPException(403, f"{role} capability is required")
    return role


def require_human(request: Request) -> AuthRole:
    """FastAPI dependency for native operations reserved to a human key."""
    return _require_role(request, "human")


def require_agent(request: Request) -> AuthRole:
    """FastAPI dependency for native operations reserved to the agent key."""
    return _require_role(request, "agent")
