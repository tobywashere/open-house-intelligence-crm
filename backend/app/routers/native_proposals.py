"""Capability-protected native proposal endpoints with sanitized errors."""
from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..auth import require_agent, require_human
from .. import native_proposals as native


class SafeProposalRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except (RequestValidationError, native.NativeProposalError) as exc:
                code = exc.code if isinstance(exc, native.NativeProposalError) else 'invalid_request'
                status, message = native.ERRORS[code]
                return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)
        return safe


router = APIRouter(route_class=SafeProposalRoute, tags=['native-proposals'])


@router.post('/chat/lead-proposal', dependencies=[Depends(require_human)])
async def propose(body: native.RequestIn):
    return await native.propose_lead(body.request_id, body.message)


@router.get('/chat/lead-proposal/{request_id}', dependencies=[Depends(require_human)])
def status(request_id: str):
    return native.get_status(request_id)


@router.post('/agent/lead-proposals', dependencies=[Depends(require_agent)])
def submit(body: native.ProposalIn):
    return native.submit_proposal(body)
