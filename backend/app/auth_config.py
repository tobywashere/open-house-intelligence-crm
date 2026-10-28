"""Dependency-free authentication policy shared by launchers and the API."""
import ipaddress
import os
import secrets
from dataclasses import dataclass
from typing import Literal


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
    if os.environ.get('AGENT_MODE', 'mock') != 'mock':
        raise RuntimeError(CONFIG_ERROR + ': use native OpenClaw profiles; the legacy shared-key agent is disabled')
    if human or agent:
        if (
            not _valid_capability_token(human)
            or not _valid_capability_token(agent)
            or secrets.compare_digest(human, agent)
        ):
            raise RuntimeError(CONFIG_ERROR)
        return AuthConfig("capabilities", human, agent)
    if (
        os.environ.get('OHI_DEMO_MODE') != '1'
        or os.environ.get('INTEGRATIONS_MODE', 'off') != 'off'
        or not is_loopback(os.environ.get('HOST', '127.0.0.1'))
    ):
        raise RuntimeError(CONFIG_ERROR + ': configure distinct human/agent keys, or explicitly select local mock demo mode')
    return AuthConfig("local", "", "")


def is_loopback(host: str) -> bool:
    if host.lower() == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback
    except ValueError:
        return False
