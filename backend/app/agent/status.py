"""Process-local status for OpenClaw transport and CRM capability."""

from dataclasses import dataclass, field
from threading import Lock
from typing import Literal


AgentStatus = Literal[
    "mock",
    "unreachable",
    "unauthorized",
    "endpoint_disabled",
    "endpoint_enabled",
    "chat_verified",
    "crm_verified",
    "degraded",
    "failed",
]


@dataclass(frozen=True)
class AgentProbe:
    status: AgentStatus
    gateway_reachable: bool
    endpoint_enabled: bool
    last_chat_ok: bool | None
    crm_verified: bool = False
    agent_id: str | None = None
    fallbacks: dict[str, int] = field(default_factory=dict)
    detail: str | None = None


_LOCK = Lock()
_LAST_CHAT_OK: bool | None = None
_LAST_DETAIL: str | None = None
_CRM_OK: bool | None = None
_CRM_DETAIL: str | None = None


def record_chat(ok: bool, detail: str | None = None) -> None:
    global _LAST_CHAT_OK, _LAST_DETAIL
    with _LOCK:
        _LAST_CHAT_OK = ok
        _LAST_DETAIL = detail


def last_chat() -> tuple[bool | None, str | None]:
    with _LOCK:
        return _LAST_CHAT_OK, _LAST_DETAIL


def record_crm_capability(ok: bool, detail: str | None = None) -> None:
    global _CRM_OK, _CRM_DETAIL
    with _LOCK:
        _CRM_OK = ok
        _CRM_DETAIL = detail


def last_crm_capability() -> tuple[bool | None, str | None]:
    with _LOCK:
        return _CRM_OK, _CRM_DETAIL


def resolved_status(*, gateway_reachable: bool, endpoint_enabled: bool) -> AgentStatus:
    with _LOCK:
        if not gateway_reachable:
            return "unreachable"
        if not endpoint_enabled:
            return "endpoint_disabled"
        if _CRM_OK is True:
            return "crm_verified"
        if _LAST_CHAT_OK is True:
            return "chat_verified"
        if _LAST_CHAT_OK is False:
            return "failed"
        return "endpoint_enabled"
