import { useState } from 'react'
import { api } from '../api'
import { toast } from './Toast'

// On-stage demo helper: fire the neglected-lead check on command instead of
// waiting for a real cron. Deliberately unobtrusive — judges see the effect,
// not the button.
export function DemoControls() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const advance = async () => {
    setBusy(true)
    try {
      const { neglected } = await api.advanceTime(3)
      toast(
        neglected.length
          ? `⏩ 3 days pass… agent flagged ${neglected.length} neglected lead${neglected.length > 1 ? 's' : ''}: ${neglected
              .slice(0, 3)
              .map((l) => l.name)
              .join(', ')}${neglected.length > 3 ? '…' : ''}`
          : '⏩ 3 days pass… no leads went neglected.',
      )
      setOpen(false)
    } catch {
      toast('⚠ advance-time failed — is the backend running?')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title="Demo controls"
        className="text-sub/60 hover:text-body text-sm px-1"
      >
        ⚙
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-40 w-64 rounded-lg border border-line bg-surface p-3 space-y-2 shadow-xl">
          <div className="text-xs font-semibold text-sub">Demo controls</div>
          <button
            onClick={advance}
            disabled={busy}
            className="w-full rounded-md bg-tile hover:bg-line disabled:opacity-50 px-3 py-2 text-sm text-left"
          >
            {busy ? 'Advancing…' : '⏩ Simulate 3 days passing'}
          </button>
          <div className="text-xs text-sub/60">
            Reset data: <code className="text-sub/80">python backend/seed.py</code>
          </div>
        </div>
      )}
    </div>
  )
}
