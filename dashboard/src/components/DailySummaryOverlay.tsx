import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, localDateKey, Metrics } from '../api'
import { DailySummary, fetchDailySummary, mockSummarySample } from '../summary'
import { BriefingSection } from './BriefingSection'
import { Markdown } from './Markdown'
import { Skeleton } from './Skeleton'
import { toast } from './Toast'

// Full-screen daily summary: morning briefing (schedule, meeting briefs,
// suggested actions) + market watch (web scrape) + AI-written insights.
// UI/UX only — content arrives from K's 7am cron via GET /api/summary.
// Mock-mode fallback data is clearly labeled; outside mock mode a 404 renders
// an honest offline/empty state instead of ever silently showing sample data.
export function DailySummaryOverlay({ onClose, metrics }: { onClose: () => void; metrics: Metrics | null }) {
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [offline, setOffline] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const alive = useRef(true)

  // agent_mode isn't known until the first /api/metrics poll lands (Task 13
  // pattern — see LocalBadge). modeKnown/isMock are stable primitives so this
  // effect doesn't rerun on every 5s metrics poll (only when mode flips).
  const modeKnown = metrics !== null
  const isMock = metrics?.agent_mode === 'mock'

  // Fetch once mode is known — keep this effect off the onClose dep, which App
  // recreates every metrics poll (with it, the overlay refetched every 5s).
  useEffect(() => {
    if (!modeKnown) return
    alive.current = true
    fetchDailySummary().then((fetched) => {
      if (!alive.current) return
      if (fetched) {
        setSummary(fetched)
      } else if (isMock) {
        // Mock mode only: labeled sample data so the overlay is demoable
        // with no backend content yet. See mockSummarySample's own comment.
        setSummary({ ...mockSummarySample(), date: localDateKey(), generated_at: new Date().toISOString(), mock: true })
      } else {
        // Real agent, nothing posted yet — honest empty state, never sample data.
        setOffline(true)
      }
    })
    return () => {
      alive.current = false
    }
  }, [modeKnown, isMock])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // The overlay is fixed and full-screen, so a <Link> inside it (All insights,
  // Open profile, a suggested action) used to route underneath and leave the
  // user staring at an unchanged summary — the click looked dead. Dismiss on
  // any route change rather than wiring onClose through every link.
  // onClose is held in a ref because App recreates it on every metrics poll
  // (see the fetch effect above); depending on it here would rerun this at 5s.
  const location = useLocation()
  const openedAt = useRef(location.pathname)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (location.pathname !== openedAt.current) closeRef.current()
  }, [location.pathname])

  // Intra-day refresh: ask the agent (via the normal chat relay) to re-run the
  // research + insights and POST a fresh summary, then poll until it lands.
  // UI/UX only — the generation itself is the agent's job (see TODO.md).
  const regenerate = async () => {
    if (refreshing) return
    setRefreshing(true)
    const before = summary?.generated_at
    api
      .chat(
        'Intra-day briefing refresh requested: re-run the market research and AI insights now, then POST the fresh daily summary to /api/summary for today.',
        'summary-trigger',
      )
      .catch(() => {})
    toast('↻ Asked the agent for a fresh briefing — this takes a minute…')
    const today = localDateKey()
    // a full research pass takes ~3 min on the GB10 — poll for 5 before giving up
    for (let i = 0; i < 60 && alive.current; i++) {
      await new Promise((r) => setTimeout(r, 5000))
      try {
        const fresh = await api.summary<DailySummary>(today)
        if (fresh.generated_at !== before) {
          if (alive.current) {
            setSummary(fresh)
            setOffline(false)
            toast('✓ Fresh intra-day briefing ready')
            setRefreshing(false)
          }
          return
        }
      } catch {
        /* not published yet */
      }
    }
    if (alive.current) {
      setRefreshing(false)
      toast('No fresh summary published yet — the agent may still be researching.')
    }
  }

  const dateLabel = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })

  return (
    <div className="fixed inset-0 z-50 bg-bg/98 backdrop-blur-sm overflow-y-auto">
      <button
        onClick={regenerate}
        disabled={refreshing}
        className="fixed top-4 right-[4.5rem] z-50 h-10 rounded-full border border-accent/30 bg-accent/10
                   px-4 text-sm text-accent hover:border-accent/60 disabled:opacity-60 transition-colors"
      >
        {refreshing ? '↻ Researching…' : '↻ Refresh now'}
      </button>
      <button
        onClick={onClose}
        aria-label="Close daily summary"
        className="fixed top-4 right-5 z-50 h-10 w-10 rounded-full border border-line
                   text-sub hover:text-ink hover:border-[#4b5563] text-lg"
      >
        ✕
      </button>

      <div className="max-w-5xl mx-auto px-6 py-14">
        <header className="rise">
          <div className="text-sm text-sub/80">{dateLabel} · Daily summary</div>
          <h1 className="text-3xl font-semibold tracking-tight mt-2">
            {summary?.greeting ?? (offline ? 'No daily summary yet' : 'Preparing your day…')}
          </h1>
          {summary?.mock && (
            <div className="mt-3 inline-block rounded-full border border-amber-400/40 bg-amber-400/10 px-2.5 py-0.5 text-xs text-amber-300">
              ⚠ Sample data (mock mode) · not from your CRM
            </div>
          )}
        </header>

        <BriefingSection onDismiss={onClose} />

        {!summary && offline ? (
          <div className="rise mt-10 rounded-xl border border-dashed border-tile bg-surface/40 p-8 text-center">
            <div className="text-2xl mb-2">☀️</div>
            <p className="text-body max-w-md mx-auto">
              No daily summary yet — your morning briefing above works fully offline from your
              CRM. Market watch (news) needs the separate news cron, which requires internet and
              is optional.
            </p>
            <p className="text-xs text-sub/60 mt-3">
              See "Morning briefing" in <code className="text-sub/80">docs/LOCAL-AI.md</code> to
              wire up the cron that posts this.
            </p>
          </div>
        ) : !summary ? (
          <div className="grid lg:grid-cols-2 gap-8 mt-10">
            <Skeleton className="h-80" />
            <Skeleton className="h-80" />
          </div>
        ) : (
          <div className="grid lg:grid-cols-2 gap-8 mt-10 items-start">
            <section className="rise" style={{ animationDelay: '80ms' }}>
              <h2 className="text-sm font-semibold text-sub mb-4">
                🌐 Market watch <span className="font-normal text-sub/60">· from today's research</span>
              </h2>
              <div className="space-y-4">
                {summary.market_watch.map((m, i) => (
                  <article
                    key={m.title}
                    className="rise rounded-xl border border-tile bg-surface p-5"
                    style={{ animationDelay: `${120 + i * 70}ms` }}
                  >
                    <div className="flex items-center gap-2 text-xs text-sub/80 mb-1">
                      <span>{m.source}</span>
                      {m.date && <span>· {m.date}</span>}
                      {m.geo && (
                        <span className="ml-auto rounded-full border border-line px-2 py-px text-[10px] text-sub">
                          {m.geo}
                        </span>
                      )}
                    </div>
                    {m.url ? (
                      <a href={m.url} target="_blank" rel="noreferrer" className="font-semibold hover:text-accent">
                        {m.title} ↗
                      </a>
                    ) : (
                      <div className="font-semibold">{m.title}</div>
                    )}
                    {m.summary && <div className="text-sm text-sub mt-2"><Markdown>{m.summary}</Markdown></div>}
                    <p className="text-sm text-sub mt-2">
                      <span className="text-accent">Why it matters:</span> <Markdown inline>{m.takeaway}</Markdown>
                    </p>
                    {m.content_opportunity && (
                      <p className="text-xs text-sub/80 mt-2">📣 <Markdown inline>{m.content_opportunity}</Markdown></p>
                    )}
                  </article>
                ))}
              </div>
            </section>

            <section className="rise" style={{ animationDelay: '140ms' }}>
              <h2 className="text-sm font-semibold text-sub mb-4">
                ✦ AI insights <span className="font-normal text-sub/60">· written by your local agent</span>
              </h2>
              <div className="space-y-4">
                {summary.ai_insights.map((ins, i) => (
                  <article
                    key={ins.title}
                    className="rise rounded-xl border border-accent/20 bg-accent/5 p-5"
                    style={{ animationDelay: `${180 + i * 70}ms` }}
                  >
                    <div className="font-semibold text-ink2">{ins.title}</div>
                    <div className="text-sm text-body mt-2 leading-relaxed"><Markdown>{ins.body}</Markdown></div>
                  </article>
                ))}
              </div>
            </section>
          </div>
        )}

        <footer className="mt-12 text-center text-xs text-sub/60">
          Generated locally · nothing here leaves this machine
        </footer>
      </div>
    </div>
  )
}
