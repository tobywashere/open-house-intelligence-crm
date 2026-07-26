// Typed client for the backend contract (docs/CONTRACT.md).
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api'

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

export interface Metrics {
  active_leads: number
  high_priority: number
  followups_due: number
  appointments_booked: number
  avg_response_minutes: number
  agent_mode: string
  cloud_llm_requests: number
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

export const api = {
  leads: () => req<Lead[]>('/leads?sort=priority'),
  lead: (id: number) => req<LeadProfile>(`/leads/${id}`),
  createLead: (raw_text: string, source = 'note') =>
    req<Lead>('/leads', { method: 'POST', body: JSON.stringify({ raw_text, source }) }),
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
  chatHistory: () => req<{ role: string; content: string; id: number }[]>('/chat/history'),
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
}

export const icsUrl = (appointmentId: number) => `${BASE}/appointments/${appointmentId}/ics`

export const fmtMoney = (n: number | null) =>
  n == null ? '—' : n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${(n / 1000).toFixed(0)}k`

// Appointment/slot timestamps are naive local time — format WITHOUT UTC conversion.
export const fmtLocal = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(iso).toLocaleString(undefined, opts)

export const fmtSlotTime = (iso: string) => fmtLocal(iso, { hour: 'numeric', minute: '2-digit' })

export const fmtSlotDay = (iso: string) =>
  fmtLocal(iso, { weekday: 'short', month: 'short', day: 'numeric' })

export const fmtDate = (iso: string) =>
  new Date(iso.endsWith('Z') ? iso : iso + 'Z').toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
