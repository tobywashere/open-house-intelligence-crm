import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { DailySummary, fetchDailySummary } from '../summary'
import { Markdown } from './Markdown'
import { Skeleton } from './Skeleton'
import { toast } from './Toast'

// Full-screen daily summary: market watch (web scrape) + AI-written insights.
// UI/UX only — content arrives from K's 7am cron via GET /api/summary (mock until then).
export function DailySummaryOverlay({ onClose }: { onClose: () => void }) {
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const alive = useRef(true)

  // Fetch once per mount — keep this effect off the onClose dep, which App
  // recreates every metrics poll (with it, the overlay refetched every 5s).
  useEffect(() => {
    alive.current = true
    fetchDailySummary().then(setSummary).catch(() => {})
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

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
    const today = new Date().toISOString().slice(0, 10)
    for (let i = 0; i < 24 && alive.current; i++) {
      await new Promise((r) => setTimeout(r, 5000))
      try {
        const fresh = await api.summary<DailySummary>(today)
        if (fresh.generated_at !== before) {
          if (alive.current) {
            setSummary(fresh)
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
            {summary?.greeting ?? 'Preparing your day…'}
          </h1>
          {summary?.mock && (
            <div className="mt-3 inline-block rounded-full border border-line px-2.5 py-0.5 text-xs text-sub/80">
              preview · live at 7:00
            </div>
          )}
        </header>

        {!summary ? (
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
          Qwen 3.6 35B-A3B · generated locally on the GB10
        </footer>
      </div>
    </div>
  )
}
