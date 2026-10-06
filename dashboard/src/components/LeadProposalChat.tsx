import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { approvalsAvailable, onApprovalDecided } from '../approvalEvents'
import { checkLeadProposal, closeLeadProposal, NativeProposalStatus, newProposalId, ProposalError,
  PROPOSAL_REQUEST_KEY, proposeLead, savedProposalId } from '../nativeProposals'

const stateText = {
  running: 'Proposal request is still running. Check status to recover its outcome.',
  unknown: 'Outcome is uncertain. Keep this request ID and check status; do not send another request.',
  failed: 'No proposal was produced. No lead was created.',
  proposed: 'Pending approval. Review and edit the proposed fields in Pending approvals.',
  approved: 'Approved. The stored result is shown below.',
  denied: 'Denied. No lead was created.',
}

export function LeadProposalChat() {
  const [requestId, setRequestId] = useState(savedProposalId)
  const activeId = useRef(requestId)
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<NativeProposalStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sequence = useRef(0)

  const load = async (id: string, prompt?: string, closing = false) => {
    const current = ++sequence.current
    setBusy(true); setError('')
    try {
      const next = closing ? await closeLeadProposal(id)
        : prompt === undefined ? await checkLeadProposal(id) : await proposeLead(id, prompt)
      if (current !== sequence.current) return
      setStatus(next)
      if (next.state === 'proposed') approvalsAvailable()
    } catch (e) {
      // A current-token 401 unmounts the panel. Still release a request that
      // the server explicitly rejected before reservation; never release uncertainty.
      if (prompt !== undefined && e instanceof ProposalError && e.notStarted
        && sessionStorage.getItem(PROPOSAL_REQUEST_KEY) === id) {
        sessionStorage.removeItem(PROPOSAL_REQUEST_KEY)
        activeId.current = null
        if (current === sequence.current) { setRequestId(null); setStatus(null) }
      }
      if (current === sequence.current) setError(e instanceof Error ? e.message : 'Status is unavailable.')
    } finally {
      if (current === sequence.current) setBusy(false)
    }
  }

  useEffect(() => {
    if (activeId.current) void load(activeId.current)
    const unsubscribe = onApprovalDecided(() => {
      if (activeId.current) void load(activeId.current)
    })
    return () => { sequence.current++; unsubscribe() }
  }, [])

  const send = (event: FormEvent) => {
    event.preventDefault()
    if (busy || requestId || !message.trim()) return
    const id = newProposalId()
    // Save before dispatch, so reload or a lost response can only check this ID.
    sessionStorage.setItem(PROPOSAL_REQUEST_KEY, id)
    activeId.current = id; setRequestId(id); setStatus(null)
    void load(id, message)
  }
  const reset = () => {
    sequence.current++
    sessionStorage.removeItem(PROPOSAL_REQUEST_KEY)
    activeId.current = null; setRequestId(null); setStatus(null); setMessage(''); setError('')
  }
  const result = status?.state === 'approved' ? status.proposal?.result : null
  return <div className="flex flex-col min-h-0 h-full p-4 gap-4 overflow-y-auto">
    <div><h2 className="text-sm font-semibold">Propose a new lead</h2>
      <p className="text-xs text-sub mt-1">Describe a name and optional email or phone. You review the fields before anything is added.</p></div>
    {!requestId && <form onSubmit={send} className="flex flex-col gap-2">
      <label htmlFor="lead-request" className="text-xs">Lead request</label>
      <textarea id="lead-request" value={message} onChange={e => setMessage(e.target.value)} maxLength={2000} rows={4}
        placeholder="Propose Jordan Example, jordan@example.com, 555-0100"
        className="rounded-lg bg-surface border border-line p-2 text-sm" />
      <button type="submit" disabled={busy || !message.trim()}
        className="rounded-lg bg-accent text-white px-3 py-2 text-sm disabled:opacity-50">Send proposal request</button>
    </form>}
    {requestId && <section aria-label="Proposal status" data-request-id={requestId} className="space-y-3 text-sm">
      <p className="text-xs text-sub break-all">Request ID: {requestId}</p>
      {busy ? <p role="status">Checking proposal outcome…</p>
        : <p role="status">{status ? stateText[status.state] : 'Outcome is uncertain. Check status to recover this request.'}</p>}
      <button onClick={() => void load(requestId)} disabled={busy}
        className="rounded-lg border border-line px-3 py-2 text-xs disabled:opacity-50">Check status</button>
      {!busy && (!status || ['running', 'unknown'].includes(status.state)) && <div className="space-y-2">
        <p className="text-xs text-sub">Close this request to prevent any late proposal. If a proposal already exists, it stays available for review.</p>
        <button onClick={() => void load(requestId, undefined, true)}
          className="rounded-lg border border-line px-3 py-2 text-xs">Close request</button>
      </div>}
      {!busy && status && ['failed', 'approved', 'denied'].includes(status.state) &&
        <button onClick={reset} className="block rounded-lg border border-line px-3 py-2 text-xs">New proposal request</button>}
    </section>}
    {error && <p role="alert" className="text-sm text-alert">{error}</p>}
    {result && <section aria-label="Approved lead" className="rounded-lg border border-accent p-3 space-y-1 text-sm">
      <h3 className="font-semibold">{result.name as string}</h3>
      <Link className="text-accent underline" to={`/lead/${result.id}`}>Lead ID {result.id as number}</Link>
      {typeof result.email === 'string' && result.email && <p>{result.email}</p>}
      {typeof result.phone === 'string' && result.phone && <p>{result.phone}</p>}
      <p className="text-xs text-sub">Verified from the approved CRM record.</p>
    </section>}
  </div>
}
