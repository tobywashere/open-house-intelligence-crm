"""Process-local status for the configured OpenClaw chat endpoint."""

from dataclasses import dataclass
from threading import Lock
from typing import Literal


AgentStatus = Literal[
    "mock",
    "unreachable",
    "unauthorized",
    "endpoint_disabled",
    "endpoint_enabled",
    "verified",
    "failed",
]


@dataclass(frozen=True)
class AgentProbe:
    status: AgentStatus
    gateway_reachable: bool
    endpoint_enabled: bool
    last_chat_ok: bool | None
    detail: str | None = None


_LOCK = Lock()
_LAST_CHAT_OK: bool | None = None
_LAST_DETAIL: str | None = None


def record_chat(ok: bool, detail: str | None = None) -> None:
    global _LAST_CHAT_OK, _LAST_DETAIL
    with _LOCK:
        _LAST_CHAT_OK = ok
        _LAST_DETAIL = detail


def last_chat() -> tuple[bool | None, str | None]:
    with _LOCK:
        return _LAST_CHAT_OK, _LAST_DETAIL
