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
_EVENT_SEQUENCE = 0
_LAST_CHAT_SEQUENCE = 0
_CRM_SEQUENCE = 0
_FALLBACKS: dict[str, int] = {}
_LAST_FALLBACK_SEQUENCE = 0


def record_chat(ok: bool, detail: str | None = None) -> None:
    global _EVENT_SEQUENCE, _LAST_CHAT_OK, _LAST_CHAT_SEQUENCE, _LAST_DETAIL
    with _LOCK:
        _EVENT_SEQUENCE += 1
        _LAST_CHAT_OK = ok
        _LAST_CHAT_SEQUENCE = _EVENT_SEQUENCE
        _LAST_DETAIL = detail


def last_chat() -> tuple[bool | None, str | None]:
    with _LOCK:
        return _LAST_CHAT_OK, _LAST_DETAIL


def record_crm_capability(ok: bool, detail: str | None = None) -> None:
    global _CRM_DETAIL, _CRM_OK, _CRM_SEQUENCE, _EVENT_SEQUENCE
    with _LOCK:
        _EVENT_SEQUENCE += 1
        _CRM_OK = ok
        _CRM_SEQUENCE = _EVENT_SEQUENCE
        _CRM_DETAIL = detail


def last_crm_capability() -> tuple[bool | None, str | None]:
    with _LOCK:
        return _CRM_OK, _CRM_DETAIL


def record_fallback(kind: Literal["extract", "draft_followup", "score_explanation"]) -> None:
    global _EVENT_SEQUENCE, _LAST_FALLBACK_SEQUENCE
    with _LOCK:
        _EVENT_SEQUENCE += 1
        _LAST_FALLBACK_SEQUENCE = _EVENT_SEQUENCE
        _FALLBACKS[kind] = _FALLBACKS.get(kind, 0) + 1


def fallback_counts() -> dict[str, int]:
    with _LOCK:
        return dict(_FALLBACKS)


def resolved_status(*, gateway_reachable: bool, endpoint_enabled: bool) -> AgentStatus:
    with _LOCK:
        if not gateway_reachable:
            return "unreachable"
        if not endpoint_enabled:
            return "endpoint_disabled"
        if (
            _CRM_OK is True
            and (
                (_LAST_CHAT_OK is False and _LAST_CHAT_SEQUENCE > _CRM_SEQUENCE)
                or _LAST_FALLBACK_SEQUENCE > _CRM_SEQUENCE
            )
        ):
            return "degraded"
        if _CRM_OK is True:
            return "crm_verified"
        if _LAST_CHAT_OK is True:
            return "chat_verified"
        if _LAST_CHAT_OK is False:
            return "failed"
        return "endpoint_enabled"
