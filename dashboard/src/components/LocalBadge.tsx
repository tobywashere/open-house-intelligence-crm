import { Metrics } from '../api'

// The pitch badge: everything runs on the GB10, nothing goes to the cloud.
export function LocalBadge({ metrics }: { metrics: Metrics | null }) {
  const mode = metrics?.agent_mode ?? '…'
  const live = mode === 'openclaw'
  return (
    <div className="flex items-center gap-2 text-xs rounded-full border border-zinc-700 px-3 py-1.5">
      <span className={`h-2 w-2 rounded-full ${live ? 'bg-emerald-400' : 'bg-amber-400'}`} />
      <span className="text-zinc-300">
        {live ? 'Qwen 3.6 35B-A3B · Local on Dell GB10' : `Inference: ${mode} mode`}
      </span>
      <span className="text-zinc-600">|</span>
      <span className="text-zinc-400">Cloud LLM requests: {metrics?.cloud_llm_requests ?? 0}</span>
    </div>
  )
}
