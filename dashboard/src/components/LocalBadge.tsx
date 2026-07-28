import { Metrics } from '../api'

// The local-inference badge: whatever model is wired up, nothing goes to the
// cloud. Shows the real agent_mode from /api/metrics rather than naming
// specific hardware — this repo runs on any tool-capable local model, not
// just the one it was demoed on.
export function LocalBadge({ metrics }: { metrics: Metrics | null }) {
  const mode = metrics?.agent_mode ?? '…'
  const live = mode === 'openclaw'
  return (
    <div className="flex items-center gap-2 text-xs rounded-full border border-line px-3 py-1.5">
      <span className={`h-2 w-2 rounded-full ${live ? 'bg-accent' : 'bg-sub'}`} />
      <span className="text-body">
        {live ? 'Local agent · live' : `Inference: ${mode} mode`}
      </span>
    </div>
  )
}
