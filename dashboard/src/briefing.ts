// Briefing facts come from GET /api/briefing, which rehydrates appointments
// and lead fields from SQLite. This module never invents fallback content.
import { api, Lead, localDateKey } from './api'
import { pack, PersonaCond } from './vertical'

export interface ScheduleBlock {
  appointment_id: number
  start: string // "HH:MM"
  end: string
  kind: 'meeting' | 'travel' | 'buffer' | 'personal'
  title: string
  lead_id?: number
}

export interface MeetingBrief {
  appointment_id: number
  lead_id: number
  name: string
  area: string | null
  budget: number | null
  timeline: string | null
  intent: string
  preferences: string[]
  persona: string | null
  score: number | null
  summary: string
  assistant_advice: {
    prepare: string[]
    recommendation: string | null
  } | null
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
  source: 'crm'
  schedule: ScheduleBlock[]
  meeting_briefs: MeetingBrief[]
  suggested_actions: SuggestedAction[]
}

export async function fetchBriefing(): Promise<Briefing> {
  return api.briefing<Briefing>(localDateKey())
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
//
// A pack is untrusted, third-party-authored JSON (Task 3b fix round 1):
// `personaOf` is called directly in render (Inbox.tsx, Lead.tsx) with no
// ErrorBoundary anywhere in the app, so any shape here that can throw —
// `{any: "not-an-array"}`, an invalid regex literal like `{op:'regex',
// value:'['}` — white-screens the Leads pages. Every path below must
// degrade to `false` instead of throwing; the outer try/catch is a backstop
// for anything not explicitly guarded (e.g. a future op with an unsafe cast).
function evalPersonaCond(cond: PersonaCond, lead: Lead): boolean {
  try {
    if (cond.any) return Array.isArray(cond.any) && cond.any.some((c) => evalPersonaCond(c, lead))
    if (cond.all) return Array.isArray(cond.all) && cond.all.every((c) => evalPersonaCond(c, lead))
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
  } catch {
    return false
  }
}

export function personaOf(lead: Lead): string {
  if (lead.persona) return lead.persona
  // Defense in depth (fix round 2): the backend now sanitizes persona_rules
  // at the loader (_sanitize_persona_rules in vertical.py), but this
  // dashboard build could be served by a backend that predates that
  // sanitizer, so these guards stay. `persona_rules` not being an array
  // (e.g. `{"x": 1}`) would otherwise throw "is not iterable"; a non-object
  // rule entry (e.g. `[null]`) would throw reading `.when` of null.
  const rules = Array.isArray(pack().persona_rules) ? pack().persona_rules : []
  for (const rule of rules) {
    if (!rule?.when || evalPersonaCond(rule.when, lead)) return rule?.persona ?? defaultPersonaKey()
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
