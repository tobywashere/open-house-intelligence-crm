// Daily summary overlay data layer. Missing or invalid content is handled by
// the UI as an explicit state; this module never substitutes sample prose.
// Two portions: market watch (web-scraped, relevant local market data) and
// AI insights (model-written narrative — SEPARATE from the deterministic
// insights engine in insights.ts).
import { api, localDateKey } from './api'

// Shape mirrors prompts/seattle-real-estate-news-reporter.md output fields —
// every displayed market claim has an operator-openable source URL.
export interface MarketItem {
  title: string
  source: string
  takeaway: string // "why this matters" for this realtor
  url: string
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
}

export async function fetchDailySummary(): Promise<DailySummary> {
  return api.summary<DailySummary>(localDateKey())
}
