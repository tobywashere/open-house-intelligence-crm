// Funnel page data layer (docs/FUNNEL-UI.md). All deterministic, computed from
// existing endpoints. Two stage conventions (group-chat noted, no schema change):
//   Qualified       = reached contacted AND score >= 70 (derived)
//   Offers Submitted = has an event with type "offer" (amount parsed from content)
import { api, Appointment, Lead, LeadProfile, localDateKey } from './api'
import { Insights } from './insights'
import { copy, pack, Stage } from './vertical'

export interface FunnelStage {
  key: string
  label: string
  count: number
}

export interface Conversion {
  pct: number // 0-100, clamped
  num: number
  den: number
}

export interface VelocityRow {
  stage: string
  days: number | null
  slow: boolean
}

export interface Opportunity {
  lead: Lead
  valueLabel: string
  stageLabel: string
  heat: 'High' | 'Medium'
  estimate: string
}

export interface NextAction {
  icon: string
  title: string
  sub: string
  impact: 'High impact' | 'Medium impact'
  cta: string
  to: string
}

export interface Kpi {
  label: string
  value: string
  delta?: string // "▲ 12% vs yesterday" — only when history exists
  up?: boolean
}

export interface FunnelData {
  stages: FunnelStage[]
  conversions: Conversion[]
  worstIdx: number
  overallPct: number
  overallLabel: string
  bottleneck: { label: string; detail: string }
  avgDaysToClose: number | null
  velocity: VelocityRow[]
  sources: { label: string; rate: number; won: number; total: number }[]
  opportunities: Opportunity[]
  actions: NextAction[]
  kpis: Kpi[]
  // Raw lists already fetched to build the funnel — exposed so callers (the
  // dashboard tick) can feed computeInsights() without a second network
  // round-trip for the same data.
  leads: Lead[]
  appts: Appointment[]
}

const RANK: Record<Lead['status'], number> = { new: 0, contacted: 1, meeting_booked: 2, closed: 3 }
const DAY = 86_400_000

// Pack-driven stage rule vocabulary (Task 4) — mirrors KNOWN_RULE_TYPES in
// backend/app/vertical.py exactly. status_at_least_or_score names BOTH
// thresholds explicitly (no implicit rank offset between them): with the
// shipped rule {status: "meeting_booked", score_status: "contacted",
// min_score: 70} this evaluates to RANK>=2 || (RANK>=1 && score>=70) — the
// same formula the funnel used before this was pack-driven. A rule whose
// `type` isn't in this map (e.g. an older backend, or a future rule type
// this client doesn't know yet) is handled by the caller: it renders as
// count 0 rather than crashing.
const RULE_EVAL: Record<string, (l: Lead, r: any, ev: Map<number, LeadProfile['events']>) => boolean> = {
  all: () => true,
  status_is: (l, r) => l.status === r.status,
  status_at_least: (l, r) => RANK[l.status] >= RANK[r.status as Lead['status']],
  status_at_least_or_score: (l, r) =>
    RANK[l.status] >= RANK[r.status as Lead['status']] ||
    (RANK[l.status] >= RANK[r.score_status as Lead['status']] && (l.score ?? 0) >= r.min_score),
  event_type_or_status: (l, r, ev) =>
    (ev.get(l.id) ?? []).some((e) => e.type === r.event_type) || l.status === r.status,
}

// Task 4b: single source of truth for "which leads are in stage X" — both
// the funnel bar (stagesFromPack, below) and the derived sections further
// down this file (velocity/sources/actions/KPIs) read this same map, so a
// pack's definition of e.g. "qualified" can never disagree between the two.
// Defense-in-depth (same policy as the persona `when` evaluator in
// briefing.ts, per vertical.py's comment: TS-side guards stay in place for a
// dashboard that might be served by a backend predating the sanitizer).
// `_sanitize_stages` on the current backend already rejects a non-list
// `stages` and a stage with a non-object `rule`, but this client must not
// assume it's always talking to the current backend — a missing/unknown
// stage key or rule type must degrade to an empty match list, never throw.
export function matchedByStage(
  leads: Lead[],
  eventsByLead: Map<number, LeadProfile['events']>,
  stages: Stage[],
): Map<string, Lead[]> {
  const byStage = new Map<string, Lead[]>()
  if (!Array.isArray(stages)) return byStage
  for (const s of stages) {
    const rule = (s as any)?.rule ?? {}
    const evaluate = RULE_EVAL[(rule as any).type as string]
    byStage.set(s.key, evaluate ? leads.filter((l) => evaluate(l, rule, eventsByLead)) : [])
  }
  return byStage
}

export function stagesFromPack(
  leads: Lead[],
  eventsByLead: Map<number, LeadProfile['events']>,
  stages: Stage[],
): FunnelStage[] {
  if (!Array.isArray(stages)) return []
  const byStage = matchedByStage(leads, eventsByLead, stages)
  return stages.map((s) => {
    const label = typeof s.label === 'string' && s.label ? s.label : s.key
    return { key: s.key, label, count: (byStage.get(s.key) ?? []).length }
  })
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

const parseOfferAmount = (content: string): number | null => {
  const m = content.replace(/,/g, '').match(/\$\s*([\d.]+)\s*(m|million|k)?/i)
  if (!m) return null
  let v = parseFloat(m[1])
  const suffix = (m[2] ?? '').toLowerCase()
  if (suffix === 'm' || suffix === 'million') v *= 1_000_000
  if (suffix === 'k') v *= 1_000
  return v >= 1000 ? v : null
}

const money = (n: number | null) =>
  n == null ? '—' : n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : `$${Math.round(n / 1000)}k`

let cache: { t: number; data: FunnelData } | null = null

export async function fetchFunnel(force = false): Promise<FunnelData> {
  if (!force && cache && Date.now() - cache.t < 60_000) return cache.data
  const [leads, appts] = await Promise.all([api.leads(), api.appointments()])
  const profiles = await Promise.all(leads.map((l) => api.lead(l.id).catch(() => null)))
  const events = new Map(profiles.filter(Boolean).map((p) => [p!.id, p!.events]))

  let yesterday: Insights | null = null
  try {
    const y = localDateKey(new Date(Date.now() - DAY))
    yesterday = await api.insightsFor<Insights>(y)
  } catch {
    yesterday = null
  }

  const data = compute(leads, appts, events, yesterday)
  cache = { t: Date.now(), data }
  return data
}

function compute(
  leads: Lead[],
  appts: Appointment[],
  eventsByLead: Map<number, LeadProfile['events']>,
  yesterday: Insights | null,
): FunnelData {
  const offerOf = (l: Lead) => {
    const ev = (eventsByLead.get(l.id) ?? []).find((e) => e.type === 'offer')
    return ev ? { at: parseUtc(ev.created_at), amount: parseOfferAmount(ev.content) } : null
  }

  // Task 4b: velocity/sources/actions/KPIs read the SAME per-stage match
  // sets the funnel bar itself is built from (matchedByStage/RULE_EVAL) —
  // no forked "qualified"/"tours"/etc. logic. The `??` fallback is required,
  // not cosmetic: a pack may validly omit a stage key (the backend sanitizer
  // only requires >=2 valid stages total), and these sections must then
  // degrade to today's exact hardcoded formula rather than reading an empty
  // array and showing zero.
  const byStage = matchedByStage(leads, eventsByLead, pack().stages)
  const reachedContact = byStage.get('contacted') ?? leads.filter((l) => RANK[l.status] >= 1)
  const qualified =
    byStage.get('qualified') ?? leads.filter((l) => RANK[l.status] >= 2 || (RANK[l.status] >= 1 && (l.score ?? 0) >= 70))
  const toured = byStage.get('tours') ?? leads.filter((l) => RANK[l.status] >= 2)
  const offered = byStage.get('offers') ?? leads.filter((l) => offerOf(l) !== null || l.status === 'closed')
  const closed = byStage.get('closed') ?? leads.filter((l) => l.status === 'closed')
  // Membership check for "has this lead reached the pack's tours stage" —
  // used below by warmUntoured and sources.won so both read the same
  // `toured` set instead of re-deriving it via a second hardcoded RANK>=2
  // filter (that was the exact class of duplication this task removes).
  const touredSet = new Set(toured)

  const stages: FunnelStage[] = stagesFromPack(leads, eventsByLead, pack().stages)

  const conversions: Conversion[] = stages.slice(1).map((s, i) => {
    const den = Math.max(stages[i].count, 1)
    return { pct: Math.min(Math.round((s.count / den) * 100), 100), num: s.count, den: stages[i].count }
  })
  const worstIdx = conversions.reduce((w, c, i) => (c.pct < conversions[w].pct ? i : w), 0)

  const overallPct = leads.length ? Math.round((closed.length / leads.length) * 1000) / 10 : 0
  const bottleneckStage = stages[worstIdx + 1]
  const bottleneck =
    !leads.length || stages.length < 2
      ? { label: 'No data yet', detail: 'Add leads to see where your pipeline is losing them.' }
      : {
          label: bottleneckStage.label,
          detail: `${bottleneckStage.count} / ${stages[worstIdx].count} · ${conversions[worstIdx].pct}% conversion — the weakest step in the pipeline.`,
        }

  // time in stage from status_change events ("a → b"); time-to-close from created → closed
  const enteredAt = (l: Lead, status: string): number | null => {
    if (status === 'new') return parseUtc(l.created_at)
    const ev = (eventsByLead.get(l.id) ?? [])
      .filter((e) => e.type === 'status_change' && e.content.includes(`→ ${status}`))
      .map((e) => parseUtc(e.created_at))
      .sort()[0]
    return ev ?? null
  }
  const durations: Record<string, number[]> = { new: [], contacted: [], meeting_booked: [] }
  // Schema-level, not pack-driven (Task 4b): this walks `status_change`
  // event content literally containing "→ contacted" / "→ meeting_booked" /
  // etc., i.e. it depends on `leads.status`'s fixed 4-value enum
  // (new|contacted|meeting_booked|closed), not on any pack stage key or
  // rule. A pack cannot rename or redefine what "contacted" means at the
  // schema layer, so there is no pack-driven equivalent to derive this
  // from — keep the literal status ladder here.
  const chain = ['new', 'contacted', 'meeting_booked', 'closed']
  for (const l of leads) {
    for (let i = 0; i < 3; i++) {
      const a = enteredAt(l, chain[i])
      const b = enteredAt(l, chain[i + 1])
      if (a != null && b != null && b >= a) durations[chain[i]].push((b - a) / DAY)
    }
  }
  const avg = (xs: number[]) => (xs.length ? Math.round((xs.reduce((t, x) => t + x, 0) / xs.length) * 10) / 10 : null)
  // Stage names mirror the pack's own stage labels (Task 4's funnel config) so
  // the velocity card never repeats a stage word the funnel itself renamed.
  const stageLabel = (key: string, fallback: string) =>
    pack().stages.find((s) => s.key === key)?.label ?? fallback
  const velocityRaw: VelocityRow[] = [
    { stage: stageLabel('new', 'New leads'), days: avg(durations.new), slow: false },
    { stage: stageLabel('contacted', 'Contacted'), days: avg(durations.contacted), slow: false },
    { stage: stageLabel('tours', 'Tours booked'), days: avg(durations.meeting_booked), slow: false },
  ]
  const known = velocityRaw.map((v) => v.days).filter((d): d is number => d != null).sort((a, b) => a - b)
  const median = known.length ? known[Math.floor(known.length / 2)] : null
  const velocity = velocityRaw.map((v) => ({
    ...v,
    slow: median != null && v.days != null && v.days > median * 1.5,
  }))

  const closeDurations = closed
    .map((l) => {
      const end = enteredAt(l, 'closed') ?? parseUtc(l.last_activity_at)
      return (end - parseUtc(l.created_at)) / DAY
    })
    .filter((d) => d >= 0)
  const avgDaysToClose = avg(closeDurations)

  // source → booked-or-closed rate. "won" here means "reached the pack's
  // tours stage" (Task 4b: was a hardcoded RANK>=2 filter — now reads the
  // same `touredSet` the tours KPI/stage count itself is built from).
  const srcNames = [...new Set(leads.map((l) => l.source ?? 'unknown'))]
  const sources = srcNames
    .map((s) => {
      const of = leads.filter((l) => (l.source ?? 'unknown') === s)
      const won = of.filter((l) => touredSet.has(l)).length
      return { label: s, won, total: of.length, rate: of.length ? Math.round((won / of.length) * 1000) / 10 : 0 }
    })
    .sort((a, b) => b.rate - a.rate)

  // top opportunities: open, warmest first; value = offer amount, else budget
  const opportunities: Opportunity[] = leads
    .filter((l) => l.status !== 'closed')
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 4)
    .map((l) => {
      const offer = offerOf(l)
      const weeks = /(\d+)\s*week/i.exec(l.timeline ?? '')
      const months = /(\d+)\s*month/i.exec(l.timeline ?? '')
      const estDays = weeks ? +weeks[1] * 7 : months ? +months[1] * 30 : null
      return {
        lead: l,
        valueLabel: offer?.amount != null ? `${money(offer.amount)} offer` : money(l.budget),
        stageLabel: offer
          ? copy('funnel.stage_negotiating', 'In negotiation')
          : l.status === 'meeting_booked'
            ? stageLabel('tours', 'Tour booked')
            : l.status,
        heat: (l.score ?? 0) >= 85 ? 'High' : 'Medium',
        estimate: estDays ? `Est. close ~${estDays} days` : l.timeline ? `Timeline: ${l.timeline}` : 'Timeline unknown',
      }
    })

  // next best actions — deterministic, hidden when count is zero
  const staleContacted = reachedContact.filter(
    (l) => l.status === 'contacted' && (Date.now() - parseUtc(l.last_activity_at)) / DAY >= 3,
  )
  const upcoming = appts.filter((a) => new Date(a.start_ts) > new Date())
  // Task 4b: "warm but not yet toured" — was a hardcoded RANK<2 filter, now
  // reads the same `touredSet` used above so a pack redefining "tours"
  // can't disagree with this count.
  const warmUntoured = qualified.filter((l) => !touredSet.has(l))
  // `l.status !== 'closed'` here is schema-level (leads.status enum), not a
  // pack concept — "closed" as a lifecycle terminus is a schema fact, and
  // `offered` itself is already pack-driven above, so this filter doesn't
  // need to be.
  const negotiating = offered.filter((l) => l.status !== 'closed')
  const actions: NextAction[] = [
    staleContacted.length && {
      icon: '📞', title: `Follow up with ${staleContacted.length} contacted lead${staleContacted.length > 1 ? 's' : ''}`,
      sub: 'No response in 3+ days', impact: 'High impact' as const, cta: 'View leads', to: '/leads',
    },
    upcoming.length < 3 && {
      icon: '📅', title: copy('funnel.action_book_tours_title', 'Book more tours this week'),
      sub: copy('funnel.action_book_tours_sub', 'Only {n} upcoming — tours drive offers').replace(
        '{n}',
        String(upcoming.length),
      ),
      impact: 'High impact' as const, cta: 'View calendar', to: '/leads',
    },
    warmUntoured.length && {
      icon: '👥',
      title: copy('funnel.action_advance_title', 'Advance {n} qualified lead{s} to a tour')
        .replace('{n}', String(warmUntoured.length))
        .replace('{s}', warmUntoured.length > 1 ? 's' : ''),
      sub: 'Warm and waiting on a next step', impact: 'Medium impact' as const, cta: 'View leads', to: '/leads',
    },
    negotiating.length && {
      icon: '📄', title: `Follow up on ${negotiating.length} negotiation${negotiating.length > 1 ? 's' : ''}`,
      sub: 'Keep momentum on live offers', impact: 'Medium impact' as const, cta: 'View deals', to: '/leads',
    },
  ].filter(Boolean) as NextAction[]

  // KPI strip — deltas only vs a real yesterday insights row
  const yFunnel = yesterday?.insights.find((i) => i.id === 'funnel')?.data
  const yCount = (label: string) => yFunnel?.find((d) => d.label === label)?.value
  const yActive =
    yFunnel != null ? (yCount('New') ?? 0) + (yCount('Contacted') ?? 0) + (yCount('Meeting booked') ?? 0) : null
  const yClosed = yCount('Closed') ?? null
  const active = leads.filter((l) => l.status !== 'closed').length
  const delta = (now: number, then: number | null): { delta?: string; up?: boolean } => {
    if (then == null || then === 0) return {}
    const d = Math.round(((now - then) / then) * 100)
    if (d === 0) return { delta: '— flat vs yesterday' }
    return { delta: `${d > 0 ? '▲' : '▼'} ${Math.abs(d)}% vs yesterday`, up: d > 0 }
  }
  const kpis: Kpi[] = [
    { label: 'Active leads', value: String(active), ...delta(active, yActive) },
    { label: copy('funnel.kpi_qualified_buyers', 'Qualified buyers'), value: String(qualified.length) },
    { label: 'Follow-ups due', value: String(leads.filter((l) => l.is_neglected).length) },
    { label: copy('funnel.kpi_tours_scheduled', 'Tours scheduled'), value: String(upcoming.length) },
    { label: 'Offers submitted', value: String(offered.length) },
    {
      label: 'Close rate',
      value: `${overallPct}%`,
      // delta the RATE, not the closed count — 2→3 closed while total grows
      // faster is a falling rate, not "▲ 50%"
      ...(() => {
        if (yClosed == null || !yFunnel) return {}
        const yTotal = (yActive ?? 0) + yClosed
        if (!yTotal) return {}
        return delta(overallPct, Math.round((yClosed / yTotal) * 100))
      })(),
    },
  ]

  return {
    stages, conversions, worstIdx,
    overallPct,
    overallLabel: `${closed.length} closed / ${leads.length} new leads`,
    bottleneck, avgDaysToClose, velocity, sources, opportunities, actions, kpis,
    leads, appts,
  }
}
