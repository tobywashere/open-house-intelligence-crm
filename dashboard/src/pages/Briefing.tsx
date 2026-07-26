import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Briefing as BriefingData, fetchBriefing, PERSONA_STYLE, ScheduleBlock } from '../briefing'
import { Skeleton } from '../components/Skeleton'
import { toast } from '../components/Toast'

export function BriefingPage() {
  const [briefing, setBriefing] = useState<BriefingData | null>(null)
  const [now, setNow] = useState(currentHHMM())
  const [memory, setMemory] = useState('')
  const [savingMemory, setSavingMemory] = useState(false)

  useEffect(() => {
    fetchBriefing().then(setBriefing).catch(() => {})
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
        <div className="text-sm text-zinc-500">{dateLabel}</div>
        <h1 className="text-2xl font-semibold tracking-tight mt-1">{briefing.greeting}</h1>
        {briefing.mock && (
          <div className="mt-2 inline-block rounded-full border border-zinc-700 px-2.5 py-0.5 text-xs text-zinc-500">
            preview · the agent generates the real briefing at 7:00 on the GB10
          </div>
        )}
      </header>

      <div className="grid lg:grid-cols-[1fr_300px] gap-8 items-start">
        <div className="space-y-8 min-w-0">
          <section className="rise" style={{ animationDelay: '60ms' }}>
            <h2 className="text-sm font-semibold text-zinc-400 mb-3">Today's schedule</h2>
            <div className="space-y-1">
              {briefing.schedule.map((b, i) => (
                <ScheduleRow key={i} block={b} active={now >= b.start && now < b.end} />
              ))}
            </div>
          </section>

          <section className="rise" style={{ animationDelay: '120ms' }}>
            <h2 className="text-sm font-semibold text-zinc-400 mb-3">Meeting briefs</h2>
            <div className="space-y-4">
              {briefing.meeting_briefs.map((brief, i) => (
                <div
                  key={brief.lead_id}
                  className="rise rounded-xl border border-zinc-800 bg-zinc-900/60 p-5"
                  style={{ animationDelay: `${160 + i * 80}ms` }}
                >
                  <div className="flex items-start gap-4">
                    <ScoreRing score={brief.score} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">{brief.name}</span>
                        {brief.area && <span className="text-zinc-500 text-sm">· {brief.area}</span>}
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${
                            PERSONA_STYLE[brief.persona] ?? PERSONA_STYLE['Home Buyer']
                          }`}
                        >
                          {brief.persona}
                        </span>
                      </div>
                      <p className="text-sm text-zinc-300 mt-1.5">{brief.summary}</p>
                    </div>
                    <Link
                      to={`/lead/${brief.lead_id}`}
                      className="ml-auto shrink-0 text-sm text-emerald-400 hover:underline"
                    >
                      Open profile →
                    </Link>
                  </div>
                  <div className="mt-4 grid sm:grid-cols-2 gap-3">
                    <div className="rounded-lg bg-zinc-950/60 border border-zinc-800 p-3">
                      <div className="text-xs text-zinc-500 mb-1.5">Prepare</div>
                      <ul className="text-sm text-zinc-300 space-y-1">
                        {brief.prepare.map((p) => (
                          <li key={p} className="flex gap-2">
                            <span className="text-zinc-600">□</span> {p}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div className="rounded-lg bg-emerald-500/5 border border-emerald-500/20 p-3">
                      <div className="text-xs text-emerald-400 mb-1.5">Recommendation</div>
                      <p className="text-sm text-zinc-200">{brief.recommendation}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside className="space-y-6">
          <section className="rise rounded-xl border border-zinc-800 bg-zinc-900/60 p-4" style={{ animationDelay: '180ms' }}>
            <h2 className="text-sm font-semibold text-zinc-400 mb-3">Suggested actions</h2>
            <div className="space-y-3">
              {briefing.suggested_actions.map((a) => (
                <div key={a.lead_id} className="border-b border-zinc-800 last:border-0 pb-3 last:pb-0">
                  <Link to={`/lead/${a.lead_id}`} className="text-sm font-medium hover:text-emerald-400">
                    {channelIcon(a.channel)} {a.action}
                  </Link>
                  <p className="text-xs text-zinc-500 mt-1">{a.reason}</p>
                </div>
              ))}
              {!briefing.suggested_actions.length && (
                <div className="text-sm text-zinc-600">Nothing urgent — pipeline is healthy.</div>
              )}
            </div>
          </section>

          <section className="rise rounded-xl border border-zinc-800 bg-zinc-900/60 p-4" style={{ animationDelay: '240ms' }}>
            <h2 className="text-sm font-semibold text-zinc-400 mb-1">Daily memory</h2>
            <p className="text-xs text-zinc-600 mb-2">
              Brain-dump anything — the agent files it on the right client.
            </p>
            <textarea
              value={memory}
              onChange={(e) => setMemory(e.target.value)}
              rows={3}
              placeholder='"Michael mentioned his daughter starts private school in September."'
              className="w-full rounded-md bg-zinc-950 border border-zinc-800 px-3 py-2 text-sm resize-y
                         placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500"
            />
            <button
              onClick={saveMemory}
              disabled={savingMemory || !memory.trim()}
              className="mt-2 w-full rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
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
  meeting: 'border-emerald-500/40 bg-emerald-500/5',
  travel: 'border-zinc-800 bg-transparent text-zinc-500',
  buffer: 'border-dashed border-zinc-800 text-zinc-500',
  personal: 'border-zinc-800 bg-zinc-900/40 text-zinc-400',
}

function ScheduleRow({ block, active }: { block: ScheduleBlock; active: boolean }) {
  const inner = (
    <div
      className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${BLOCK_STYLE[block.kind]} ${
        active ? 'ring-1 ring-emerald-400/60' : ''
      }`}
    >
      <span className="font-mono text-xs text-zinc-500 w-24 shrink-0">
        {block.start}–{block.end}
      </span>
      <span className={block.kind === 'meeting' ? 'font-medium text-zinc-100' : ''}>{block.title}</span>
      {active && (
        <span className="ml-auto rounded-full bg-emerald-500/20 text-emerald-300 px-2 py-0.5 text-xs">
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
  const color = value >= 70 ? '#34d399' : value >= 40 ? '#fbbf24' : '#71717a'
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" className="shrink-0 -rotate-90">
      <circle cx="22" cy="22" r={r} fill="none" stroke="#27272a" strokeWidth="4" />
      <circle
        cx="22" cy="22" r={r} fill="none"
        stroke={color} strokeWidth="4" strokeLinecap="round"
        strokeDasharray={`${(value / 100) * c} ${c}`}
      />
      <text
        x="22" y="22" transform="rotate(90 22 22)"
        textAnchor="middle" dominantBaseline="central"
        fill="#e4e4e7" fontSize="12" fontWeight="600"
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
