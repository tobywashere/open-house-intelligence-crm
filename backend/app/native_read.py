"""One native OpenClaw read, followed by a correlated structured receipt.

The completion's prose is deliberately unused. The isolated plugin exposes no
write capability and publishes receipts only from its actual tool handler.
"""
import asyncio
import ipaddress
import os
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class NativeReadError(Exception):
    def __init__(self, code: str):
        self.code = code


class DirectoryLead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1000)
    status: Literal["new", "contacted", "meeting_booked", "closed"]


class Directory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    total: int = Field(ge=0)
    offset: Literal[0]
    limit: Literal[25]
    leads: list[DirectoryLead] = Field(max_length=25)

    @model_validator(mode="after")
    def consistent_page(self):
        if len(self.leads) != min(self.total, self.limit):
            raise ValueError("inconsistent directory page")
        if len({lead.id for lead in self.leads}) != len(self.leads):
            raise ValueError("duplicate directory rows")
        return self


class ReadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation: Literal["list_lead_directory"]
    result: Directory


# This narrow English vocabulary describes the whole directory only. Unknown
# words (including filter values, dates, negation, and page numbers) must not
# silently become a verified answer to a different question. This is a scope
# guard, not authorization: the plugin still independently permits only GET.
_UNFILTERED_WORDS = frozenset("""
    all and are as check count crm current directory do for get give have how
    i in is its lead leads list listed many me my now number of our people
    please read report retrieve right show size summarize tell the there to
    total we what with
""".split())


def supports_request(message: str) -> bool:
    normalized = message.casefold().replace("what's", "what is").replace("what’s", "what is")
    words = set(re.sub(r"[,.?!]", " ", normalized).split())
    return (words <= _UNFILTERED_WORDS
            and bool(words & {"lead", "leads", "directory"})
            and bool(words & {"count", "number", "many", "total", "directory", "list", "show", "size"}))


def settings():
    url = os.environ.get("NATIVE_READ_GATEWAY_URL", "").rstrip("/")
    token = os.environ.get("NATIVE_READ_GATEWAY_TOKEN", "")
    agent = os.environ.get("NATIVE_READ_AGENT_ID", "native-read")
    try:
        parts = urlsplit(url)
        local = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname or "").is_loopback
        if not local or parts.scheme != "http" or parts.path or parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError()
        if not token or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", agent):
            raise ValueError()
        _ = parts.port
    except ValueError:
        raise NativeReadError("not_configured") from None
    return url, token, agent


async def read_directory(message: str, *, client_factory=httpx.AsyncClient, timeout_seconds=60.0) -> ReadReceipt:
    if not supports_request(message):
        raise NativeReadError("unsupported_request")
    url, token, agent = settings()
    request_id = secrets.token_hex(16)
    receipt_url = f"{url}/openhouse/read-receipts/{request_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with client_factory(timeout=timeout_seconds, headers=headers, follow_redirects=False) as client:
            try:
                async with asyncio.timeout(timeout_seconds):
                    reserved = await client.post(receipt_url)
                    if reserved.status_code != 201:
                        raise NativeReadError("receipt_unavailable")
                    completion = await client.post(f"{url}/v1/chat/completions", json={
                        "model": f"openclaw/{agent}",
                        "user": f"ohi-read-{request_id}",
                        "messages": [{"role": "user", "content": message}],
                    })
                    if completion.status_code != 200:
                        raise NativeReadError("gateway_failed")
                    response = await client.get(receipt_url)
                    if response.status_code != 200:
                        raise NativeReadError("missing_result")
                    try:
                        receipt = ReadReceipt.model_validate(response.json())
                        if receipt.request_id != request_id:
                            raise ValueError("stale receipt")
                    except (ValueError, TypeError):
                        raise NativeReadError("invalid_result") from None
                    return receipt
            finally:
                # Deletes pending/failed reservations too. Late handler returns
                # cannot re-create a deleted reservation; expiry is a backstop.
                try:
                    async with asyncio.timeout(2):
                        await client.delete(receipt_url, timeout=2)
                except Exception:
                    pass
    except NativeReadError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise NativeReadError("timeout") from None
    except httpx.HTTPError:
        raise NativeReadError("gateway_unavailable") from None


ERRORS = {
    "unsupported_request": (
        "This view supports only unfiltered lead counts and the first directory page. "
        "Filters, sorting, and later pages are not supported. "
        "Try 'How many leads are in the CRM?' or 'Show the lead directory.' "
        "It cannot change CRM records."
    ),
    "not_configured": "Native CRM reads are not configured on this machine.",
    "receipt_unavailable": "The CRM read service could not start this request. Try again.",
    "gateway_failed": "OpenClaw could not complete this CRM read. Try again.",
    "gateway_unavailable": "OpenClaw is unavailable. No current CRM result was received.",
    "missing_result": "OpenClaw did not return a verified CRM tool result. Try again.",
    "invalid_result": "The CRM tool result could not be verified. No result is displayed.",
    "timeout": "The CRM read timed out. No current result was received.",
}
