import { useState } from 'react'
import { api } from '../api'
import { toast } from './Toast'

// Free-form email to the lead via POST /email/send (Gmail when live, simulated
// when integrations are off). The backend logs the event + closed loop.
export function EmailCompose({ leadId, email, onSent }:
  { leadId: number; email: string; onSent: () => void }) {
  const [open, setOpen] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async () => {
    if (!subject.trim() || !body.trim()) return
    setBusy(true)
    try {
      const res = await api.sendEmail(leadId, subject, body)
      toast(res.simulated ? '✓ Simulated send — integrations off' : `✓ Emailed ${email}`)
      setSubject('')
      setBody('')
      setOpen(false)
      onSent()
    } catch {
      toast('✗ Send failed — try again')
    } finally {
      setBusy(false)
    }
  }

  if (!open)
    return (
      <button onClick={() => setOpen(true)} className="text-sm text-accent hover:underline">
        ✉ Compose email
      </button>
    )
  return (
    <div className="rounded-lg border border-tile bg-surface p-3 space-y-2">
      <div className="text-xs text-sub/80">Email {email}</div>
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="Subject"
        className="w-full rounded-md bg-tile border border-line px-2 py-1.5 text-sm"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Message…"
        rows={4}
        className="w-full rounded-md bg-tile border border-line px-2 py-1.5 text-sm"
      />
      <div className="flex gap-2">
        <button
          onClick={send}
          disabled={busy || !subject.trim() || !body.trim()}
          className="rounded-md bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-50 px-3 py-1.5 text-xs font-medium"
        >
          {busy ? 'Sending…' : 'Send via Gmail'}
        </button>
        <button onClick={() => setOpen(false)} className="text-xs text-sub hover:text-ink px-2">
          Cancel
        </button>
      </div>
    </div>
  )
}
