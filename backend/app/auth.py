"""Request identity for local, legacy token, and capability-token installs."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request


AuthMode = Literal["local", "token", "capabilities"]
AuthRole = Literal["human", "agent"]
CONFIG_ERROR = "invalid capability authentication configuration"


@dataclass(frozen=True)
class AuthConfig:
    mode: AuthMode
    human_token: str
    agent_token: str


def _valid_capability_token(value: str) -> bool:
    """Capability credentials are header-safe printable ASCII secrets."""
    return len(value) >= 32 and all(33 <= ord(char) <= 126 for char in value)


def get_auth_config() -> AuthConfig:
    """Read and validate the current environment authentication settings.

    Environment is read for every request so a bad runtime mutation fails
    closed as well as being rejected during application startup.
    """
    human = os.environ.get("OHI_API_TOKEN", "")
    agent = os.environ.get("OHI_AGENT_API_TOKEN", "")
    if agent:
        if (
            not _valid_capability_token(human)
            or not _valid_capability_token(agent)
            or secrets.compare_digest(human, agent)
        ):
            raise RuntimeError(CONFIG_ERROR)
        return AuthConfig("capabilities", human, agent)
    if human:
        return AuthConfig("token", human, "")
    return AuthConfig("local", "", "")


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
