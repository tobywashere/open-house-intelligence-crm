import { useEffect, useState } from 'react'
import { api, Appointment, fmtSlotDay, fmtSlotTime, icsUrl, IntegrationsStatus, localDateKey } from '../api'

interface Slot {
  start_ts: string
  end_ts: string
}

function nextTuesday(): string {
  const d = new Date()
  d.setDate(d.getDate() + ((2 - d.getDay() + 7) % 7 || 7))
  return localDateKey(d)
}

export function BookingCard({ leadId, onBooked }: { leadId: number; onBooked: () => void }) {
  const [date, setDate] = useState(nextTuesday())
  const [slots, setSlots] = useState<Slot[]>([])
  const [selected, setSelected] = useState<Slot | null>(null)
  const [location, setLocation] = useState('')
  const [booked, setBooked] = useState<Appointment | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [intg, setIntg] = useState<IntegrationsStatus | null>(null)
  useEffect(() => {
    api.integrationsStatus().then(setIntg).catch(() => {})
  }, [])

  const loadSlots = (d: string) => {
    setSelected(null)
    setError(null)
    api.availability(d).then(setSlots).catch(() => setSlots([]))
  }
  useEffect(() => {
    loadSlots(date)
  }, [date])

  const book = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      const appt = await api.book(leadId, selected.start_ts, selected.end_ts, location || undefined)
      setBooked(appt)
      onBooked()
    } catch (e) {
      if (e instanceof Error && e.message.startsWith('409')) {
        setError('That slot just got taken — pick another.')
        loadSlots(date)
      } else {
        setError('Booking failed — is the backend running?')
      }
    } finally {
      setBusy(false)
    }
  }

  if (booked) {
    return (
      <div className="rounded-lg border border-accent/30 bg-accent/5 p-4 text-sm space-y-1">
        <div className="text-accent font-medium">✓ Tour booked</div>
        <div>
          {fmtSlotDay(booked.start_ts)} · {fmtSlotTime(booked.start_ts)}–{fmtSlotTime(booked.end_ts)}
          {booked.location ? ` · ${booked.location}` : ''}
        </div>
        <a href={icsUrl(booked.id)} className="inline-block text-accent hover:underline">
          Download .ics ↓
        </a>
        {intg?.mode === 'live' && (
          <div className="text-xs text-accent">✓ Added to Google Calendar</div>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-tile bg-surface p-4 space-y-3">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold">Book a tour</h2>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="ml-auto rounded-md bg-surface border border-tile px-2 py-1 text-sm
                     [color-scheme:dark] focus:outline-none focus:border-accent"
        />
      </div>

      {slots.length === 0 ? (
        <div className="text-sm text-sub/80">No free slots this day — try another date.</div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {slots.map((s) => (
            <button
              key={s.start_ts}
              onClick={() => setSelected(s)}
              className={`rounded-md px-3 py-1.5 text-sm border transition-colors ${
                selected?.start_ts === s.start_ts
                  ? 'border-accent bg-accent/10 text-accent'
                  : 'border-line hover:border-[#4b5563]'
              }`}
            >
              {fmtSlotTime(s.start_ts)}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Location (optional)"
            className="flex-1 min-w-40 rounded-md bg-surface border border-tile px-2 py-1.5
                       text-sm placeholder:text-sub/50 focus:outline-none focus:border-accent"
          />
          <button
            onClick={book}
            disabled={busy}
            className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-sm font-medium"
          >
            {busy ? 'Booking…' : `Book ${fmtSlotDay(selected.start_ts)} ${fmtSlotTime(selected.start_ts)}`}
          </button>
        </div>
      )}

      {error && <div className="text-sm text-alert">{error}</div>}
    </div>
  )
}
