// Insights engine — AI purpose #4 (docs/INSIGHTS.md).
// Deterministic by design: pure functions over CRM data. The dashboard renders
// this JSON, and the morning summary consumes it as narrative input. No LLM
// is involved in computing a number here, ever.
import { Appointment, AuditRow, fmtMoney, Lead, localDateKey } from './api'
import { copy } from './vertical'

export interface InsightDatum {
  label: string
  value: number
  display?: string // formatted value shown at the bar end
}

export interface Insight {
  id: string
  title: string
  severity: 'info' | 'good' | 'warn'
  headline: string
  detail: string
  data: InsightDatum[]
  // leads this insight is about — rendered as profile links on the card
  related?: { name: string; lead_id: number }[]
}

export interface Insights {
  date: string
  computed_at: string
  insights: Insight[]
}

// Storage is naive local (Task 7): a bare `new Date(t)` on a `Z`-suffixed
// legacy row parses as aware UTC (native Date behavior), and on a naive
// `T`-separated row parses as local per ES2015+ — no forced `+ 'Z'` needed
// (or wanted: that shifts every new-convention row 7-8h into the future).
// Space-separated rows are an older legacy shape, normalized to `T` first.
const parseUtc = (ts: string) => {
  const t = ts.replace(' ', 'T')
  return new Date(t).getTime()
}

export const daysIdle = (l: Lead) =>
  Math.max(0, Math.floor((Date.now() - parseUtc(l.last_activity_at)) / 86_400_000))

const pct = (n: number, d: number) => (d === 0 ? 0 : Math.round((n / d) * 100))

export function computeInsights(leads: Lead[], appts: Appointment[], audit: AuditRow[]): Insights {
  const open = leads.filter((l) => l.status !== 'closed')
  const insights: Insight[] = [
    funnel(leads),
    sourceEffectiveness(leads),
    pipelineValue(open),
    demandMap(open),
    aging(open),
    bookingPattern(appts),
    agentActivity(audit),
  ]
  // warn first, then good, then info — the briefing takes the top 3
  const order = { warn: 0, good: 1, info: 2 }
  insights.sort((a, b) => order[a.severity] - order[b.severity])
  return {
    date: localDateKey(),
    computed_at: new Date().toISOString(),
    insights,
  }
}

function funnel(leads: Lead[]): Insight {
  const count = (s: Lead['status']) => leads.filter((l) => l.status === s).length
  const stages = {
    new: count('new'),
    contacted: count('contacted'),
    meeting_booked: count('meeting_booked'),
    closed: count('closed'),
  }
  const reachedContact = stages.contacted + stages.meeting_booked + stages.closed
  const reachedBooking = stages.meeting_booked + stages.closed
  const contactRate = pct(reachedContact, leads.length)
  const bookRate = pct(reachedBooking, reachedContact)
  const bottleneck =
    contactRate <= bookRate
      ? 'first contact (new → contacted)'
      : `booking (contacted → ${copy('insights.meeting_noun', 'meeting')})`
  return {
    id: 'funnel',
    title: 'Pipeline funnel',
    severity: stages.new > reachedContact ? 'warn' : 'info',
    headline: `${bookRate}% of contacted leads ${copy('insights.meeting_verb_phrase', 'book a meeting')}`,
    detail: `${stages.new} new → ${reachedContact} contacted (${contactRate}%) → ${reachedBooking} booked (${bookRate}%). Bottleneck: ${bottleneck}.`,
    data: [
      { label: 'New', value: stages.new },
      { label: 'Contacted', value: stages.contacted },
      { label: 'Meeting booked', value: stages.meeting_booked },
      { label: 'Closed', value: stages.closed },
    ],
  }
}

function sourceEffectiveness(leads: Lead[]): Insight {
  const sources = [...new Set(leads.map((l) => l.source ?? 'unknown'))]
  const rows = sources
    .map((s) => {
      const of = leads.filter((l) => (l.source ?? 'unknown') === s)
      const booked = of.filter((l) => l.status === 'meeting_booked' || l.status === 'closed').length
      return { label: s, total: of.length, booked, rate: pct(booked, of.length) }
    })
    .filter((r) => r.total > 0)
    .sort((a, b) => b.rate - a.rate)
  const best = rows[0]
  return {
    id: 'source_effectiveness',
    title: 'Source effectiveness',
    severity: 'info',
    headline: best ? `${best.label} leads book at ${best.rate}%` : 'No source data yet',
    detail: rows.map((r) => `${r.label}: ${r.booked}/${r.total}`).join(' · '),
    data: rows.map((r) => ({ label: r.label, value: r.rate, display: `${r.rate}% (${r.booked}/${r.total})` })),
  }
}

function pipelineValue(open: Lead[]): Insight {
  const sum = (ls: Lead[]) => ls.reduce((t, l) => t + (l.budget ?? 0), 0)
  const byStage = (['new', 'contacted', 'meeting_booked'] as const).map((s) => ({
    label: s.replace('_', ' '),
    value: sum(open.filter((l) => l.status === s)),
  }))
  const atRisk = open.filter((l) => daysIdle(l) >= 2 && (l.score ?? 0) >= 70)
  const riskValue = sum(atRisk)
  return {
    id: 'pipeline_value',
    title: 'Pipeline value',
    severity: riskValue > 0 ? 'warn' : 'good',
    headline:
      riskValue > 0
        ? `${fmtMoney(riskValue)} of warm pipeline is going cold`
        : `${fmtMoney(sum(open))} active pipeline, none at risk`,
    detail:
      riskValue > 0
        ? `${atRisk.length} high-score lead${atRisk.length === 1 ? '' : 's'} (${atRisk
            .map((l) => l.name)
            .slice(0, 3)
            .join(', ')}) idle 2+ days. Total open pipeline: ${fmtMoney(sum(open))}.`
        : `Every high-score lead has been touched in the last 2 days.`,
    data: byStage.map((r) => ({ ...r, display: fmtMoney(r.value) })),
    related: atRisk.slice(0, 4).map((l) => ({ name: l.name, lead_id: l.id })),
  }
}

function demandMap(open: Lead[]): Insight {
  const areas = [...new Set(open.map((l) => l.area).filter(Boolean))] as string[]
  const rows = areas
    .map((a) => {
      const of = open.filter((l) => l.area === a)
      const budgets = of.filter((l) => l.budget)
      const avg = budgets.length ? budgets.reduce((t, l) => t + l.budget!, 0) / budgets.length : 0
      return { label: a, value: of.length, avg }
    })
    .sort((a, b) => b.value - a.value)
    .slice(0, 5)
  const top = rows[0]
  return {
    id: 'demand_map',
    title: 'Demand by area',
    severity: 'info',
    headline: top
      ? `${top.label} leads demand — ${top.value} ${copy('insights.demand_actor_plural', 'active buyers')}`
      : 'No area data yet',
    detail: rows.map((r) => `${r.label}: ${r.value}${r.avg ? ` (avg ${fmtMoney(Math.round(r.avg))})` : ''}`).join(' · '),
    data: rows.map((r) => ({
      label: r.label,
      value: r.value,
      display: r.avg ? `${r.value} · avg ${fmtMoney(Math.round(r.avg))}` : String(r.value),
    })),
  }
}

function aging(open: Lead[]): Insight {
  const active = open.filter((l) => l.status === 'new' || l.status === 'contacted')
  const stale = active.filter((l) => daysIdle(l) >= 2)
  const avgIdle = (ls: Lead[]) =>
    ls.length ? Math.round((ls.reduce((t, l) => t + daysIdle(l), 0) / ls.length) * 10) / 10 : 0
  return {
    id: 'aging',
    title: 'Lead aging',
    severity: stale.length > 0 ? 'warn' : 'good',
    headline:
      stale.length > 0
        ? `${stale.length} lead${stale.length === 1 ? '' : 's'} sliding toward neglect`
        : 'No leads going stale',
    detail: `Average idle: ${avgIdle(active)} days across ${active.length} active leads. ${stale.length} idle 2+ days.`,
    data: [
      { label: 'Touched < 2d', value: active.length - stale.length },
      { label: 'Idle 2+ days', value: stale.length },
    ],
    related: stale
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      .slice(0, 4)
      .map((l) => ({ name: l.name, lead_id: l.id })),
  }
}

function bookingPattern(appts: Appointment[]): Insight {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const counts = new Map<string, number>()
  let evening = 0
  for (const a of appts) {
    const d = new Date(a.start_ts)
    const label = days[(d.getDay() + 6) % 7]
    counts.set(label, (counts.get(label) ?? 0) + 1)
    if (d.getHours() >= 17) evening++
  }
  const rows = days.filter((d) => counts.has(d)).map((d) => ({ label: d, value: counts.get(d)! }))
  const eveningRate = pct(evening, appts.length)
  const tourNoun = copy('insights.tour_noun', 'tour')
  // Plural is its own pack key (not `${tourNoun}s`) — English "-s" appending
  // isn't a safe default across verticals/languages (e.g. it would mangle an
  // irregular plural a future pack might need).
  const tourNounPlural = copy('insights.tour_noun_plural', 'tours')
  return {
    id: 'booking_pattern',
    title: `When ${tourNounPlural} get booked`,
    severity: 'info',
    headline: appts.length
      ? `${eveningRate}% of ${tourNounPlural} are evening slots`
      : `No ${tourNounPlural} booked yet`,
    detail: appts.length
      ? `${appts.length} ${appts.length === 1 ? tourNoun : tourNounPlural} on the calendar. Lead with evening availability when proposing times.`
      : `Booking patterns appear once ${tourNounPlural} are on the calendar.`,
    data: rows,
  }
}

function agentActivity(audit: AuditRow[]): Insight {
  const dayAgo = Date.now() - 86_400_000
  const recent = audit.filter((r) => parseUtc(r.ts) >= dayAgo)
  const counts = new Map<string, number>()
  for (const r of recent) counts.set(r.tool, (counts.get(r.tool) ?? 0) + 1)
  const rows = [...counts.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 5)
  return {
    id: 'agent_activity',
    title: 'Agent activity (24h)',
    severity: 'info',
    headline: `${recent.length} agent actions in the last 24 hours`,
    detail: rows.length
      ? `Most frequent: ${rows[0].label} (${rows[0].value}×). Every action is in the audit log.`
      : 'The agent has been quiet — activity appears here as tools run.',
    data: rows,
  }
}
