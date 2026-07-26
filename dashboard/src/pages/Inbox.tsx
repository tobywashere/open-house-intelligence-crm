import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDate, fmtMoney, Lead } from '../api'
import { PERSONA_STYLE, personaOf } from '../briefing'
import { daysIdle } from '../insights'
import { Skeleton } from '../components/Skeleton'

const STATUS_STYLE: Record<string, string> = {
  new: 'bg-sky-500/15 text-sky-300',
  contacted: 'bg-violet-500/15 text-violet-300',
  meeting_booked: 'bg-emerald-500/15 text-emerald-300',
  closed: 'bg-zinc-500/15 text-zinc-400',
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
        <section className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-4">
          <h2 className="text-sm font-semibold text-amber-300 mb-3">⚠ Needs attention</h2>
          <div className="grid sm:grid-cols-3 gap-3">
            {attention.map((l) => (
              <Link
                key={l.id}
                to={`/lead/${l.id}`}
                className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-3 hover:border-amber-500/40 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <ScoreBadge score={l.score} />
                  <span className="font-medium text-sm truncate">{l.name}</span>
                </div>
                <div className="text-xs text-zinc-500 mt-1.5">
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
          placeholder='New lead from a note, e.g. "Met Alex at the open house, looking in Redmond around $950k…"'
          className="flex-1 rounded-lg bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm
                     placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500"
        />
        <button
          onClick={addLead}
          disabled={adding}
          className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-2 text-sm font-medium"
        >
          {adding ? 'Analyzing…' : 'Add lead'}
        </button>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
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
              <tr key={`s${i}`} className="border-b border-zinc-900">
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
              <td colSpan={7} className="py-12 text-center text-zinc-600">
                No leads yet — paste a note above and let the agent extract the details.
              </td>
            </tr>
          )}
          {leads.map((l) => (
            <tr key={l.id} className="border-b border-zinc-900 hover:bg-zinc-900/50">
              <td className="py-2.5 pr-3">
                <ScoreBadge score={l.score} />
              </td>
              <td className="py-2.5 pr-3">
                <Link to={`/lead/${l.id}`} className="font-medium hover:text-emerald-400">
                  {l.name}
                </Link>
                <span
                  className={`ml-2 hidden md:inline-block rounded-full border px-1.5 py-px text-[10px] align-middle ${
                    PERSONA_STYLE[personaOf(l)] ?? PERSONA_STYLE['Home Buyer']
                  }`}
                >
                  {personaOf(l)}
                </span>
                {l.is_neglected === 1 && (
                  <span className="ml-2 text-xs text-amber-400">⚠ neglected</span>
                )}
              </td>
              <td className="py-2.5 pr-3">
                <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[l.status]}`}>
                  {l.status.replace('_', ' ')}
                </span>
              </td>
              <td className="py-2.5 pr-3 text-zinc-300">{fmtMoney(l.budget)}</td>
              <td className="py-2.5 pr-3 text-zinc-300">{l.area ?? '—'}</td>
              <td className="py-2.5 pr-3 text-zinc-300">{l.timeline ?? '—'}</td>
              <td className="py-2.5 text-zinc-500">{fmtDate(l.last_activity_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-zinc-600">—</span>
  const color =
    score >= 70 ? 'bg-emerald-500/20 text-emerald-300'
    : score >= 40 ? 'bg-amber-500/20 text-amber-300'
    : 'bg-zinc-500/20 text-zinc-400'
  return (
    <span className={`inline-block w-10 text-center rounded-md py-0.5 text-xs font-semibold ${color}`}>
      {score}
    </span>
  )
}
