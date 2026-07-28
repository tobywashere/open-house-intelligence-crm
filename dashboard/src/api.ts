// Typed client for the backend contract (docs/CONTRACT.md).
// Dev: vite on :5173 talks to the backend on :8000. Production (GB10): the
// backend serves the built dashboard, so the API is same-origin at /api.
const BASE =
  import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000/api' : '/api')

export interface Lead {
  id: number
  name: string
  phone: string | null
  email: string | null
  source: string
  status: 'new' | 'contacted' | 'meeting_booked' | 'closed'
  score: number | null
  score_reason: string | null
  budget: number | null
  area: string | null
  timeline: string | null
  preferences: string[]
  intent: string
  missing_fields: string[]
  is_neglected: number
  created_at: string
  last_activity_at: string
  // filled by the agent once K adds the columns; UI hides them when absent
  persona?: string | null
  relationship_summary?: string | null
}

export interface LeadEvent {
  id: number
  lead_id: number
  type: string
  content: string
  created_at: string
}

export interface Appointment {
  id: number
  lead_id: number
  start_ts: string
  end_ts: string
  location: string | null
  lead_name?: string
}

export interface LeadProfile extends Lead {
  events: LeadEvent[]
  appointments: Appointment[]
}

export interface AuditRow {
  id: number
  ts: string
  actor: 'agent' | 'user' | 'cron'
  tool: string
  input: string
  output: string
  lead_id: number | null
  lead_name: string | null
}

export interface Reminder {
  id: number
  lead_id: number
  due_ts: string
  note: string | null
  done: number
  lead_name?: string
}

export interface ChatMessage {
  id: number
  session_id: string
  role: string
  content: string
  created_at: string
}

export interface ChatSession {
  session_id: string
  message_count: number
  last_at: string
  preview: string
}

export interface IntegrationsStatus {
  mode: 'off' | 'live'
  gmail: boolean
  gcal: boolean
}

export interface Metrics {
  active_leads: number
  high_priority: number
  followups_due: number
  appointments_booked: number
  avg_response_minutes: number
  agent_mode: string
  cloud_llm_requests: number
}

// Non-OK responses throw this. `message` keeps the legacy `"<status>: <body>"`
// shape (some callers match on the status prefix); `detail` carries the parsed
// JSON {"detail": "..."} body when the backend sent one.
export class ApiError extends Error {
  status: number
  detail?: string
  constructor(status: number, body: string) {
    super(`${status}: ${body}`)
    this.status = status
    try {
      const parsed = JSON.parse(body)
      if (parsed && typeof parsed.detail === 'string') this.detail = parsed.detail
    } catch {
      // body wasn't JSON — no detail
    }
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(import.meta.env.VITE_API_TOKEN ? { 'X-API-Token': import.meta.env.VITE_API_TOKEN } : {}),
    },
    ...init,
  })
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json()
}

export const api = {
  leads: () => req<Lead[]>('/leads?sort=priority'),
  lead: (id: number) => req<LeadProfile>(`/leads/${id}`),
  createLead: (raw_text: string, source = 'note') =>
    req<Lead>('/leads', { method: 'POST', body: JSON.stringify({ raw_text, source }) }),
  createLeadFields: (fields: Partial<Lead> & { name: string }) =>
    req<Lead>('/leads', { method: 'POST', body: JSON.stringify(fields) }),
  scanCard: (filename: string, data: string) =>
    req<{ extracted: Record<string, string>; duplicates: { lead: Lead; match_on: string }[]; image: string }>(
      '/scan-card',
      { method: 'POST', body: JSON.stringify({ filename, data }) },
    ),
  patchLead: (id: number, fields: Partial<Lead>) =>
    req<Lead>(`/leads/${id}`, { method: 'PATCH', body: JSON.stringify(fields) }),
  processLead: (id: number) =>
    req<{ lead: Lead; followup_draft: string }>(`/leads/${id}/process`, { method: 'POST' }),
  duplicates: (id: number) =>
    req<{ lead: Lead; match_on: string }[]>(`/leads/${id}/duplicates`),
  merge: (primary_id: number, duplicate_id: number) =>
    req<Lead>('/leads/merge', { method: 'POST', body: JSON.stringify({ primary_id, duplicate_id }) }),
  availability: (date: string) =>
    req<{ start_ts: string; end_ts: string }[]>(`/availability?date=${date}`),
  book: (lead_id: number, start_ts: string, end_ts: string, location?: string) =>
    req<Appointment>('/appointments', {
      method: 'POST',
      body: JSON.stringify({ lead_id, start_ts, end_ts, location }),
    }),
  appointments: () => req<Appointment[]>('/appointments'),
  chat: (message: string, session_id = 'dashboard') =>
    req<{ reply: string }>('/chat', { method: 'POST', body: JSON.stringify({ message, session_id }) }),
  chatHistory: (session_id = 'dashboard') =>
    req<ChatMessage[]>(`/chat/history?session_id=${encodeURIComponent(session_id)}`),
  chatSessions: () => req<ChatSession[]>('/chat/sessions'),
  clearChat: (session_id: string) =>
    req<{ deleted: number }>(`/chat/history?session_id=${encodeURIComponent(session_id)}`, { method: 'DELETE' }),
  audit: (limit = 30) => req<AuditRow[]>(`/audit?limit=${limit}`),
  metrics: () => req<Metrics>('/metrics'),
  addEvent: (lead_id: number, type: string, content: string) =>
    req<LeadEvent>(`/leads/${lead_id}/events`, {
      method: 'POST',
      body: JSON.stringify({ type, content }),
    }),
  scheduleReminder: (lead_id: number, due_ts: string, note?: string) =>
    req<Reminder>('/reminders', {
      method: 'POST',
      body: JSON.stringify({ lead_id, due_ts, note }),
    }),
  dueReminders: () => req<Reminder[]>('/reminders?due=1'),
  completeReminder: (id: number) => req<Reminder>(`/reminders/${id}`, { method: 'PATCH' }),
  advanceTime: (days = 3) =>
    req<{ neglected: Lead[] }>('/demo/advance-time', { method: 'POST', body: JSON.stringify({ days }) }),
  briefing: <T>(date: string) => req<T>(`/briefing?date=${date}`),
  summary: <T>(date: string) => req<T>(`/summary?date=${date}`),
  postInsights: <T>(payload: T) =>
    req<T>('/insights', { method: 'POST', body: JSON.stringify(payload) }),
  insightsFor: <T>(date: string) => req<T>(`/insights?date=${date}`),
  sendEmail: (lead_id: number, subject: string, body: string) =>
    req<{ sent: boolean; simulated: boolean }>('/email/send', {
      method: 'POST',
      body: JSON.stringify({ lead_id, subject, body }),
    }),
  integrationsStatus: () => req<IntegrationsStatus>('/integrations/status'),
}

// Calendar download must go through an authenticated fetch — a plain <a href>
// navigation cannot attach X-API-Token, so it would 401 once a token is set.
export const downloadIcs = async (appointmentId: number): Promise<void> => {
  const res = await fetch(`${BASE}/appointments/${appointmentId}/ics`, {
    headers: {
      ...(import.meta.env.VITE_API_TOKEN ? { 'X-API-Token': import.meta.env.VITE_API_TOKEN } : {}),
    },
  })
  if (!res.ok) throw new ApiError(res.status, await res.text())
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `appointment-${appointmentId}.ics`
  document.body.appendChild(a)
  a.click()
  // Firefox-safe: revoke/remove on next tick, after the click has been processed.
  setTimeout(() => {
    URL.revokeObjectURL(url)
    a.remove()
  }, 0)
}

export const fmtMoney = (n: number | null) =>
  n == null ? '—' : n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${(n / 1000).toFixed(0)}k`

// Appointment/slot timestamps are naive local time — format WITHOUT UTC conversion.
export const fmtLocal = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(iso).toLocaleString(undefined, opts)

// Local calendar date key (YYYY-MM-DD). NOT toISOString().slice(0,10) — that is
// the UTC date, which rolls over at 4/5pm Pacific and breaks every "today" lookup.
export const localDateKey = (d: Date = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

// Serialize a Date as naive local wall-clock — the API's one timestamp
// convention (schema.sql, parse_ts). NOT toISOString().slice(0,19) — that is
// UTC, which is the bug that made dashboard-created reminders land in
// Google Calendar 7-8 hours off. Use for every dashboard timestamp write
// (reminders, appointments, etc.), not just date-only keys (see localDateKey
// above for the date-only case).
export const toNaiveLocal = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export const fmtSlotTime = (iso: string) => fmtLocal(iso, { hour: 'numeric', minute: '2-digit' })

export const fmtSlotDay = (iso: string) =>
  fmtLocal(iso, { weekday: 'short', month: 'short', day: 'numeric' })

export const fmtDate = (iso: string) =>
  new Date(iso.endsWith('Z') ? iso : iso + 'Z').toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
