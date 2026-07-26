import { useState } from 'react'
import { api } from '../api'

// Demo story step 3: "Annie records a note and schedules a follow-up."
export function NoteBox({ leadId, onSaved }: { leadId: number; onSaved: () => void }) {
  const [note, setNote] = useState('')
  const [followupDays, setFollowupDays] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  const save = async () => {
    if (!note.trim() || busy) return
    setBusy(true)
    try {
      await api.addEvent(leadId, 'note', note.trim())
      let msg = 'Note saved.'
      if (followupDays !== null) {
        const due = new Date()
        due.setDate(due.getDate() + followupDays)
        due.setHours(9, 0, 0, 0)
        await api.scheduleReminder(leadId, due.toISOString().slice(0, 19), note.trim())
        msg = `Note saved · follow-up ${followupDays === 1 ? 'tomorrow' : `in ${followupDays} days`} at 9am.`
      }
      setNote('')
      setFollowupDays(null)
      setConfirmation(msg)
      setTimeout(() => setConfirmation(null), 4000)
      onSaved()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 space-y-2">
      <h2 className="text-sm font-semibold">Log a note</h2>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder='e.g. "Spoke on the phone — wants to see the Lakemont house this weekend"'
        className="w-full rounded-md bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm resize-y
                   placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500"
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-zinc-500">Remind me:</span>
        {[
          { label: 'tomorrow', days: 1 },
          { label: 'in 3 days', days: 3 },
          { label: 'in a week', days: 7 },
        ].map((o) => (
          <button
            key={o.days}
            onClick={() => setFollowupDays(followupDays === o.days ? null : o.days)}
            className={`rounded-md px-2 py-1 text-xs border transition-colors ${
              followupDays === o.days
                ? 'border-emerald-500 bg-emerald-500/15 text-emerald-300'
                : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
            }`}
          >
            {o.label}
          </button>
        ))}
        <button
          onClick={save}
          disabled={busy || !note.trim()}
          className="ml-auto rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
        >
          {busy ? 'Saving…' : followupDays !== null ? 'Save + schedule' : 'Save note'}
        </button>
      </div>
      {confirmation && <div className="text-xs text-emerald-400">✓ {confirmation}</div>}
    </div>
  )
}
