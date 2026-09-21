import { FormEvent, useEffect, useRef, useState } from 'react'
import { readCrmDirectory, NativeDirectoryReceipt } from '../nativeRead'

export function LeadDirectoryChat() {
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<NativeDirectoryReceipt | null>(null)
  const [error, setError] = useState('')
  const sequence = useRef(0)
  useEffect(() => () => { sequence.current++ }, [])
  const send = async (event: FormEvent) => {
    event.preventDefault()
    if (!message.trim() || pending) return
    const current = ++sequence.current
    setResult(null); setError(''); setPending(true)
    try {
      const receipt = await readCrmDirectory(message.trim())
      if (current === sequence.current) setResult(receipt)
    } catch (e) {
      if (current === sequence.current) setError(e instanceof Error ? e.message : 'The CRM read failed.')
    } finally {
      if (current === sequence.current) setPending(false)
    }
  }
  return <div className="flex flex-col min-h-0 h-full p-4 gap-4 overflow-y-auto">
    <div><h2 className="text-sm font-semibold">Ask about your lead directory</h2>
      <p className="text-xs text-sub mt-1">Read-only. Unfiltered total and first 25 leads only; no filters, sorting, or later pages.</p></div>
    <form onSubmit={send} className="flex flex-col gap-2">
      <label htmlFor="directory-question" className="text-xs">Your question</label>
      <textarea id="directory-question" value={message} onChange={e => setMessage(e.target.value)}
        maxLength={2000} rows={3} placeholder="How many leads are in the CRM?"
        className="rounded-lg bg-surface border border-line p-2 text-sm" />
      <button type="submit" disabled={pending || !message.trim()}
        className="rounded-lg bg-accent text-white px-3 py-2 text-sm disabled:opacity-50">{pending ? 'Reading CRM…' : 'Read CRM'}</button>
    </form>
    {pending && <p role="status" className="text-sm text-sub">Reading the current CRM through OpenClaw…</p>}
    {error && <p role="alert" className="text-sm text-alert">{error}</p>}
    {result && <section aria-label="Verified CRM result" data-request-id={result.request_id} className="text-sm">
      <h3 className="font-semibold">{result.result.total} {result.result.total === 1 ? 'lead' : 'leads'} total</h3>
      <p className="text-xs text-sub my-2">Verified CRM read for this request · All leads, unfiltered</p>
      {result.result.total === 0 ? <p>No leads in the CRM.</p> : <>
        <p className="text-xs text-sub mb-2">Showing {result.result.leads.length} of {result.result.total} leads.</p>
        <ul className="space-y-2">{result.result.leads.map(lead => <li key={lead.id} className="border-b border-tile pb-2">
          <span>{lead.name}</span><span className="block text-xs text-sub">ID {lead.id} · {lead.status}</span>
        </li>)}</ul>
      </>}
    </section>}
  </div>
}
