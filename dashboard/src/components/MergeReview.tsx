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
    <div className="rounded-lg border border-line bg-surface p-4 space-y-3">
      <div className="text-sm font-semibold">
        Merge review: <span className="text-sub">{duplicate.name}</span> →{' '}
        <span className="text-accent">{primary.name}</span>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-sub/80 border-b border-tile">
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
              <tr key={key} className="border-b border-tile">
                <td className="py-1.5 pr-3 text-sub/80">{label}</td>
                <td className="py-1.5 pr-3">{show(p)}</td>
                <td className="py-1.5 pr-3 text-sub">{show(d)}</td>
                <td className={`py-1.5 ${adopted ? 'text-accent font-medium' : ''}`}>
                  {show(result)}
                  {adopted && <span className="ml-1 text-xs text-accent">← adopted</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="text-xs text-sub/80">
        The duplicate's timeline events move to this profile; the duplicate record is removed.
      </div>
      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          disabled={busy}
          className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-sm font-medium"
        >
          {busy ? 'Merging…' : 'Confirm merge'}
        </button>
        <button onClick={onCancel} className="rounded-md border border-line hover:border-[#4b5563] px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </div>
  )
}
