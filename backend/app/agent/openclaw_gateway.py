"""The single HTTP boundary for the local OpenClaw Gateway."""
import json
import math
import os
from collections.abc import Mapping

import httpx


def resolve_gateway_url(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the Gateway URL while keeping environment overrides intact."""
    values = os.environ if environ is None else environ
    return values.get("AGENT_GATEWAY_URL", "http://localhost:18789")


class OpenClawGatewayError(RuntimeError):
    """A bounded, safe description of a Gateway request failure."""

    def __init__(self, message: str, *, definite_pre_dispatch: bool = False):
        super().__init__(message)
        self.definite_pre_dispatch = definite_pre_dispatch


class OpenClawGateway:
    def __init__(
        self,
        client_factory=None,
        *,
        gateway_url: str | None = None,
        chat_path: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ):
        self._client_factory = client_factory or httpx.AsyncClient
        self._gateway_url = (gateway_url or resolve_gateway_url()).rstrip("/")
        self._chat_path = chat_path or os.environ.get(
            "AGENT_CHAT_PATH", "/v1/chat/completions"
        )
        self._token = token if token is not None else os.environ.get(
            "AGENT_GATEWAY_TOKEN", ""
        )
        self._timeout = timeout if timeout is not None else float(
            os.environ.get("AGENT_TIMEOUT_SECONDS", "120")
        )

    async def chat_completion(
        self,
        payload: dict,
        *,
        channel: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        return await self._post_json(
            self._chat_path, payload, channel=channel, timeout=timeout
        )

    async def invoke_tool(
        self,
        name: str,
        args: dict,
        *,
        agent_id: str,
        session_key: str,
        idempotency_key: str,
        timeout: float | None = None,
    ) -> dict:
        payload = {
            "tool": name,
            "args": args,
            "agentId": agent_id,
            "sessionKey": session_key,
            "idempotencyKey": idempotency_key,
        }
        response = await self._post_json("/tools/invoke", payload, timeout=timeout)
        return _tool_invoke_details(response)

    async def chat_endpoint_status(self) -> int:
        try:
            async with self._client_factory(timeout=3) as client:
                response = await client.options(
                    self._url(self._chat_path), headers=self._headers()
                )
            return response.status_code
        except OpenClawGatewayError:
            raise
        except httpx.TimeoutException:
            raise OpenClawGatewayError("gateway timeout") from None
        except Exception:
            raise OpenClawGatewayError("gateway request failed") from None

    async def _post_json(
        self,
        path: str,
        payload: dict,
        *,
        channel: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        timeout_candidates = (
            (self._timeout,) if timeout is None else (self._timeout, timeout)
        )
        if any(
            not isinstance(candidate, (int, float))
            or isinstance(candidate, bool)
            or not math.isfinite(candidate)
            or candidate <= 0
            for candidate in timeout_candidates
        ):
            raise OpenClawGatewayError(
                "gateway timeout", definite_pre_dispatch=True
            )
        request_timeout = min(timeout_candidates)
        try:
            async with self._client_factory(timeout=request_timeout) as client:
                response = await client.post(
                    self._url(path),
                    headers=self._headers(channel),
                    json=payload,
                )
            if response.status_code >= 400:
                raise OpenClawGatewayError(
                    _status_detail(response.status_code),
                    definite_pre_dispatch=response.status_code in {
                        400, 401, 403, 404, 429,
                    },
                )
            try:
                data = response.json()
            except (TypeError, ValueError):
                raise OpenClawGatewayError("invalid gateway response") from None
            if not isinstance(data, dict):
                raise OpenClawGatewayError("invalid gateway response")
            return data
        except OpenClawGatewayError:
            raise
        except httpx.TimeoutException:
            raise OpenClawGatewayError("gateway timeout") from None
        except Exception:
            raise OpenClawGatewayError("gateway request failed") from None

    def _url(self, path: str) -> str:
        return self._gateway_url + "/" + path.lstrip("/")

    def _headers(self, channel: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if channel is not None:
            headers["x-openclaw-message-channel"] = channel
        return headers


def _status_detail(status_code: int) -> str:
    if status_code == 400:
        return "gateway rejected tool input"
    if status_code in (401, 403):
        return "gateway authentication failed"
    if status_code == 404:
        return "gateway tool is unavailable"
    if status_code == 429:
        return "gateway authentication is throttled"
    return "gateway request failed"


def _tool_invoke_details(payload: object) -> dict:
    """Return the receipt from OpenClaw's one supported tool-invoke envelope."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"ok", "result"}
        or payload.get("ok") is not True
    ):
        raise OpenClawGatewayError("invalid gateway response")
    result = payload.get("result")
    if (
        not isinstance(result, dict)
        or set(result) != {"content", "details"}
        or not isinstance(result.get("details"), dict)
    ):
        raise OpenClawGatewayError("invalid gateway response")
    content = result.get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or set(content[0]) != {"type", "text"}
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise OpenClawGatewayError("invalid gateway response")
    details = result["details"]
    try:
        mirrored_details = json.loads(content[0]["text"])
    except (TypeError, ValueError, json.JSONDecodeError):
        raise OpenClawGatewayError("invalid gateway response") from None
    if mirrored_details != details or not _is_crm_receipt(details):
        raise OpenClawGatewayError("invalid gateway response")
    return details


def _is_crm_receipt(payload: object) -> bool:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("operation"), str)
        or not payload["operation"].strip()
    ):
        return False
    if payload.get("ok") is True:
        return (
            set(payload) == {"ok", "operation", "kind", "result"}
            and payload.get("kind") in {
                "read", "narrative", "proposal", "validated_write",
            }
        )
    return (
        payload.get("ok") is False
        and set(payload) == {"ok", "operation", "kind", "error"}
        and payload.get("kind") == "error"
        and isinstance(payload.get("error"), dict)
    )
