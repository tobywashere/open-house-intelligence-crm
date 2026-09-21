"""Test-only synthetic completion boundary; never used by the production app.

Loaded only by the disposable browser runner. Real reservation, capability
middleware, HTTP agent submission, SQLite and human decisions remain in use.
"""
import os
import httpx

from app import native_proposals
from app.main import app


async def synthetic_completion(request_id, message, config):
    if message == 'Synthetic unknown':
        raise TimeoutError('simulated uncertain model transport')
    if message == 'Synthetic failed':
        return 'Invented success prose is not evidence.'
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client:
        response = await client.post(
            os.environ['CRM_FIXTURE_URL'] + '/api/agent/lead-proposals',
            headers={'X-API-Token': os.environ['OHI_AGENT_API_TOKEN']},
            json={'request_id': request_id, 'name': 'Synthetic Proposal',
                  'email': 'synthetic@example.invalid', 'phone': '555-0100'},
        )
        response.raise_for_status()
    return 'Invented lead ID 999999 must never be shown.'


native_proposals.complete_native = synthetic_completion
