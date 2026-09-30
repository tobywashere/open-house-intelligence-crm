import { PendingChange } from './api'
import { API_BASE, authenticatedFetch } from './auth'

export type ProposalState = 'running' | 'unknown' | 'failed' | 'proposed' | 'approved' | 'denied'
export interface NativeProposalStatus {
  request_id: string
  state: ProposalState
  proposal: PendingChange | null
}

export const PROPOSAL_REQUEST_KEY = 'ohi-lead-proposal-request'
const validId = (id: unknown): id is string => typeof id === 'string' && /^[a-f0-9]{32}$/.test(id)
export function savedProposalId(): string | null {
  const id = sessionStorage.getItem(PROPOSAL_REQUEST_KEY)
  return validId(id) ? id : null
}
export const newProposalId = () => crypto.randomUUID().replaceAll('-', '')

export class ProposalError extends Error {
  constructor(message: string, readonly notStarted = false) { super(message) }
}

function verified(data: NativeProposalStatus, requestId: string): boolean {
  if (!data || data.request_id !== requestId || !validId(data.request_id)) return false
  const p = data.proposal
  if (['running', 'unknown', 'failed'].includes(data.state)) return p === null
  if (!['proposed', 'approved', 'denied'].includes(data.state) || !p
    || !Number.isSafeInteger(p.id) || p.id < 1 || p.operation !== 'create_lead'
    || p.status !== (data.state === 'proposed' ? 'pending' : data.state)
    || !p.payload || typeof p.payload.name !== 'string' || !p.payload.name.trim()) return false
  if (data.state === 'approved') {
    const r = p.result
    return !!r && Number.isSafeInteger(r.id) && (r.id as number) > 0
      && typeof r.name === 'string' && !!r.name.trim()
      && ['email', 'phone'].every(k => r[k] == null || typeof r[k] === 'string')
  }
  return true
}

async function request(requestId: string, message?: string, closing = false): Promise<NativeProposalStatus> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 65_000)
  const sending = message !== undefined
  try {
    const response = await authenticatedFetch(`${API_BASE}/chat/lead-proposal${sending ? '' : '/' + requestId + (closing ? '/close' : '')}`, {
      method: sending || closing ? 'POST' : 'GET', signal: controller.signal,
      ...(sending ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId, message }) } : {}),
    })
    const data = await response.json()
    if (!response.ok) {
      const code = data?.error?.code
      // These explicit protocol/auth responses occur before reservation. A
      // missing status row, arbitrary HTTP error or lost response cannot prove that.
      const notStarted = sending && (
        (response.status === 503 && code === 'not_configured')
        || (response.status === 422 && code === 'invalid_request')
        || ([401, 403, 503].includes(response.status) && typeof data?.detail === 'string')
      )
      if (notStarted) throw new ProposalError('Request was not started. Check configuration, credentials or input, then send again.', true)
      if (response.status === 404 && code === 'not_found') {
        throw new ProposalError('No stored request was found yet. Keep this request ID and check status again; the outcome remains uncertain.')
      }
      throw new ProposalError('Status could not be confirmed. Keep this request ID and check status again.')
    }
    if (!verified(data, requestId)) throw new ProposalError('The proposal result could not be verified. Keep this request ID and check status again.')
    return data
  } catch (e) {
    if (e instanceof ProposalError) throw e
    throw new ProposalError('Outcome is uncertain. Keep this request ID and check status; do not send another request.')
  } finally { clearTimeout(timeout) }
}

export const proposeLead = (requestId: string, message: string) => request(requestId, message)
export const checkLeadProposal = (requestId: string) => request(requestId)

export const closeLeadProposal = (requestId: string) => request(requestId, undefined, true)
