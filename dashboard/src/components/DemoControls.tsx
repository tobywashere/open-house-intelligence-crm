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
        className="text-zinc-600 hover:text-zinc-300 text-sm px-1"
      >
        ⚙
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-40 w-64 rounded-lg border border-zinc-700 bg-zinc-900 p-3 space-y-2 shadow-xl">
          <div className="text-xs font-semibold text-zinc-400">Demo controls</div>
          <button
            onClick={advance}
            disabled={busy}
            className="w-full rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 px-3 py-2 text-sm text-left"
          >
            {busy ? 'Advancing…' : '⏩ Simulate 3 days passing'}
          </button>
          <div className="text-xs text-zinc-600">
            Reset data: <code className="text-zinc-500">python backend/seed.py</code>
          </div>
        </div>
      )}
    </div>
  )
}
