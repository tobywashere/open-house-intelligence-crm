import { useState } from 'react'
import { api } from '../api'

// Demo story step 3: "Annie records a note and schedules a follow-up."
// "in 1 min" exists for the live demo — the reminder visibly fires in the
// banner while the audience watches (design doc F5 + scheduler-risk mitigation).
const OPTIONS = [
  { label: 'in 1 min ⚡', mins: 1 },
  { label: 'tomorrow', mins: 1440 },
  { label: 'in 3 days', mins: 4320 },
  { label: 'in a week', mins: 10080 },
]
export function NoteBox({ leadId, onSaved }: { leadId: number; onSaved: () => void }) {
  const [note, setNote] = useState('')
  const [isOffer, setIsOffer] = useState(false)
  const [followupMins, setFollowupMins] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  const save = async () => {
    if (!note.trim() || busy) return
    setBusy(true)
    try {
      // "offer" events are the agreed convention that feeds the funnel's
      // Offers Submitted stage (docs/FUNNEL-UI.md) — amount parsed from text
      await api.addEvent(leadId, isOffer ? 'offer' : 'note', note.trim())
      let msg = isOffer ? 'Offer logged — it now counts in the funnel.' : 'Note saved.'
      if (followupMins !== null) {
        const due = new Date(Date.now() + followupMins * 60_000)
        if (followupMins >= 1440) due.setHours(9, 0, 0, 0)
        await api.scheduleReminder(leadId, due.toISOString().slice(0, 19), note.trim())
        const opt = OPTIONS.find((o) => o.mins === followupMins)
        msg = `Note saved · follow-up ${opt?.label ?? 'scheduled'}.`
      }
      setNote('')
      setFollowupMins(null)
      setConfirmation(msg)
      setTimeout(() => setConfirmation(null), 4000)
      onSaved()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-tile bg-surface p-4 space-y-2">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{isOffer ? 'Log an offer' : 'Log a note'}</h2>
        <div className="ml-auto flex gap-1">
          {[
            { label: 'Note', offer: false },
            { label: '💰 Offer', offer: true },
          ].map((t) => (
            <button
              key={t.label}
              onClick={() => setIsOffer(t.offer)}
              className={`rounded-md px-2 py-0.5 text-[11px] border transition-colors ${
                isOffer === t.offer
                  ? 'border-accent bg-accent/10 text-accent'
                  : 'border-line text-sub hover:border-[#4b5563]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder={
          isOffer
            ? 'e.g. "Offer submitted: $1,250,000 on the Lakemont house"'
            : 'e.g. "Spoke on the phone — wants to see the Lakemont house this weekend"'
        }
        className="w-full rounded-md bg-surface border border-tile px-3 py-2 text-sm resize-y
                   placeholder:text-sub/50 focus:outline-none focus:border-accent"
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-sub/80">Remind me:</span>
        {OPTIONS.map((o) => (
          <button
            key={o.mins}
            onClick={() => setFollowupMins(followupMins === o.mins ? null : o.mins)}
            className={`rounded-md px-2 py-1 text-xs border transition-colors ${
              followupMins === o.mins
                ? 'border-accent bg-accent/10 text-accent'
                : 'border-line text-sub hover:border-[#4b5563]'
            }`}
          >
            {o.label}
          </button>
        ))}
        <button
          onClick={save}
          disabled={busy || !note.trim()}
          className="ml-auto rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
        >
          {busy ? 'Saving…' : followupMins !== null ? 'Save + schedule' : 'Save note'}
        </button>
      </div>
      {confirmation && <div className="text-xs text-accent">✓ {confirmation}</div>}
    </div>
  )
}
