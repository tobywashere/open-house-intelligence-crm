import { useEffect, useState } from 'react'
import { api, fmtDate, PendingChange } from '../api'
import { toast } from './Toast'

// Polls for lead-lifecycle writes the agent proposed (create/update/close/
// delete/merge) and, while any are pending, blocks the UI behind a modal
// until the operator approves or denies each one. Mirrors ReminderBanner's
// polling; manual dashboard edits never appear here — only agent-tagged
// writes do (docs/CONTRACT.md's pending-changes section).
export function PendingApprovals() {
  const [pending, setPending] = useState<PendingChange[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)
  const [denyingId, setDenyingId] = useState<number | null>(null)
  const [reason, setReason] = useState('')

  useEffect(() => {
    const load = () => api.pendingChanges().then(setPending).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const approve = async (id: number) => {
    setBusyId(id)
    try {
      await api.approvePending(id)
      setPending((p) => p.filter((x) => x.id !== id))
      toast('Change approved and applied.')
    } catch {
      toast('Could not approve — try again.')
    } finally {
      setBusyId(null)
    }
  }

  const confirmDeny = async (id: number) => {
    setBusyId(id)
    try {
      await api.denyPending(id, reason.trim() || undefined)
      setPending((p) => p.filter((x) => x.id !== id))
      toast('Change denied.')
    } catch {
      toast('Could not deny — try again.')
    } finally {
      setBusyId(null)
      setDenyingId(null)
    }
  }

  if (!pending.length) return null

  return (
    <div className="fixed inset-0 z-40 bg-bg/80 backdrop-blur-sm flex items-start justify-center overflow-y-auto py-10 px-4">
      <div className="w-full max-w-xl rounded-lg border border-line bg-surface p-5 space-y-4 shadow-2xl">
        <div>
          <div className="text-sm font-semibold">Pending approvals</div>
          <div className="text-xs text-sub/80 mt-0.5">
            The agent proposed {pending.length} change{pending.length === 1 ? '' : 's'} to the CRM — review each before it applies.
          </div>
        </div>
        <div className="space-y-3">
          {pending.map((p) => (
            <div key={p.id} className="rounded-md border border-tile p-3 space-y-2">
              <div className="text-sm">{p.summary}</div>
              <div className="text-[11px] text-sub/60">
                {p.operation} · {fmtDate(p.created_at)}
              </div>
              {denyingId === p.id ? (
                <div className="flex gap-2 items-center">
                  <input
                    autoFocus
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Reason (optional)"
                    className="flex-1 rounded-md border border-line bg-bg px-2 py-1 text-xs"
                  />
                  <button
                    onClick={() => confirmDeny(p.id)}
                    disabled={busyId === p.id}
                    className="rounded-md bg-alert text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1 text-xs font-medium"
                  >
                    {busyId === p.id ? 'Denying…' : 'Confirm deny'}
                  </button>
                  <button
                    onClick={() => setDenyingId(null)}
                    disabled={busyId === p.id}
                    className="text-xs text-sub hover:text-ink px-2 py-1"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <button
                    onClick={() => approve(p.id)}
                    disabled={busyId !== null}
                    className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-xs font-medium"
                  >
                    {busyId === p.id ? 'Approving…' : 'Approve'}
                  </button>
                  <button
                    onClick={() => setDenyingId(p.id)}
                    disabled={busyId !== null}
                    className="rounded-md border border-line hover:border-alert/60 hover:text-alert disabled:opacity-50 px-3 py-1.5 text-xs"
                  >
                    Deny
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
