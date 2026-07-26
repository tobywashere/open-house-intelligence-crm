import { Lead, LeadProfile } from '../api'

// Field-by-field merge preview: primary wins conflicts, duplicate fills blanks —
// mirrors the backend merge semantics so what you see is what you get.
const FIELDS: { key: keyof Lead; label: string }[] = [
  { key: 'phone', label: 'Phone' },
  { key: 'email', label: 'Email' },
  { key: 'budget', label: 'Budget' },
  { key: 'area', label: 'Area' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'intent', label: 'Intent' },
]

export function MergeReview({
  primary,
  duplicate,
  busy,
  onConfirm,
  onCancel,
}: {
  primary: LeadProfile
  duplicate: Lead
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v))

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-4 space-y-3">
      <div className="text-sm font-semibold">
        Merge review: <span className="text-zinc-400">{duplicate.name}</span> →{' '}
        <span className="text-emerald-300">{primary.name}</span>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
            <th className="py-1.5 pr-3">Field</th>
            <th className="py-1.5 pr-3">This profile</th>
            <th className="py-1.5 pr-3">Duplicate</th>
            <th className="py-1.5">After merge</th>
          </tr>
        </thead>
        <tbody>
          {FIELDS.map(({ key, label }) => {
            const p = primary[key]
            const d = duplicate[key]
            const adopted = (p === null || p === undefined || p === '') && d != null && d !== ''
            const result = adopted ? d : p
            return (
              <tr key={key} className="border-b border-zinc-800/60">
                <td className="py-1.5 pr-3 text-zinc-500">{label}</td>
                <td className="py-1.5 pr-3">{show(p)}</td>
                <td className="py-1.5 pr-3 text-zinc-400">{show(d)}</td>
                <td className={`py-1.5 ${adopted ? 'text-emerald-300 font-medium' : ''}`}>
                  {show(result)}
                  {adopted && <span className="ml-1 text-xs text-emerald-500">← adopted</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="text-xs text-zinc-500">
        The duplicate's timeline events move to this profile; the duplicate record is removed.
      </div>
      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          disabled={busy}
          className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium"
        >
          {busy ? 'Merging…' : 'Confirm merge'}
        </button>
        <button onClick={onCancel} className="rounded-md border border-zinc-700 hover:border-zinc-500 px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </div>
  )
}
