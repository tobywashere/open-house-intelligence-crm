import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Appointment, fmtSlotDay, fmtSlotTime } from '../api'
import { Briefing as BriefingData, fetchBriefing, personaStyle, ScheduleBlock } from '../briefing'
import { Markdown } from './Markdown'
import { Skeleton } from './Skeleton'
import { toast } from './Toast'

// Morning-briefing content embedded in the DailySummaryOverlay. Factual data
// is rebuilt from the CRM; a scheduled skill may add bounded, visibly labeled
// meeting advice.
// onDismiss lets a link whose target is the page already behind the overlay
// ("All insights →" routes to "/") close it — a route-change handler cannot,
// because navigating to the route you are already on changes nothing.
export function BriefingSection({ onDismiss }: { onDismiss?: () => void } = {}) {
  const [briefing, setBriefing] = useState<BriefingData | null>(null)
  const [briefingError, setBriefingError] = useState<string | null>(null)
  const [briefingLoading, setBriefingLoading] = useState(true)
  const [upcoming, setUpcoming] = useState<Appointment[]>([])
  const [now, setNow] = useState(currentHHMM())
  const [memory, setMemory] = useState('')
  const [savingMemory, setSavingMemory] = useState(false)

  const loadBriefing = async () => {
    setBriefingLoading(true)
    setBriefingError(null)
    try {
      setBriefing(await fetchBriefing())
    } catch {
      setBriefingError('The CRM briefing could not be loaded.')
    } finally {
      setBriefingLoading(false)
    }
  }

  useEffect(() => {
    void loadBriefing()
    // upcoming appointments feed the schedule empty state on meeting-free days
    api
      .appointments()
      .then((a) =>
        setUpcoming(
          a
            .filter((x) => new Date(x.start_ts) > new Date())  // start_ts is naive local time
            .sort((x, y) => x.start_ts.localeCompare(y.start_ts))
            .slice(0, 3),
        ),
      )
      .catch(() => {})
    const t = setInterval(() => setNow(currentHHMM()), 30_000)
    return () => clearInterval(t)
  }, [])

  const saveMemory = async () => {
    const text = memory.trim()
    if (!text || savingMemory) return
    setSavingMemory(true)
    try {
      await api.chat(`Remember this: ${text}`, 'memory')
      setMemory('')
      toast('🧠 Memory saved — the agent will file it on the right profile.')
    } catch {
      toast('⚠ Could not reach the agent.')
    } finally {
      setSavingMemory(false)
    }
  }

  if (briefingLoading && !briefing)
    return (
      <div className="grid lg:grid-cols-[1fr_300px] gap-8 mt-10">
        <div className="space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
        <Skeleton className="h-48 w-full" />
      </div>
    )

  if (briefingError && !briefing)
    return (
      <div className="mt-10 rounded-xl border border-alert/30 bg-alert/5 px-5 py-4">
        <div className="text-sm font-medium text-alert">Briefing unavailable</div>
        <p className="mt-1 text-sm text-sub">{briefingError}</p>
        <button
          onClick={() => void loadBriefing()}
          className="mt-3 rounded-md border border-line px-3 py-1.5 text-sm text-body hover:border-accent/60"
        >
          Retry
        </button>
      </div>
    )

  if (!briefing) return null

  return (
    <div className="grid lg:grid-cols-[1fr_300px] gap-8 items-start mt-10">
      <div className="space-y-8 min-w-0">
        <section className="rise" style={{ animationDelay: '40ms' }}>
          <h2 className="text-sm font-semibold text-sub mb-3">Today's schedule</h2>
          <div className="space-y-1">
            {briefing.schedule.map((b) => (
              <ScheduleRow key={b.appointment_id} block={b} active={now >= b.start && now < b.end} />
            ))}
            {!briefing.schedule.length && (
              <div className="rounded-lg border border-dashed border-tile px-4 py-3.5 text-sm text-sub">
                No appointments are scheduled today.
                {upcoming.length > 0 && (
                  <div className="mt-2.5 space-y-1">
                    <div className="text-xs text-sub/60">Coming up</div>
                    {upcoming.map((a) => (
                      <Link
                        key={a.id}
                        to={`/lead/${a.lead_id}`}
                        className="flex items-center gap-3 text-sm text-body hover:text-accent"
                      >
                        <span className="font-mono text-xs text-sub/80 w-28 shrink-0">
                          {fmtSlotDay(a.start_ts)} {fmtSlotTime(a.start_ts)}
                        </span>
                        <span>{a.lead_name}</span>
                        {a.location && <span className="text-sub/60 truncate">· {a.location}</span>}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        <section className="rise" style={{ animationDelay: '80ms' }}>
          <h2 className="text-sm font-semibold text-sub mb-3">Meeting briefs</h2>
          <div className="space-y-4">
            {!briefing.meeting_briefs.length && (
              <div className="rounded-lg border border-dashed border-tile px-4 py-3.5 text-sm text-sub">
                No client meetings today — briefs appear here the morning of each meeting.
              </div>
            )}
            {briefing.meeting_briefs.map((brief, i) => (
              <div
                key={brief.lead_id}
                className="rise rounded-xl border border-tile bg-surface p-5"
                style={{ animationDelay: `${120 + i * 80}ms` }}
              >
                <div className="flex items-start gap-4">
                  <ScoreRing score={brief.score} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{brief.name}</span>
                      {brief.area && <span className="text-sub/80 text-sm">· {brief.area}</span>}
                      {brief.persona && (
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${personaStyle(brief.persona)}`}
                        >
                          {brief.persona}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-body mt-1.5"><Markdown>{brief.summary}</Markdown></div>
                  </div>
                  <Link
                    to={`/lead/${brief.lead_id}`}
                    className="ml-auto shrink-0 text-sm text-accent hover:underline"
                  >
                    Open profile →
                  </Link>
                </div>
                {brief.assistant_advice ? (
                  <div className="mt-4 grid sm:grid-cols-2 gap-3">
                    <div className="rounded-lg bg-surface/60 border border-tile p-3">
                      <div className="text-xs text-sub/80 mb-1.5">AI preparation suggestions</div>
                      {brief.assistant_advice.prepare.length ? (
                        <ul className="text-sm text-body space-y-1">
                          {brief.assistant_advice.prepare.map((p) => (
                            <li key={p} className="flex gap-2">
                              <span className="text-sub/60">□</span> <Markdown inline>{p}</Markdown>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <div className="text-sm text-sub/60">No preparation checklist was generated.</div>
                      )}
                    </div>
                    <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
                      <div className="text-xs text-accent mb-1.5">AI recommendation</div>
                      <div className="text-sm text-body">
                        {brief.assistant_advice.recommendation ? (
                          <Markdown>{brief.assistant_advice.recommendation}</Markdown>
                        ) : (
                          <span className="text-sub/60">No recommendation was generated.</span>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4 rounded-lg border border-dashed border-tile px-3 py-2 text-xs text-sub/70">
                    No AI suggestions have been generated for this meeting.
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>

      <aside className="space-y-6">
        <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '140ms' }}>
          <h2 className="text-sm font-semibold text-sub mb-3">Suggested actions</h2>
          <div className="space-y-3">
            {briefing.suggested_actions.map((a) => (
              <div key={a.lead_id} className="border-b border-tile last:border-0 pb-3 last:pb-0">
                {/* Markdown sits outside the Link: an agent-written [Name](lead:N)
                    inside would nest <a> in <a> and break clicks */}
                <div className="text-sm font-medium">
                  <Link to={`/lead/${a.lead_id}`} className="hover:text-accent">
                    {channelIcon(a.channel)}
                  </Link>{' '}
                  <Markdown inline>{a.action}</Markdown>
                </div>
                <p className="text-xs text-sub/80 mt-1"><Markdown inline>{a.reason}</Markdown></p>
              </div>
            ))}
            {!briefing.suggested_actions.length && (
              <div className="text-sm text-sub/60">No due reminders or neglected leads.</div>
            )}
          </div>
        </section>

        <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '180ms' }}>
          <h2 className="text-sm font-semibold text-sub mb-1">Daily memory</h2>
          <p className="text-xs text-sub/60 mb-2">
            Brain-dump anything — the agent files it on the right client.
          </p>
          <textarea
            value={memory}
            onChange={(e) => setMemory(e.target.value)}
            rows={3}
            placeholder='"Michael mentioned his daughter starts private school in September."'
            className="w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm resize-y
                       placeholder:text-sub/50 focus:outline-none focus:border-accent"
          />
          <button
            onClick={saveMemory}
            disabled={savingMemory || !memory.trim()}
            className="mt-2 w-full rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
          >
            {savingMemory ? 'Saving…' : 'Remember it'}
          </button>
        </section>
      </aside>
    </div>
  )
}

const BLOCK_STYLE: Record<ScheduleBlock['kind'], string> = {
  meeting: 'border-accent/40 bg-accent/5',
  travel: 'border-tile bg-transparent text-sub/80',
  buffer: 'border-dashed border-tile text-sub/80',
  personal: 'border-tile bg-surface/70 text-sub',
}

function ScheduleRow({ block, active }: { block: ScheduleBlock; active: boolean }) {
  const inner = (
    <div
      className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${BLOCK_STYLE[block.kind]} ${
        active ? 'ring-1 ring-accent/60' : ''
      }`}
    >
      <span className="font-mono text-xs text-sub/80 w-24 shrink-0">
        {block.start}–{block.end}
      </span>
      <span className={block.kind === 'meeting' ? 'font-medium text-ink2' : ''}>{block.title}</span>
      {active && (
        <span className="ml-auto rounded-full bg-accent/15 text-accent px-2 py-0.5 text-xs">
          Now
        </span>
      )}
    </div>
  )
  return block.lead_id ? (
    <Link to={`/lead/${block.lead_id}`} className="block hover:opacity-90">
      {inner}
    </Link>
  ) : (
    inner
  )
}

export function ScoreRing({ score }: { score: number | null }) {
  const value = score ?? 0
  const r = 17
  const c = 2 * Math.PI * r
  const color = value >= 70 ? '#38bdf8' : value >= 40 ? '#818cf8' : '#64748b'
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" className="shrink-0 -rotate-90">
      <circle cx="22" cy="22" r={r} fill="none" stroke="#374151" strokeWidth="4" />
      <circle
        cx="22" cy="22" r={r} fill="none"
        stroke={color} strokeWidth="4" strokeLinecap="round"
        strokeDasharray={`${(value / 100) * c} ${c}`}
      />
      <text
        x="22" y="22" transform="rotate(90 22 22)"
        textAnchor="middle" dominantBaseline="central"
        fill="#f3f4f6" fontSize="12" fontWeight="600"
      >
        {score ?? '—'}
      </text>
    </svg>
  )
}

function channelIcon(channel: string) {
  return channel === 'text' ? '💬' : channel === 'call' ? '📞' : '✉️'
}

function currentHHMM(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
