/** The active vertical pack. Fetched once at startup; every consumer resolves
 *  pack value -> built-in default, so the UI renders correctly even if the
 *  request fails. */
import { api } from './api'

export interface Stage { key: string; label: string; rule: Record<string, unknown> }
export interface Persona { key: string; default: boolean }
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
  copy: {
    app_name: 'Open Intelligence CRM',
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
    'funnel.action_book_tours_sub': 'Only {n} upcoming — tours drive offers',
    'export.upcoming_tours_heading': '## Upcoming tours',
    'export.summary_title': 'Home search summary',
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
