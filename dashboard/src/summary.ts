// Daily summary overlay data layer (UI/UX only — the agentic work is K's).
// Two portions: market watch (web-scraped, relevant local market data) and
// AI insights (model-written narrative — SEPARATE from the deterministic
// insights engine in insights.ts). Tries GET /api/summary; mock until K's cron
// and Toby's endpoint exist. Same pattern as the briefing: UI never changes.
import { api, localDateKey } from './api'

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
  const date = localDateKey()
  try {
    return await api.summary<DailySummary>(date)
  } catch {
    return { ...MOCK_SUMMARY, date, generated_at: new Date().toISOString(), mock: true }
  }
}

// Placeholder content so the overlay is fully designable/demoable today.
// K's 7am cron replaces this wholesale via POST /api/summary.
// Items below are lifted from a real daily-brief run (K's skill, 2026-07-26
// report) so the mock matches what the news-reporter prompt actually produces:
// real sources, real URLs, numbers quoted exactly as printed.
const MOCK_SUMMARY: Omit<DailySummary, 'date' | 'generated_at'> = {
  greeting: 'Good morning, Annie — here is your day at a glance.',
  market_watch: [
    {
      title: 'Issaquah leads state with program to self-certify backyard cottage plans',
      source: 'The Urbanist',
      url: 'https://www.theurbanist.org/issaquah-leads-state-with-program-to-self-certify-backyard-cottage-plans/',
      date: '2026-07-24',
      geo: 'Eastside',
      summary: 'Issaquah is the first city in Washington to let homeowners self-certify DADU (backyard cottage) plans, changing permitting feasibility on every eligible lot — and a likely template for other Washington cities.',
      takeaway: 'Every Issaquah homeowner you know just gained an option worth real money. Strong opener for owners on large lots weighing "improve vs. move."',
      content_opportunity: 'Client email to Issaquah homeowners: what to verify with the city before drawing backyard-cottage plans under the new self-certification program.',
    },
    {
      title: '30-year fixed averages 6.58%, up from 6.55% last week',
      source: 'Freddie Mac PMMS',
      url: 'https://www.freddiemac.com/pmms',
      date: '2026-07-23',
      geo: 'Washington State',
      summary: 'Both benchmarks ticked up this week — the 30-year to 6.58% and the 15-year to 5.96% — an actual printed move, distinct from commentary about expected cuts.',
      takeaway: 'Rate-watching leads deciding between "lock now" and "wait" just saw the wait get slightly more expensive — a fact-based nudge, not a scare tactic.',
      content_opportunity: 'Short post: "Rates moved up 3bps this week — what a $750K Eastside mortgage actually costs at 6.58% vs. 6.55%."',
    },
    {
      title: 'Bellevue moves to rezone Bellevue College campus to unlock expansion',
      source: 'The Registry Puget Sound',
      url: 'https://news.theregistryps.com/bellevue-moves-to-rezone-bellevue-college-campus-with-new-institutional-district-to-unlock-expansion/',
      date: '2026-07-24',
      geo: 'Bellevue',
      summary: 'A proposed (not yet adopted) institutional zoning district would set the campus expansion envelope and reshape demand for adjacent Bellevue housing. The public comment window is the actionable moment.',
      takeaway: 'Buyers and owners near the campus should know this is proposed, not decided — being the agent who explains the process builds trust either way.',
      content_opportunity: 'Neighborhood newsletter section: what a proposed rezone means for adjacent streets, and exactly where in the process this one sits.',
    },
    {
      title: 'AT&T weighs exit from Bothell campus, eyes 250,000 sq ft in Bellevue',
      source: 'The Registry Puget Sound',
      url: 'https://news.theregistryps.com/att-weighs-exit-from-bothell-campus-eyes-250000-sqft-in-bellevue/',
      date: '2026-07-24',
      geo: 'Eastside',
      summary: 'Under consideration only — no lease signed, no move decided. If it firms up, it is a two-submarket employment shift: Bothell vacancy against 250,000 sq ft of Bellevue absorption.',
      takeaway: 'Do not present this to clients as decided. For anyone buying near the Bothell campus, "considering" is the operative word.',
      content_opportunity: 'Short-form video: why "considering" matters for anyone house-hunting near the Bothell campus right now.',
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
