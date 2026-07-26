import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Reminder } from '../api'

// Polls for due follow-ups — this is the "scheduler fires a reminder in the UI" moment.
export function ReminderBanner() {
  const [due, setDue] = useState<Reminder[]>([])

  useEffect(() => {
    const load = () => api.dueReminders().then(setDue).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  if (!due.length) return null
  return (
    <div className="bg-alert/10 border-b border-alert/30 px-6 py-2 space-y-1">
      {due.map((r) => (
        <div key={r.id} className="flex items-center gap-3 text-sm">
          <span className="text-alert">⏰ Follow-up due:</span>
          <Link to={`/lead/${r.lead_id}`} className="font-medium hover:underline">
            {r.lead_name}
          </Link>
          <span className="text-sub">{r.note}</span>
          <button
            onClick={() => api.completeReminder(r.id).then(() => setDue((d) => d.filter((x) => x.id !== r.id)))}
            className="ml-auto text-xs text-sub hover:text-ink"
          >
            Done ✓
          </button>
        </div>
      ))}
    </div>
  )
}
