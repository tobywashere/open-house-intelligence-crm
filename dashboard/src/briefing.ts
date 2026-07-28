// Briefing data layer. Tries GET /api/briefing (agent-generated, per docs/BRIEFING-UI.md);
// until K+Toby ship it, a client-side mock derives a plausible briefing from live CRM data
// so the page is fully demoable today and flips to real content with zero UI changes.
import { api, Appointment, fmtMoney, Lead, localDateKey } from './api'
import { computeInsights } from './insights'
import { pack, PersonaCond } from './vertical'

export interface ScheduleBlock {
  start: string // "HH:MM"
  end: string
  kind: 'meeting' | 'travel' | 'buffer' | 'personal'
  title: string
  lead_id?: number
}

export interface MeetingBrief {
  lead_id: number
  name: string
  area: string | null
  persona: string
  score: number | null
  summary: string
  prepare: string[]
  recommendation: string
}

export interface SuggestedAction {
  lead_id: number
  name: string
  channel: 'text' | 'call' | 'email'
  action: string
  reason: string
}

export interface Briefing {
  date: string
  greeting: string
  generated_at: string
  schedule: ScheduleBlock[]
  meeting_briefs: MeetingBrief[]
  suggested_actions: SuggestedAction[]
  // top insights (AI purpose #4, the deterministic insights engine) — input for the summary narrative
  insight_headlines?: { severity: 'info' | 'good' | 'warn'; headline: string }[]
  mock?: boolean
}

export async function fetchBriefing(): Promise<Briefing> {
  const today = new Date()
  const date = localDateKey(today)
  try {
    return await api.briefing<Briefing>(date)
  } catch {
    const [leads, appts, audit] = await Promise.all([api.leads(), api.appointments(), api.audit(200)])
    const briefing = mockBriefing(date, leads, appts)
    briefing.insight_headlines = computeInsights(leads, appts, audit)
      .insights.slice(0, 3)
      .map(({ severity, headline }) => ({ severity, headline }))
    return briefing
  }
}

// Resolves one field referenced by a persona rule's `when` condition off a
// Lead. Field vocabulary is intentionally small — just what today's rules
// need — so a pack can only reference fields the evaluator understands.
function fieldValue(field: string | undefined, lead: Lead): string | number {
  switch (field) {
    case 'intent':
      return lead.intent
    case 'budget':
      return lead.budget ?? 0
    case 'preferences_text':
      return (lead.preferences ?? []).join(' ').toLowerCase()
    case 'name':
      return lead.name
    case 'timeline':
      return lead.timeline ?? ''
    default:
      return ''
  }
}

// Evaluates one persona_rules `when` condition against a lead. `any`/`all`
// compose sub-conditions (OR/AND); a leaf condition compares `field` via
// `op`. See vertical.ts's PersonaCond doc comment for why any/all exist.
function evalPersonaCond(cond: PersonaCond, lead: Lead): boolean {
  if (cond.any) return cond.any.some((c) => evalPersonaCond(c, lead))
  if (cond.all) return cond.all.every((c) => evalPersonaCond(c, lead))
  const actual = fieldValue(cond.field, lead)
  switch (cond.op) {
    case 'eq':
      return actual === cond.value
    case 'lt':
      return (actual as number) < (cond.value as number)
    case 'lte':
      return (actual as number) <= (cond.value as number)
    case 'gt':
      return (actual as number) > (cond.value as number)
    case 'gte':
      return (actual as number) >= (cond.value as number)
    case 'regex':
      return new RegExp(String(cond.value ?? ''), cond.flags ?? '').test(String(actual))
    default:
      return false
  }
}

export function personaOf(lead: Lead): string {
  if (lead.persona) return lead.persona
  const rules = pack().persona_rules ?? []
  for (const rule of rules) {
    if (!rule.when || evalPersonaCond(rule.when, lead)) return rule.persona
  }
  return defaultPersonaKey()
}

// deck palette: accent washes (sky/indigo family), alert red only for the last
// slot — colors are styling and stay in code; the persona NAMES they get
// assigned to come from the active pack (pack().personas), so a non-real-estate
// pack (e.g. recruiting) gets the same look with its own persona labels.
const PERSONA_PALETTE = [
  'bg-accent2/15 text-[#a5b4fc] border-accent2/30',
  'bg-accent/10 text-accent border-accent/30',
  'bg-cyan-400/10 text-cyan-300 border-cyan-400/30',
  'bg-sky-300/10 text-sky-200 border-sky-300/30',
  'bg-alert/10 text-alert border-alert/30',
  'bg-tile text-sub border-line',
]

function defaultPersonaKey(): string {
  const personas = pack().personas
  return personas.find((p) => p.default)?.key ?? personas[0]?.key ?? 'Home Buyer'
}

function personaStyleMap(): Record<string, string> {
  const map: Record<string, string> = {}
  pack().personas.forEach((p, i) => {
    map[p.key] = PERSONA_PALETTE[i % PERSONA_PALETTE.length]
  })
  return map
}

/** Resolves a persona name to its chip classes, falling back to the pack's
 *  default persona's style (rather than a hardcoded 'Home Buyer') so an
 *  unrecognized/legacy persona name still renders styled, not bare. */
export function personaStyle(name: string): string {
  const map = personaStyleMap()
  return map[name] ?? map[defaultPersonaKey()] ?? PERSONA_PALETTE[PERSONA_PALETTE.length - 1]
}

function briefOf(lead: Lead): MeetingBrief {
  const persona = personaOf(lead)
  const bits: string[] = []
  if (lead.intent === 'sell') bits.push(`Selling in ${lead.area ?? 'the area'}.`)
  else bits.push(`${persona === 'Luxury Executive' ? 'High-end buyer' : 'Buyer'} focused on ${lead.area ?? 'the Eastside'}.`)
  if (lead.budget) bits.push(`Budget ${fmtMoney(lead.budget)}.`)
  if (lead.timeline) bits.push(`Timeline: ${lead.timeline}.`)
  if (lead.preferences?.length) bits.push(`Wants: ${lead.preferences.join(', ')}.`)
  if (lead.relationship_summary) bits.unshift(lead.relationship_summary)

  const prepare = [
    `${lead.area ?? 'Local'} comparable sales`,
    lead.budget ? `Inventory near ${fmtMoney(lead.budget)}` : 'Ask budget range',
    ...(lead.preferences ?? []).slice(0, 2).map((p) => `Notes on: ${p}`),
  ]

  const recTemplate =
    pack().persona_recommendations?.[persona] ??
    pack().persona_recommendation_default ??
    'Confirm their timeline and agree the next concrete step.'
  const recommendation = recTemplate.replace('{timeline}', lead.timeline ?? 'tight')

  return {
    lead_id: lead.id,
    name: lead.name,
    area: lead.area,
    persona,
    score: lead.score,
    summary: bits.join(' '),
    prepare,
    recommendation,
  }
}

function hhmm(iso: string): string {
  return iso.slice(11, 16)
}

function mockBriefing(date: string, leads: Lead[], appts: Appointment[]): Briefing {
  const open = leads.filter((l) => l.status !== 'closed')
  const byId = new Map(open.map((l) => [l.id, l]))

  // real appointments today, else fabricate showings from the top leads
  const todays = appts.filter((a) => a.start_ts.startsWith(date))
  let meetings: { start: string; end: string; lead: Lead; title: string }[]
  if (todays.length) {
    meetings = todays
      .filter((a) => byId.has(a.lead_id))
      .map((a) => ({
        start: hhmm(a.start_ts),
        end: hhmm(a.end_ts),
        lead: byId.get(a.lead_id)!,
        title: `${pack().schedule_titles?.default ?? 'Showing'} — ${byId.get(a.lead_id)!.name}${a.location ? ` (${a.location})` : ''}`,
      }))
  } else {
    const top = open.slice(0, 2)
    const times = [
      ['10:00', '10:45'],
      ['14:00', '14:45'],
    ]
    meetings = top.map((lead, i) => ({
      start: times[i][0],
      end: times[i][1],
      lead,
      title: `${pack().schedule_titles?.[lead.intent] ?? pack().schedule_titles?.default ?? 'Showing'} — ${lead.name}${lead.area ? ` (${lead.area})` : ''}`,
    }))
  }
  meetings.sort((a, b) => a.start.localeCompare(b.start))

  const schedule: ScheduleBlock[] = []
  for (const m of meetings) {
    schedule.push({
      start: shiftMinutes(m.start, -20),
      end: m.start,
      kind: 'travel',
      title: `Drive to ${m.lead.area ?? 'meeting'}`,
    })
    schedule.push({ start: m.start, end: m.end, kind: 'meeting', title: m.title, lead_id: m.lead.id })
    schedule.push({
      start: m.end,
      end: shiftMinutes(m.end, 30),
      kind: 'buffer',
      title: 'Buffer / follow-ups',
    })
  }
  schedule.push({ start: '12:00', end: '13:00', kind: 'personal', title: 'Lunch' })
  schedule.sort((a, b) => a.start.localeCompare(b.start))

  const meetingIds = new Set(meetings.map((m) => m.lead.id))
  const actionable = open
    .filter((l) => !meetingIds.has(l.id))
    .sort((a, b) => (b.is_neglected - a.is_neglected) || (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 3)
  const channels: SuggestedAction['channel'][] = ['text', 'call', 'email']
  const suggested_actions: SuggestedAction[] = actionable.map((l, i) => ({
    lead_id: l.id,
    name: l.name,
    channel: channels[i % 3],
    action: `${channels[i % 3] === 'text' ? 'Text' : channels[i % 3] === 'call' ? 'Call' : 'Email'} ${l.name}`,
    reason: l.is_neglected
      ? `No contact since ${l.last_activity_at.slice(0, 10)} — score ${l.score ?? '—'} is too warm to go cold.`
      : `Score ${l.score ?? '—'}${l.timeline ? ` with a ${l.timeline} timeline` : ''} — keep the momentum.`,
  }))

  const followupsDue = open.filter((l) => l.is_neglected).length
  return {
    date,
    greeting: `Good morning, Annie 👋 — ${meetings.length} meeting${meetings.length === 1 ? '' : 's'} today, ${followupsDue} follow-up${followupsDue === 1 ? '' : 's'} due.`,
    generated_at: new Date().toISOString(),
    schedule,
    meeting_briefs: meetings.map((m) => briefOf(m.lead)),
    suggested_actions,
    mock: true,
  }
}

function shiftMinutes(time: string, delta: number): string {
  const [h, m] = time.split(':').map(Number)
  const total = Math.max(0, h * 60 + m + delta)
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}
