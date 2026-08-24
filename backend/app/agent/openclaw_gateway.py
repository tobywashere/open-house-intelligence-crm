"""The single HTTP boundary for the local OpenClaw Gateway."""
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
        return await self._post_json("/tools/invoke", payload, timeout=timeout)

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
            raise OpenClawGatewayError("gateway timeout")
        request_timeout = min(timeout_candidates)
        try:
            async with self._client_factory(timeout=request_timeout) as client:
                response = await client.post(
                    self._url(path),
                    headers=self._headers(channel),
                    json=payload,
                )
            if response.status_code >= 400:
                raise OpenClawGatewayError(_status_detail(response.status_code))
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
