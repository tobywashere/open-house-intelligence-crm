// Client-safe profile export: what a realtor could forward to the client.
// Deliberately EXCLUDES internal fields — score, score_reason, intent guess,
// missing_fields, neglect flags, and the raw activity timeline.
import { fmtSlotDay, fmtSlotTime, LeadProfile } from './api'
import { copy } from './vertical'

export function clientSafeMarkdown(lead: LeadProfile): string {
  const lines: string[] = [`# Home search summary — ${lead.name}`, '']
  lines.push(`Prepared ${new Date().toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}`, '')

  lines.push('## Your search')
  if (lead.area) lines.push(`- **Area:** ${lead.area}`)
  if (lead.budget) lines.push(`- **Budget:** $${lead.budget.toLocaleString()}`)
  if (lead.timeline) lines.push(`- **Timeline:** ${lead.timeline}`)
  if (lead.preferences.length) lines.push(`- **Priorities:** ${lead.preferences.join(', ')}`)
  lines.push('')

  const upcoming = lead.appointments.filter((a) => new Date(a.start_ts) > new Date())
  if (upcoming.length) {
    lines.push(copy('export.upcoming_tours_heading', '## Upcoming tours'))
    for (const a of upcoming) {
      lines.push(`- ${fmtSlotDay(a.start_ts)} · ${fmtSlotTime(a.start_ts)}–${fmtSlotTime(a.end_ts)}${a.location ? ` — ${a.location}` : ''}`)
    }
    lines.push('')
  }

  lines.push('## Contact', `- ${lead.phone ?? ''}${lead.phone && lead.email ? ' · ' : ''}${lead.email ?? ''}`, '')
  return lines.join('\n')
}

export function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  // Firefox-safe: revoke/remove on next tick, after the click has been
  // processed — a click on an element that was never attached to the DOM
  // (or is removed before the click resolves) silently no-ops in Firefox.
  setTimeout(() => {
    URL.revokeObjectURL(url)
    a.remove()
  }, 0)
}
