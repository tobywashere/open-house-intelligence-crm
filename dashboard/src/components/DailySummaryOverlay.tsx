import { useEffect, useState } from 'react'
import { DailySummary, fetchDailySummary } from '../summary'
import { Skeleton } from './Skeleton'

// Full-screen daily summary: market watch (web scrape) + AI-written insights.
// UI/UX only — content arrives from K's 7am cron via GET /api/summary (mock until then).
export function DailySummaryOverlay({ onClose }: { onClose: () => void }) {
  const [summary, setSummary] = useState<DailySummary | null>(null)

  useEffect(() => {
    fetchDailySummary().then(setSummary).catch(() => {})
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const dateLabel = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })

  return (
    <div className="fixed inset-0 z-50 bg-zinc-950/98 backdrop-blur-sm overflow-y-auto">
      <button
        onClick={onClose}
        aria-label="Close daily summary"
        className="fixed top-4 right-5 z-50 h-10 w-10 rounded-full border border-zinc-700
                   text-zinc-400 hover:text-zinc-100 hover:border-zinc-500 text-lg"
      >
        ✕
      </button>

      <div className="max-w-5xl mx-auto px-6 py-14">
        <header className="rise">
          <div className="text-sm text-zinc-500">{dateLabel} · Daily summary</div>
          <h1 className="text-3xl font-semibold tracking-tight mt-2">
            {summary?.greeting ?? 'Preparing your day…'}
          </h1>
          {summary?.mock && (
            <div className="mt-3 inline-block rounded-full border border-zinc-700 px-2.5 py-0.5 text-xs text-zinc-500">
              preview · the agent writes the real summary at 7:00 on the GB10
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
              <h2 className="text-sm font-semibold text-zinc-400 mb-4">
                🌐 Market watch <span className="font-normal text-zinc-600">· from today's research</span>
              </h2>
              <div className="space-y-4">
                {summary.market_watch.map((m, i) => (
                  <article
                    key={m.title}
                    className="rise rounded-xl border border-zinc-800 bg-zinc-900/60 p-5"
                    style={{ animationDelay: `${120 + i * 70}ms` }}
                  >
                    <div className="flex items-center gap-2 text-xs text-zinc-500 mb-1">
                      <span>{m.source}</span>
                      {m.date && <span>· {m.date}</span>}
                      {m.geo && (
                        <span className="ml-auto rounded-full border border-zinc-700 px-2 py-px text-[10px] text-zinc-400">
                          {m.geo}
                        </span>
                      )}
                    </div>
                    {m.url ? (
                      <a href={m.url} target="_blank" rel="noreferrer" className="font-semibold hover:text-emerald-300">
                        {m.title} ↗
                      </a>
                    ) : (
                      <div className="font-semibold">{m.title}</div>
                    )}
                    {m.summary && <p className="text-sm text-zinc-400 mt-2">{m.summary}</p>}
                    <p className="text-sm text-zinc-400 mt-2">
                      <span className="text-emerald-400">Why it matters:</span> {m.takeaway}
                    </p>
                    {m.content_opportunity && (
                      <p className="text-xs text-zinc-500 mt-2">📣 {m.content_opportunity}</p>
                    )}
                  </article>
                ))}
              </div>
            </section>

            <section className="rise" style={{ animationDelay: '140ms' }}>
              <h2 className="text-sm font-semibold text-zinc-400 mb-4">
                ✦ AI insights <span className="font-normal text-zinc-600">· written by your local agent</span>
              </h2>
              <div className="space-y-4">
                {summary.ai_insights.map((ins, i) => (
                  <article
                    key={ins.title}
                    className="rise rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-5"
                    style={{ animationDelay: `${180 + i * 70}ms` }}
                  >
                    <div className="font-semibold text-emerald-200">{ins.title}</div>
                    <p className="text-sm text-zinc-300 mt-2 leading-relaxed">{ins.body}</p>
                  </article>
                ))}
              </div>
            </section>
          </div>
        )}

        <footer className="mt-12 text-center text-xs text-zinc-600">
          Qwen 3.6 35B-A3B · generated locally on the Dell GB10 · press Esc or ✕ to close
        </footer>
      </div>
    </div>
  )
}
