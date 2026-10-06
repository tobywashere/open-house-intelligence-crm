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


# Native-mode presentation fixture: model selection is simulated; directory
# records still come through the real capability-protected CRM endpoint.
if os.environ.get('OHI_NATIVE_ONLY') == '1':
    import secrets
    from app.native_read import ReadReceipt, NativeReadError, supports_request
    from app.routers import native_read as read_router
    async def synthetic_read(message):
        if not supports_request(message):raise NativeReadError('unsupported_request')
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=5) as client:
            response=await client.get(os.environ['CRM_FIXTURE_URL']+'/api/leads',headers={'X-API-Token':os.environ['OHI_AGENT_API_TOKEN']})
            response.raise_for_status()
        return ReadReceipt(request_id=secrets.token_hex(16),operation='list_lead_directory',result={'total':len(response.json()),'offset':0,'limit':25,'leads':[{k:lead[k] for k in ('id','name','status')} for lead in response.json()[:25]]})
    read_router.read_directory=synthetic_read
