import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from '../components/Toast'
import { api, fmtDate, fmtMoney, Lead } from '../api'
import { personaOf, personaStyle } from '../briefing'
import { copy } from '../vertical'
import { daysIdle } from '../insights'
import { Skeleton } from '../components/Skeleton'
import { CameraIcon } from '../components/icons'

const STATUS_STYLE: Record<string, string> = {
  new: 'bg-accent2/15 text-[#a5b4fc]',
  contacted: 'bg-accent/10 text-accent',
  meeting_booked: 'bg-accent/20 text-sky-200',
  closed: 'bg-tile text-sub',
}

// Urgency = days idle × score. Client-side for now; if Toby exposes an official
// urgency field/sort later (additive change), swap the computation for the field.
const urgency = (l: Lead) => daysIdle(l) * ((l.score ?? 30) / 100)

export function Inbox() {
  const [leads, setLeads] = useState<Lead[]>([])
  const [loaded, setLoaded] = useState(false)
  const [note, setNote] = useState('')
  const [adding, setAdding] = useState(false)

  const load = () =>
    api
      .leads()
      .then(setLeads)
      .catch(() => {})
      .finally(() => setLoaded(true))
  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const addLead = async () => {
    if (!note.trim()) return
    setAdding(true)
    try {
      const lead = await api.createLead(note)
      await api.processLead(lead.id)
      setNote('')
      load()
    } catch {
      toast('⚠ Could not add the lead — is the backend running?')
    } finally {
      setAdding(false)
    }
  }

  const attention = leads
    .filter((l) => (l.status === 'new' || l.status === 'contacted') && (daysIdle(l) >= 2 || l.is_neglected === 1))
    .sort((a, b) => urgency(b) - urgency(a))
    .slice(0, 3)

  return (
    <div className="max-w-4xl space-y-4">
      {attention.length > 0 && (
        <section className="rounded-xl border border-alert/25 bg-alert/5 p-4">
          <h2 className="text-sm font-semibold text-alert mb-3">⚠ Needs attention</h2>
          <div className="grid sm:grid-cols-3 gap-3">
            {attention.map((l) => (
              <Link
                key={l.id}
                to={`/lead/${l.id}`}
                className="rounded-lg border border-tile bg-tile p-3 hover:border-alert/40 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <ScoreBadge score={l.score} />
                  <span className="font-medium text-sm truncate">{l.name}</span>
                </div>
                <div className="text-xs text-sub/80 mt-1.5">
                  {daysIdle(l)}d idle × score {l.score ?? '—'} — too warm to go cold
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

      <div className="flex gap-2">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && addLead()}
          placeholder={copy(
            'inbox.add_placeholder',
            'New lead from a note, e.g. "Met Alex at the open house, looking in Redmond around $950k…"',
          )}
          className="flex-1 rounded-lg bg-surface border border-tile px-3 py-2 text-sm
                     placeholder:text-sub/50 focus:outline-none focus:border-accent"
        />
        <button
          onClick={addLead}
          disabled={adding}
          className="rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-4 py-2 text-sm font-medium"
        >
          {adding ? 'Analyzing…' : 'Add lead'}
        </button>
        <Link
          to="/scan"
          title="Scan a business card"
          className="inline-flex items-center rounded-lg border border-line hover:border-accent/60
                     px-3 py-2 text-sm text-body hover:text-accent transition-colors"
        >
          <CameraIcon size={18} />
        </Link>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-sub/80 border-b border-tile">
            <th className="py-2 pr-3">Score</th>
            <th className="py-2 pr-3">Lead</th>
            <th className="py-2 pr-3">Status</th>
            <th className="py-2 pr-3">Budget</th>
            <th className="py-2 pr-3">Area</th>
            <th className="py-2 pr-3">Timeline</th>
            <th className="py-2">Last activity</th>
          </tr>
        </thead>
        <tbody>
          {!loaded &&
            Array.from({ length: 8 }, (_, i) => (
              <tr key={`s${i}`} className="border-b border-tile/40">
                <td className="py-3 pr-3"><Skeleton className="h-5 w-10" /></td>
                <td className="py-3 pr-3"><Skeleton className="h-4 w-40" /></td>
                <td className="py-3 pr-3"><Skeleton className="h-4 w-20" /></td>
                <td className="py-3 pr-3"><Skeleton className="h-4 w-12" /></td>
                <td className="py-3 pr-3"><Skeleton className="h-4 w-16" /></td>
                <td className="py-3 pr-3"><Skeleton className="h-4 w-16" /></td>
                <td className="py-3"><Skeleton className="h-4 w-20" /></td>
              </tr>
            ))}
          {loaded && !leads.length && (
            <tr>
              <td colSpan={7} className="py-12 text-center text-sub/60">
                No leads yet — paste a note above and let the agent extract the details.
              </td>
            </tr>
          )}
          {leads.map((l) => (
            <tr key={l.id} className="border-b border-tile/40 hover:bg-tile/40">
              <td className="py-2.5 pr-3">
                <ScoreBadge score={l.score} />
              </td>
              <td className="py-2.5 pr-3">
                <Link to={`/lead/${l.id}`} className="font-medium hover:text-accent">
                  {l.name}
                </Link>
                <span
                  className={`ml-2 hidden md:inline-block rounded-full border px-1.5 py-px text-[10px] align-middle ${personaStyle(
                    personaOf(l),
                  )}`}
                >
                  {personaOf(l)}
                </span>
                {l.is_neglected === 1 && (
                  <span className="ml-2 text-xs text-alert">⚠ neglected</span>
                )}
              </td>
              <td className="py-2.5 pr-3">
                <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[l.status]}`}>
                  {l.status.replace('_', ' ')}
                </span>
              </td>
              <td className="py-2.5 pr-3 text-body">{fmtMoney(l.budget)}</td>
              <td className="py-2.5 pr-3 text-body">{l.area ?? '—'}</td>
              <td className="py-2.5 pr-3 text-body">{l.timeline ?? '—'}</td>
              <td className="py-2.5 text-sub/80">{fmtDate(l.last_activity_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-sub/60">—</span>
  const color =
    score >= 70 ? 'bg-accent/15 text-accent'
    : score >= 40 ? 'bg-accent2/15 text-[#a5b4fc]'
    : 'bg-tile text-sub'
  return (
    <span className={`inline-block w-10 text-center rounded-md py-0.5 text-xs font-semibold ${color}`}>
      {score}
    </span>
  )
}
