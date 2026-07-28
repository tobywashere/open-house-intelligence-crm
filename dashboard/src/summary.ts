// Daily summary overlay data layer (UI/UX only — the agentic work is K's).
// Two portions: market watch (web-scraped, relevant local market data) and
// AI insights (model-written narrative — SEPARATE from the deterministic
// insights engine in insights.ts). Tries GET /api/summary; mock until K's cron
// and Toby's endpoint exist. Same pattern as the briefing: UI never changes.
import { api, localDateKey } from './api'
import { pack } from './vertical'

// Shape mirrors prompts/seattle-real-estate-news-reporter.md output fields —
// only title/source/takeaway are required; the rest render when present.
export interface MarketItem {
  title: string
  source: string
  takeaway: string // "why this matters" for this realtor
  url?: string
  date?: string
  summary?: string // the 2-4 sentence story summary
  geo?: string // geographic impact, e.g. "Eastside", "King County"
  content_opportunity?: string // suggested post/newsletter/client email idea
}

export interface AiInsight {
  title: string
  body: string
}

export interface DailySummary {
  date: string
  generated_at: string
  greeting: string
  market_watch: MarketItem[]
  ai_insights: AiInsight[]
  mock?: boolean
}

// Returns null on a 404/network failure instead of silently substituting
// sample data — callers decide what to show (labeled mock fallback vs an
// honest offline/empty state) based on whether the agent is actually in mock
// mode. See DailySummaryOverlay.tsx.
export async function fetchDailySummary(): Promise<DailySummary | null> {
  const date = localDateKey()
  try {
    return await api.summary<DailySummary>(date)
  } catch {
    return null
  }
}

// Placeholder content so the overlay is fully designable/demoable in mock
// mode. K's 7am cron replaces real data wholesale via POST /api/summary.
// The real-estate content (lifted from a real daily-brief run — K's skill,
// 2026-07-26 report — real sources, real URLs, numbers quoted exactly as
// printed) now lives in the active vertical pack (`pack().mock_summary`), not
// here, so a non-real-estate pack can supply its own sample narrative. This
// is a function, not a module-level const, because the pack resolves
// asynchronously after app startup (see vertical.ts's loadVertical) — a
// const captured at import time would freeze on the built-in default.
//
// MUST NEVER render unlabeled, and must never render at all outside mock
// mode (AGENT_MODE=mock) — see DailySummaryOverlay.tsx, which gates this
// behind metrics.agent_mode and always tags it "Sample data (mock mode)".
export function mockSummarySample(): Omit<DailySummary, 'date' | 'generated_at'> {
  return pack().mock_summary as unknown as Omit<DailySummary, 'date' | 'generated_at'>
}
