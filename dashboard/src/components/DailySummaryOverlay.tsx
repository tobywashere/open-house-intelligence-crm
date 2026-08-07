import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, ApiError, localDateKey } from '../api'
import { DailySummary, fetchDailySummary } from '../summary'
import { BriefingSection } from './BriefingSection'
import { Markdown } from './Markdown'
import { ResearchSettings } from './ResearchSettings'
import { Skeleton } from './Skeleton'
import { toast } from './Toast'

// Full-screen daily summary: canonical CRM briefing + source-linked market
// watch + AI-written insights. Optional report content is separately
// published and validated.
// Missing, invalid, or unavailable content renders an explicit state. There
// is no mock-summary fallback in any agent mode.
export function DailySummaryOverlay({ onClose }: { onClose: () => void }) {
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [summaryError, setSummaryError] = useState<'missing' | 'invalid' | 'unavailable' | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    fetchDailySummary()
      .then((fetched) => {
        if (!alive.current) return
        setSummary(fetched)
        setSummaryError(null)
      })
      .catch((error) => {
        if (!alive.current) return
        setSummaryError(
          error instanceof ApiError && error.status === 404
            ? 'missing'
            : error instanceof ApiError && error.status === 422
              ? 'invalid'
              : 'unavailable',
        )
      })
    return () => {
      alive.current = false
    }
  }, [])

  // Research-scope editor, opened from the market-watch header below.
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    // With the settings panel up, Escape belongs to the panel — closing the
    // overlay out from under it would dismiss both at once.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (settingsOpen) setSettingsOpen(false)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, settingsOpen])

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
    try {
      await api.chat(
        'Intra-day briefing refresh requested. Use the daily-brief skill in Mode 1 and follow its installed {baseDir} runner instruction. Do not use general Python, a repository-relative path, WebFetch, temporary files, or a direct API call. Reply only after the runner prints JSON with "ok": true and "published": true.',
        `summary-trigger-${Date.now()}`,
      )
    } catch {
      setRefreshing(false)
      toast('Could not ask the agent to refresh the summary.')
      return
    }
    toast('↻ Asked the agent for a fresh briefing — this takes a minute…')
    const today = localDateKey()
    // A full local research pass can take a few minutes; poll for five before giving up.
    for (let i = 0; i < 60 && alive.current; i++) {
      await new Promise((r) => setTimeout(r, 5000))
      try {
        const fresh = await api.summary<DailySummary>(today)
        if (fresh.generated_at !== before) {
          if (alive.current) {
            setSummary(fresh)
            setSummaryError(null)
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
            {summary?.greeting ??
              (summaryError === 'missing'
                ? 'No daily summary yet'
                : summaryError
                  ? 'Daily summary unavailable'
                  : 'Preparing your day…')}
          </h1>
        </header>

        {settingsOpen && <ResearchSettings onClose={() => setSettingsOpen(false)} />}

        <BriefingSection onDismiss={onClose} />

        {!summary && summaryError ? (
          <div className="rise mt-10 rounded-xl border border-dashed border-tile bg-surface/40 p-8 text-center">
            <div className="text-2xl mb-2">☀️</div>
            <p className="text-body max-w-md mx-auto">
              {summaryError === 'missing'
                ? 'No daily summary has been published. Your real CRM appointments and due follow-ups are shown above.'
                : summaryError === 'invalid'
                  ? 'The published daily summary was rejected because its structure or source links were invalid.'
                  : 'The daily summary could not be loaded. Your CRM information above is still current.'}
            </p>
            {summaryError === 'missing' && (
              <p className="text-xs text-sub/60 mt-3">
                Market research appears only after the configured agent publishes a source-backed summary.
              </p>
            )}
          </div>
        ) : !summary ? (
          <div className="grid lg:grid-cols-2 gap-8 mt-10">
            <Skeleton className="h-80" />
            <Skeleton className="h-80" />
          </div>
        ) : (
          <div className="grid lg:grid-cols-2 gap-8 mt-10 items-start">
            <section className="rise" style={{ animationDelay: '80ms' }}>
              <h2 className="text-sm font-semibold text-sub mb-4 flex items-center gap-2">
                🌐 Market watch <span className="font-normal text-sub/60">· from today's research</span>
                {/* the moment an operator sees off-target results is the moment
                    they want to retune the keywords — put the door right here */}
                <button
                  onClick={() => setSettingsOpen(true)}
                  className="ml-auto rounded-full border border-line px-2.5 py-1 text-[11px]
                             font-normal text-sub hover:text-accent hover:border-accent/60 transition-colors"
                >
                  Adjust research keywords
                </button>
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
                    <a href={m.url} target="_blank" rel="noreferrer" className="font-semibold hover:text-accent">
                      {m.title} ↗
                    </a>
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
          CRM inference is local. Optional market research and Google integrations may send
          necessary data to their configured providers.
        </footer>
      </div>
    </div>
  )
}
