"""Durable native create-lead requests; generated completion text is never evidence."""
import asyncio
import ipaddress
import os
import re
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .approvals import insert_pending_change
from .db import get_conn
from .routers.pending_changes import _parsed


ERRORS = {
    'invalid_request': (422, 'Invalid native proposal request.'),
    'not_configured': (503, 'Native lead proposals are not configured on this machine.'),
    'not_found': (404, 'Native lead request was not found.'),
    'request_conflict': (409, 'This request ID is already bound to another message.'),
    'proposal_conflict': (409, 'This request already has different proposed fields.'),
    'request_settled': (409, 'This request cannot accept a proposal.'),
}


class NativeProposalError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_request_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{32}', value) is None:
        raise NativeProposalError('invalid_request')
    return value


class RequestIn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str
    message: str = Field(min_length=1, max_length=2000)

    @field_validator('request_id')
    @classmethod
    def exact_id(cls, value):
        return validate_request_id(value)


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=100)

    @field_validator('request_id')
    @classmethod
    def exact_id(cls, value):
        return validate_request_id(value)

    @field_validator('email', 'phone', mode='before')
    @classmethod
    def optional_strings(cls, value):
        if not isinstance(value, str):
            raise ValueError('expected string')
        return value

    @field_validator('name', 'email', 'phone')
    @classmethod
    def normalize(cls, value, info):
        value = value.strip()
        if info.field_name == 'name' and not value:
            raise ValueError('empty name')
        return value


def _request(conn, request_id):
    row = conn.execute('SELECT * FROM native_lead_requests WHERE request_id=?', (request_id,)).fetchone()
    if row is None:
        raise NativeProposalError('not_found')
    return row


def _status(conn, row):
    proposal = None
    state = row['state']
    if row['pending_id'] is not None:
        proposal = _parsed(dict(conn.execute('SELECT * FROM pending_changes WHERE id=?', (row['pending_id'],)).fetchone()))
        state = proposal['status'] if proposal['status'] in ('approved', 'denied') else 'proposed'
    return {'request_id': row['request_id'], 'state': state, 'proposal': proposal}


def get_status(request_id):
    validate_request_id(request_id)
    with get_conn() as conn:
        return _status(conn, _request(conn, request_id))


def close_request(request_id):
    """Retire an unresolved ID atomically; a bound proposal always wins."""
    validate_request_id(request_id)
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM native_lead_requests WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            # Internal tombstone only. RequestIn still rejects empty messages.
            conn.execute("INSERT INTO native_lead_requests(request_id,message,state) VALUES (?,'','failed')", (request_id,))
        elif row['pending_id'] is None:
            conn.execute("UPDATE native_lead_requests SET state='failed' WHERE request_id=? AND pending_id IS NULL", (request_id,))
        return _status(conn, _request(conn, request_id))


def _replay(conn, request_id, message):
    row = conn.execute('SELECT * FROM native_lead_requests WHERE request_id=?', (request_id,)).fetchone()
    if row is not None:
        # An explicit human close can precede the original HTTP request. This
        # empty-message tombstone retires the ID without accepting any later text.
        if row['state'] == 'failed' and row['pending_id'] is None and row['message'] == '':
            return _status(conn, row)
        if row['message'] != message:
            raise NativeProposalError('request_conflict')
        return _status(conn, row)
    return None


def reserve_request(request_id, message):
    """Atomic reservation. Return (durable status, created); no network in lock."""
    body = RequestIn(request_id=request_id, message=message)
    with get_conn() as conn:
        replay = _replay(conn, body.request_id, body.message)
        if replay is not None:
            return replay, False
        conn.execute("INSERT INTO native_lead_requests(request_id,message,state) VALUES (?,?,'running')", (request_id,message))
        return _status(conn, _request(conn, request_id)), True


def submit_proposal(body: ProposalIn):
    """Queue resolved fields and bind the request in the same existing transaction."""
    fields = body.model_dump(exclude={'request_id'}, exclude_none=True)
    with get_conn() as conn:
        row = _request(conn, body.request_id)
        payload = {**fields, 'source': 'note', 'raw_text': row['message']}
        if row['pending_id'] is not None:
            status = _status(conn, row)
            # Original payload remains unchanged by human edits/approval.
            if status['proposal']['payload'] != payload:
                raise NativeProposalError('proposal_conflict')
            return status
        if row['state'] not in ('running', 'unknown'):
            raise NativeProposalError('request_settled')
        pending = insert_pending_change(conn, 'create_lead', None, payload,
                                        f"Create lead: {body.name}")
        changed = conn.execute(
            "UPDATE native_lead_requests SET state='proposed', pending_id=? "
            "WHERE request_id=? AND pending_id IS NULL AND state IN ('running','unknown')",
            (pending['id'], body.request_id),
        )
        if changed.rowcount != 1:
            raise NativeProposalError('request_settled')
        return _status(conn, _request(conn, body.request_id))


def recover_running_requests():
    with get_conn() as conn:
        conn.execute("UPDATE native_lead_requests SET state='unknown' WHERE state='running' AND pending_id IS NULL")


def _settle(request_id, state):
    with get_conn() as conn:
        conn.execute("UPDATE native_lead_requests SET state=? WHERE request_id=? AND pending_id IS NULL AND state='running'", (state, request_id))
        return _status(conn, _request(conn, request_id))


def settings():
    url = os.environ.get('NATIVE_PROPOSAL_GATEWAY_URL', '').rstrip('/')
    token = os.environ.get('NATIVE_PROPOSAL_GATEWAY_TOKEN', '')
    agent = os.environ.get('NATIVE_PROPOSAL_AGENT_ID', 'native-proposals')
    try:
        parts = urlsplit(url)
        local = parts.hostname == 'localhost' or ipaddress.ip_address(parts.hostname or '').is_loopback
        if not local or parts.scheme != 'http' or parts.path or parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError()
        if not token or not all(33 <= ord(c) <= 126 for c in token) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', agent):
            raise ValueError()
        _ = parts.port
    except ValueError:
        raise NativeProposalError('not_configured') from None
    return url, token, agent


async def complete_native(request_id, message, config, *, client_factory=httpx.AsyncClient):
    """Inject this async seam in test fixtures only. One ordinary native completion."""
    url, token, agent = config
    async with client_factory(timeout=60.0, follow_redirects=False, trust_env=False,
                              headers={'Authorization': f'Bearer {token}'}) as client:
        # Stream and discard prose, bounding memory regardless of provider response.
        async with client.stream('POST', f'{url}/v1/chat/completions', json={
            'model': f'openclaw/{agent}', 'user': f'ohi-propose-{request_id}',
            'messages': [{'role': 'user', 'content': message}],
        }) as response:
            response.raise_for_status()
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError('gateway response too large')


async def propose_lead(request_id, message):
    body = RequestIn(request_id=request_id, message=message)
    with get_conn() as conn:
        replay = _replay(conn, body.request_id, body.message)
        if replay is not None:
            return replay
    config = settings()  # Missing config must not consume an unused request ID.
    status, created = reserve_request(request_id, message)
    if not created:
        return status
    try:
        async with asyncio.timeout(60.0):
            await complete_native(request_id, message, config)
    except asyncio.CancelledError:
        _settle(request_id, 'unknown')
        raise
    except Exception:
        # All failed dispatches are uncertain. No provider details or prompt logs.
        return _settle(request_id, 'unknown')
    return _settle(request_id, 'failed')
