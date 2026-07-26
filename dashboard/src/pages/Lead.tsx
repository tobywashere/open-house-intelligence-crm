import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmtDate, fmtMoney, fmtSlotDay, fmtSlotTime, icsUrl, LeadProfile } from '../api'
import { BookingCard } from '../components/BookingCard'
import { ScoreBadge } from './Inbox'

export function LeadPage() {
  const { id } = useParams()
  const leadId = Number(id)
  const [lead, setLead] = useState<LeadProfile | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [dupes, setDupes] = useState<{ lead: { id: number; name: string }; match_on: string }[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.lead(leadId).then(setLead).catch(() => {})
    api.duplicates(leadId).then(setDupes).catch(() => {})
  }, [leadId])
  useEffect(() => {
    load()
  }, [load])

  const process = async () => {
    setBusy(true)
    try {
      const res = await api.processLead(leadId)
      setDraft(res.followup_draft)
      load()
    } finally {
      setBusy(false)
    }
  }

  const merge = async (dupId: number) => {
    await api.merge(leadId, dupId)
    setDupes([])
    load()
  }

  if (!lead) return <div className="text-zinc-500">Loading…</div>

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-start gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{lead.name}</h1>
          <div className="text-sm text-zinc-400">
            {lead.phone ?? 'no phone'} · {lead.email ?? 'no email'} · via {lead.source}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <ScoreBadge score={lead.score} />
          <button
            onClick={process}
            disabled={busy}
            className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm"
          >
            {busy ? 'Agent thinking…' : 'Analyze & draft'}
          </button>
        </div>
      </div>

      {dupes.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
          <div className="text-amber-300 mb-1">Possible duplicates detected:</div>
          {dupes.map((d) => (
            <div key={d.lead.id} className="flex items-center gap-2">
              <span>
                {d.lead.name} <span className="text-zinc-500">(matched on {d.match_on})</span>
              </span>
              <button onClick={() => merge(d.lead.id)} className="text-emerald-400 hover:underline">
                Merge into this profile
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Fact label="Budget" value={fmtMoney(lead.budget)} />
        <Fact label="Area" value={lead.area ?? '—'} />
        <Fact label="Timeline" value={lead.timeline ?? '—'} />
        <Fact label="Intent" value={lead.intent} />
      </div>

      {lead.score_reason && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-sm">
          <div className="text-xs text-zinc-500 mb-1">Why this priority</div>
          {lead.score_reason}
        </div>
      )}

      {lead.missing_fields.length > 0 && (
        <div className="text-sm text-zinc-400">
          Still missing: {lead.missing_fields.join(', ')}
        </div>
      )}

      {draft && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm">
          <div className="text-xs text-emerald-400 mb-1">Suggested follow-up</div>
          <p className="whitespace-pre-wrap">{draft}</p>
        </div>
      )}

      {lead.appointments.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-zinc-400 mb-2">Appointments</h2>
          {lead.appointments.map((a) => (
            <div key={a.id} className="text-sm text-zinc-300 flex items-center gap-2">
              📅 {fmtSlotDay(a.start_ts)} · {fmtSlotTime(a.start_ts)}–{fmtSlotTime(a.end_ts)} —{' '}
              {a.location ?? 'location TBD'}
              <a href={icsUrl(a.id)} className="text-emerald-400 hover:underline text-xs">
                .ics ↓
              </a>
            </div>
          ))}
        </section>
      )}

      {lead.status !== 'closed' && <BookingCard leadId={leadId} onBooked={load} />}

      <section>
        <h2 className="text-sm font-semibold text-zinc-400 mb-2">Activity timeline</h2>
        <div className="space-y-2">
          {lead.events.map((e) => (
            <div key={e.id} className="flex gap-3 text-sm">
              <span className="text-zinc-600 shrink-0 w-28">{fmtDate(e.created_at)}</span>
              <span className="text-zinc-500 shrink-0 w-24 uppercase text-xs pt-0.5">{e.type}</span>
              <span className="text-zinc-300">{e.content}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  )
}
