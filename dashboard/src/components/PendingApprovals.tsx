import { useEffect, useRef, useState } from 'react'
import { api, fmtDate, fmtMoney, Lead, LeadProfile, PendingChange } from '../api'
import { toast } from './Toast'

// Polls for lead-lifecycle writes the agent proposed (create/update/close/
// delete/merge) and, while any are pending, blocks the UI behind a modal
// showing the actual proposed fields — editable — until the operator
// approves (with any edits) or denies each one. Mirrors ReminderBanner's
// polling; manual dashboard edits never appear here — only agent-tagged
// writes do (docs/CONTRACT.md's pending-changes section).

type FieldValue = string | number | null

const LABELS: Record<string, string> = {
  name: 'Name', phone: 'Phone', email: 'Email', budget: 'Budget',
  area: 'Area', timeline: 'Timeline', intent: 'Intent', status: 'Status',
  reason: 'Reason', outcome: 'Outcome',
}

const inputCls =
  'w-full rounded-md bg-bg border border-tile px-2 py-1.5 text-sm text-body focus:outline-none focus:border-accent'

// The fields shown/editable per operation, seeded from the queued payload —
// this IS "the actual fields being added", not just the one-line summary.
function editableFieldsFor(item: PendingChange): Record<string, FieldValue> {
  const p = item.payload
  switch (item.operation) {
    case 'create_lead':
      return {
        name: (p.name as string) ?? '',
        phone: (p.phone as string) ?? '',
        email: (p.email as string) ?? '',
        budget: (p.budget as number) ?? null,
        area: (p.area as string) ?? '',
        timeline: (p.timeline as string) ?? '',
        intent: (p.intent as string) ?? '',
      }
    case 'update_lead': {
      const out: Record<string, FieldValue> = {}
      for (const [k, v] of Object.entries(p)) out[k] = v as FieldValue
      return out
    }
    case 'close_lead':
      return { outcome: (p.outcome as string) ?? 'won', reason: (p.reason as string) ?? '' }
    case 'delete_lead':
      return { reason: (p.reason as string) ?? '' }
    default:
      return {} // merge_leads: not field-editable, see FieldsFor below
  }
}

function leadIdsToFetch(item: PendingChange): number[] {
  const ids: number[] = []
  if (item.lead_id != null) ids.push(item.lead_id)
  if (item.operation === 'merge_leads' && typeof item.payload.duplicate_id === 'number') {
    ids.push(item.payload.duplicate_id as number)
  }
  return ids
}

function coerceForSubmit(values: Record<string, FieldValue>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(values)) {
    if (k === 'budget') {
      out[k] = v === '' || v === null || v === undefined ? null : Number(v)
    } else if (typeof v === 'string') {
      out[k] = v.trim() === '' ? null : v.trim()
    } else {
      out[k] = v
    }
  }
  return out
}

function fmtOld(lead: LeadProfile, key: string): string {
  const v = (lead as unknown as Record<string, unknown>)[key]
  if (v === null || v === undefined || v === '') return '—'
  return key === 'budget' ? fmtMoney(v as number) : String(v)
}

export function PendingApprovals() {
  const [pending, setPending] = useState<PendingChange[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)
  const [denyingId, setDenyingId] = useState<number | null>(null)
  const [denyReason, setDenyReason] = useState('')
  const [edits, setEdits] = useState<Record<number, Record<string, FieldValue>>>({})
  const [leadCache, setLeadCache] = useState<Record<number, LeadProfile>>({})
  const fetchedLeadIds = useRef<Set<number>>(new Set())

  useEffect(() => {
    const load = () => {
      api.pendingChanges().then((items) => {
        setPending(items)
        setEdits((prev) => {
          const next = { ...prev }
          for (const item of items) {
            if (!(item.id in next)) next[item.id] = editableFieldsFor(item)
          }
          return next
        })
        for (const item of items) {
          for (const id of leadIdsToFetch(item)) {
            if (fetchedLeadIds.current.has(id)) continue
            fetchedLeadIds.current.add(id)
            api.lead(id)
              .then((l) => setLeadCache((c) => ({ ...c, [id]: l })))
              .catch(() => fetchedLeadIds.current.delete(id))
          }
        }
      }).catch(() => {})
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const setField = (id: number, key: string, value: FieldValue) =>
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }))

  const approve = async (item: PendingChange) => {
    setBusyId(item.id)
    try {
      await api.approvePending(item.id, coerceForSubmit(edits[item.id] ?? {}))
      setPending((p) => p.filter((x) => x.id !== item.id))
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
      await api.denyPending(id, denyReason.trim() || undefined)
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
            The agent proposed {pending.length} change{pending.length === 1 ? '' : 's'} to the CRM — review
            the fields below, edit anything that's wrong, then decide.
          </div>
        </div>
        <div className="space-y-3">
          {pending.map((item) => (
            <div key={item.id} className="rounded-md border border-tile p-3 space-y-3">
              <div>
                <div className="text-sm">{item.summary}</div>
                <div className="text-[11px] text-sub/60 mt-0.5">
                  {item.operation} · {fmtDate(item.created_at)}
                </div>
              </div>

              <FieldsFor
                item={item}
                values={edits[item.id] ?? {}}
                setField={(k, v) => setField(item.id, k, v)}
                lead={item.lead_id != null ? leadCache[item.lead_id] : undefined}
                duplicateLead={
                  item.operation === 'merge_leads'
                    ? leadCache[item.payload.duplicate_id as number]
                    : undefined
                }
              />

              {denyingId === item.id ? (
                <div className="flex gap-2 items-center">
                  <input
                    autoFocus
                    value={denyReason}
                    onChange={(e) => setDenyReason(e.target.value)}
                    placeholder="Reason (optional)"
                    className={`flex-1 ${inputCls}`}
                  />
                  <button
                    onClick={() => confirmDeny(item.id)}
                    disabled={busyId === item.id}
                    className="rounded-md bg-alert text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1 text-xs font-medium"
                  >
                    {busyId === item.id ? 'Denying…' : 'Confirm deny'}
                  </button>
                  <button
                    onClick={() => setDenyingId(null)}
                    disabled={busyId === item.id}
                    className="text-xs text-sub hover:text-ink px-2 py-1"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <button
                    onClick={() => approve(item)}
                    disabled={busyId === item.id}
                    className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-xs font-medium"
                  >
                    {busyId === item.id ? 'Approving…' : 'Approve'}
                  </button>
                  <button
                    onClick={() => {
                      setDenyingId(item.id)
                      setDenyReason('')
                    }}
                    disabled={busyId === item.id}
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

function TextField({
  label, value, onChange, type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
}) {
  return (
    <label className="block text-xs text-sub">
      <span>{label}</span>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} className={`mt-1 ${inputCls}`} />
    </label>
  )
}

function FieldsFor({
  item, values, setField, lead, duplicateLead,
}: {
  item: PendingChange
  values: Record<string, FieldValue>
  setField: (key: string, value: FieldValue) => void
  lead?: LeadProfile
  duplicateLead?: LeadProfile
}) {
  const str = (k: string) => (values[k] === null || values[k] === undefined ? '' : String(values[k]))

  if (item.operation === 'create_lead') {
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <TextField label="Name" value={str('name')} onChange={(v) => setField('name', v)} />
          <TextField label="Phone" value={str('phone')} onChange={(v) => setField('phone', v)} />
          <TextField label="Email" value={str('email')} onChange={(v) => setField('email', v)} />
          <TextField label="Budget" type="number" value={str('budget')} onChange={(v) => setField('budget', v)} />
          <TextField label="Area" value={str('area')} onChange={(v) => setField('area', v)} />
          <TextField label="Timeline" value={str('timeline')} onChange={(v) => setField('timeline', v)} />
        </div>
        <TextField label="Intent" value={str('intent')} onChange={(v) => setField('intent', v)} />
        {typeof item.payload.raw_text === 'string' && item.payload.raw_text && (
          <div className="text-[11px] text-sub/70 italic border-l-2 border-tile pl-2">
            "{item.payload.raw_text as string}"
          </div>
        )}
      </div>
    )
  }

  if (item.operation === 'update_lead') {
    return (
      <div className="grid grid-cols-2 gap-2">
        {Object.keys(values).map((key) => (
          <TextField
            key={key}
            label={`${LABELS[key] ?? key}${lead ? ` (was ${fmtOld(lead, key)})` : ''}`}
            type={key === 'budget' ? 'number' : 'text'}
            value={str(key)}
            onChange={(v) => setField(key, v)}
          />
        ))}
      </div>
    )
  }

  if (item.operation === 'close_lead') {
    return (
      <div className="space-y-2">
        <div className="flex gap-2">
          {(['won', 'lost'] as const).map((outcome) => (
            <button
              key={outcome}
              type="button"
              onClick={() => setField('outcome', outcome)}
              className={`rounded-md border px-3 py-1.5 text-xs capitalize transition-colors ${
                values.outcome === outcome
                  ? 'border-accent bg-accent/10 text-ink'
                  : 'border-line text-sub hover:border-accent/50'
              }`}
            >
              {outcome}
            </button>
          ))}
        </div>
        <label className="block text-xs text-sub">
          <span>Reason (optional)</span>
          <textarea
            value={str('reason')}
            onChange={(e) => setField('reason', e.target.value)}
            rows={2}
            className={`mt-1 ${inputCls} resize-y`}
          />
        </label>
      </div>
    )
  }

  if (item.operation === 'delete_lead') {
    return (
      <label className="block text-xs text-sub">
        <span>Reason (optional){lead ? ` — deleting ${lead.name}` : ''}</span>
        <textarea
          value={str('reason')}
          onChange={(e) => setField('reason', e.target.value)}
          rows={2}
          className={`mt-1 ${inputCls} resize-y`}
        />
      </label>
    )
  }

  // merge_leads: which record wins is deterministic merge policy, not a
  // value to type in — show a read-only preview instead of an edit form.
  if (item.operation === 'merge_leads') {
    if (!lead || !duplicateLead) return <div className="text-xs text-sub/60">Loading merge preview…</div>
    return <MergePreview primary={lead} duplicate={duplicateLead} />
  }

  return null
}

const MERGE_FIELDS: { key: keyof Lead; label: string }[] = [
  { key: 'phone', label: 'Phone' },
  { key: 'email', label: 'Email' },
  { key: 'budget', label: 'Budget' },
  { key: 'area', label: 'Area' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'intent', label: 'Intent' },
]

function MergePreview({ primary, duplicate }: { primary: LeadProfile; duplicate: LeadProfile }) {
  const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v))
  return (
    <div className="text-xs space-y-1.5">
      <div className="text-sub/80">
        <span className="text-ink">{duplicate.name}</span> merges into{' '}
        <span className="text-accent">{primary.name}</span>; the duplicate is removed.
      </div>
      <table className="w-full">
        <tbody>
          {MERGE_FIELDS.map(({ key, label }) => {
            const p = primary[key]
            const d = duplicate[key]
            const adopted = (p === null || p === undefined || p === '') && d != null && d !== ''
            return (
              <tr key={key} className="border-t border-tile">
                <td className="py-1 pr-2 text-sub/70">{label}</td>
                <td className="py-1 pr-2">{show(p)}</td>
                <td className={`py-1 ${adopted ? 'text-accent' : 'text-sub'}`}>
                  {show(adopted ? d : p)}
                  {adopted && ' ←'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
