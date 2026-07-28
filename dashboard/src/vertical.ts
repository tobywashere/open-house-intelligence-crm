/** The active vertical pack. Fetched once at startup; every consumer resolves
 *  pack value -> built-in default, so the UI renders correctly even if the
 *  request fails. */
import { api } from './api'

export interface Stage { key: string; label: string; rule: Record<string, unknown> }
export interface Persona { key: string; default: boolean }

// Declarative vocabulary for the mock briefing generator's persona inference
// (Task 3b). `when` is evaluated against a Lead; first rule in `persona_rules`
// whose `when` matches (or is absent — the unconditional default, always last)
// wins, mirroring the original fall-through `if/else if` chain exactly.
// `any`/`all` let a single rule express the two real-estate rules that test
// more than one lead field (e.g. preferences text OR name) — widened beyond
// the brief's minimum {field,op,value} sketch because the minimum vocabulary
// could not express those two rules without a compound condition.
export interface PersonaCond {
  field?: string // 'intent' | 'budget' | 'preferences_text' | 'name' | 'timeline'
  op?: 'eq' | 'lt' | 'lte' | 'gt' | 'gte' | 'regex'
  value?: string | number
  flags?: string // regex flags, e.g. 'i'
  any?: PersonaCond[]
  all?: PersonaCond[]
}
export interface PersonaRule { persona: string; when?: PersonaCond | null }

export interface Pack {
  name: string; display_name: string
  stages: Stage[]
  labels: Record<string, string>
  intent_values: { value: string; label: string }[]
  // DEFAULT_PACK (backend/app/vertical.py) ships personas as {key, default}
  // objects, not bare strings — the brief's sketch interface simplified this;
  // transcribing the real shape here since downstream tasks need `default`
  // to pick the persona a new lead starts with.
  personas: Persona[]
  copy: Record<string, string>
  // App wordmark (Task 3b) — two-tone, e.g. "Open House" + "Intelligence",
  // rendered as plain text + `.brand-gradient` span respectively.
  brand: { name: string; name_accent: string }
  persona_rules: PersonaRule[]
  // keyed by persona; a persona absent here falls back to
  // persona_recommendation_default (kept outside this map so every key in
  // it is a real persona name, not a sentinel — see test_vertical.py).
  persona_recommendations: Record<string, string>
  persona_recommendation_default: string
  // schedule block titles for the mock briefing, keyed by lead intent;
  // 'default' covers everything without a more specific title (e.g. 'sell').
  schedule_titles: Record<string, string>
  // Sample content for the daily-summary overlay's mock mode (Task 3b) —
  // same shape as `Omit<DailySummary, 'date' | 'generated_at'>` in summary.ts,
  // duplicated here (not imported) to avoid a circular import.
  mock_summary: {
    greeting: string
    market_watch: Array<Record<string, unknown>>
    ai_insights: Array<{ title: string; body: string }>
  }
}

// Mirrors DEFAULT_PACK in backend/app/vertical.py — duplicated by design so
// the dashboard renders correctly before/without a successful fetch. Keep
// these in sync by hand; a future task could generate one from the other.
const BUILT_IN: Pack = {
  name: 'real-estate',
  display_name: 'Real estate',
  stages: [
    { key: 'new', label: 'New leads', rule: { type: 'all' } },
    { key: 'contacted', label: 'Contacted',
      rule: { type: 'status_at_least', status: 'contacted' } },
    { key: 'qualified', label: 'Qualified',
      rule: { type: 'status_at_least_or_score', status: 'meeting_booked',
              score_status: 'contacted', min_score: 70 } },
    { key: 'tours', label: 'Tours booked',
      rule: { type: 'status_at_least', status: 'meeting_booked' } },
    { key: 'offers', label: 'Offers submitted',
      rule: { type: 'event_type_or_status', event_type: 'offer', status: 'closed' } },
    { key: 'closed', label: 'Closed', rule: { type: 'status_is', status: 'closed' } },
  ],
  labels: { budget: 'Budget', area: 'Area', timeline: 'Timeline', intent: 'Intent' },
  intent_values: [
    { value: 'buy', label: 'Buy' },
    { value: 'sell', label: 'Sell' },
    { value: 'browse', label: 'Browse' },
    { value: 'unknown', label: 'Unknown' },
  ],
  personas: [
    { key: 'Luxury Executive', default: false },
    { key: 'Growing Family', default: false },
    { key: 'Relocating Professional', default: false },
    { key: 'First-Time Buyer', default: false },
    { key: 'Seller', default: false },
    { key: 'Home Buyer', default: true },
  ],
  brand: { name: 'Open House', name_accent: 'Intelligence' },
  persona_rules: [
    { persona: 'Seller', when: { field: 'intent', op: 'eq', value: 'sell' } },
    { persona: 'Luxury Executive', when: { field: 'budget', op: 'gte', value: 1_400_000 } },
    {
      persona: 'Growing Family',
      when: {
        any: [
          { field: 'preferences_text', op: 'regex', value: 'school|yard|family|cul-de-sac' },
          { field: 'name', op: 'regex', value: '&| and ', flags: 'i' },
        ],
      },
    },
    {
      persona: 'Relocating Professional',
      when: {
        any: [
          { field: 'preferences_text', op: 'regex', value: 'relocat' },
          { field: 'timeline', op: 'regex', value: 'week|asap', flags: 'i' },
        ],
      },
    },
    {
      persona: 'First-Time Buyer',
      when: { all: [{ field: 'budget', op: 'gt', value: 0 }, { field: 'budget', op: 'lt', value: 700_000 }] },
    },
    { persona: 'Home Buyer', when: null },
  ],
  persona_recommendations: {
    'Luxury Executive': 'Lead with data — comps and market evidence, not opinions.',
    'Growing Family': 'Ask about schools first; keep the shortlist to three homes.',
    'Relocating Professional': 'Move fast — their {timeline} timeline is the priority.',
    Seller: 'Bring the listing presentation and a pricing range.',
  },
  persona_recommendation_default: 'Confirm their timeline and agree the next concrete step.',
  schedule_titles: { default: 'Showing', sell: 'Listing appointment' },
  mock_summary: {
    greeting: 'Good morning, Annie — here is your day at a glance.',
    market_watch: [
      {
        title: 'Issaquah leads state with program to self-certify backyard cottage plans',
        source: 'The Urbanist',
        url: 'https://www.theurbanist.org/issaquah-leads-state-with-program-to-self-certify-backyard-cottage-plans/',
        date: '2026-07-24',
        geo: 'Eastside',
        summary:
          'Issaquah is the first city in Washington to let homeowners self-certify DADU (backyard cottage) plans, changing permitting feasibility on every eligible lot — and a likely template for other Washington cities.',
        takeaway:
          'Every Issaquah homeowner you know just gained an option worth real money. Strong opener for owners on large lots weighing "improve vs. move."',
        content_opportunity:
          'Client email to Issaquah homeowners: what to verify with the city before drawing backyard-cottage plans under the new self-certification program.',
      },
      {
        title: '30-year fixed averages 6.58%, up from 6.55% last week',
        source: 'Freddie Mac PMMS',
        url: 'https://www.freddiemac.com/pmms',
        date: '2026-07-23',
        geo: 'Washington State',
        summary:
          'Both benchmarks ticked up this week — the 30-year to 6.58% and the 15-year to 5.96% — an actual printed move, distinct from commentary about expected cuts.',
        takeaway:
          'Rate-watching leads deciding between "lock now" and "wait" just saw the wait get slightly more expensive — a fact-based nudge, not a scare tactic.',
        content_opportunity:
          'Short post: "Rates moved up 3bps this week — what a $750K Eastside mortgage actually costs at 6.58% vs. 6.55%."',
      },
      {
        title: 'Bellevue moves to rezone Bellevue College campus to unlock expansion',
        source: 'The Registry Puget Sound',
        url: 'https://news.theregistryps.com/bellevue-moves-to-rezone-bellevue-college-campus-with-new-institutional-district-to-unlock-expansion/',
        date: '2026-07-24',
        geo: 'Bellevue',
        summary:
          'A proposed (not yet adopted) institutional zoning district would set the campus expansion envelope and reshape demand for adjacent Bellevue housing. The public comment window is the actionable moment.',
        takeaway:
          'Buyers and owners near the campus should know this is proposed, not decided — being the agent who explains the process builds trust either way.',
        content_opportunity:
          'Neighborhood newsletter section: what a proposed rezone means for adjacent streets, and exactly where in the process this one sits.',
      },
      {
        title: 'AT&T weighs exit from Bothell campus, eyes 250,000 sq ft in Bellevue',
        source: 'The Registry Puget Sound',
        url: 'https://news.theregistryps.com/att-weighs-exit-from-bothell-campus-eyes-250000-sqft-in-bellevue/',
        date: '2026-07-24',
        geo: 'Eastside',
        summary:
          'Under consideration only — no lease signed, no move decided. If it firms up, it is a two-submarket employment shift: Bothell vacancy against 250,000 sq ft of Bellevue absorption.',
        takeaway:
          'Do not present this to clients as decided. For anyone buying near the Bothell campus, "considering" is the operative word.',
        content_opportunity:
          'Short-form video: why "considering" matters for anyone house-hunting near the Bothell campus right now.',
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
  },
  copy: {
    'booking.booked': 'Tour booked',
    'booking.cta': 'Book a tour',
    'chat.example_1': 'Add Minh Nguyen, 425-555-0198, buyer interested in Kirkland and Redmond',
    'chat.example_2': 'Which active buyers need a follow-up?',
    'chat.example_3': 'Show me everything we know about Sarah',
    'lead.subject_with_area': 'Your home search in {area}',
    'lead.subject_generic': 'Following up on your home search',
    'inbox.add_placeholder':
      'New lead from a note, e.g. "Met Alex at the open house, looking in Redmond around $950k…"',
    'funnel.stage_negotiating': 'In negotiation',
    'funnel.action_book_tours_title': 'Book more tours this week',
    'funnel.action_advance_title': 'Advance {n} qualified lead{s} to a tour',
    'funnel.kpi_qualified_buyers': 'Qualified buyers',
    'funnel.kpi_tours_scheduled': 'Tours scheduled',
    'insights.meeting_verb_phrase': 'book a meeting',
    'insights.meeting_noun': 'meeting',
    'insights.tour_noun': 'tour',
    'insights.demand_actor_plural': 'active buyers',
    'funnel.action_book_tours_sub': 'Only {n} upcoming — tours drive offers',
    'export.upcoming_tours_heading': '## Upcoming tours',
    'export.summary_title': 'Home search summary',
    'note.offer_heading': 'Log an offer',
    'note.offer_chip': '💰 Offer',
    'note.offer_saved': 'Offer logged — it now counts in the funnel.',
    'note.offer_placeholder': 'e.g. "Offer submitted: $1,250,000 on the Lakemont house"',
    'note.note_placeholder': 'e.g. "Spoke on the phone — wants to see the Lakemont house this weekend"',
  },
}

let active: Pack = BUILT_IN

export const pack = (): Pack => active
export const copy = (key: string, fallback: string): string => active.copy?.[key] ?? fallback

export async function loadVertical(): Promise<Pack> {
  try {
    active = { ...BUILT_IN, ...(await api.vertical<Pack>()) }
  } catch {
    active = BUILT_IN // offline/401/404 — the UI still works, in real-estate copy
  }
  return active
}
