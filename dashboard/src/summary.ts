// Daily summary overlay data layer (UI/UX only — the agentic work is K's).
// Two portions: market watch (web-scraped, relevant local market data) and
// AI insights (model-written narrative — SEPARATE from the deterministic
// insights engine in insights.ts). Tries GET /api/summary; mock until K's cron
// and Toby's endpoint exist. Same pattern as the briefing: UI never changes.
import { api } from './api'

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

export async function fetchDailySummary(): Promise<DailySummary> {
  const date = new Date().toISOString().slice(0, 10)
  try {
    return await api.summary<DailySummary>(date)
  } catch {
    return { ...MOCK_SUMMARY, date, generated_at: new Date().toISOString(), mock: true }
  }
}

// Plausible placeholder content so the overlay is fully designable/demoable today.
// K's 7am cron replaces this wholesale via POST /api/summary.
const MOCK_SUMMARY: Omit<DailySummary, 'date' | 'generated_at'> = {
  greeting: 'Good morning, Annie — here is your day at a glance.',
  market_watch: [
    {
      title: 'Eastside inventory up 8% month-over-month',
      source: 'NWMLS weekly digest',
      geo: 'Eastside',
      summary: 'Active listings across Bellevue, Kirkland and Redmond rose for the third straight week as new construction closings hit the market.',
      takeaway: 'More options for your Bellevue and Kirkland buyers — good week to re-engage anyone who paused for lack of inventory.',
      content_opportunity: 'Newsletter: "3 new Eastside listings my buyers should see this week."',
    },
    {
      title: '30-year fixed rates dip to 5.9%',
      source: 'Freddie Mac PMMS',
      takeaway: 'Rate-sensitive leads (Ryan, Emily) may be ready to move — a quick "rates just dropped" text tends to convert.',
    },
    {
      title: 'Issaquah school ratings released ahead of enrollment',
      source: 'Seattle Times',
      takeaway: 'Directly relevant to your Growing Family personas — a strong conversation opener for the Tran and Henderson tours.',
    },
  ],
  ai_insights: [
    {
      title: 'Your referral channel is quietly your best',
      body: 'Referred leads move through your pipeline faster than any other source this month. Consider asking Linda Park and Priya Natarajan — both recently booked — for an introduction while the experience is fresh.',
    },
    {
      title: 'Evening momentum is real',
      body: 'Every tour on your calendar landed in an evening slot. When proposing times to new leads, offering 5–7pm first is likely to shorten the back-and-forth.',
    },
    {
      title: 'Two warm leads are one text from cold',
      body: 'Marcus Webb and Kevin O’Leary are both high-score and idle. A short, specific message today (new townhome listing for Marcus; Kirkland condo update for Kevin) keeps roughly $1.6M of pipeline moving.',
    },
  ],
}
