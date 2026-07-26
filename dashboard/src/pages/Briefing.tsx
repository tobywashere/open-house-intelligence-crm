import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Appointment, fmtSlotDay, fmtSlotTime } from '../api'
import { Briefing as BriefingData, fetchBriefing, PERSONA_STYLE, ScheduleBlock } from '../briefing'
import { Markdown } from '../components/Markdown'
import { Skeleton } from '../components/Skeleton'
import { toast } from '../components/Toast'

export function BriefingPage() {
  const [briefing, setBriefing] = useState<BriefingData | null>(null)
  const [upcoming, setUpcoming] = useState<Appointment[]>([])
  const [now, setNow] = useState(currentHHMM())
  const [memory, setMemory] = useState('')
  const [savingMemory, setSavingMemory] = useState(false)

  useEffect(() => {
    fetchBriefing().then(setBriefing).catch(() => {})
    // upcoming appointments feed the schedule empty state on meeting-free days
    api
      .appointments()
      .then((a) =>
        setUpcoming(
          a
            .filter((x) => x.start_ts > new Date().toISOString())
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

  if (!briefing)
    return (
      <div className="max-w-5xl space-y-8">
        <div>
          <Skeleton className="h-4 w-32 mb-3" />
          <Skeleton className="h-8 w-96" />
        </div>
        <div className="grid lg:grid-cols-[1fr_300px] gap-8">
          <div className="space-y-2">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
            <Skeleton className="h-48 w-full mt-6" />
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    )

  const dateLabel = new Date(briefing.date + 'T12:00:00').toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })

  return (
    <div className="max-w-5xl space-y-8">
      <header className="rise">
        <div className="text-sm text-sub/80">{dateLabel}</div>
        <h1 className="text-2xl font-semibold tracking-tight mt-1">{briefing.greeting}</h1>
        {briefing.mock && (
          <div className="mt-2 inline-block rounded-full border border-line px-2.5 py-0.5 text-xs text-sub/80">
            preview · live at 7:00
          </div>
        )}
      </header>

      <div className="grid lg:grid-cols-[1fr_300px] gap-8 items-start">
        <div className="space-y-8 min-w-0">
          <section className="rise" style={{ animationDelay: '60ms' }}>
            <h2 className="text-sm font-semibold text-sub mb-3">Today's schedule</h2>
            <div className="space-y-1">
              {briefing.schedule.map((b, i) => (
                <ScheduleRow key={i} block={b} active={now >= b.start && now < b.end} />
              ))}
              {!briefing.schedule.length && (
                <div className="rounded-lg border border-dashed border-tile px-4 py-3.5 text-sm text-sub">
                  Nothing on the calendar today — a good day for follow-ups.
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

          <section className="rise" style={{ animationDelay: '120ms' }}>
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
                  style={{ animationDelay: `${160 + i * 80}ms` }}
                >
                  <div className="flex items-start gap-4">
                    <ScoreRing score={brief.score} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">{brief.name}</span>
                        {brief.area && <span className="text-sub/80 text-sm">· {brief.area}</span>}
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${
                            PERSONA_STYLE[brief.persona] ?? PERSONA_STYLE['Home Buyer']
                          }`}
                        >
                          {brief.persona}
                        </span>
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
                  <div className="mt-4 grid sm:grid-cols-2 gap-3">
                    <div className="rounded-lg bg-surface/60 border border-tile p-3">
                      <div className="text-xs text-sub/80 mb-1.5">Prepare</div>
                      <ul className="text-sm text-body space-y-1">
                        {brief.prepare.map((p) => (
                          <li key={p} className="flex gap-2">
                            <span className="text-sub/60">□</span> <Markdown inline>{p}</Markdown>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
                      <div className="text-xs text-accent mb-1.5">Recommendation</div>
                      <div className="text-sm text-body"><Markdown>{brief.recommendation}</Markdown></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside className="space-y-6">
          {briefing.insight_headlines && briefing.insight_headlines.length > 0 && (
            <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '150ms' }}>
              <h2 className="text-sm font-semibold text-sub mb-3">Pipeline insights</h2>
              <div className="space-y-2.5">
                {briefing.insight_headlines.map((h) => (
                  <div key={h.headline} className="flex gap-2 text-sm">
                    <span className={h.severity === 'warn' ? 'text-alert' : h.severity === 'good' ? 'text-accent' : 'text-sub/80'}>
                      {h.severity === 'warn' ? '▲' : h.severity === 'good' ? '●' : '○'}
                    </span>
                    <span className="text-body">{h.headline}</span>
                  </div>
                ))}
              </div>
              <Link to="/" className="mt-3 inline-block text-xs text-accent hover:underline">
                All insights →
              </Link>
            </section>
          )}

          <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '180ms' }}>
            <h2 className="text-sm font-semibold text-sub mb-3">Suggested actions</h2>
            <div className="space-y-3">
              {briefing.suggested_actions.map((a) => (
                <div key={a.lead_id} className="border-b border-tile last:border-0 pb-3 last:pb-0">
                  <Link to={`/lead/${a.lead_id}`} className="text-sm font-medium hover:text-accent">
                    {channelIcon(a.channel)} <Markdown inline>{a.action}</Markdown>
                  </Link>
                  <p className="text-xs text-sub/80 mt-1"><Markdown inline>{a.reason}</Markdown></p>
                </div>
              ))}
              {!briefing.suggested_actions.length && (
                <div className="text-sm text-sub/60">Nothing urgent — pipeline is healthy.</div>
              )}
            </div>
          </section>

          <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '240ms' }}>
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
