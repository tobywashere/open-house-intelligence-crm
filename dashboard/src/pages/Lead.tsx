import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmtDate, fmtMoney, fmtSlotDay, fmtSlotTime, icsUrl, Lead, LeadProfile } from '../api'
import { PERSONA_STYLE, personaOf } from '../briefing'
import { Markdown } from '../components/Markdown'
import { Skeleton } from '../components/Skeleton'
import { BookingCard } from '../components/BookingCard'
import { MergeReview } from '../components/MergeReview'
import { NoteBox } from '../components/NoteBox'
import { toast } from '../components/Toast'
import { clientSafeMarkdown, downloadMarkdown } from '../export'
import { ScoreBadge } from './Inbox'

export function LeadPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const leadId = Number(id)
  const [lead, setLead] = useState<LeadProfile | null>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [dupes, setDupes] = useState<{ lead: Lead; match_on: string }[]>([])
  const [reviewing, setReviewing] = useState<Lead | null>(null)
  const [busy, setBusy] = useState(false)
  const [merging, setMerging] = useState(false)

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

  const confirmMerge = async () => {
    if (!reviewing) return
    setMerging(true)
    try {
      await api.merge(leadId, reviewing.id)
      setDupes([])
      setReviewing(null)
      toast(`✓ Merged ${reviewing.name} into this profile`)
      load()
    } finally {
      setMerging(false)
    }
  }

  const exportSummary = () => {
    if (!lead) return
    downloadMarkdown(`${lead.name.replace(/\s+/g, '-')}-summary.md`, clientSafeMarkdown(lead))
    toast('✓ Client-safe summary downloaded (no scores or internal notes)')
  }

  const markSent = async () => {
    if (!draft || !lead) return
    await api.addEvent(leadId, 'text', `Follow-up sent: ${draft}`)
    if (lead.status === 'new') await api.patchLead(leadId, { status: 'contacted' })
    const due = new Date(Date.now() + 3 * 86_400_000)
    await api.scheduleReminder(leadId, due.toISOString().slice(0, 19), `Check for a reply from ${lead.name}`)
    toast('✓ Sent logged — status updated, reply check scheduled in 3 days')
    setDraft(null)
    load()
  }

  if (!lead)
    return (
      <div className="max-w-3xl space-y-6">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-9 w-64" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )

  const persona = personaOf(lead)

  return (
    <div className="max-w-3xl space-y-6">
      <nav className="flex items-center gap-2 text-sm text-sub/80">
        <button onClick={() => navigate(-1)} className="hover:text-ink2">
          ← Back
        </button>
        <span className="text-sub/40">/</span>
        <Link to="/" className="hover:text-ink2">Insights</Link>
        <span className="text-sub/40">/</span>
        <span className="text-body">{lead.name}</span>
      </nav>

      <div className="flex items-start gap-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-semibold">{lead.name}</h1>
            <span className={`rounded-full border px-2 py-0.5 text-xs ${PERSONA_STYLE[persona] ?? PERSONA_STYLE['Home Buyer']}`}>
              {persona}
            </span>
          </div>
          <div className="text-sm text-sub">
            {lead.phone ?? 'no phone'} · {lead.email ?? 'no email'} · via {lead.source}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <ScoreBadge score={lead.score} />
          <button
            onClick={exportSummary}
            title="Download a client-safe markdown summary"
            className="rounded-lg border border-line hover:border-[#4b5563] px-3 py-1.5 text-sm"
          >
            Export ↓
          </button>
          <button
            onClick={process}
            disabled={busy}
            className="rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-sm"
          >
            {busy ? 'Agent thinking…' : 'Analyze & draft'}
          </button>
        </div>
      </div>

      {dupes.length > 0 && !reviewing && (
        <div className="rounded-lg border border-alert/30 bg-alert/10 p-3 text-sm">
          <div className="text-alert mb-1">Possible duplicates detected:</div>
          {dupes.map((d) => (
            <div key={d.lead.id} className="flex items-center gap-2">
              <span>
                {d.lead.name} <span className="text-sub/80">(matched on {d.match_on})</span>
              </span>
              <button onClick={() => setReviewing(d.lead)} className="text-accent hover:underline">
                Review merge →
              </button>
            </div>
          ))}
        </div>
      )}

      {reviewing && (
        <MergeReview
          primary={lead}
          duplicate={reviewing}
          busy={merging}
          onConfirm={confirmMerge}
          onCancel={() => setReviewing(null)}
        />
      )}

      {lead.relationship_summary && (
        <div className="rounded-lg border border-tile bg-surface p-4 text-sm">
          <div className="text-xs text-sub/80 mb-1">Relationship summary</div>
          <div className="text-body"><Markdown>{lead.relationship_summary}</Markdown></div>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <Fact label="Budget" value={fmtMoney(lead.budget)} />
        <Fact label="Area" value={lead.area ?? '—'} />
        <Fact label="Timeline" value={lead.timeline ?? '—'} />
        <Fact label="Intent" value={lead.intent} />
      </div>

      {lead.score_reason && (
        <div className="rounded-lg border border-tile bg-surface p-3 text-sm">
          <div className="text-xs text-sub/80 mb-1">Why this priority</div>
          {lead.score_reason}
        </div>
      )}

      {lead.missing_fields.length > 0 && (
        <div className="text-sm text-sub">
          Still missing: {lead.missing_fields.join(', ')}
        </div>
      )}

      {draft && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-sm space-y-2">
          <div className="text-xs text-accent">Suggested follow-up</div>
          <p className="whitespace-pre-wrap">{draft}</p>
          <div className="flex gap-2 pt-1">
            <button
              onClick={markSent}
              className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 px-3 py-1.5 text-xs font-medium"
            >
              Mark as sent ✓
            </button>
            <span className="text-xs text-sub/80 self-center">
              logs it, sets status, schedules a 3-day reply check
            </span>
          </div>
        </div>
      )}

      {lead.appointments.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-sub mb-2">Appointments</h2>
          {lead.appointments.map((a) => (
            <div key={a.id} className="text-sm text-body flex items-center gap-2">
              📅 {fmtSlotDay(a.start_ts)} · {fmtSlotTime(a.start_ts)}–{fmtSlotTime(a.end_ts)} —{' '}
              {a.location ?? 'location TBD'}
              <a href={icsUrl(a.id)} className="text-accent hover:underline text-xs">
                .ics ↓
              </a>
            </div>
          ))}
        </section>
      )}

      {lead.status !== 'closed' && (
        <>
          <NoteBox leadId={leadId} onSaved={load} />
          <BookingCard leadId={leadId} onBooked={load} />
        </>
      )}

      <section>
        <h2 className="text-sm font-semibold text-sub mb-2">Activity timeline</h2>
        <div className="space-y-2">
          {lead.events.map((e) => (
            <div key={e.id} className="flex gap-3 text-sm">
              <span className="text-sub/60 shrink-0 w-28">{fmtDate(e.created_at)}</span>
              <span className="text-sub/80 shrink-0 w-24 uppercase text-xs pt-0.5">{e.type}</span>
              <span className="text-body">{e.content}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-tile bg-surface p-3">
      <div className="text-xs text-sub/80">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  )
}
